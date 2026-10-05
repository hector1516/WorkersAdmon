"""
mailbox_worker/almacen.py — cuerpos de correo en disco y cache de adjuntos.

A diferencia de `hubmail_worker/almacen.py`, este NO tiene backend SMB, y es
un cambio de fondo, no una simplificación:

  · Los ADJUNTOS ya no se guardan. Se TRANSMITEN al dispositivo cuando el
    usuario los abre, así que no hay nada que escribir aquí.
  · Las IMÁGENES DE FIRMA las guarda la APP en ESTE MISMO volumen (son ~5 por
    usuario y un logo pesa 30-80 KB; meter pysmb en la app por 1.6 MB no valía
    la pena). El worker las lee al construir el MIME del envío.
  · Las imágenes INLINE de los correos recibidos NO se guardan en disco: la app
    las pide por HTTP (`/adjuntos/<id>/inline`) y este worker las trae de IMAP
    con la cache de abajo. Guardarlas duplicaría los bytes sin ganar nada.
  · Del hubmail_worker queda solo el disco local.

─── EL VOLUMEN TIENE QUE ESTAR EN LOS DOS CONTENEDORES ─────────────────────
El worker escribe los cuerpos y la app los lee. Es el MISMO volumen
(`mailbox_data`) montado en los dos: el worker en `/data/mailbox`, la app en
`/data/mailbox`. Si el volumen no está en los dos, el worker sincroniza
perfecto y el usuario no ve ningún correo — sin ningún error en ninguna parte,
que es la peor forma de fallar.

Layout:
    cuerpos/<cuenta>/<clave>.gz      cuerpo HTML, comprimido con gzip
    firmas/<firma>/<clave>.<ext>     (los escribe la app, los lee el worker)
    cache/<sha256>                   cache de adjuntos e inline, 6 h, autolimpiable
"""

import gzip
import hashlib
import os
import time

from .config import settings


class AlmacenError(Exception):
    pass


def _raiz(sub: str) -> str:
    return os.path.join(settings.datos_dir, sub)


def _asegurar(ruta: str) -> None:
    os.makedirs(ruta, exist_ok=True)


def _cuerpo_rel(cuenta_id: int, clave: str) -> str:
    """
    Ruta RELATIVA del cuerpo.

    Relativa y no absoluta a propósito: si el volumen se mueve de host, una ruta
    absoluta en la base apunta a /data de otra máquina y el cuerpo desaparece
    sin que nada avise. Con la relativa, la base solo guarda el nombre y el
    volumen se puede montar donde sea.
    """
    return f"cuerpos/{int(cuenta_id)}/{clave}.gz"


def guardar_cuerpo(cuenta_id: int, clave: str, html: str) -> bool:
    """Escribe el cuerpo comprimido. Devuelve False si no se pudo.

    Nunca propaga la excepción: un cuerpo que no se pudo guardar es un mensaje
    que se puede volver a bajar, y el ciclo tiene que seguir con los demás
    mensajes de la cuenta.
    """
    rel = _cuerpo_rel(cuenta_id, clave)
    destino = os.path.join(settings.datos_dir, rel)
    try:
        _asegurar(os.path.dirname(destino))
        crudo = (html or "").encode("utf-8")
        if not crudo:
            return False
        # mtime=0 para que el gzip sea REPRODUCIBLE: el mismo cuerpo da los
        # mismos bytes, y así un re-sync que no cambió nada no reescribe el
        # archivo (que en un disco lleno es la diferencia entre funcionar y no).
        with open(destino, "wb") as fh:
            fh.write(gzip.compress(crudo, compresslevel=6, mtime=0))
        return True
    except OSError as exc:
        print(f"[almacen] no pude guardar el cuerpo {rel}: {exc}")
        return False


def leer_cuerpo(cuenta_id: int, clave: str) -> str:
    """Lee y descomprime. "" si no está o si está corrupto."""
    if not clave:
        return ""
    ruta = os.path.join(settings.datos_dir, _cuerpo_rel(cuenta_id, clave))
    try:
        with open(ruta, "rb") as fh:
            crudo = fh.read()
        if crudo[:2] == b"\x1f\x8b":
            crudo = gzip.decompress(crudo)
        return crudo.decode("utf-8", errors="replace")
    except (OSError, EOFError, gzip.BadGzipFile):
        return ""


def existe_cuerpo(cuenta_id: int, clave: str) -> bool:
    return bool(clave) and os.path.isfile(
        os.path.join(settings.datos_dir, _cuerpo_rel(cuenta_id, clave))
    )


def borrar_cuerpo(cuenta_id: int, clave: str) -> bool:
    try:
        os.unlink(os.path.join(settings.datos_dir, _cuerpo_rel(cuenta_id, clave)))
        return True
    except OSError:
        return False


def leer_firma(firma_id: int, token: str, ext: str = "") -> bytes:
    """
    Lee una imagen de firma. Las escribió la app en su volumen; este worker las
    necesita para adjuntarlas inline al enviar.

    Devuelve b"" si no está. Un logo que falta no puede tumbar un envío: se
    manda el correo sin el logo y se avisa.
    """
    if not token or "/" in token or ".." in token:
        return b""
    try:
        base = _raiz("firmas")
        for intento in (f"{int(firma_id)}/{token}{ext}", f"{token}{ext}"):
            ruta = os.path.join(base, intento)
            if os.path.isfile(ruta):
                with open(ruta, "rb") as fh:
                    return fh.read()
    except (OSError, ValueError):
        pass
    return b""


def listar_cuerpos(cuenta_id: int) -> list:
    """Cuerpos de una cuenta, para la purga de retención."""
    directorio = os.path.join(_raiz("cuerpos"), str(int(cuenta_id)))
    if not os.path.isdir(directorio):
        return []
    try:
        return [f for f in os.listdir(directorio) if f.endswith(".gz")]
    except OSError:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Cache de adjuntos
# ═══════════════════════════════════════════════════════════════════════════════
# NO es un almacén: es un coalescador. Cinco personas abriendo el mismo PDF de
# 3 MB≫ un solo fetch IMAP. Vive 6 horas y se autolimita por tamaño.
#
# Por qué NO usar el mecanismo de bodies: son datos de USUARIOS y no se deben
# quedar en el disco del servidor. Un adjunto es contenido de otra persona.


def _cache_ruta(digest: str) -> str:
    return os.path.join(settings.cache_dir, digest[:2], digest)


def cache_leer(digest: str):
    """(bytes, edad_en_segundos) o (None, 0)."""
    ruta = _cache_ruta(digest)
    try:
        edad = time.time() - os.path.getmtime(ruta)
        if edad > settings.cache_ttl_h * 3600:
            os.unlink(ruta)
            return None, 0
        with open(ruta, "rb") as fh:
            return fh.read(), int(edad)
    except OSError:
        return None, 0


def cache_escribir(digest: str, datos: bytes) -> bool:
    """
    Guarda un adjunto en cache. False si no cupo.

    Si la cache ya está llena, NO se escribe: preferimos un cache viejo y útil
    que uno lleno de archivos que nunca se limpian. La limpieza por LRU ocurre
    en `cache_limpiar`, que el worker corre una vez por ciclo.
    """
    try:
        ruta = _cache_ruta(digest)
        _asegurar(os.path.dirname(ruta))
        with open(ruta, "wb") as fh:
            fh.write(datos)
        os.utime(ruta, None)
        return True
    except OSError:
        return False


def digest_de(cuenta_id: int, uid: int, parte: str, desde: int = 0, cantidad: int = 0) -> str:
    """Identidad de un trozo. El sha1 es suficiente: no es seguridad, es una
    clave de cache, y sha256 sería más lento sin agregar nada."""
    crudo = f"{int(cuenta_id)}:{int(uid)}:{parte}:{int(desde)}:{int(cantidad)}"
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


def cache_tamano_mb() -> float:
    total = 0
    try:
        for raiz, _dirs, archivos in os.walk(settings.cache_dir):
            for f in archivos:
                try:
                    total += os.path.getsize(os.path.join(raiz, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total / 1048576


def cache_limpiar() -> int:
    """
    Purga la cache por TTL y por tamaño. Devuelve cuántos archivos borró.

    El orden importa: primero lo viejo (por TTL), y solo si todavía se pasa del
    tope, lo menos reciente de lo restante. Así lo que se borra siempre es lo
    que menos chances tenía de volver a pedirse.
    """
    borrados = 0
    ahora = time.time()
    archivos = []
    try:
        for raiz, _dirs, nombres in os.walk(settings.cache_dir):
            for f in nombres:
                ruta = os.path.join(raiz, f)
                try:
                    m = os.path.getmtime(ruta)
                    tam = os.path.getsize(ruta)
                except OSError:
                    continue
                if ahora - m > settings.cache_ttl_h * 3600:
                    try:
                        os.unlink(ruta)
                        borrados += 1
                    except OSError:
                        pass
                    continue
                archivos.append((m, tam, ruta))
    except OSError:
        return borrados

    if cache_tamano_mb() > settings.cache_max_mb:
        archivos.sort()                      # de más viejo a más nuevo
        excedente = cache_tamano_mb() - settings.cache_max_mb
        for _m, tam, ruta in archivos:
            if excedente <= 0:
                break
            try:
                os.unlink(ruta)
                borrados += 1
                excedente -= tam / 1048576
            except OSError:
                pass

    return borrados