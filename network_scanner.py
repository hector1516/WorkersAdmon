"""
network_scanner.py
==================
Worker que corre dentro del contenedor Docker bajo supervisor.
Procesa los resultados de ARP scan escritos por network_scanner_host.py,
detecta cambios de presencia (entrada/salida) y genera alertas Telegram.

Algoritmo mejorado (v2):
- Anti-duplicados: verifica último evento antes de registrar
- Consecutive scan counting: confirma cambios con N scans consecutivos
- Limpieza automática de scans antiguos (>7 días)
- Filtrado de MACs multicast/broadcast en procesamiento
"""

import sys
import os
import time
import datetime

# Rutas donde puede estar `eccsa_db`. "/app" es el contenedor. Las del HUB
# viejo ("/workspace/hub_repo", "/workspace/HUB") se quitaron a proposito:
# existen en maquinas donde el repo del HUB esta clonado junto a este, y meterlas
# al principio del sys.path hacia que ese HUB **sombree los modulos de este
# repo** (p.ej. su network_scanner_host.py, que es otro archivo distinto). No
# hizo falta: este worker solo depende de eccsa_db, que esta junto a el.
for p in ("/app", os.path.dirname(os.path.abspath(__file__))):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    import eccsa_db as db
except Exception as e:
    print(f"[{datetime.datetime.now()}] Core Import Error: {e}")
    sys.exit(1)


def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


MACS_INVALIDAS = {
    '00:00:00:00:00:00', 'FF:FF:FF:FF:FF:FF', 'DESCONOCIDA',
}


def es_mac_util(mac):
    """Descarta multicast, broadcast, MACs inválidas y desconocidas."""
    mac = mac.strip().upper()
    if mac in MACS_INVALIDAS:
        return False
    parts = mac.split(':')
    if len(parts) != 6:
        return False
    try:
        primer_byte = int(parts[0], 16)
    except ValueError:
        return False
    if primer_byte & 0x01:
        return False
    return True


def get_network_config():
    # Defaults alineados con la migración 0042: la tolerancia de ENTRADA es 0
    # porque es un retardo, no una tolerancia (ver procesar_scans_pendientes).
    config = {
        'net_tolerance_salida_min': '5',
        'net_tolerance_entrada_min': '0',
        'net_scan_interval_seg': '180',
        'net_scan_enabled': '1',
    }
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Clave, Valor FROM HUB_Config WHERE Clave LIKE 'net_%'")
                for row in cur.fetchall():
                    config[row['Clave']] = row['Valor']
    except Exception as e:
        print(f"[{ts()}] Error leyendo config: {e}")
    return config


def get_proximo_scan_pendiente():
    """
    El escaneo pendiente MÁS ANTIGUO, con los MACs que se detectaron en él.

    OJO con el orden: antes pedía el más NUEVO (`ORDER BY FechaScan DESC`) y
    como el bucle repite hasta vaciar la cola, los escaneos se procesaban del
    más reciente al más viejo. El máquina de estados (¿estaba FUERA? ¿ya volvió
    a aparecer?) necesita la secuencia real: al revés, un ENTRADA se podía
    registrar con la hora de un escaneo posterior al del SALIDA que lo mêmes
    dispositivo había generado en el mismo lote.
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 FechaScan
                    FROM HUB_NetworkScanResults
                    WHERE Procesado = 0
                    ORDER BY FechaScan ASC
                """)
                row = cur.fetchone()
                if not row:
                    return None, set()

                fecha_scan = row['FechaScan']

                cur.execute("""
                    SELECT MACAddress
                    FROM HUB_NetworkScanResults
                    WHERE FechaScan = %s AND Procesado = 0
                """, (fecha_scan,))

                macs = set()
                for r in cur.fetchall():
                    mac = r['MACAddress'].strip().upper()
                    if es_mac_util(mac):
                        macs.add(mac)

                return fecha_scan, macs
    except Exception as e:
        print(f"[{ts()}] Error obteniendo scans: {e}")
        return None, set()


def mark_scan_processed(fecha_scan):
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_NetworkScanResults
                    SET Procesado = 1
                    WHERE FechaScan = %s AND Procesado = 0
                """, (fecha_scan,))
                conn.commit()
    except Exception as e:
        print(f"[{ts()}] Error marcando scan procesado: {e}")


def get_all_device_states():
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk,
                           u.Nombre AS NombreUsuario
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE d.Activo = 1
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"[{ts()}] Error obteniendo estados: {e}")
        return []


def get_last_presence_event(device_id):
    """Obtiene el último evento de presencia de un dispositivo."""
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 TipoEvento, FechaHora
                    FROM HUB_NetworkPresence
                    WHERE IdDispositivo = %s
                    ORDER BY FechaHora DESC
                """, (device_id,))
                return cur.fetchone()
    except Exception:
        return None


def count_scans_since(fecha_desde):
    """Cuenta cuántos scans hubo desde una fecha."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT FechaScan)
                    FROM HUB_NetworkScanResults
                    WHERE FechaScan > %s
                """, (fecha_desde,))
                return cur.fetchone()[0]
    except Exception:
        return 0


def update_device_state(device_id, estado, momento=None):
    """
    Delega en la capa de datos: hay UNA sola copia de esta función.

    El worker tenía su propia versión y `eccsa_db` tenía otra, y divergieron:
    la de acá guardaba el instante del escaneo y la de allá sellaba con la hora
    de proceso y en la rama FUERA no guardaba ningún instante. Dos
    implementaciones del mismo MERGE, con la mitad del arreglado: si mañana se
    toca una y no la otra, el sistema se contradice en silencio, que es la
    peor forma de fallar. Ahora la verdad está en `eccsa_db` (la capa de datos,
    donde debe estar) y el worker la usa.
    """
    return db.update_device_state(device_id, estado, momento)


def register_presence_event(device_id, tipo_evento, confianza=95.0, notas='',
                           fecha_deteccion=None, ultima_vez_visto=None,
                           incertidumbre_min=None, origen='RED'):
    """Delega en eccsa_db; ver update_device_state por qué ya no hay copia aquí."""
    return db.register_presence_event(
        device_id, tipo_evento, confianza, notas,
        fecha_deteccion=fecha_deteccion,
        ultima_vez_visto=ultima_vez_visto,
        incertidumbre_min=incertidumbre_min,
        origen=origen)


def get_device_name(device):
    nombre = device.get('NombreDispositivo') or device.get('NombreUsuario') or ''
    if not nombre:
        nombre = f"Dispositivo {device.get('MACAddress', '???')}"
    return nombre


def alertar_cambio_presencia(device, tipo_evento):
    try:
        from telegram_alerts import _queue_alert, _now_str
        fecha, hora = _now_str()
        nombre_usuario = device.get('NombreUsuario') or 'Sin asignar'
        nombre_disp = device.get('NombreDispositivo') or device.get('MACAddress', '???')
        evento_id = 'DISPOSITIVO_RED_ENTRADA' if tipo_evento == 'ENTRADA' else 'DISPOSITIVO_RED_SALIDA'
        datos = {
            'Usuario': nombre_usuario,
            'Dispositivo': nombre_disp,
            'MAC': device.get('MACAddress', ''),
            'Tipo': device.get('Tipo', ''),
            'Fecha': fecha,
            'Hora': hora,
        }
        count = _queue_alert(evento_id, datos)
        if count > 0:
            print(f"[{ts()}] Alerta Telegram encolada: {evento_id} para {nombre_usuario} ({count} dest.)")
    except Exception as e:
        print(f"[{ts()}] Error en alerta Telegram: {e}")


def limpiar_scans_antiguos():
    """Elimina scan results de más de 7 días para liberar espacio."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
                cur.execute("""
                    DELETE FROM HUB_NetworkScanResults
                    WHERE FechaScan < %s AND Procesado = 1
                """, (cutoff,))
                eliminados = cur.rowcount
                conn.commit()
                if eliminados > 0:
                    print(f"[{ts()}] Limpieza: {eliminados} scan results antiguos eliminados")
    except Exception as e:
        print(f"[{ts()}] Error en limpieza: {e}")


def limpiar_eventos_antiguos():
    """Elimina historial de presencia de más de 90 días."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cutoff = datetime.datetime.now() - datetime.timedelta(days=90)
                cur.execute("""
                    DELETE FROM HUB_NetworkPresence
                    WHERE FechaHora < %s
                """, (cutoff,))
                eliminados = cur.rowcount
                conn.commit()
                if eliminados > 0:
                    print(f"[{ts()}] Limpieza: {eliminados} eventos presencia antiguos eliminados")
    except Exception as e:
        print(f"[{ts()}] Error en limpieza eventos: {e}")


def procesar_scans_pendientes():
    """
    Procesa TODOS los escaneos pendientes, en orden cronológico.

    El instante de referencia de cada transición es el `FechaScan` del escaneo
    que la produjo, no la hora en que este bucle la está viendo. Con el
    intervalo de 180s, usar la hora de proceso metía 1-4 minutos de retraso
    inventado encima de la llegada real.
    """
    config = get_network_config()

    if config.get('net_scan_enabled', '1') != '1':
        return

    # Tolerancias ASIMÉTRICAS, y a propósito:
    # · la de ENTRADA no es una tolerancia de puntualidad, es un retardo. Con 2
    #   minutos, el que llegaba 8:58 y se le veía a las 8:59 todavía no
    #   registraba nada: la entrada se coría sola. En 0, cualquier reaparición
    #   tras un FUERA es una entrada.
    # · la de SALIDA sí es un debounce de verdad: evita que un AP que se cae un
    #   escaneo genere una salida falsa. Y nunca puede bajar de DOS intervalos
    #   de escaneo, o un solo escaneo perdido se convertiría en "se fue".
    intervalo_min = max(1, int(config.get('net_scan_interval_seg', '180') or 180) // 60)
    tol_entrada = int(config.get('net_tolerance_entrada_min', '0') or 0)
    tol_salida = max(int(config.get('net_tolerance_salida_min', '5') or 5),
                     intervalo_min * 2)

    procesados = 0

    while True:
        fecha_scan, macs_detectadas = get_proximo_scan_pendiente()
        if fecha_scan is None:
            break

        # El instante del escaneo es el "ahora" de este lote. Si por lo que sea
        # no vino (escaneo muy viejo), se usa la hora real para no escribir
        # tiempos en el pasado.
        momento = fecha_scan or datetime.datetime.now()

        mark_scan_processed(fecha_scan)
        procesados += 1

        if not macs_detectadas:
            continue

        dispositivos = get_all_device_states()
        dev_map = {}
        for d in dispositivos:
            mac = d['MACAddress'].strip().upper()
            dev_map[mac] = d

        for mac in macs_detectadas:
            if mac not in dev_map:
                continue

            dev = dev_map[mac]
            device_id = dev['Id']
            estado_actual = dev['Estado']

            if estado_actual == 'FUERA':
                ultimo_ok = dev.get('UltimoScanOk')
                if not ultimo_ok:
                    register_presence_event(
                        device_id, 'ENTRADA', 100.0, 'Primer registro',
                        fecha_deteccion=momento, ultima_vez_visto=None,
                        incertidumbre_min=intervalo_min)
                    alertar_cambio_presencia(dev, 'ENTRADA')
                    update_device_state(device_id, 'AQUI', momento)
                    print(f"[{ts()}] ENTRADA (primer registro): {get_device_name(dev)}")
                    continue

                minutos_ausente = (momento - ultimo_ok).total_seconds() / 60
                if minutos_ausente < tol_entrada:
                    update_device_state(device_id, 'AQUI', momento)
                    continue

                ultimo_evento = get_last_presence_event(device_id)
                if ultimo_evento and ultimo_evento['TipoEvento'] == 'ENTRADA':
                    update_device_state(device_id, 'AQUI', momento)
                    continue

                # La llegada ocurrió ENTRE la última vez que se vio ausente
                # (ultimo_ok) y este escaneo. Se guardan los dos: la asistencia
                # con eso sabe que fue un intervalo y no un minuto.
                register_presence_event(
                    device_id, 'ENTRADA', 95.0,
                    f'Ausente {minutos_ausente:.0f}min, tolerancia {tol_entrada}min',
                    fecha_deteccion=momento,
                    ultima_vez_visto=ultimo_ok,
                    incertidumbre_min=int(round(minutos_ausente)))
                alertar_cambio_presencia(dev, 'ENTRADA')
                update_device_state(device_id, 'AQUI', momento)
                print(f"[{ts()}] ENTRADA: {get_device_name(dev)} "
                      f"(ventana {ultimo_ok}–{momento}, ausente {minutos_ausente:.0f}min)")

            elif estado_actual == 'AQUI':
                update_device_state(device_id, 'AQUI', momento)

        for mac, dev in dev_map.items():
            if mac in macs_detectadas:
                continue
            if dev['Estado'] != 'AQUI':
                continue

            device_id = dev['Id']
            ultimo_ok = dev.get('UltimoScanOk')
            if not ultimo_ok:
                continue

            minutos_ausente = (momento - ultimo_ok).total_seconds() / 60
            if minutos_ausente < tol_salida:
                continue

            ultimo_evento = get_last_presence_event(device_id)
            if ultimo_evento and ultimo_evento['TipoEvento'] == 'SALIDA':
                continue

            # La salida ocurrió ENTRE el último escaneo que lo vio (ultimo_ok) y
            # este, que ya no lo vio. Ojo al orden: aquí los dos extremos están
            # al revés que en la entrada, y por eso la asistencia los invierte
            # al usarlos. `FechaDeteccion` es el escaneo que constató la
            # ausencia (el techo de la salida) y `UltimaVezVisto` la última vez
            # que se vio presente (el piso).
            register_presence_event(
                device_id, 'SALIDA', 95.0,
                f'Ausente {minutos_ausente:.0f}min, tolerancia {tol_salida}min',
                fecha_deteccion=momento,
                ultima_vez_visto=ultimo_ok,
                incertidumbre_min=int(round(minutos_ausente)))
            alertar_cambio_presencia(dev, 'SALIDA')
            update_device_state(device_id, 'FUERA', momento)
            print(f"[{ts()}] SALIDA: {get_device_name(dev)} "
                  f"(ventana {ultimo_ok}–{momento}, ausente {minutos_ausente:.0f}min)")

    if procesados > 0:
        print(f"[{ts()}] Procesados {procesados} scans en lote")


def run_loop():
    print(f"[{ts()}] === Network Scanner Worker v2 iniciado ===")

    ciclo_limpieza = 0

    while True:
        try:
            procesar_scans_pendientes()
        except Exception as e:
            print(f"[{ts()}] Error en ciclo: {e}")

        ciclo_limpieza += 1
        if ciclo_limpieza >= 360:
            try:
                limpiar_scans_antiguos()
                limpiar_eventos_antiguos()
                ciclo_limpieza = 0
            except Exception as e:
                print(f"[{ts()}] Error en limpieza: {e}")

        time.sleep(60)


if __name__ == '__main__':
    run_loop()
