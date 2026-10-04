"""
hubmail_worker/almacen.py — Dónde viven los adjuntos
=========================================================
Dos destinos posibles, misma interfaz:

  * `disco`  → un directorio local (el comportamiento heredado de HUBMail).
  * `smb`    → `\\\\Fileserver\\HUB\\Mailbox`, el share que ya usa el resto de
               del ecosistema (pdf_storage_worker, file_indexer).

**Por qué SMB y no el volumen:** Docker en este host (Windows + Docker Desktop)
NO puede montar un share SMB como volumen — `-v //10.188.141.15/HUB:/smb` no
monta nada, se crea un directorio local vacío y el contenedor sigue "funcionando"
con datos que nadie ve. Verificado. Por eso el acceso va por pysmb, que ya está
instalado y ya usan otros dos workers de este mismo contenedor.

**Lo que NO cambia en la base:** `HUBMAIL_Attachments.FilePath` sigue
guardando la ruta RELATIVA (`<Cuenta>/<Carpeta>/<UID>/<n>_<nombre>`). Sobre SMB
se le antepone `Mailbox/`. Cambiar de destino NO obliga a migrar la columna: la
ruta relativa es la misma y lo que cambia es la base contra la que se resuelve.
Por eso `leer_adjunto()` es la función que debe usar la app, y no un `open()`
sobre una ruta: con SMB no existe tal ruta local.

**Decisiones que conviene no deshacer sin pensarlo:**

1. **Una conexión por hilo, no una global.** `sync_account()` corre en un hilo
   por cuenta y las operaciones de SMB no son seguras entre hilos. Se cachea la
   conexión con `threading.local()`.

2. **Reconexión automática.** El share corta sesiones: se vio al indexar los
   562k archivos del Drive, con `Broken pipe` a media lista. Por eso toda
   operación reintenta con una conexión nueva en vez de propagar el fallo.

3. **Si el share no está, el adjunto NO se guarda y se dice.** Nunca se cae al
   disco "por si acaso": dos destinos son justamente el problema que se vino a
   arreglar. El mensaje se sincroniza igual y el worker lo reporta, para que se
   vea en el log en vez de perder el archivo sin ruido.

OJO — firma de pysmb: es `SMBConnection(username, password, my_name,
remote_name, ...)`, NO empieza por la IP. Con `is_direct_tcp=True` la IP va en
`connect()`. Pasarla como username falla con "Authentication failed" y es
confuso porque las credenciales sí son válidas.
"""
import logging
import os
import threading

from . import config   # NO `from .config import settings`: se leeria una
                      # sola vez y el modulo se quedaria con un settings
                      # viejo si config se recarga (tests, panel).

# pysmb es ruidoso: en modo debug vuelca cada paquete SMB2 en hex al logger.
# A 13 cuentas x 5 min eso es gigabytes de log.
logging.getLogger("smb").setLevel(logging.CRITICAL)
logging.getLogger("nmb").setLevel(logging.CRITICAL)


class ErrorAlmacen(Exception):
    """Fallo al leer o escribir un adjunto. El mensaje dice dónde."""


# ─────────────────────────────────────────────────────────────────────────────
# Disco
# ─────────────────────────────────────────────────────────────────────────────
class AlmacenDisco:
    """Adjuntos en un directorio local. Es lo que hacía HUBMail."""

    nombre = "disco"

    def __init__(self, base):
        self.base = base

    def _abs(self, rel):
        return os.path.join(self.base, rel.replace("/", os.sep))

    def escribir(self, rel, datos):
        abs_ = self._abs(rel)
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        with open(abs_, "wb") as fh:
            fh.write(datos)
        return True

    def leer(self, rel):
        abs_ = self._abs(rel)
        if not os.path.isfile(abs_):
            return None
        with open(abs_, "rb") as fh:
            return fh.read()

    def existe(self, rel):
        return os.path.isfile(self._abs(rel))


# ─────────────────────────────────────────────────────────────────────────────
# SMB
# ─────────────────────────────────────────────────────────────────────────────
class AlmacenSmb:
    """Adjuntos en un share SMB, vía pysmb."""

    nombre = "smb"

    def __init__(self, servidor, share, usuario, clave, base="",
                 reintentos=3, timeout=60):
        self.servidor = servidor
        self.share = share
        self.usuario = usuario
        self.clave = clave
        # `base` es la carpeta DENTRO del share (p.ej. "Mailbox").
        self.base = (base or "").strip("/")
        self.reintentos = max(1, reintentos)
        self.timeout = timeout
        self._local = threading.local()

    # -- conexión ----------------------------------------------------------
    def _conexion(self):
        """Conexión de ESTE hilo, reconectada si se cayó.

        El share corta la sesión a media operación (es lo que produce los
        `Broken pipe` del file_indexer), así que si la conexión no está o no
        responde, se rehace. `threading.local` porque hay un hilo por cuenta y
        SMBConnection no es thread-safe.
        """
        from smb.SMBConnection import SMBConnection

        actual = getattr(self._local, "conn", None)
        if actual is not None:
            return actual

        c = SMBConnection(self.usuario, self.clave, "mailbox_worker", self.servidor,
                          domain="", use_ntlm_v2=True, is_direct_tcp=True)
        if not c.connect(self.servidor, 445, timeout=self.timeout):
            raise ErrorAlmacen(f"No se pudo conectar a {self.servidor}:445 por SMB")
        self._local.conn = c
        return c

    def _cerrar(self):
        c = getattr(self._local, "conn", None)
        self._local.conn = None
        if c is None:
            return
        try:
            c.close()
        except Exception:
            pass

    def _con_reintentos(self, fn, que):
        """Ejecuta `fn(conn)`; si falla, reconecta y reintenta."""
        ultimo = None
        for intento in range(self.reintentos):
            try:
                return fn(self._conexion())
            except Exception as exc:               # noqa: BLE001 - se reintenta
                ultimo = exc
                self._cerrar()
                if intento + 1 < self.reintentos:
                    print(f"[adjuntos] {que}: fallo ({exc}); "
                          f"reintento {intento + 2}/{self.reintentos}", flush=True)
        raise ErrorAlmacen(f"{que} fallo tras {self.reintentos} intentos: {ultimo}")

    # -- rutas -------------------------------------------------------------
    def _remoto(self, rel):
        rel = rel.lstrip("/")
        return f"{self.base}/{rel}" if self.base else rel

    def _asegurar_directorios(self, conn, remoto):
        """Crea la cadena de carpetas del share.

        SMB no crea los directorios intermedios solo (a diferencia de un
        `os.makedirs`), y `storeFile` falla con STATUS_OBJECT_PATH_NOT_FOUND si
        falta cualquiera. Se crean de arriba hacia abajo.
        """
        partes = remoto.split("/")[:-1]
        acumulado = ""
        for parte in partes:
            acumulado = f"{acumulado}/{parte}" if acumulado else parte
            try:
                conn.createDirectory(self.share, acumulado, timeout=self.timeout)
            except Exception:
                # Ya existe es el caso normal: createDirectory falla si existe.
                pass

    # -- operaciones -------------------------------------------------------
    def escribir(self, rel, datos):
        remoto = self._remoto(rel)

        def op(conn):
            self._asegurar_directorios(conn, remoto)
            # pysmb storeFile espera un objeto de archivo abierto, no una ruta.
            temporal = f"/tmp/_adj_{os.getpid()}_{threading.get_ident()}.bin"
            try:
                with open(temporal, "wb") as fh:
                    fh.write(datos)
                with open(temporal, "rb") as fh:
                    conn.storeFile(self.share, remoto, fh, timeout=self.timeout)
            finally:
                try:
                    os.remove(temporal)
                except OSError:
                    pass
            return True

        self._con_reintentos(op, f"escribir {rel}")
        return True

    def leer(self, rel):
        remoto = self._remoto(rel)

        def op(conn):
            destino = f"/tmp/_adj_leer_{os.getpid()}_{threading.get_ident()}.bin"
            try:
                # OJO: retrieveFile también toma un objeto de archivo abierto,
                # no una ruta (igual que storeFile). Pasarle el string de la
                # ruta falla con "'str' object has no attribute 'write'" y el
                # mensaje no dice nada de SMB, que es lo que confunde.
                with open(destino, "wb") as fh:
                    conn.retrieveFile(self.share, remoto, fh, timeout=self.timeout)
                with open(destino, "rb") as fh:
                    return fh.read()
            finally:
                try:
                    os.remove(destino)
                except OSError:
                    pass

        try:
            return self._con_reintentos(op, f"leer {rel}")
        except ErrorAlmacen:
            return None            # no está: es normal, no es un error

    def existe(self, rel):
        remoto = self._remoto(rel)

        def op(conn):
            try:
                conn.getSize(self.share, remoto, timeout=self.timeout)
                return True
            except Exception:
                return False

        return self._con_reintentos(op, f"verificar {rel}")


# ─────────────────────────────────────────────────────────────────────────────
# Fábrica
# ─────────────────────────────────────────────────────────────────────────────
_almacen = None
_candado = threading.Lock()


def get_almacen():
    """Devuelve el almacén configurado (SMB si está activo, si no disco)."""
    global _almacen
    with _candado:
        if _almacen is not None:
            return _almacen
        ajustes = config.settings
        if ajustes.adjuntos_smb:
            _almacen = AlmacenSmb(
                servidor=ajustes.smb_server,
                share=ajustes.smb_share,
                usuario=ajustes.smb_user,
                clave=ajustes.smb_password,
                base=ajustes.smb_base,
            )
            print(f"[adjuntos] destino SMB: \\\\"
                  f"{ajustes.smb_server}\\{ajustes.smb_share}"
                  f"\\{ajustes.smb_base}", flush=True)
        else:
            _almacen = AlmacenDisco(ajustes.attachments_dir)
            print(f"[adjuntos] destino local: {ajustes.attachments_dir}", flush=True)
        return _almacen


def reiniciar_almacen():
    """Solo para pruebas: olvida el almacén cacheado."""
    global _almacen
    with _candado:
        _almacen = None