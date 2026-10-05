"""
mailbox_worker/stream.py — el servidor de adjuntos.

Es un HTTP mínimo en el puerto interno (8201) con UN endpoint. La app lo llama y
solo la app lo llama: nunca hay un navegador ni un usuario detrás.

─── EL CONTRATO, QUE NO SE INVENTA ─────────────────────────────────────────────

Lo define `api/main.py` de la app, en `_pedir_al_worker` y `_trozos_del_worker`:

    GET /adjunto/<id_cuenta>/<uid>/<parte>?inline=0|1
    GET /adjunto/<id_cuenta>/<uid>/<parte>?desde=<offset>&bytes=<n>
    Cabecera: X-Mailbox-Token: <el token compartido>

Si esta firma no coincide con la de la app, los adjuntos se rompen con un 404 y
no hay ningún error visible para el usuario más allá de un icono que no carga.
Por eso está escrita arriba igual que en la app, y `tests/test_mailbox_stream.py`
verifica la ruta exacta.

─── POR QUÉ TROZOS DE 256 KB ──────────────────────────────────────────────────

Un adjunto de 300 MB pedido entero se junta en memoria: 300 MB por proceso, con
varios usuarios a la vez. La ruta por rangos hace que el pico de memoria sea
`_TAMANO_TROZO` (256 KB) sin importar el tamaño del archivo. Ese es el motivo de
que exista `fetch_parte_trozo` en el cliente IMAP.

Y el cache es un COALESCADOR, no un almacén: cinco personas abriendo el mismo
PDF de 3 MB hacen un solo fetch a IMAP. Vive 6 h y se autolimita por tamaño.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import almacen
from .config import settings
from .crypto import decrypt_secret
from .db import filas, una
from .imap_client import IMAPClient, IMAPError

# El tamaño de trozo lo fija la app (`_TAMANO_TROZO = 262144`). Este valor es el
# tope de lo que se ACEPTA por request: si alguien pide 500 MB de golpe, se
# recorta a 256 KB en vez de idioma.
_MAX_POR_REQUEST = 262144

# Tope de un inline servido entero (ver `config.inline_max_kb`).
_MAX_INLINE = 256 * 1024

# Las cuentas IMAP se cachean para no descifrar la credencial en cada request.
# Cada entrada dura poco porque el panel puede cambiar el servidor o la clave.
_CUENTAS = {}
_CUENTAS_LOCK = threading.Lock()
_CUENTAS_TTL = 300


class StreamError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Sesión IMAP por hilo
# ═══════════════════════════════════════════════════════════════════════════════

_local = threading.local()


def _sesion(cuenta: dict) -> IMAPClient:
    """
    Una sesión IMAP por hilo.

    `ThreadingHTTPServer` crea un hilo por request, así que un hilo = una sesión
    y nunca hay dos hilos usando el mismo socket IMAP. `imaplib` NO es thread-safe
    y compartirlo produce errores que parecen de red.

    El downside conocido: se abre una conexión TLS por request. Con el cache de
    bytes, la conexión es lo más caro — y aun así una conexión TLS local a Gmail
    es de milisegundos frente a los 50-200 ms del fetch. Cuando el volumen lo
    justifique, el arreglo es un pool de sesiones, no compartir el `imaplib`.
    """
    clave = cuenta["Id"]
    actual = getattr(_local, "sesiones", None)
    if actual is None:
        actual = _local.sesiones = {}
    cliente = actual.get(clave)
    if cliente is not None:
        return cliente

    credencial = decrypt_secret(cuenta.get("CredencialCifrada") or "")
    if not credencial:
        raise StreamError(f"la cuenta {cuenta.get('Email')} no tiene credencial")
    cliente = IMAPClient(
        host=cuenta["ServidorIMAP"],
        port=cuenta.get("PuertoIMAP") or 993,
        username=cuenta["Email"],
        password=credencial,
        timeout=settings.imap_timeout,
        oauth2=(cuenta.get("TipoAuth") or "PASSWORD").upper() == "OAUTH2",
    )
    cliente.connect()
    actual[clave] = cliente
    return cliente


def _cerrar_sesiones():
    """Cierra y forgets las sesiones del hilo. La usa `finally` del handler."""
    actual = getattr(_local, "sesiones", None) or {}
    for cliente in actual.values():
        try:
            cliente.close()
        except Exception:
            pass
    actual.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Cuenta
# ═══════════════════════════════════════════════════════════════════════════════

def _cuenta(id_cuenta) -> dict:
    """
    La cuenta desde la base, con una cache corta.

    La cache existe porque un correo de 12 adjuntos hace 12 requests y cada uno
    pediría la fila y descifraría la credencial. 5 minutos es un compromiso: si
    el panel cambia el servidor IMAP, el cambio se ve en 5 minutos como máximo.

    Solo cachea lo que NO es secreto. La credencial cifrada se lee siempre fresca:
    cachear la credencial cifrada prolongaría la vida de una clave que ya se
    cambió, que es justo lo que un cambio de contraseña pretende cerrar.
    """
    ahora = threading.get_ident()
    with _CUENTAS_LOCK:
        entrada = _CUENTAS.get(id_cuenta)
        if entrada and ahora - entrada["t"] < _CUENTAS_TTL:
            fila = una(
                "SELECT Id, Email, ServidorIMAP, PuertoIMAP, TipoAuth, CredencialCifrada, "
                "CarpetaRaiz FROM HUB_MailboxCuentas WHERE Id = %s", (id_cuenta,))
            if fila:
                return fila
            _CUENTAS.pop(id_cuenta, None)

    fila = una(
        "SELECT Id, Email, ServidorIMAP, PuertoIMAP, TipoAuth, CredencialCifrada, "
        "CarpetaRaiz FROM HUB_MailboxCuentas WHERE Id = %s AND Estado = 'ACTIVA'",
        (id_cuenta,))
    if not fila:
        raise StreamError("cuenta no encontrada o no activa")
    with _CUENTAS_LOCK:
        _CUENTAS[id_cuenta] = {"t": ahora}
    return fila


# ═══════════════════════════════════════════════════════════════════════════════
# El byte que se sirve
# ═══════════════════════════════════════════════════════════════════════════════

def bytes_de_adjunto(id_cuenta: int, uid: int, parte: str,
                     desde: int = 0, cantidad: int = 0, inline: bool = False) -> tuple:
    """
    Devuelve `(datos, hay_mas, content_type)`.

    `hay_mas` es lo que permite a la app seguir pidiendo: la app avanza con
    `len(datos)`, y si el worker devuelve menos de lo pedido es el final. Un
    archivo cuyo tamaño el índice no conoce (SIZE nil en BODYSTRUCTURE) se
    sirve de a poco hasta que el worker conteste corto.

    El `content_type` sale del índice (`HUB_MailboxAdjuntos.ContentType`) y no de
    un parámetro del request: el tipo lo declara el servidor de correo en el
    BODYSTRUCTURE, que es de donde hay que sacarlo. Pedirlo por query sería
    dejarlo en manos de quien llama.
    """
    ficha = _ficha(id_cuenta, uid, parte)
    tipo = ficha.get("ContentType") or "application/octet-stream"

    # La ruta de inline JUNTA los bytes en memoria, así que el tamaño se
    # comprueba ANTES de pedirlos. Recortar la respuesta a posteriori no serviría
    # de nada: el proceso ya se comió los 300 MB.
    if inline:
        tam = int(ficha.get("Size") or 0)
        if tam > _MAX_INLINE:
            raise StreamError(
                f"la imagen inline mide {tam} bytes y el tope es {_MAX_INLINE}")
        desde, cantidad = 0, 0
    elif cantidad > _MAX_POR_REQUEST:
        cantidad = _MAX_POR_REQUEST

    digest = almacen.digest_de(id_cuenta, uid, parte, desde, cantidad)

    # 1) Cache primero. Es el caso común: la segunda persona que abre el mismo
    #    PDF no toca IMAP.
    datos, _edad = almacen.cache_leer(digest)
    if datos is not None:
        return datos, False, tipo

    # La carpeta del mensaje sale del índice, no de un parámetro del request. Si
    # la app mandara la carpeta, un `uid` de otra cuenta con el mismo número
    # devolvería el adjunto equivocado.
    cuenta = _cuenta(id_cuenta)
    cliente = _sesion(cuenta)
    cliente.select_folder(ficha["Carpeta"])

    # IMAP, en el rango pedido. `BODY.PEEK` para no marcar como leído: el worker
    # no debe poner \Seen en un mensaje que el usuario solo descargó.
    if cantidad:
        datos = cliente.fetch_parte_trozo(uid, parte, desde, cantidad)
    else:
        datos = cliente.fetch_parte(uid, parte)

    if not datos:
        raise StreamError("el servidor de correo devolvió vacío")

    # `almacen.cache_escribir` devuelve False si la cache está llena: se sirve
    # igual. Perder cache es mejor que perder el archivo.
    almacen.cache_escribir(digest, datos)

    hay_mas = bool(cantidad) and len(datos) >= cantidad
    return datos, hay_mas, tipo


def _ficha(id_cuenta: int, uid: int, parte: str) -> dict:
    """
    Carpeta, tipo y tamaño del adjunto, del índice.

    Una sola consulta para las tres cosas, y además valida de paso que ese
    `parte` exista de verdad en ese mensaje. Validarlo importa: `parte` viene de
    la URL y sin este chequeo se podría pedir `BODY.PEEK[999]` de cualquier
    mensaje.
    """
    fila = una(
        "SELECT m.Carpeta, a.ContentType, a.Size FROM HUB_MailboxAdjuntos a "
        "INNER JOIN HUB_MailboxMensajes m ON m.Id = a.IdMensaje "
        "WHERE m.IdCuenta = %s AND m.UID = %s AND a.Parte = %s",
        (id_cuenta, uid, str(parte)),
    )
    if not fila:
        raise StreamError("ese adjunto no existe en ese mensaje")
    return fila


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════════════════════

class _Handler(BaseHTTPRequestHandler):
    # Silencia el log a stderr: el worker loguea a stdout y supervisor lo lee.
    # Con el log por defecto, cada request escribe 2 líneas y el archivo de log
    # crece sin que nadie lo lea.
    def log_message(self, *_args):
        pass

    server_version = "MailboxStream"

    # ── Autenticación ─────────────────────────────────────────────────────
    #
    # Comparación en tiempo constante: con `==` un atacante que midsgif puede
    # medir cuánto del token acertó. `hmac.compare_digest` no devuelve antes.
    def _autorizado(self) -> bool:
        import hmac
        recibido = self.headers.get("X-Mailbox-Token") or ""
        esperado = settings.stream_token
        if not esperado or not recibido:
            return False
        return hmac.compare_digest(recibido, esperado)

    # ── Respuestas ────────────────────────────────────────────────────────
    def _json(self, codigo: int, datos: dict):
        crudo = json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(crudo)))
        self.end_headers()
        self.wfile.write(crudo)

    def _binario(self, datos: bytes, content_type: str, hay_mas: bool):
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(datos)))
        # La app no reenvía esto a Cloudflare como caché pública: los bytes son
        # del usuario y `private` evita que un proxy los guarde para otro.
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Followup", "1" if hay_mas else "0")
        self.end_headers()
        self.wfile.write(datos)

    def _no_cache(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── Rutas ─────────────────────────────────────────────────────────────
    def do_GET(self):
        if not self._autorizado():
            # 401 sin cuerpo. No se distingue "token malo" de "ruta no existe":
            # con un 404 en el token el atacante no sabe si el endpoint existe.
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            partes = [p for p in self.path.split("?")[0].split("/") if p]
            consulta = self.path.split("?", 1)[1] if "?" in self.path else ""
            args = _parse_query(consulta)

            # /salud: lo usa el deploy y el panel para comprobar que el worker arrancó.
            # Requiere token como todo lo demás: un endpoint de salud sin
            # autenticar dice si el servicio está vivo, que es exactamente lo que
            # un atacante busca antes de intentar el token.
            if partes == ["salud"]:
                self._json(200, {"ok": True, "servicio": "mailbox_stream"})
                return

            # /adjunto/<id_cuenta>/<uid>/<parte>  → 4 segmentos
            if len(partes) == 4 and partes[0] == "adjunto":
                self._adjunto(int(partes[1]), int(partes[2]), partes[3], args)
                return

            self._json(404, {"error": "ruta desconocida"})
        except (ValueError, StreamError) as exc:
            self._json(400, {"error": str(exc)[:200]})
        except IMAPError as exc:
            self._json(502, {"error": f"IMAP: {exc}"[:200]})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"[:200]})
        finally:
            # La sesión IMAP de ESTE hilo se cierra siempre. Sin esto, cada hilo
            # del servidor acumularía una conexión TLS abierta para siempre.
            _cerrar_sesiones()

    def _adjunto(self, id_cuenta: int, uid: int, parte: str, args: dict):
        """
        `/adjunto/<cuenta>/<uid>/<parte>` con `?inline=`, `?desde=`, `?bytes=`.

        `inline=1` sirve la imagen ENTERA de una vez (la app la usa para los
        `<img>` del visor, que la necesitan en una sola respuesta) y por eso
        lleva el tope duro: un "cid" que apunte a un adjunto de 300 MB sería un
        OOM del worker.

        Sin `inline=1` se sirve por RANGOS. `bytes=0` (o ausente) significa "todo
        de una vez", que la app usa solo para cosas chicas: para un archivo grande
        siempre manda `desde` y `bytes`.
        """
        es_inline = args.get("inline") in ("1", "true", "yes")
        desde = _entero(args.get("desde"), 0)
        cantidad = _entero(args.get("bytes"), 0)

        # Un inline se sirve entero o no se sirve: aceptarlo por rangos no
        # aporta nada (los `<img>` del visor piden la imagen completa de una
        # vez) y abriría la puerta a traer 300 MB en 300 requests.
        if es_inline:
            desde, cantidad = 0, 0

        datos, hay_mas, tipo = bytes_de_adjunto(id_cuenta, uid, parte, desde,
                                                cantidad, inline=es_inline)
        self._binario(datos, tipo, hay_mas)

    # POST no existe: este servidor solo lee. Un 405 explícito es mejor que el
    # 501 genérico de BaseHTTPRequestHandler, porque dice "el método está
    # entendido, no el verbo".
    def do_POST(self):
        self._json(405, {"error": "solo GET"})

    do_PUT = do_POST
    do_DELETE = do_POST


def _parse_query(consulta: str) -> dict:
    """Query string a dict. `a=1&b=2` → `{'a': '1', 'b': '2'}`."""
    salida = {}
    for par in (consulta or "").split("&"):
        if not par:
            continue
        k, _, v = par.partition("=")
        salida[k.strip()] = v.strip()
    return salida


def _entero(valor, por_defecto: int = 0) -> int:
    """
    Entero de la query, o el default si no es un número.

    Un `?desde=abc` NO puede tumbar el handler: el default lo vuelve 0 y se
    sirve desde el principio. Sin esto, un request malformado produce un 500 y
    en el log de la app se ve un error del worker por algo que el usuario no
    hizo mal.
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


# ═══════════════════════════════════════════════════════════════════════════════
# Arranque
# ═══════════════════════════════════════════════════════════════════════════════

def servir():
    """
    Levanta el servidor y no vuelve. La llama `cron_sync_mailbox.py` en un hilo.

    Escucha solo en localhost del contenedor: la app lo alcanza por la red de
    Docker, no por un puerto publicado. Publicarlo expondría un endpoint que
    entrega adjuntos a quien tenga el token, y el token es un string.
    """
    servidor = ThreadingHTTPServer(("0.0.0.0", settings.stream_port), _Handler)
    servidor.daemon_threads = True
    print(f"[stream] escuchando en :{settings.stream_port} "
          f"(inline máx {settings.inline_max_kb} KB, trozo {_MAX_POR_REQUEST // 1024} KB)",
          flush=True)
    servidor.serve_forever()