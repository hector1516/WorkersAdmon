"""
panel/spec.py — Catálogo de configuración editable de cada worker
=================================================================
Cada entrada describe un valor configurable de un worker, con su ORIGEN:

  origen = "env"        → variable de entorno del conf de supervisord
                           (se persiste en /data/worker_env.json y se
                           reaplica en cada arranque del contenedor)
  origen = "hub_config" → clave de la tabla HUB_Config en SQL Server
                           (la MISMA que edita el HUB: el valor aplica de
                           inmediato, los workers la leen en cada ciclo)
  origen = "const"      → constante escrita en el código (solo lectura;
                           para cambiarla hay que editar el worker en el
                           repo HUB, que es la fuente de verdad)
  origen = "info"       → nota informativa (sin valor editable)

Tipos soportados: text | secret | number | hour | minute | bool |
                  readonly | const | info

Regla de los `secret`: el valor NUNCA se devuelve al navegador; un campo en
blanco significa "no cambiar" y por eso un token ya guardado no se muestra.
"""
import os

# Dirección por defecto de la línea environment= de un conf (inyectable en tests)
ACTIVE_CONF_DIR = os.environ.get("SUPERVISOR_PROGRAM_DIR", "/etc/supervisor/conf.d")


def _f(id_, label, tipo, origen, **kw):
    """Helper corto para declarar un campo del spec."""
    field = {"id": id_, "label": label, "tipo": tipo, "origen": origen}
    field.update(kw)
    return field


# ─── Especificación por worker ────────────────────────────────────────────────
# El orden de la lista es el orden en que se muestran los campos.
WORKERS_SPEC = {
    "tipo_cambio_worker": [
        _f("CRON_TC_HORA", "Hora de ejecución", "hour", "env", default=6,
           ayuda="Refresco diario del dólar (hora del contenedor, TZ America/Mexico_City)."),
        _f("CRON_TC_MIN", "Minuto", "minute", "env", default=0),
        _f("banxico_token", "Token Banxico", "secret", "hub_config",
           clave="banxico_token",
           ayuda="Serie SF51158 (dólar FIX). Si está vacío se usa la API gratuita open.er-api.com."),
        _f("tipo_cambio_usd", "Último tipo de cambio", "readonly", "hub_config",
           clave="tipo_cambio_usd",
           ayuda="Lo escribe el worker cada mañana; aquí solo se consulta."),
    ],
    "bing_worker": [
        _f("CRON_BING_INTERVAL", "Intervalo de sincronización", "number", "env",
           default=21600, min=60, max=604800, unidad="seg",
           ayuda="Cada cuántos segundos se descarga el fondo nuevo de Bing."),
        _f("CRON_BING_MAX", "Imágenes en el historial", "number", "env",
           default=5, min=1, max=50, unidad="imgs",
           ayuda="Cuántas imágenes conserva la rotación (HUB_BingWallpapers)."),
    ],
    "oxxogas_contactos_worker": [
        _f("CRON_OXXOGAS_CONTACTOS_INTERVAL", "Intervalo de sincronización", "number",
           "env", default=21600, min=60, max=604800, unidad="seg",
           ayuda="Contactos de OxxoGas vía Playwright (6 h por defecto)."),
    ],
    "govale_vouchers_worker": [
        _f("CRON_GOVALE_INTERVAL", "Intervalo de revisión", "number", "env",
           default=300, min=60, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos revisa vouchers nuevos de GoVale."),
        _f("govale_user", "Usuario GoVale", "secret", "hub_config",
           clave="govale_user",
           ayuda="Cuenta compartida del portal; la usan también vales_worker."),
        _f("govale_password", "Contraseña GoVale", "secret", "hub_config",
           clave="govale_password",
           ayuda="Misma cuenta que vales_worker (generación de vales QR)."),
    ],
    "pdf_storage_worker": [
        _f("CRON_PDF_HORA", "Hora de ejecución", "hour", "env", default=3,
           ayuda="Respaldo diario de PDFs de reportes hacia el Fileserver."),
        _f("CRON_PDF_MIN", "Minuto", "minute", "env", default=0),
        _f("smb_share_path", "Recurso SMB", "text", "hub_config",
           clave="smb_share_path", ayuda="Ej. \\\\Fileserver\\PDFs\\Reportes"),
        _f("smb_user", "Usuario SMB", "text", "hub_config", clave="smb_user"),
        _f("smb_password", "Contraseña SMB", "secret", "hub_config",
           clave="smb_password"),
        _f("smb_domain", "Dominio SMB", "text", "hub_config", clave="smb_domain"),
        _f("pdf_output_dir", "Carpeta de salida", "text", "hub_config",
           clave="pdf_output_dir", ayuda="Directorio destino dentro del recurso."),
    ],
    "telegram_worker": [
        _f("telegram_bot_token", "Token del bot", "secret", "hub_config",
           clave="telegram_bot_token",
           ayuda="Token de @BotFather; lo usan todas las alertas del HUB.", test=True),
        _f("cadencia", "Cadencia de la cola", "const", "const",
           valor="cada 20 s (lee HUB_TelegramQueue)",
           ayuda="Constante del código; envía hasta 3 reintentos por mensaje."),
    ],
    "oxxogas_worker": [
        _f("intervalo", "Cadencia de sincronización", "const", "const",
           valor="cada 1 h",
           ayuda="Revisa vales OxxoGas en la bandeja IMAP de cada usuario."),
        _f("cuentas", "Correos IMAP", "info", "info",
           valor="Se configuran por usuario desde el HUB (Vales OxxoGas → Vincular correo)."),
    ],
    "vales_worker": [
        _f("intervalo", "Cadencia de revisión", "const", "const",
           valor="cada 5 min",
           ayuda="Genera los vales APROBADO sin QR vía GoVale (Playwright)."),
        _f("credenciales", "Credenciales", "info", "info",
           valor="Usa govale_user / govale_password (editar en govale_vouchers_worker)."),
    ],
    "network_scanner_worker": [
        _f("net_scan_enabled", "Escaneo activo", "bool", "hub_config",
           clave="net_scan_enabled", default="1",
           ayuda="Si está apagado el worker no escanea la subred."),
        _f("net_scan_subnet", "Subred a escanear", "text", "hub_config",
           clave="net_scan_subnet", default="172.26.90.0/24"),
        _f("net_scan_interval_seg", "Intervalo de escaneo", "number", "hub_config",
           clave="net_scan_interval_seg", default="180", min=10, max=86400, unidad="seg"),
        _f("net_tolerance_entrada_min", "Tolerancia de entrada", "number", "hub_config",
           clave="net_tolerance_entrada_min", default="2", min=0, max=60, unidad="min"),
        _f("net_tolerance_salida_min", "Tolerancia de salida", "number", "hub_config",
           clave="net_tolerance_salida_min", default="5", min=0, max=60, unidad="min"),
        _f("zerotier_enabled", "ZeroTier activo", "bool", "hub_config",
           clave="zerotier_enabled", default="1",
           ayuda="Marca las IPs de la subred ZeroTier para ignorarlas en los reportes."),
        _f("zerotier_subnet", "Subred ZeroTier", "text", "hub_config",
           clave="zerotier_subnet", default="10.147."),
    ],
    "mcp_server": [
        _f("HUB_JWT_SECRET", "Secreto JWT (passkeys)", "secret", "env",
           ayuda="Reto de passkeys de HUB/Field. Si no se define se usa el secreto "
                 "por defecto del código (mcp_server.py)."),
        _f("puerto", "Puerto expuesto", "const", "const", valor="8000 (host)",
           ayuda="Se publica -p 8000:8000 solo si se levanta aquí (Fase mcp_server)."),
    ],
    "status_web": [
        _f("nota", "Sin configuración expuesta", "info", "info",
           valor="Este panel no expone variables de entorno editables."),
    ],
    # ─── Workers de Field (migrados 2026-09-26) ──────────────────────────────
    # Sus intervalos y credenciales están escritos en el código (field/api),
    # que es la fuente de verdad: aquí solo se documentan. Lo que sí es
    # editable son las claves de HUB_Config que el código ya lee.
    "avisos": [
        _f("TZ", "Zona horaria del proceso", "const", "const", valor="UTC",
           ayuda="El conf fija environment=TZ=\"UTC\" para no mover el "
                 "comportamiento de datetime.now() que tenía en Field. "
                 "cron_avisos usa utcnow()-6, así que los lunes 8:00 AM "
                 "siguen siendo hora México."),
        _f("intervalo_rep", "Revisión de reportes", "const", "const",
           valor="3600 s (código)",
           ayuda="Reportes sin firmar nuevos; el dedupe vive en "
                 "HUB_Config.field_avisos_rep_<IdUsuario>, así que reiniciar "
                 "no re-envía avisos."),
        _f("km_lunes", "Kilómetros semanales", "const", "const",
           valor="lunes 8:00 AM (código)",
           ayuda="UTC-6 fijo calculado con utcnow(); push a usuarios con auto "
                 "asignado sin registro desde el lunes."),
        _f("vapid", "Claves VAPID", "info", "info",
           valor="HUB_Config.vapid_private_key / vapid_public_key",
           ayuda="Se generan solas la primera vez y se comparten con Field, "
                 "por eso las suscripciones push no se invalidan."),
    ],
    "file_indexer": [
        _f("smb_user", "Usuario SMB", "const", "const", valor="eccsa",
           ayuda="FILESERVER 10.188.141.15; usuario y contraseña están "
                 "escritos en cron_index_files.py (origen: repo Field)."),
        _f("intervalo", "Intervalo de indexado", "const", "const", valor="300 s (código)",
           ayuda="Indexa Docs/Shared y Docs/Aplicaciones en HUB_FileIndex."),
    ],
    "legends_cron": [
        _f("ganador", "Cálculo del ganador", "const", "const",
           valor="domingo 3:00 AM (código)",
           ayuda="Usa pytz America/Mexico_City explícito, así que el TZ=UTC del "
                 "conf no mueve el corte. Guarda en HUB_WeeklyWinners y resetea "
                 "HUB_UserScores.PuntuacionSemanal."),
        _f("intervalo", "Frecuencia de verificación", "const", "const",
           valor="3600 s (código)", ayuda="Solo calcula si ya pasó el domingo 3 AM."),
    ],
    "legends_audit": [
        _f("instancia", "Instancias en el entorno", "const", "const",
           valor="1 (obligatorio)",
           ayuda="⚠️ REGLA DURA: solo puede existir UNA instancia en todo el "
                 "entorno; con dos se duplica HUB_ScoreLog. Verificado: field "
                 "corre solo nginx+api y admon no lo tiene."),
        _f("intervalo", "Frecuencia de auditoría", "const", "const", valor="300 s (código)",
           ayuda="Recalcula HUB_UserScores desde ReportesServicio, "
                 "HUB_RegistroKilometros y HUB_OxxoGasTickets, con dedupe por "
                 "Metrica+ReferenciaId."),
    ],
}


def spec_for(name):
    """Lista de campos de un worker (vacío si no tiene configuración declarada)."""
    return WORKERS_SPEC.get(name, [])


def editable_fields(name):
    """Solo campos editables (env/hub_config) — los que procesa el POST."""
    return [f for f in spec_for(name)
            if f["origen"] in ("env", "hub_config")
            and f["tipo"] not in ("readonly", "info", "const")]


def hub_config_keys(name):
    """Claves de HUB_Config de un worker (incluye las de solo lectura, que
    hay que poder MOSTRAR)."""
    return [f.get("clave") or f["id"] for f in spec_for(name)
            if f["origen"] == "hub_config"]


def env_keys(name):
    """Variables de entorno que administra el panel para un worker."""
    return [f["id"] for f in spec_for(name) if f["origen"] == "env"]


def all_workers():
    """Workers con configuración declarada (orden de declaración)."""
    return list(WORKERS_SPEC.keys())
