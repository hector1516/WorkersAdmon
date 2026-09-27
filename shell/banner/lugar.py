"""
ECCSA-Shell · lugar.py — regla de ubicación (oficina o remoto) del banner.
=========================================================================
Fuente ÚNICA de la regla que usan las 3 apps. No editar la copia de cada
app: se propaga con tools/sync_shell.py (ver docs/CONTRATO.md).

Regla: si la IP del cliente es privada o de loopback, está en la red de ECCSA
(la oficina); si es pública, está remoto; si no se puede saber, desconocido.

Por qué el orden de las cabeceras importa
-----------------------------------------
Delante de las apps hay un nginx en el mismo host, y ese nginx **reescribe**
X-Real-IP con el $remote_addr del proxy inmediato (la IP del puente de
Docker), no la del usuario. Por eso:

  1. X-Forwarded-For, TOMANDO LA PRIMERA entrada = el cliente original. Es la
     única fuente fiable cuando hay un proxy delante.
  2. X-Real-IP — solo si no hay X-Forwarded-For.
  3. El host del socket (request.client.host / client_address[0]).

Leer X-Real-IP primero hace que todo el mundo caiga en "oficina", porque esa
IP siempre es privada. Field ya tenía este caso documentado en
api/routers/online.py; acá queda la regla para las 3.

Solo biblioteca estándar: lo importan tanto FastAPI (Field, Admon) como el
panel de WorkersAdmon, que es http.server pelado.
"""
import ipaddress

# Los 3 valores posibles de lugar.modo en el contrato del banner.
OFICINA = "oficina"
REMOTO = "remoto"
DESCONOCIDO = "desconocido"


def es_red_eccsa(ip):
    """True si la IP es privada o de loopback, es decir la red de ECCSA.

    Acepta IPv4 e IPv6. Una IP vacía, con espacios o no parseable devuelve
    False: preferimos "remoto" a inventar una oficina.
    """
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except (ValueError, TypeError):
        return False
    return addr.is_private or addr.is_loopback


def lugar_de_ip(ip):
    """'oficina' | 'remoto' | 'desconocido' a partir de la IP (o del vacío)."""
    limpio = (ip or "").strip()
    if not limpio:
        return DESCONOCIDO
    try:
        ipaddress.ip_address(limpio)
    except ValueError:
        return DESCONOCIDO
    return OFICINA if es_red_eccsa(limpio) else REMOTO


def ip_de_headers(headers, client_host=""):
    """Saca la IP real del cliente de unas cabeceras + el host del socket.

    `headers` solo necesita tener .get(cabecera) → sirve para los Headers de
    Starlette/FastAPI y para el email.message.Message de http.server.
    """
    if headers is not None:
        try:
            # 1) X-Forwarded-For: la PRIMERA entrada es el cliente original.
            #    Las siguientes son los proxies que agregó cada salto.
            xff = headers.get("X-Forwarded-For") or ""
            if xff.split(",")[0].strip():
                return xff.split(",")[0].strip()
            # 2) X-Real-IP, solo como respaldo.
            real = headers.get("X-Real-IP") or ""
            if real.strip():
                return real.strip()
        except AttributeError:
            pass
    # 3) El socket.
    return (client_host or "").strip()


def lugar_de(headers, client_host=""):
    """(modo, ip) del banner. Es la función que usan las 3 apps.

    headers     -> lo que devuelva request.headers (ASGI) o handler.headers
                   (http.server). Puede ser None.
    client_host -> request.client.host o handler.client_address[0].
    """
    ip = ip_de_headers(headers, client_host)
    return lugar_de_ip(ip), ip


def lugar_de_handler(handler):
    """(modo, ip) para un handler de http.server (el panel de WorkersAdmon).

    OJO: no usar auth.client_ip() del panel para esto — esa función existe para
    el límite de intentos de login y mira X-Real-IP primero, que bajo nginx
    devuelve el puente de Docker y hace que todos parezcan estar en oficina.
    """
    host = ""
    try:
        host = handler.client_address[0]
    except (AttributeError, IndexError, TypeError):
        host = ""
    return lugar_de(getattr(handler, "headers", None), host)
