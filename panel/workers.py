"""
panel/workers.py — Estado, control y logs de los workers del contenedor
========================================================================
Responsabilidades:
  * build_status()  → estado consolidado (supervisord + lista persistente +
                      heartbeats) que alimenta la tabla y el JSON /api/status
  * enable/disable/restart → mismas acciones que los scripts CLI
                      (docker/bin/enable_worker, disable_worker)
  * tail_log()      → últimas N líneas de /var/log/supervisor/<prog>.log

Reglas de seguridad:
  * Solo se aceptan nombres que existan como archivo en conf.d.available
    (además del patrón alfanumérico) → no se puede inyectar rutas.
  * `status_web` (este panel) es inmutable: no se puede deshabilitar ni
    reiniciar desde la propia interfaz (rompería la petición en curso).
"""
import datetime
import json
import os
import re
import subprocess

from . import config

_NAME_RE = re.compile(r"^[a-z0-9_]{2,40}$")

# Metadatos visibles en la tabla.
#   desc        → una línea (se muestra siempre en la columna "Programa")
#   descripcion → párrafo "qué hace" (se pliega con <details> en la tabla y
#                 se muestra completo en la tarjeta del módulo Configuración)
# Fuente de cada texto: el docstring/comentario de cada worker en este repo.
CATALOGO = {
    "status_web": {
        "desc": "Este panel de control",
        "cadencia": "siempre activo",
        "app": "workersadmon",
        "descripcion": (
            "Punto de entrada del panel: servidor HTTP con el módulo estándar "
            "de Python en el 8080 interno (8200 en el host). Sirve el login con "
            "cuenta del HUB, la tabla de estado, los logs y la configuración. "
            "Solo toca SQL Server para el login, la sesión, la bitácora y "
            "HUB_Config."),
    },
    "tipo_cambio_worker": {
        "desc": "Tipo de cambio USD/MXN (Banxico + fallback)",
        "cadencia": "diario 6:00 AM",
        "app": "HUB",
        "descripcion": (
            "Cada mañana consulta el dólar FIX de Banxico (serie SF51158, con "
            "token) y, si falla, la API gratuita open.er-api.com. Guarda el "
            "resultado en HUB_Config.tipo_cambio_usd, que leen Cotizaciones, "
            "Órdenes de Compra y el Dashboard del HUB."),
    },
    "bing_worker": {
        "desc": "Fondos de pantalla de Bing",
        "cadencia": "cada 6 h",
        "app": "HUB",
        "descripcion": (
            "Descarga los wallpapers de Bing y rota los últimos N en la tabla "
            "HUB_BingWallpapers (por defecto 5). La app solo llama a "
            "bing_wallpaper.get_active_background(), que lee la BD: el "
            "descargado ocurre únicamente aquí."),
    },
    "oxxogas_contactos_worker": {
        "desc": "Contactos OxxoGas (playwright)",
        "cadencia": "cada 6 h",
        "app": "HUB",
        "descripcion": (
            "Entra con Playwright a Go Vale y refresca el caché de "
            "empresas/contactos de OxxoGas que usa el HUB como catálogo al "
            "registrar vales. Requiere la imagen con Playwright instalado y "
            "las credenciales govale_user / govale_password."),
    },
    "network_scanner_worker": {
        "desc": "Escaneo de red / inventario de IPs",
        "cadencia": "cada 3 min",
        "app": "HUB",
        "descripcion": (
            "Procesa los ARP scan de la subred (los escribe "
            "network_scanner_host fuera del contenedor), detecta entradas y "
            "salidas exigiendo N escaneos consecutivos, filtra MACs "
            "multicast, marca las IPs ZeroTier, limpia escaneos de más de 7 "
            "días y dispara alertas de presencia por Telegram. Alimenta el "
            "módulo Detección de red del HUB."),
    },
    "telegram_worker": {
        "desc": "Alertas por Telegram (cola outbox)",
        "cadencia": "cada 20 s",
        "app": "HUB",
        "descripcion": (
            "Consume la cola HUB_TelegramQueue: atiende los /start entrantes "
            "(vinculación automática de usuarios), envía hasta 10 mensajes por "
            "ciclo —texto con sendMessage o adjunto con sendDocument—, marca "
            "ENVIADO o FALLADO con máximo 3 reintentos y limpia el historial "
            "de más de 30 días. Es quien materializa las alertas de "
            "telegram_alerts.py."),
    },
    "pdf_storage_worker": {
        "desc": "Respaldo de PDFs de reportes a SMB",
        "cadencia": "diario 3:00 AM",
        "app": "HUB",
        "descripcion": (
            "Cada madrugada regenera los PDFs del día anterior de los cinco "
            "módulos (Cotizaciones Materiales, CSP, Reportes, Órdenes de "
            "Compra y Remisiones) y los sube al Fileserver con smbclient en "
            "carpetas por año/mes. Deja la fecha de la última corrida en "
            "HUB_Config.pdf_worker_last_run."),
    },
    "oxxogas_worker": {
        "desc": "Vales OxxoGas vía correo IMAP",
        "cadencia": "cada 1 h",
        "app": "HUB",
        "descripcion": (
            "Revisa la bandeja IMAP de cada usuario con AccesoValesOxxoGas y "
            "sincroniza los correos de vales de OxxoGas al HUB (búsqueda "
            "SINCE optimizada + cabecera Message-ID para saltar los ya "
            "guardados). La última sincronización queda en "
            "HUB_GmailTokens.LastSyncTime."),
    },
    "vales_worker": {
        "desc": "Vales autogenerados (playwright)",
        "cadencia": "cada 5 min",
        "app": "HUB",
        "descripcion": (
            "Busca vales en estatus APROBADO que todavía no tienen "
            "CodigoQR —p. ej. los aprobados desde Field— y los genera en Go "
            "Vale con Playwright (crear_vale), guardando el QR en la BD. "
            "Usa las mismas credenciales que govale_vouchers_worker."),
    },
    "govale_vouchers_worker": {
        "desc": "Vouchers GoVale (playwright)",
        "cadencia": "cada 5 min",
        "app": "HUB",
        "descripcion": (
            "Hace login en el portal de Go Vale, extrae de forma incremental "
            "los vales nuevos desde la última sincronización, genera su imagen "
            "QR con la librería qrcode y los vincula con las solicitudes "
            "pendientes del HUB."),
    },
    "mcp_server": {
        "desc": "Servidor MCP (passkeys, push, /message) — puerto 8000",
        "cadencia": "siempre activo",
        "app": "HUB / Field",
        "descripcion": (
            "Servidor HTTP del protocolo MCP en el puerto 8000 del host: "
            "expone run_command, write_file y read_file, resuelve el reto de "
            "passkeys (JWT) y el envío de notificaciones push VAPID. Lo "
            "consumen Field y las integraciones; de aquí sale la ayuda remota "
            "de opencode (http://ServerVM:8000/message)."),
    },
    # ── Workers de Field (migrados de `field` el 2026-09-26, código en api/) ──
    "avisos": {
        "desc": "Avisos Field (push PWA)",
        "cadencia": "5 min / reportes cada hora / km lunes 8 AM",
        "app": "Field",
        "descripcion": (
            "Notificaciones push de Field: kilómetros semanales (lunes 8:00 AM "
            "hora México, UTC-6 fijo) a usuarios sin registro y reportes sin "
            "firmar nuevos cada hora (dedupe en HUB_Config "
            "field_avisos_rep_<IdUsuario>). Claves VAPID en HUB_Config."),
    },
    "file_indexer": {
        "desc": "Índice de archivos (SMB al Fileserver)",
        "cadencia": "cada 5 min",
        "app": "Field",
        "descripcion": (
            "Recorre Docs/Shared y Docs/Aplicaciones por SMB (FILESERVER "
            "10.188.141.15, pysmb) y escribe el índice en HUB_FileIndex para "
            "la búsqueda de archivos de Field."),
    },
    "legends_cron": {
        "desc": "Sincronización de Legends",
        "cadencia": "cada hora",
        "app": "Field",
        "descripcion": (
            "Sincroniza los datos de Legends cada 3600 s contra la BD "
            "ECCSA_Admon (usa config.load_db_config con las env HUB_DB_*)."),
    },
    "legends_audit": {
        "desc": "Auditoría de Scores de Legends ⚠️ 1 sola instancia",
        "cadencia": "cada 5 min",
        "app": "Field",
        "descripcion": (
            "Audita Scores/Partidas cada 300 s. REGLA DURA: solo puede existir "
            "1 instancia en todo el entorno (con uvicorn --workers 2 se "
            "duplicaba ScoreLog). Desde 2026-09-26 vive SOLO aquí; `field` "
            "corre únicamente nginx + api."),
    },
}

PROTECTED = {"status_web"}   # programas que el panel no manipula


def shell_check():
    """Último resultado del chequeo automático de ECCSA-Shell (o None)."""
    import json
    try:
        with open(config.SHELL_CHECK_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def now():
    return datetime.datetime.now()


# ─── lectura de fuentes ──────────────────────────────────────────────────────
def parse_uptime(text):
    """'1 day, 2:03:04' / '0:05:33' → segundos."""
    if not text:
        return None
    text = text.strip()
    days = 0
    m = re.match(r"^(\d+)\s+days?,\s*(.+)$", text)
    if m:
        days = int(m.group(1))
        text = m.group(2).strip()
    parts = text.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 3:
        h, mi, s = parts
    elif len(parts) == 2:
        h, mi, s = 0, parts[0], parts[1]
    else:
        return None
    return days * 86400 + h * 3600 + mi * 60 + s


def supervisor_status():
    """[(nombre, estado, pid, uptime_texto)] desde supervisord. (filas, error)."""
    try:
        proc = subprocess.run(
            [config.SUPERVISORCTL, "-c", config.SUPERVISOR_CONF, "status"],
            capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return None, str(exc)
    if proc.returncode != 0 and not proc.stdout.strip():
        return None, (proc.stderr or "").strip() or f"rc={proc.returncode}"
    rows = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s*(.*)$", line.strip())
        if not m:
            continue
        name, state, rest = m.group(1), m.group(2).upper(), m.group(3)
        pid = mp = uptime_txt = None
        mp = re.search(r"\bpid\s+(\d+)", rest)
        if mp:
            pid = int(mp.group(1))
        mu = re.search(r"\buptime\s+(.+)$", rest)
        if mu:
            uptime_txt = mu.group(1).strip()
        rows.append((name, state, pid, uptime_txt))
    return rows, None


def read_enabled():
    """Nombres de la lista persistente /data/workers_enabled.txt."""
    names = set()
    try:
        with open(config.ENABLED_FILE, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line:
                    names.add(line)
    except FileNotFoundError:
        pass
    return names


def read_available():
    """Nombres con conf en conf.d.available (workers candidatos)."""
    names = set()
    try:
        for fn in os.listdir(config.AVAILABLE_DIR):
            if fn.endswith(".conf"):
                names.add(fn[:-5])
    except FileNotFoundError:
        pass
    return names


def read_heartbeats():
    """{worker: dict} con la última ejecución reportada por worker_heartbeat."""
    out = {}
    try:
        for fn in os.listdir(config.HEARTBEAT_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(config.HEARTBEAT_DIR, fn), "r",
                          encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    out[data.get("worker") or fn[:-5]] = data
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out


# ─── estado consolidado ──────────────────────────────────────────────────────
def rel_time(when):
    """Texto relativo ('hace 12 min') para un datetime o None."""
    if when is None:
        return "—"
    secs = int((now() - when).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"hace {secs} s"
    if secs < 3600:
        return f"hace {secs // 60} min"
    if secs < 86400:
        return f"hace {secs // 3600} h"
    return f"hace {secs // 86400} d"


def parse_dt(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(str(text)[:19], fmt)
        except ValueError:
            continue
    return None


def build_status():
    """Estado consolidado → dict (lo consume la tabla y el JSON /api/status)."""
    sup_rows, sup_err = supervisor_status()
    enabled = read_enabled()
    available = read_available()
    beats = read_heartbeats()

    progs = {}
    for name in sorted(available):
        progs[name] = {"name": name, "state": "NO_HABILITADO", "pid": None,
                       "uptime_seconds": None, "enabled": False, "available": True}
    for name, state, pid, uptime_txt in (sup_rows or []):
        entry = progs.setdefault(name, {"name": name, "available": False})
        entry.update({"state": state, "pid": pid,
                      "uptime_seconds": parse_uptime(uptime_txt),
                      "uptime_text": uptime_txt,
                      "enabled": entry.get("enabled") or state in ("RUNNING", "STARTING")})
    for name in enabled:
        entry = progs.setdefault(name, {"name": name, "available": name in available})
        entry["enabled"] = True
        if entry.get("state") in (None, "NO_HABILITADO") and not entry.get("pid"):
            entry["state"] = "UNKNOWN"
    if "status_web" in progs:
        progs["status_web"]["enabled"] = True

    for name, entry in progs.items():
        hb = beats.get(name)
        if hb:
            last = parse_dt(hb.get("last_run"))
            entry.update({"last_run": hb.get("last_run"), "last_run_dt": last,
                          "detail": hb.get("detail") or "",
                          "count": hb.get("count")})
        else:
            entry.update({"last_run": None, "last_run_dt": None,
                          "detail": "", "count": None})
        up = entry.get("uptime_seconds")
        entry["started_at"] = (now() - datetime.timedelta(seconds=up)).strftime(
            "%Y-%m-%d %H:%M:%S") if up is not None else None
        entry["running"] = entry.get("state") in ("RUNNING", "STARTING")
        meta = CATALOGO.get(entry["name"], {})
        entry["desc"] = meta.get("desc", "")
        entry["descripcion"] = meta.get("descripcion", "")
        entry["cadencia"] = meta.get("cadencia", "")
        entry["app"] = meta.get("app", "")
        entry["protected"] = entry["name"] in PROTECTED
        lp = log_path(entry["name"])
        entry["log_ok"] = bool(lp and os.path.exists(lp))

    ordered = sorted(progs.values(), key=lambda e: (not e["running"], e["name"]))
    totals = {
        "running": sum(1 for e in ordered if e["running"]),
        "stopped": sum(1 for e in ordered
                       if e["state"] in ("STOPPED", "EXITED", "NO_HABILITADO", "UNKNOWN")),
        "errors": sum(1 for e in ordered if e["state"] in ("FATAL", "BACKOFF")),
        "enabled": sum(1 for e in ordered if e["enabled"]),
        "available": len(available),
        "total": len(ordered),
    }
    return {
        "title": config.TITLE,
        "now": now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": os.environ.get("TZ", "local"),
        "data_dir": config.DATA_DIR,
        "supervisor_error": sup_err,
        "totals": totals,
        "programs": [{k: v for k, v in e.items() if k != "last_run_dt"} for e in ordered],
    }


# ─── acciones ────────────────────────────────────────────────────────────────
def _valid_name(name):
    """True si el nombre es un identificador seguro Y existe en available."""
    if not _NAME_RE.match(name or ""):
        return False
    return os.path.exists(os.path.join(config.AVAILABLE_DIR, f"{name}.conf"))


def _run(argv, timeout=30):
    """Ejecuta un comando (lista, sin shell) y devuelve (ok, salida)."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return False, f"no se encontró el programa: {exc.filename}"
    except subprocess.TimeoutExpired:
        return False, "timeout: el comando tardó demasiado"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


def enable(name):
    """Habilita un worker (mismo camino que el script CLI enable_worker)."""
    if name in PROTECTED:
        return False, f"'{name}' no se puede deshabilitar/habilitar desde el panel"
    if not _valid_name(name):
        return False, f"no existe conf para '{name}'"
    return _run([os.path.join(config.BIN_DIR, "enable_worker"), name])


def disable(name):
    """Deshabilita y detiene un worker."""
    if name in PROTECTED:
        return False, f"'{name}' es el panel y no se puede deshabilitar"
    if not _valid_name(name):
        return False, f"no existe conf para '{name}'"
    return _run([os.path.join(config.BIN_DIR, "disable_worker"), name])


def restart(name):
    """Reinicia un programa en ejecución (debe estar habilitado)."""
    if name in PROTECTED:
        return False, f"'{name}' no se puede reiniciar desde su propia interfaz"
    if not _NAME_RE.match(name or ""):
        return False, "nombre inválido"
    return _run([config.SUPERVISORCTL, "-c", config.SUPERVISOR_CONF,
                 "restart", name])


def log_path(name):
    if not _NAME_RE.match(name or ""):
        return None
    return os.path.join(config.SUPERVISOR_LOG_DIR, f"{name}.log")

def tail_log(name, lines=300):
    """Últimas N líneas del log de un programa. Devuelve (texto, error)."""
    path = log_path(name)
    if not path or not os.path.exists(path):
        return "", f"no hay log para '{name}'"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # Efficientish: leer todo y quedarse con el final (los logs de
            # supervisor están limitados a 10 MB, cabe cómodo en memoria)
            data = fh.read()
    except Exception as exc:
        return "", str(exc)
    rows = data.splitlines()
    return "\n".join(rows[-int(lines):]), ""


def test_telegram_bot():
    """
    Comprueba el token guardado contra la API de Telegram (getMe).
    Devuelve (ok, mensaje). El token jamás aparece en el mensaje ni en la
    bitácora: solo se informa si el bot responde.
    """
    from . import db
    token = ((db.get_config_values(["telegram_bot_token"]) or {})
             .get("telegram_bot_token", "") or "").strip()
    if not token:
        return False, "No hay token guardado en HUB_Config (telegram_bot_token)."
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getMe", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:                     # noqa: BLE001
        return False, f"Sin respuesta de api.telegram.org ({exc})."
    if not isinstance(data, dict) or not data.get("ok"):
        return False, "Telegram rechazó el token (getMe no responde)."
    user = (data.get("result") or {}).get("username") or "?"
    return True, f"Bot @{user} responde correctamente."
