"""
Escaner de red del HOST (corre FUERA del contenedor).

Por que vive aqui y no en el contenedor: para descubrir equipos hay que ver la
capa 2, o sea la tabla ARP del host. Un contenedor tiene su propia tabla ARP y
solo ve a su gateway, asi que desde ahi no se puede saber que maquinas hay en la
oficina. Este script corre en el host (que esta en la LAN), lee /proc/net/arp y
mete cada hallazgo en `HUB_NetworkScanResults` por medio del contenedor, que ya
tiene pymssql (en el host no esta instalado y no hace falta instalarlo).

El contenedor `network_scanner.py` sigue siendo el que procesa esos resultados
y calcula entradas/salidas. Este script solo produce.

Por que esta versionado: el equivalente anterior (`network_scanner_host.py`) vivia
solo en el repo del HUB, que se esta retirando. Al mover el stack a WebbApps, el
escaneo siguio consumiendo pero sin nadie que produjera, y nadie se entero en
cinco dias: el panel mostraba el latido del consumidor, no el ultimo escaneo real.

Que relacion tiene con el archivo del HUB (`/workspace/HUB/network_scanner_host.py`):
es el mismo trabajo reescrito, porque **no se puede correr tal cual en WebbApps**.
El viejo requiere en el host `arp-scan` y `pymssql`, y ahi no estan (arrancaria y
saldria con "pymssql no instalado"). Este usa lo que si hay en un Debian de
frescura: `ping` y `/proc/net/arp`, y delega el guardado al contenedor (que ya
tiene pymssql). Ademas cubre lo que al viejo le faltaba: entradas de ARP a medio
llenar, MACs multicast y un presupuesto de tiempo para los hostnames.

Los dos archivos comparten nombre a proposito, para que el que este en el host
sustituya al otro sin cambiar el nombre en el servicio.

Ciclo (cada 3 min, el mismo ritmo que el consumidor espera):
  1. Detecta la IP del host y calcula la subred /24.
  2. Ping rapido a todas las IPs de la subred (en paralelo).
  3. Lee la tabla ARP y se queda solo con las IPs que respondieron.
  4. Resuelve hostnames con PRESUPUESTO DE TIEMPO (un DNS lento cuelga el ciclo).
  5. Envia los hallazgos al contenedor, que los guarda.

    sudo python3 network_scanner_host.py            (una vez)
    sudo python3 network_scanner_host.py --once     (un solo ciclo)
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# Mismo ritmo que el consumidor: cada 180 s.
INTERVALO = 180

# Tiempo total para resolver hostnames. Medido en produccion: 26 hostnames en
# serie tardaron 186 s (un cliente DNS girando sin limite). Con presupuesto el
# ciclo se acaba puntual aunque el DNS no responda.
PRESUPUESTO_HOSTNAMES = 8.0

# Codigo de Python (base64) que corre DENTRO del contenedor y guarda los
# hallazgos. Va incrustado para que este script no dependa de que haya otro
# archivo desplegado dentro del contenedor.
PROGRAMA_INGESTA = '''
import json, sys
sys.path.insert(0, "/app")
try:
    import eccsa_db as db
except Exception as e:
    print("[scan_host] no se pudo importar eccsa_db:", e)
    sys.exit(1)
try:
    datos = json.load(sys.stdin)
except Exception as e:
    print("[scan_host] JSON invalido:", e)
    sys.exit(1)
n = 0
for d in datos:
    try:
        if db.register_scan_result(d.get("mac", ""), d.get("ip", ""), d.get("hostname")):
            n += 1
    except Exception as e:
        print("[scan_host] fallo guardando", d.get("ip"), e)
print("[scan_host] guardados", n, "de", len(datos))
'''


def log(mensaje):
    print(f"[scan_host {time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}", flush=True)


# ── Subred ───────────────────────────────────────────────────────────────────

def subred_desde_ip(ip):
    """
    Lista de IPs utilizables de la /24 a la que pertenece `ip`.

    Solo /24 a proposito: es la red de la oficina y es lo que el barrido anterior
    cubria. Con una mascara mas grande el ciclo se alargaria muchisimo.
    """
    try:
        red = ipaddress.ip_network(f"{ip}/24", strict=False)
    except ValueError:
        return []
    return [str(h) for h in red.hosts()]


def ip_local():
    """IP del host en la LAN (la del default gateway hacia internet)."""
    try:
        salida = subprocess.run(['ip', 'route', 'get', '1.1.1.1'],
                                capture_output=True, text=True, timeout=10).stdout
        m = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', salida)
        if m:
            return m.group(1)
    except Exception as e:
        log(f"no se pudo obtener la IP local ({e})")
    return None


# ── Tabla ARP ────────────────────────────────────────────────────────────────

_MAC_VALIDA = re.compile(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$')
_MULTICAST = re.compile(r'^(01:00:5E|33:33)', re.IGNORECASE)


def mac_valida(mac):
    """True si parece una MAC de un equipo real (6 bytes hex, no toda cero)."""
    if not mac:
        return False
    mac = mac.strip().upper()
    if not _MAC_VALIDA.match(mac):
        return False
    if mac == '00:00:00:00:00:00':
        return False
    if _MULTICAST.match(mac):
        return False
    return True


def leer_tabla_arp(contenido=None):
    """
    Lee /proc/net/arp y devuelve `[{ip, mac}]`.

    Se descartan las entradas incompletas: con flag 0x0 la MAC viene vacia o en
    ceros, y si se cuela una, el sistema de asistencia cuenta un equipo que no
    existe. El flag 0x2 es "completa".
    """
    if contenido is None:
        try:
            with open('/proc/net/arp', 'r', encoding='utf-8', errors='replace') as fh:
                contenido = fh.read()
        except OSError as e:
            log(f"no se pudo leer /proc/net/arp ({e})")
            return []

    filas = []
    for linea in contenido.splitlines()[1:]:
        partes = linea.split()
        if len(partes) < 4:
            continue
        ip, _hwtype, flags, mac = partes[0], partes[1], partes[2], partes[3]
        if flags != '0x2':
            continue                      # entrada a medio llenar
        mac = mac.strip().upper()
        if not mac_valida(mac):
            continue
        filas.append({'ip': ip, 'mac': mac})
    return filas


def es_mi_equipo(ip, filas):
    """True si la IP esta entre las que detectamos en el ping de este ciclo."""
    return any(f['ip'] == ip for f in filas)


# ── Descubrimiento ──────────────────────────────────────────────────────────

def barrer_subred(ips, timeout=1):
    """Ping en paralelo. Devuelve el set de IPs que respondieron."""
    def uno(ip):
        subprocess.run(['ping', '-c', '1', '-W', str(timeout), ip],
                       capture_output=True, timeout=timeout + 2)
        return ip
    with ThreadPoolExecutor(max_workers=64) as pool:
        return {ip for ip in pool.map(uno, ips) if ip}


def resolver_hostnames(ips, resolver=None, presupuesto=PRESUPUESTO_HOSTNAMES):
    """
    Resuelve nombre de cada IP, con tiempo total limitado.

    Es lo que hace que el ciclo no se cuelgue: si el DNS no responde, se sigue
    hasta que se acaba el presupuesto y se devuelve lo que haya.
    """
    if resolver is None:
        def resolver(ip):
            try:
                return socket.gethostbyaddr(ip)[0]
            except Exception:
                return None

    nombres = {}
    limite = time.monotonic() + presupuesto
    for ip in ips:
        if time.monotonic() >= limite:
            log(f"presupuesto de hostnames agotado: {len(nombres)} resueltos")
            break
        try:
            nombre = resolver(ip)
        except Exception:
            nombre = None
        if nombre:
            nombres[ip] = nombre
    return nombres


# ── Envio al contenedor ──────────────────────────────────────────────────────

def a_json(filas):
    """
    JSON ASCII-only. Via `docker exec ... python3 -c` un acento mal codificado
    rompe el comando entero y el ciclo se queda sin escanear.
    """
    return json.dumps(filas, ensure_ascii=True)


def enviar_a_contenedor(filas, contenedor='workersadmon'):
    """Manda los hallazgos al contenedor, que es quien tiene pymssql."""
    if not filas:
        return 0
    import base64
    prog = base64.b64encode(PROGRAMA_INGESTA.encode()).decode()
    cmd = ['docker', 'exec', '-i', contenedor, 'python3', '-c',
           f"import base64; exec(base64.b64decode('{prog}'))"]
    try:
        r = subprocess.run(cmd, input=a_json(filas).encode('ascii'),
                           capture_output=True, timeout=120)
        salida = (r.stdout or b'').decode('utf-8', 'replace').strip()
        if salida:
            log(salida)
        if r.returncode != 0:
            log(f"el contenedor respondio {r.returncode}: "
                f"{(r.stderr or b'').decode('utf-8', 'replace')[:200]}")
        return r.returncode
    except Exception as e:
        log(f"no se pudo enviar al contenedor ({e})")
        return -1


def un_ciclo(contenedor='workersadmon', timeout_ping=1):
    """Un ciclo completo. Devuelve cuantas filas seقية."""
    ip = ip_local()
    if not ip:
        log('sin IP local: no se puede barrer')
        return 0
    ips = subred_desde_ip(ip)
    if not ips:
        log(f'subred no valida para {ip}')
        return 0

    log(f'barrido de {len(ips)} IPs (host {ip})')
    respondieron = barrer_subred(ips, timeout=timeout_ping)
    tabla = leer_tabla_arp()
    # Solo lo que respondio a nuestro ping: la tabla ARP guarda tambien a quien
    # se cruzo hace rato, y ese no esta en la oficina ahora.
    filas = [f for f in tabla if f['ip'] in respondieron]

    nombres = resolver_hostnames([f['ip'] for f in filas])
    for f in filas:
        f['hostname'] = nombres.get(f['ip'])

    codigo = enviar_a_contenedor(filas, contenedor)
    if codigo != 0:
        log(f'NO se pudieron guardar las filas (rc={codigo}). '
            f'Este es el fallo que hay que ver: sin esto el escaner '
            f'sigue vivo pero no produce nada.')
    else:
        log(f'ciclo OK: {len(filas)} equipos en la red')
    return len(filas)


def main():
    ap = argparse.ArgumentParser(description='Escaner de red del host')
    ap.add_argument('--once', action='store_true', help='un solo ciclo y salir')
    ap.add_argument('--contenedor', default=os.environ.get(
        'HUB_SCANNER_CONTAINER', 'workersadmon'))
    args = ap.parse_args()

    log(f'arrancando (contenedor destino: {args.contenedor})')
    while True:
        try:
            un_ciclo(args.contenedor)
        except KeyboardInterrupt:
            log('detenido')
            break
        except Exception as e:
            # Un ciclo que revienta no debe matar el servicio: se reporta y se
            # sigue. Este es el otro modo de falla silencioso.
            log(f'ERROR en ciclo: {type(e).__name__}: {e}')
        if args.once:
            break
        time.sleep(INTERVALO)


if __name__ == '__main__':
    main()