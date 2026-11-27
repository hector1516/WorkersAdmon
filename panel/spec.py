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
           valor="cada 10 s (1 mensaje por ciclo)",
           ayuda="Constante del código; 3 reintentos por mensaje, 429 sin gastar intento y watchdog de 4 min."),
    ],
    "wa_firmados": [
        _f("WA_FIRMADOS_CICLO_SEG", "Intervalo de revisión", "number", "env",
           default=120, min=30, max=3600, unidad="seg",
           ayuda="Cada cuántos segundos busca reportes firmados nuevos."),
        _f("WA_FIRMADOS_LOTE", "Reportes por ciclo", "number", "env",
           default=5, min=1, max=20,
           ayuda="Tope por vuelta: evita soltar una ráfaga al grupo."),
        _f("WA_FIRMADOS_DRY_RUN", "Modo ensayo", "bool", "env", default=False,
           ayuda="Con ON arma el texto y el PDF y los deja en el log, pero no "
                 "envía al grupo ni mueve el cursor."),
        _f("whatsapp_grupo_servicios", "Grupo de WhatsApp", "text", "hub_config",
           clave="whatsapp_grupo_servicios",
           ayuda="Id del grupo «Servicios» (@g.us). Se copia de Notificaciones → "
                 "WhatsApp → Grupos. Sin un @g.us válido el worker no manda nada."),
        _f("wa_firmados_ultimo_id", "Último reporte avisado", "readonly",
           "hub_config", clave="wa_firmados_ultimo_id",
           ayuda="Cursor: IdReporte del último firmado que ya salió al grupo. "
                 "Lo escribe el worker."),
    ],
    "wa_limpieza": [
        _f("WA_LIMPIEZA_CICLO_SEG", "Intervalo de revisión", "number", "env",
           default=1800, min=300, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos revisa si ya es la ventana de purga "
                 "(03:00-03:30 hora México)."),
        _f("wa_purga_dias", "Días de retención", "number", "hub_config",
           clave="wa_purga_dias", default=90, min=1, max=3650, unidad="días",
           ayuda="Borra los mensajes y las conversaciones CERRADAS con más de "
                 "estos días. Las conversaciones en BOT o HUMANO nunca se borran."),
        _f("wa_purga_activa", "Purga activa", "bool", "hub_config",
           clave="wa_purga_activa", default="1",
           ayuda="Con No el worker late pero no borra nada."),
        _f("ventana", "Ventana de purga", "const", "const",
           valor="03:00-03:30 hora México, una vez al día",
           ayuda="Es de madrugada a propósito: no compite con la bandeja "
                 "del panel. Borra en lotes de 500 para no bloquear la tabla."),
    ],
    "wa_recordatorios": [
        _f("WA_REC_CICLO_SEG", "Intervalo de revisión", "number", "env",
           default=900, min=60, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos busca qué recordar. La ventana es de "
                 "horas, así que 15 min basta."),
        _f("wa_rec_activo", "Recordatorios activos", "bool", "hub_config",
           clave="wa_rec_activo", default="1",
           ayuda="Con No el worker late pero no manda ningún mensaje."),
        _f("wa_rec_km_dias", "Días sin lectura de km", "number", "hub_config",
           clave="wa_rec_km_dias", default=3, min=1, max=365, unidad="días",
           ayuda="Un auto asignado con más de estos días sin lectura de "
                 "odómetro dispara el aviso a su conductor."),
        _f("wa_rec_vale_horas", "Horas de gracia del vale", "number",
           "hub_config", clave="wa_rec_vale_horas", default=48, min=1,
           max=8760, unidad="horas",
           ayuda="Un vale GENERADO sin comprobante avisa pasado este tiempo."),
        _f("wa_rec_vale_max", "Avisos máximos por persona", "number",
           "hub_config", clave="wa_rec_vale_max", default=3, min=1, max=20,
           ayuda="Tope de recordatorios de vales por persona, para no insistir "
                 "para siempre. Va uno al día, en un solo mensaje con todos "
                 "sus vales pendientes."),
        _f("govale_recordatorio_dias_max", "Días máximos del vale",
           "number", "hub_config", clave="govale_recordatorio_dias_max",
           default=15, min=1, max=365, unidad="días",
           ayuda="Un vale con más días desde FechaSolicitud ya no se recuerda: "
                 "son tickets que no se van a subir. La misma regla aplica en "
                 "cron_vales_recordatorios y en el resumen de Telegram."),
        _f("wa_rec_resumen", "Resumen del lunes", "bool", "hub_config",
           clave="wa_rec_resumen", default="1",
           ayuda="Con No se omite el resumen del lunes 09:00-09:30."),
        _f("wa_rec_max_dia", "Mensajes por usuario al día", "number",
           "hub_config", clave="wa_rec_max_dia", default=3, min=1, max=20,
           ayuda="Tope diario por usuario (cuenta sólo lo enviado)."),
        _f("ventana", "Ventana de envío", "const", "const",
           valor="lunes a viernes, 09:00-18:30 (hora México)",
           ayuda="Fuera de la ventana no sale nada; el ciclo siguiente lo "
                 "recoge. El resumen del lunes sale sólo entre 09:00 y 09:30."),
    ],
    "wa_grupo_semana": [
        _f("WA_GRUPO_SEMANA_CICLO_SEG", "Intervalo de revisión", "number",
           "env", default=3600, min=60, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos mira si ya es la ventana de los "
                 "lunes. La ventana es de media hora, así que 1 h basta."),
        _f("wa_grupo_ia", "Crónica con IA", "bool", "hub_config",
           clave="wa_grupo_ia", default="1",
           ayuda="Con No la semana se resume con el método determinista "
                 "(conteo por día y citas más largas), sin llamar a la IA. "
                 "Requiere ApiKey y modelo en Notificaciones → IA."),
        _f("wa_grupo_semana_dia", "Día de la crónica", "number", "hub_config",
           clave="wa_grupo_semana_dia", default=1, min=1, max=7,
           unidad="(1=lunes, 7=domingo)",
           ayuda="Día ISO en que se resume la semana anterior."),
        _f("wa_grupo_semana_hora", "Hora de la crónica", "text", "hub_config",
           clave="wa_grupo_semana_hora", default="09:00",
           ayuda="Hora de México en que abre la ventana de 30 minutos. Si la "
                 "IA falla, la semana queda CAPTURADA y se reintenta el "
                 "siguiente día configurado."),
        _f("ventana", "Ventana de la crónica", "const", "const",
           valor="30 minutos desde la hora configurada",
           ayuda="Fuera de la ventana el worker late pero no genera nada. "
                 "Nunca pisa una semana RESUMIDA o REVISADA: para rehacerla "
                 "está «Regenerar» en el módulo ECCSita."),
    ],
    "wa_asistencia": [
        _f("WA_ASIST_CICLO_SEG", "Intervalo de revisión", "number", "env",
           default=300, min=60, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos revisa los escaneos del día. La "
                 "precisión se mide en minutos, así que 5 min basta."),
        _f("wa_asist_activo", "Avisos activos", "bool", "hub_config",
           clave="wa_asist_activo", default="1",
           ayuda="Con No el worker late pero no manda ningún mensaje."),
        _f("wa_asist_tolerancia_min", "Tolerancia de entrada", "number",
           "hub_config", clave="wa_asist_tolerancia_min", default=10, min=0,
           max=120, unidad="min",
           ayuda="Pasada esta tolerancia, la llegada se avisa como tarde. Solo "
                 "aplica si el turno del usuario no fija Tol_Llegada_Min."),
        _f("wa_asist_ausencia_min", "Minutos para la ausencia", "number",
           "hub_config", clave="wa_asist_ausencia_min", default=150, min=1,
           max=1440, unidad="min",
           ayuda="Sin escaneo, pasado este tiempo desde la entrada se manda "
                 "el primer aviso (08:00 → 10:30; 09:00 → 11:30)."),
        _f("wa_asist_salida_gap_min", "Minutos sin escaneo para la salida",
           "number", "hub_config", clave="wa_asist_salida_gap_min", default=30,
           min=1, max=240, unidad="min",
           ayuda="Pasadas las 17:00, este hueco sin escaneo se toma como "
                 "salida. El fin de semana se mide 30 min antes de la salida "
                 "del turno."),
        _f("wa_asist_max_dia", "Avisos por persona al día", "number",
           "hub_config", clave="wa_asist_max_dia", default=3, min=1, max=20,
           ayuda="Tope diario por persona; cuenta sólo lo enviado y el "
                 "resumen del viernes no entra."),
        _f("wa_asist_resumen", "Resumen del viernes", "bool", "hub_config",
           clave="wa_asist_resumen", default="1",
           ayuda="Con No se omite el resumen del viernes a las 18:30."),
        _f("ventana", "Ventana de envío", "const", "const",
           valor="08:00-19:30 (sábado hasta 14:30), hora México",
           ayuda="Fuera de la ventana no sale nada. Solo al propio empleado y "
                 "solo si tiene WhatsApp y un dispositivo activo."),
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
    # ─── Avisos push de las apps (nuevo 2026-10-06) ─────────────────────────
    # Sustituye a `avisos` (abajo), que quedó muerto por un desajuste de
    # columnas. Los intervalos SÍ son editables por entorno; el horario de
    # jornada no: vive en HUB_Config y lo edita el módulo "Avisos" del
    # panel, no esta pantalla (está en HUB_Config para que el worker y la
    # pantalla no puedan discrepar).
    "avisos_push": [
        _f("AVISOS_CICLO_SEG", "Cada cuánto se da un turno", "number", "env",
           default=300, ayuda="Cinco minutos. Es el atraso máximo de un aviso "
           "dentro de jornada; más corto solo consume consultas."),
        _f("AVISOS_APP", "App que despacha", "text", "env", default="admon",
           ayuda="Admon, y en el futuro field o mailbox. Cada suscripción push "
                 "pertenece a un origen, así que cada app despacha solo la "
                 "suya."),
        _f("AVISOS_LIMPIAR_CADA", "Turnos entre limpiezas", "number", "env",
           default=480, ayuda="Cada cuántos turnos se borran los avisos ya "
           "entregados (a los 30 días). Nunca se borra lo pendiente."),
        _f("horario", "Horario de jornada", "info", "info",
           valor="HUB_Config avisos_push_horario_inicio / _fin",
           ayuda="Lunes a viernes, por defecto 09:00 a 18:30 hora de la Ciudad "
                 "de México. Fuera de esa ventana no sale nada: se acumula y "
                 "al siguiente día laboral se entrega un solo resumen. Se "
                 "edita en el módulo Avisos del panel."),
        _f("tipos", "Tipos de aviso", "info", "info",
           valor="HUB_Config avisos_push_<tipo>",
           ayuda="Uno por evento, con su interruptor y su módulo de la "
                 "pestaña Apps. El permiso que decide quién lo recibe NO se "
                 "configura aquí: sale de HUB_Users."),
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
           ayuda="REGLA DURA: solo puede existir UNA instancia en todo el "
                 "entorno; con dos se duplica HUB_ScoreLog. Verificado: field "
                 "corre solo nginx+api y admon no lo tiene."),
        _f("intervalo", "Frecuencia de auditoría", "const", "const", valor="300 s (código)",
           ayuda="Recalcula HUB_UserScores desde ReportesServicio, "
                 "HUB_RegistroKilometros y HUB_OxxoGasTickets, con dedupe por "
                 "Metrica+ReferenciaId."),
    ],
    # El worker de ECCSA_Mailbox. Las OBLIGATORIAS van arriba, y el orden importa:
    # el panel muestra los campos en este orden y lo primero que se lee es lo que
    # hay que rellenar.
    "mailbox_worker": [
        _f("HUB_DB_PASSWORD", "Contraseña de SQL Server", "secret", "env",
           ayuda="La de ECCSA_Admon, la MISMA que usa el resto del panel. Sin "
                 "esto el worker no arranca y dice por qué en el log."),
        _f("MAILBOX_ENCRYPTION_KEY", "Clave de cifrado de las cuentas", "secret", "env",
           ayuda="Clave Fernet con la que el panel cifró las credenciales IMAP. "
                 "Si está vacía se cae a MAILBOX_KEY_FILE. Si no coincide con "
                 "la que se usó al cifrar, decrypt_secret LANZA: no hay "
                 "recuperación y todas las cuentas quedan como ERROR."),
        _f("MAILBOX_STREAM_TOKEN", "Token del servidor de adjuntos", "secret", "env",
           ayuda="Secreto compartido con la app. Sin esto el servidor de adjuntos "
                 "responde 401 a todo: la sincronización funciona pero NINGÚN "
                 "adjunto se puede descargar."),
        _f("HUB_DB_SERVER", "Servidor SQL Server", "text", "env",
           default="10.188.141.15"),
        _f("HUB_DB_DATABASE", "Base de datos", "text", "env", default="ECCSA_Admon",
           ayuda="NO apuntes a ECCSA_Admon_Pruebas en producción: el worker "
                 "escribe mensajes y movería datos de prueba al buzón real."),
        _f("MAILBOX_SYNC_ENABLED", "Sincronizar", "bool", "env", default=True,
           ayuda="En OFF el proceso queda vivo y el panel lo muestra, pero no "
                 "sincroniza. Es la llave del corte: se apaga MAILBOX_SYNC_ENABLED "
                 "aquí, HUBMAIL_SYNC_ENABLED en el worker viejo, y se verifica."),
        _f("MAILBOX_DRY_RUN", "Modo ensayo", "bool", "env", default=False,
           ayuda="Con ON lee IMAP y llena el índice pero NO escribe en el buzón "
                 "(no drena la cola de envío, no mueve mensajes ni envía). Los dos "
                 "workers pueden LEER a la vez; lo que no puede ser es escribir "
                 "los dos."),
        _f("MAILBOX_SYNC_PERIOD", "Intervalo de sincronización", "number", "env",
           default=300, min=60, max=86400, unidad="seg",
           ayuda="Cada cuántos segundos refresca cada cuenta."),
        _f("MAILBOX_CONCURRENCIA", "Cuentas en paralelo", "number", "env",
           default=4, min=1, max=20,
           ayuda="Un hilo por cuenta con reloj propio, para que una cuenta caída "
                 "no rezague a las demás."),
        _f("MAILBOX_ESCALONAR", "Escalonado del arranque", "number", "env",
           default=5, min=0, max=60, unidad="seg",
           ayuda="Espera entre conexiones IMAP al arrancar. No es decorativo: N "
                 "cuentas abriendo N conexiones en el mismo segundo se caen entre "
                 "ellas en Gmail, que tiene tope por cuenta."),
        _f("MAILBOX_DATOS_DIR", "Carpeta de datos", "text", "env",
           default="/data/mailbox",
           ayuda="Volumen COMPARTIDO con la app de Mailbox (cuerpos de los correos "
                 "e imágenes de firma). Si el volumen no está en los dos "
                 "contenedores, el worker sincroniza perfecto y el usuario no ve "
                 "ningún correo, sin error en ninguna parte."),
        _f("MAILBOX_VAPID_PRIVATE", "Clave VAPID privada", "secret", "env",
           ayuda="Tiene que ser la MISMA que la de la app. Si no coincide, el "
                 "navegador rechaza cada push con un error que no menciona la "
                 "llave. Sin esto no hay push, pero la sincronización funciona."),
        _f("MAILBOX_DIAS_INDICE", "Retención del índice (días)", "number", "env",
           default=365, min=30, max=1095,
           ayuda="Después de esto se borra el mensaje entero. Antes sigue "
                 "listándose (para buscar) aunque el cuerpo ya se purgó."),
        _f("MAILBOX_HORAS_CUERPO", "Retención del cuerpo (horas)", "number", "env",
           default=2160, min=24, max=17520,
           ayuda="90 días. Pasado esto el cuerpo se borra de disco pero el "
                 "mensaje sigue en el índice y la vista dice que ya se purgó."),
        _f("MAILBOX_CACHE_MAX_MB", "Cache de adjuntos (MB)", "number", "env",
           default=2048, min=128, max=10240,
           ayuda="Es un COALESCADOR, no un almacén: cinco personas abriendo el "
                 "mismo PDF hacen un solo fetch a IMAP. Vive 6 h y se autolimita."),
        _f("MAILBOX_CACHE_TTL_H", "Vida de la cache (horas)", "number", "env",
           default=6, min=1, max=168),
        _f("MAILBOX_INLINE_MAX_KB", "Tope de imagen inline (KB)", "number", "env",
           default=256, min=16, max=2048,
           ayuda="Límite duro de una imagen inline servida de una vez. Existe "
                 "porque un cid malicioso apuntando a un adjunto grande sería un "
                 "OOM del worker, y con él caerían TODAS las cuentas."),
        _f("MAILBOX_IMAP_TIMEOUT", "Timeout IMAP (seg)", "number", "env",
           default=30, min=5, max=120,
           ayuda="Un servidor de correo que no responde más que esto no bloquea "
                 "el ciclo entero."),
        _f("instancia", "Instancias en el entorno", "const", "const",
           valor="1 (obligatorio)",
           ayuda="REGLA DURA: solo UNA instancia. El worker toma un "
                 "sp_getapplock de SQL Server ('mailbox_worker_sync') y se sale "
                 "con código 3 si ya está tomado. Con dos, la cola de envío se "
                 "drena dos veces y el cliente recibe correos duplicados."),
    ],

    # ─── Servicios de infraestructura y workers en standby ──────────────────
    # Estas fichas solo documentan: no exponen variables nuevas. Un worker en
    # NO_HABILITADO no está en /data/workers_enabled.txt y eso es normal.
    "docker_cleanup_worker": [
        _f("nota", "Vive en el HOST, no en el contenedor", "info", "info",
           valor="cron de Docker: /usr/local/bin/docker-gc.sh",
           ayuda="El contenedor no tiene montado el socket de Docker, así que su "
                 "conf se retiró de conf.d.available: no se habilita desde el "
                 "panel. La limpieza la hace el cron del host."),
    ],
    "estatus_servicios_worker": [
        _f("nota", "En standby", "info", "info",
           valor="conf conservada, worker no habilitado",
           ayuda="Estar en NO_HABILITADO es normal: no está en "
                 "workers_enabled.txt. Revisa servicios HTTP/HTTPS de "
                 "HUB_EstatusServicios cuando se encienda."),
    ],
    "hubmail_worker": [
        _f("nota", "Worker retirado", "info", "info",
           valor="sin conf en conf.d.available",
           ayuda="El HUB (Streamlit) se retiró en septiembre de 2026. Sigue en "
                 "workers_enabled.txt pero nunca arranca: su latido viejo no es "
                 "una falla. El correo lo sirve mailbox_worker."),
    ],
    "openwa_worker": [
        _f("config", "Conexión de OpenWA", "info", "info",
           valor="Notificaciones → Conexión → OpenWA",
           ayuda="La URL, la sesión y la API key viven en HUB_Config; aquí no se "
                 "duplican. Sin API key los avisos se acumulan en la cola hasta "
                 "que se pegue."),
    ],
    "vales_recordatorios": [
        _f("nota", "Apagado a propósito", "info", "info",
           valor="Telegram está apagado",
           ayuda="Su conf se conserva para volver a encenderlo, pero mientras "
                 "Telegram no esté activo no se habilita. NO_HABILITADO aquí no "
                 "es un error."),
    ],
    "vales_watchdog": [
        _f("VALES_WATCHDOG_UMBRAL", "Umbral del latido", "number", "env",
           default=600, min=60, max=86400, unidad="seg",
           ayuda="Si el latido de vales_worker es más viejo que esto, se "
                 "reinicia. Por omisión 600 s."),
        _f("VALES_WATCHDOG_INTERVALO", "Intervalo de revisión", "number", "env",
           default=60, min=10, max=3600, unidad="seg"),
    ],
    "vigilante": [
        _f("umbrales", "Umbrales del vigilante", "info", "info",
           valor="VIGILANTE_CICLO_SEG=600 · VIGILANTE_REPETIR_HORAS=6",
           ayuda="El ciclo y el antirrepetición están fijados en "
                 "docker/conf.d.available/vigilante.conf (environment). Para "
                 "cambiarlos hay que editar la conf y reiniciar el worker: un "
                 "override de entorno aquí no gana contra supervisor."),
        _f("montos", "Montos vigilados", "info", "info",
           valor="VIGILANTE_MONTO=500 · VIGILANTE_SALDO_BAJO=2000",
           ayuda="Monto de vale y saldo de GoVale que disparan un hallazgo. "
                 "También van en el entorno del proceso."),
    ],

}


# ─── Bot de WhatsApp / Volt ──────────────────────────────────────────────────
# Las seis llaves NO son un programa de supervisord: `wa_bot.py` atiende el
# webhook de `openwa_worker`. Por eso el catálogo vive aparte de WORKERS_SPEC
# (no aparece como una tarjeta por worker) y su tarjeta propia es «Bot de
# WhatsApp (Volt)» en Configuración. Las leen `wa_bot`, `wa_persona`,
# `wa_volt`, `wa_redaccion` y `wa_asistencia`.
BOT_SPEC = [
    _f("wa_bot_sarcasmo", "Sarcasmo con registrados", "bool", "hub_config",
       clave="wa_bot_sarcasmo", default="1", app="workersadmon",
       ayuda="Con No el bot contesta el dato sin bromas sobre vales, retardos, "
             "reportes u odómetro. A un desconocido nunca se le hace roast."),
    _f("wa_roast_grupo", "Usar lo que se dice en ECCSita", "bool",
       "hub_config", clave="wa_roast_grupo", default="1", app="workersadmon",
       ayuda="Con No el roast ignora lo que se dice en el grupo ECCSita y se "
             "arma solo con los datos de la persona."),
    _f("wa_volt_activo", "Mensajes sueltos de Volt", "bool", "hub_config",
       clave="wa_volt_activo", default="1", app="workersadmon",
       ayuda="Con No Volt deja de soltar su línea propia. El dato y el humor "
             "de los registrados no dependen de esto."),
    _f("wa_volt_cada", "Interacciones entre mensajes de Volt", "number",
       "hub_config", clave="wa_volt_cada", default=12, min=1, max=100,
       unidad="msgs", app="workersadmon",
       ayuda="Cada cuántos mensajes entrantes del chat toca el múltiplo (y aun "
             "así habla 6 de 10 veces). Uno al día como máximo."),
    _f("wa_bot_ia", "Redacción con IA", "bool", "hub_config",
       clave="wa_bot_ia", default="0", app="workersadmon",
       ayuda="Con Sí el texto se pule con la IA de Notificaciones → IA. Si la "
             "IA falla o cambia cifras, ligas o emojis, se manda el original."),
    _f("wa_asist_activo", "Avisos de asistencia", "bool", "hub_config",
       clave="wa_asist_activo", default="1", app="workersadmon",
       ayuda="Interruptor del worker wa_asistencia: entradas tarde, ausencias "
             "y resumen del viernes. Aquí se apaga sin tocar la base."),
]

# Pseudo-workers: catálogos que no son programas de supervisord. No salen en la
# lista de Configuración, pero `spec_for()` los resuelve para su tarjeta propia
# y `panel/config_form.py` valida sus campos igual que los de un worker.
PSEUDO_SPEC = {"wa_bot": BOT_SPEC}


def spec_for(name):
    """Lista de campos de un worker (vacío si no tiene configuración declarada)."""
    return WORKERS_SPEC.get(name) or PSEUDO_SPEC.get(name, [])


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
