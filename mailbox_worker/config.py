"""
mailbox_worker/config.py — configuración del worker de correo de Mailbox.

A diferencia de la versión que trae `hubmail_worker` (que habla MySQL), este
worker habla **SQL Server** con pymssql. Todo lo demás sigue igual: una clase
`Settings` con dataclass, leída del entorno, sin secretos por defecto.

Los secretos NO tienen default. Si falta `HUB_DB_PASSWORD`, `_requerido()`
avisa y devuelve ""; el worker sigue vivo y el panel lo muestra en rojo con el
motivo, en vez de entrar en crash-loop con el log vacío. Es la diferencia entre
"el operador lee qué falta" y "el operador adivina".
"""

import os
from dataclasses import dataclass


def _vacio_si_no(k: str) -> str:
    return (os.getenv(k) or "").strip()


def _int(k: str, defecto: int) -> int:
    crudo = (os.getenv(k) or "").strip()
    if not crudo:
        return defecto
    try:
        return int(crudo)
    except ValueError:
        print(f"[mailbox] {k}={crudo!r} no es un entero; se usa {defecto}")
        return defecto


def _bool(k: str, defecto: bool) -> bool:
    crudo = (os.getenv(k) or "").strip().lower()
    if not crudo:
        return defecto
    return crudo in ("1", "true", "yes", "si", "sí", "on")


def _requerido(nombre: str, que_usa: str) -> str:
    valor = _vacio_si_no(nombre)
    if not valor:
        print(f"[mailbox] FALTA {nombre} (la necesita {que_usa}).")
        print(f"[mailbox] Pasala con -e {nombre}=... desde el panel o deploy/env.local.")
    return valor


@dataclass
class Settings:
    # ── SQL Server ──────────────────────────────────────────────────────────
    # Se leen de las MISMAS variables HUB_DB_* que usa el resto del ecosistema,
    # y de `secretos_local.py` vía el entrypoint del contenedor. No hay una
    # lista nueva que mantener sincronizada con la app.
    db_server: str
    db_user: str
    db_password: str
    db_name: str

    # ── IMAP / SMTP ─────────────────────────────────────────────────────────
    default_imap_host: str
    default_imap_port: int
    default_smtp_host: str
    default_smtp_port: int
    imap_timeout: int
    #  Los servidores de correo rechazan contraseñas con espacios.
    # Google da una App Password en grupos de 4 sin espacios; si un admin la pega
    # con espacios, falla con un "Invalid credentials" que no dice por qué.
    quitar_espacios_credencial: bool

    # ── Cifrado ─────────────────────────────────────────────────────────────
    encryption_key: str
    key_file: str

    # ── Almacenamiento ───────────────────────────────────────────────────────
    # Cuerpo de los correos e imágenes inline. SOLO disco local.
    #
    # NO hay backend SMB a propósito, y es un cambio de fondo respecto del
    # hubmail_worker: las imágenes de firma viven en el volumen de la APP (son
    # ~5 por usuario, unos 30-80 KB cada una), no en el share. Y como los
    # adjuntos ya no se guardan —se transmiten al dispositivo—, el FileServer
    # se quedó sin trabajo en Mailbox.
    #
    # Este volumen TIENE que estar montado también en el contenedor de la app:
    # el worker escribe los cuerpos y la app los lee. Sin el volumen compartido
    # el worker sincroniza perfecto y el usuario no ve ningún correo.
    datos_dir: str

    # ── Ciclo ───────────────────────────────────────────────────────────────
    sync_period: int
    sync_enabled: bool
    # Corta sin apagar el proceso: deja leer pero NO escribir en IMAP. Es lo que
    # permite hacer el cambio de un worker al otro sin que los dos escriban y
    # dupliquen correos (ver AGENTS.md de WorkersAdmon, "corte sin solapar").
    dry_run: bool
    # Máximo de cuentas sincronizadas a la vez. Cada una abre un socket IMAP, y
    # 20 cuentas juntas lo que tumba es al servidor de correo del proveedor, no
    # al worker.
    concurrencia: int
    # Segundos entre el arranque de cada cuenta. Sin escalonar, 20 cuentas abren
    # 20 sockets en el mismo segundo.
    escalonar_arranque: int

    # ── Streaming de adjuntos ───────────────────────────────────────────────
    # Puerto interno del servidor de adjuntos. NO es el 8200 del panel: es otro
    # proceso y otro puerto, con otro token.
    stream_port: int
    stream_token: str
    # Cache de adjuntos: NO es un almacén, es un coalescador. Cinco personas
    # abriendo el mismo PDF = un solo fetch IMAP.
    cache_dir: str
    cache_ttl_h: int
    cache_max_mb: int

    # ── Push ────────────────────────────────────────────────────────────────
    vapid_private_key: str
    # El `sub` de los claims VAPID: el contacto que el push service muestra si un
    # usuario pregunta quién le manda notificaciones. Tiene que ser `mailto:` o
    # una URL HTTPS; RFC 8292 lo exige y los push services lo rechazan.
    vapid_subject: str

    # ── Retención ───────────────────────────────────────────────────────────
    dias_indice: int          # headers sola, hasta acá
    horas_cuerpo: int         # cuerpo en disco, hasta acá

    # ── Imágenes inline ────────────────────────────────────────────────────
    # Tope de una imagen INLINE servida en una sola respuesta por
    # `/adjunto/<cuenta>/<uid>/<parte>?inline=1`. Esa ruta junta los bytes en
    # memoria, así que necesita un tope duro: un "cid" malicioso que apunte a un
    # adjunto de 300 MB (el endpoint /inline de la app ya filtra por
    # `EsInline`, pero la defensa se duplica acá) sería un OOM del worker, y con
    # él caen TODAS las cuentas porque comparten proceso.
    #
    # Los adjuntos NORMALES no tienen tope: van por la ruta de rangos, que
    # transmite en trozos de 256 KB sin juntar nada.
    inline_max_kb: int


def _cargar() -> Settings:
    return Settings(
        # ── BD: el entrypoint ya dejó estos valores en el entorno ──────────
        db_server=_vacio_si_no("HUB_DB_SERVER") or "10.188.141.15",
        db_user=_vacio_si_no("HUB_DB_USER") or "sa",
        db_password=_requerido("HUB_DB_PASSWORD", "conectarse a SQL Server"),
        db_name=_vacio_si_no("HUB_DB_DATABASE") or "ECCSA_Admon",

        # ── IMAP / SMTP ───────────────────────────────────────────────────
        default_imap_host=_vacio_si_no("MAILBOX_IMAP_HOST") or "imap.gmail.com",
        default_imap_port=_int("MAILBOX_IMAP_PORT", 993),
        default_smtp_host=_vacio_si_no("MAILBOX_SMTP_HOST") or "smtp.gmail.com",
        default_smtp_port=_int("MAILBOX_SMTP_PORT", 587),
        imap_timeout=_int("MAILBOX_IMAP_TIMEOUT", 30),
        quitar_espacios_credencial=_bool("MAILBOX_QUITAR_ESPACIOS", True),

        # ── Cifrado ───────────────────────────────────────────────────────
        encryption_key=_vacio_si_no("MAILBOX_ENCRYPTION_KEY"),
        key_file=_vacio_si_no("MAILBOX_KEY_FILE") or "/data/mailbox/.mailbox_key",

        # ── Almacenamiento ─────────────────────────────────────────────────
        datos_dir=_vacio_si_no("MAILBOX_DATOS_DIR") or "/data/mailbox",

        # ── Ciclo ─────────────────────────────────────────────────────────
        sync_period=_int("MAILBOX_SYNC_PERIOD", 300),
        sync_enabled=_bool("MAILBOX_SYNC_ENABLED", True),
        dry_run=_bool("MAILBOX_SYNC_DRYRUN", False),
        concurrencia=max(1, min(_int("MAILBOX_CONCURRENCIA", 4), 8)),
        escalonar_arranque=_int("MAILBOX_ESCALONAR", 5),

        # ── Streaming ─────────────────────────────────────────────────────
        stream_port=_int("MAILBOX_STREAM_PORT", 8201),
        stream_token=_requerido(
            "MAILBOX_STREAM_TOKEN",
            "el endpoint de adjuntos. Sin esto la app no puede descargar",
        ),
        cache_dir=_vacio_si_no("MAILBOX_CACHE_DIR") or "/data/mailbox/cache",
        cache_ttl_h=_int("MAILBOX_CACHE_TTL_H", 6),
        cache_max_mb=_int("MAILBOX_CACHE_MAX_MB", 2048),

        # ── Push ──────────────────────────────────────────────────────────
        vapid_private_key=_vacio_si_no("MAILBOX_VAPID_PRIVATE") or _vacio_si_no("VAPID_PRIVATE_KEY"),
        vapid_subject=_vacio_si_no("MAILBOX_VAPID_SUBJECT") or "mailto:admin@ecc-sa.com.mx",

        # ── Retención ─────────────────────────────────────────────────────
        dias_indice=_int("MAILBOX_DIAS_INDICE", 365),
        horas_cuerpo=_int("MAILBOX_HORAS_CUERPO", 24 * 90),

        # ── Imágenes inline ────────────────────────────────────────────────
        inline_max_kb=_int("MAILBOX_INLINE_MAX_KB", 256),
    )


settings = _cargar()


def aviso_arranque() -> str:
    """Resumen de la configuración para el log de arranque y para el panel."""
    modo = "ENSAYO (no escribe en IMAP)" if settings.dry_run else "normal"
    return (
        f"IMAP {settings.default_imap_host}:{settings.default_imap_port} · "
        f"SMTP {settings.default_smtp_host}:{settings.default_smtp_port} · "
        f"periodo {settings.sync_period}s · concurrencia {settings.concurrencia} · "
        f"streaming :{settings.stream_port} · cache {settings.cache_max_mb}MB/{settings.cache_ttl_h}h · "
        f"retención {settings.horas_cuerpo // 24}d cuerpos / {settings.dias_indice}d índice · "
        f"modo {modo}"
    )