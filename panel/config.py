"""
panel/config.py — Configuración central del panel de WorkersAdmon
=================================================================
Único lugar donde se definen rutas, cookies, puertos y constantes de
autenticación. Todo es sobreescribible por variables de entorno para que
el mismo código corra dentro del contenedor (/app, /data) y en pruebas
locales (un directorio temporal cualquiera).
"""
import os

# ─── Rutas del sistema de archivos ────────────────────────────────────────────
def get_data_dir():
    """Directorio de datos persistente (/data en Docker, ./workers_data en local)."""
    for candidate in (os.environ.get("WORKERS_DATA_DIR", "/data"),
                      os.path.join(os.path.dirname(os.path.dirname(
                          os.path.abspath(__file__))), "workers_data")):
        try:
            os.makedirs(candidate, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    return os.environ.get("WORKERS_DATA_DIR", "/tmp")


DATA_DIR = get_data_dir()
ENABLED_FILE = os.path.join(DATA_DIR, "workers_enabled.txt")
HEARTBEAT_DIR = os.path.join(DATA_DIR, "heartbeats")
SECRET_FILE = os.path.join(DATA_DIR, "panel_secret.txt")

# Directorios de supervisord y del binario/panel (mismos defaults que el contenedor)
AVAILABLE_DIR = os.environ.get("WORKERS_AVAILABLE_DIR", "/app/docker/conf.d.available")
BIN_DIR = os.environ.get("WORKERS_BIN_DIR", "/app/docker/bin")
SUPERVISOR_CONF = os.environ.get("SUPERVISOR_CONF", "/etc/supervisor/supervisord.conf")
SUPERVISOR_LOG_DIR = os.environ.get("SUPERVISOR_LOG_DIR", "/var/log/supervisor")
SUPERVISORCTL = os.environ.get("SUPERVISORCTL", "supervisorctl")

# ─── Red ──────────────────────────────────────────────────────────────────────
PANEL_PORT = int(os.environ.get("STATUS_PORT", "8080"))
TITLE = os.environ.get("STATUS_TITLE", "Workers Admon")

# ─── Autenticación (mismo esquema que el HUB: cookie + HUB_Sessions) ──────────
COOKIE_NAME = "ecsa_token"      # cookie de sesión compartida conceptualmente con HUB
CSRF_COOKIE = "panel_csrf"      # cookie de doble envío para formularios POST
SESSION_DAYS = 30               # vigencia de la sesión (misma que eccsa_db.SESSION_DAYS)
PANEL_PERMISSION = "AccesoConfiguracion"   # permiso requerido para entrar al panel
PANEL_PERMISSIONS = {           # permisos por pestaña (fases B y C)
    "notificaciones": ["AccesoTelegram", "AccesoConfigurarCorreo", "AccesoConfigAI"],
    "apps": ["AccesoAppConfig"],
}

# Límite de intentos de login por IP (anti fuerza bruta)
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 60

# ─── Pestañas del panel ───────────────────────────────────────────────────────
# "disabled": la pestaña se muestra grisada hasta que llegue su fase.
TABS = [
    {"id": "workers", "label": "🔧 Workers", "href": "/", "enabled": True},
    {"id": "notificaciones", "label": "🔔 Notificaciones",
     "href": "/notificaciones", "enabled": False},
    {"id": "apps", "label": "⚙️ Apps", "href": "/apps", "enabled": False},
]


def is_cookie_secure(handler):
    """
    Indica si la cookie debe llevar el flag Secure.
    - Detrás de Cloudflare (cloudflared envía X-Forwarded-Proto: https) → sí.
    - Por LAN con http://IP:8200 → no, para no romper el login.
    """
    proto = (handler.headers.get("X-Forwarded-Proto") or "").lower().split(",")[0].strip()
    return proto == "https"
