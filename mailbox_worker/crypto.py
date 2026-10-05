"""
mailbox_worker/crypto.py — Descifrado de las contraseñas de las cuentas IMAP
=================================================================================
Adaptación de `HUBMail/app/crypto.py`. **Aquí sí hubo un cambio de fondo.**

El original hacía esto:

    key = env var  →  archivo  →  GENERAR UNA NUEVA (y escribirla)

Generar una nueva es inofensivo en la API (es el proceso que crea las cuentas y
tiene el archivo en su propio volumen), pero **catastrófico en el worker**: si
no encuentra el archivo, se inventa una clave distinta a la de la API, y
`decrypt_secret` empieza a devolver `""` para TODAS las cuentas. El worker
seguiría vivo, conectaría a IMAP con contraseña vacía y fallaría en silencio,
un ciclo cada 5 minutos, sin que nada lo delate.

Por eso acá la autogeneración **no existe**. Las dos claves válidas son:

1. `MAILBOX_ENCRYPTION_KEY` (env var) — la preferida: viaja en la conf del
   contenedor, no depende de ningún volumen y se puede copiar tal cual desde el
   contenedor viejo.
2. `MAILBOX_KEY_FILE` (archivo) — el volumen `mailbox_data`.

Si no hay ninguna, `_load_key()` devuelve `None`; el primer `decrypt_secret`
lanza `RuntimeError` con un mensaje que dice exactamente qué hacer. Es
preferible que el worker se caiga y se vea en rojo en el panel a que sincronice
durante horas con contraseñas vacías.
"""
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_lock = threading.Lock()
_cached = None
_cached_source = None

AVISO_KEY = (
    "[mailbox] No encuentro la clave de cifrado de las cuentas IMAP.\n"
    "           Se revisaron, en este orden:\n"
    "             1) env MAILBOX_ENCRYPTION_KEY\n"
    "             2) archivo MAILBOX_KEY_FILE "
    f"(ahora: {settings.key_file})\n"
    "           Sin esa clave NO se puede descifrar ninguna contraseña de buzón.\n"
    "           Sácala del contenedor viejo y pásala por aquí, p. ej.:\n"
    "             docker exec mailbox cat /data/mailbox/.mailbox_key\n"
    "           y luego  -e MAILBOX_ENCRYPTION_KEY=<contenido>  al crear el contenedor,\n"
    "           o con MAILBOX_KEY_FILE apuntando al volumen compartido."
)


def _leer_archivo(ruta: str):
    try:
        with open(ruta, "rb") as f:
            crudo = f.read().strip()
        return crudo or None
    except OSError:
        return None


def _load_key():
    """Devuelve la clave (bytes) o None. NUNCA genera una nueva."""
    global _cached, _cached_source
    with _lock:
        if _cached is not None:
            return _cached

        fuente = None
        clave = None

        # 1) Variable de entorno (tiene prioridad: es la explícita).
        env = (os.getenv("MAILBOX_ENCRYPTION_KEY") or "").strip()
        if env:
            clave, fuente = env.encode(), "MAILBOX_ENCRYPTION_KEY"

        # 2) Archivo (volumen).
        if clave is None and settings.key_file:
            crudo = _leer_archivo(settings.key_file)
            if crudo:
                clave, fuente = crudo, settings.key_file

        if clave is None:
            print(AVISO_KEY, flush=True)
            return None

        # Validación temprana: una clave con formato raro produciría
        # InvalidToken en cada cuenta, mucho más difícil de leer que esto.
        try:
            Fernet(clave)
        except (ValueError, TypeError) as exc:
            print(f"[mailbox] La clave de {fuente} no es válida para Fernet: {exc}",
                  flush=True)
            return None

        _cached = clave
        _cached_source = fuente
        print(f"[mailbox] Clave de cifrado cargada desde: {fuente}", flush=True)
        return _cached


def fuente_clave():
    """De dónde se tomó la clave (para el log de arranque del worker)."""
    _load_key()
    return _cached_source


def encrypt_secret(value: str) -> str:
    """Cifra un secreto. El worker no crea cuentas, pero se conserva la API."""
    clave = _load_key()
    if clave is None:
        raise RuntimeError(AVISO_KEY)
    return Fernet(clave).encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Descifra una contraseña de buzón.

    A diferencia del original NO devuelve "" cuando falla: lanza, porque en el
    worker un "" silencioso se traduce en "no puedo autenticarme contra IMAP" y
    la causa real (no tener clave) se pierde a tres saltos de la excepción.
    """
    if not token:
        return ""
    clave = _load_key()
    if clave is None:
        raise RuntimeError(AVISO_KEY)
    try:
        return Fernet(clave).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError(
            "La contraseña cifrada de esta cuenta no se puede descifrar. "
            "Casi siempre significa que la clave de MAILBOX_ENCRYPTION_KEY no es "
            f"la misma que se usó para cifrarla ({exc})."
        ) from exc