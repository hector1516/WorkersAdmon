"""
hubmail_worker/config.py — Configuración del worker de correo (HUBMail → Mailbox)
=================================================================================
Adaptación de `HUBMail/app/config.py` para el contenedor `workersadmon`.

Diferencias deliberadas respecto al original:

1. **Sin secretos por defecto.** El original traía la contraseña de MySQL, la de
   SQL Server, el secreto JWT y la clave VAPID privada *escritas en el código*
   (`eyccazo`, `cambia-este-secreto-hubmail`). Eso queda commiteado al historial
   de git y además hacía que un despliegue sin variables de entorno arrancara
   "funcionando" contra producción con credenciales equivocadas. Aquí todo
   secreto viene del entorno y `_requerido()` falla ruidosamente si falta.
2. **Sin SQL Server.** El worker (sync, filtros, push, IMAP) habla *solo* MySQL:
   `HUB_Users` nunca se consulta desde acá. Se eliminó `users_db_*` y con él la
   dependencia de pymssql para este paquete.
3. **Sin DeepL.** La traducción vive en la API de la app, no en el worker.
4. **Gates de operación.** `sync_enabled` y `sync_period` permiten apagar el
   ciclo sin tocar el código (útil para el corte del worker viejo: se apaga
   UN lado, se verifica, y luego el otro).

Los secretos se inyectan por `-e` en `docker run` o desde el panel
(`/data/worker_env.json`, que el entrypoint reaplica en cada arranque).
"""
import os
from dataclasses import dataclass


def _vacio_si_no(md5_key: str) -> str:
    """Devuelve '' si la variable no existe o quedó en blanco tras el strip."""
    return (os.getenv(md5_key) or "").strip()


def _int(md5_key: str, defecto: int) -> int:
    crudo = (os.getenv(md5_key) or "").strip()
    if not crudo:
        return defecto
    try:
        return int(crudo)
    except ValueError:
        print(f"[hubmail] {md5_key}={crudo!r} no es un entero; se usa {defecto}")
        return defecto


def _requerido(nombre: str, que_usa: str) -> str:
    """Lee una variable obligatoria. Si falta, avisa y devuelve ''.

    No se lanza excepción: el worker debe poder arrancar para que el panel lo
    muestre en rojo y el operador lea el motivo, en vez de un crash-loop mudo.
    Lo que sí hace es imprimir el aviso en cada arranque.
    """
    valor = _vacio_si_no(nombre)
    if not valor:
        print(f"[hubmail] FALTA {nombre} (la necesita {que_usa}). "
              f"Pasala con -e {nombre}=... o desde el panel.")
    return valor


@dataclass
class Settings:
    """Solo lo que el worker necesita. Sin usuarios ni IA."""

    # --- MySQL (HUBMAIL) ---
    db_server: str
    db_user: str
    db_password: str
    db_name: str

    # --- IMAP / SMTP (GoDaddy) ---
    default_imap_host: str
    default_imap_port: int
    default_smtp_host: str
    default_smtp_port: int

    # --- Cifrado de las contraseñas de las cuentas ---
    # Prefiere la env var (se puede inyectar sin tocar el volumen); si no está,
    # cae al archivo del volumen. Ver crypto._load_key().
    key_file: str

    # --- Adjuntos en disco ---
    attachments_dir: str

    # --- Adjuntos en SMB (el destino preferido) ---
    # Cuando `adjuntos_smb` está activo, los adjuntos van a
    # \\<smb_server>\<smb_share>\<smb_base>\... en vez de a `attachments_dir`.
    # El share es el mismo que ya usan pdf_storage_worker y file_indexer, así
    # que no hay un segundo almacén de archivos en la empresa.
    adjuntos_smb: bool
    smb_server: str
    smb_share: str
    smb_user: str
    smb_password: str
    smb_base: str

    # --- Web Push (VAPID) ---
    vapid_public_key: str
    vapid_private_key: str
    vapid_subject: str

    # --- Ritmo del ciclo ---
    sync_enabled: bool
    sync_period: int
    max_attachment_mb: int

    # --- Modo ensayo ---
    # Con dry_run=1 el worker lee IMAP y llena la caché, pero NO escribe nada
    # en IMAP: no drena HUBMAIL_PendingOps ni aplica filtros que marquen/muevan.
    # Sirve para probar el worker en paralelo al viejo sin que los dos toquen
    # el buzón. Los dos pueden leer a la vez sin problema; lo que no se puede es
    # escribir a la vez.
    dry_run: bool


def get_settings() -> Settings:
    enabled = (os.getenv("HUBMAIL_SYNC_ENABLED") or "1").strip().lower()
    return Settings(
        # MySQL: aquí SÍ hay defaults, pero solo de DIRECCIÓN/nombre. La
        # contraseña es obligatoria (ver _requerido).
        db_server=os.getenv("HUBMAIL_DB_SERVER", "172.26.90.159"),
        db_user=os.getenv("HUBMAIL_DB_USER", "hubmail"),
        db_password=_requerido("HUBMAIL_DB_PASSWORD", "conectarse a MySQL"),
        db_name=os.getenv("HUBMAIL_DB_NAME", "HUBMAIL"),

        default_imap_host=os.getenv("HUBMAIL_IMAP_HOST", "imap.secureserver.net"),
        default_imap_port=_int("HUBMAIL_IMAP_PORT", 993),
        default_smtp_host=os.getenv("HUBMAIL_SMTP_HOST", "smtpout.secureserver.net"),
        default_smtp_port=_int("HUBMAIL_SMTP_PORT", 465),

        key_file=os.getenv("HUBMAIL_KEY_FILE", "/data/.hubmail_key"),
        attachments_dir=os.getenv("HUBMAIL_ATTACHMENTS_DIR", "/data/attachments"),

        # SMB: por defecto apagado, para que un despliegue nuevo no dependa del
        # share. Se enciende con HUBMAIL_ADJUNTOS_SMB=1 (o poniendo el share).
        adjuntos_smb=(os.getenv("HUBMAIL_ADJUNTOS_SMB") or "").strip().lower()
        in ("1", "true", "yes", "on")
        or bool((os.getenv("HUBMAIL_SMB_SHARE") or "").strip()),
        smb_server=os.getenv("HUBMAIL_SMB_SERVER", "10.188.141.15"),
        smb_share=os.getenv("HUBMAIL_SMB_SHARE", "HUB"),
        smb_user=os.getenv("HUBMAIL_SMB_USER", "eccsa"),
        smb_password=os.getenv("HUBMAIL_SMB_PASSWORD", ""),
        smb_base=os.getenv("HUBMAIL_SMB_BASE", "Mailbox"),

        vapid_public_key=_vacio_si_no("HUBMAIL_VAPID_PUBLIC"),
        vapid_private_key=_vacio_si_no("HUBMAIL_VAPID_PRIVATE"),
        vapid_subject=os.getenv("HUBMAIL_VAPID_SUBJECT", "mailto:it@ecc-sa.com.mx"),

        sync_enabled=enabled not in ("0", "false", "no", "off"),
        sync_period=_int("HUBMAIL_SYNC_PERIOD", 300),
        max_attachment_mb=_int("HUBMAIL_MAX_ATTACH_MB", 25),
        dry_run=(os.getenv("HUBMAIL_SYNC_DRYRUN") or "0").strip().lower()
        in ("1", "true", "yes", "on"),
    )


settings = get_settings()