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
# Donde supervisord tiene los .conf ACTIVOS de los programas (los que copia
# enable_worker desde conf.d.available). El panel lo parchea al editar env.
ACTIVE_CONF_DIR = os.environ.get("SUPERVISOR_PROGRAM_DIR", "/etc/supervisor/conf.d")

# ─── Logo del panel ──────────────────────────────────────────────────────────
# Misma imagen que /workspace/HUB/workers/worker.png (idéntica en los dos repos).
def _logo_path():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("PANEL_LOGO", os.path.join(base, "workers", "worker.png"))


LOGO_FILE = _logo_path()

# Textura de fondo (engrane) — la misma que usan Field y Admon; el panel la
# pinta como body::before al 5% de opacidad (patrón "ECCSA Shell", ver DESIGN.md).
def _asset(name):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ENGRANE_FILE = os.environ.get("PANEL_ENGRANE", _asset("engrane.png"))

# Estado del chequeo automático del shell (lo escribe shell/tools/check_daily.py)
SHELL_CHECK_FILE = os.environ.get("SHELL_CHECK_FILE", "/data/shell_check.json")
# Resultado del chequeo de versiones de las 3 apps contra el mandato de Field
# (lo escribe C:\ECCSA-Shell\tools\check_versiones.py en el chequeo diario).
VERSIONES_CHECK_FILE = os.environ.get("VERSIONES_CHECK_FILE", "/data/versiones.json")

# ─── Assets PWA (manifest + service worker + iconos) ────────────────────────
# Viven en static/ (igual que Field y Admon): el servidor los sirve en la raíz
# del sitio (/manifest.webmanifest, /sw.js, /icons/*.png) para que el navegador
# pueda instalar el panel como aplicación.
def _static_dir():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "static")


STATIC_DIR = _static_dir()
MANIFEST_FILE = os.path.join(STATIC_DIR, "manifest.webmanifest")
SW_FILE = os.path.join(STATIC_DIR, "sw.js")
ICONS_DIR = os.path.join(STATIC_DIR, "icons")
# Whitelist de iconos: nada de `..` ni archivos arbitrarios desde /icons/
ICONS_WHITELIST = ("icon-120x120.png", "icon-152x152.png", "icon-167x167.png",
                   "icon-180x180.png", "icon-192x192.png", "icon-512x512.png",
                   "icon-maskable-512.png", "apple-touch-icon.png")

# ─── Red ──────────────────────────────────────────────────────────────────────
PANEL_PORT = int(os.environ.get("STATUS_PORT", "8080"))
TITLE = os.environ.get("STATUS_TITLE", "Workers Admon")

# ─── Banner común (ECCSA-Shell) ───────────────────────────────────────────────
# Versión del panel (la de la app) y del shell (estampada por
# tools/sync_shell.py del repo ECCSA-Shell al copiar panel/shell.css).
APP_ID = "workersadmon"
APP_VERSION = "1.1.0"


def _shell_version():
    """Lee ECCSA_SHELL_VERSION (copia del repo) → '?' si no está."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "ECCSA_SHELL_VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "?"
    except OSError:
        return "?"

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

# ─── Módulos del panel ────────────────────────────────────────────────────────
# El panel se organiza en MÓDULOS, uno por sección, igual que las otras apps
# del shell. Antes la pestaña de inicio se llamaba "Workers" y mostraba el
# estado de los programas: ese contenido es un módulo de estado, y así se llama.
#
# "disabled": la pestaña se muestra grisada hasta que llegue su fase.
# "perm": si el usuario no lo tiene, la pestaña sale grisada con el motivo.
TABS = [
    {"id": "estado", "label": "📊 Estado", "href": "/", "enabled": True,
     "modulo": "Estado",
     "desc": "Qué está corriendo ahora mismo: cada worker, en qué estado y "
             "hace cuánto."},
    {"id": "logs", "label": "📄 Logs", "href": "/logs", "enabled": True,
     "modulo": "Logs",
     "desc": "La última salida de cada worker, y los errores recientes."},
    {"id": "config", "label": "⚙️ Configuración", "href": "/configuracion",
     "enabled": True, "modulo": "Configuración",
     "desc": "Variables de entorno y claves por worker."},
    {"id": "notificaciones", "label": "🔔 Notificaciones",
     "href": "/notificaciones", "enabled": True, "modulo": "Notificaciones",
     "desc": "Telegram, correo saliente y ajustes de IA."},
    {"id": "apps", "label": "⚙️ Apps", "href": "/apps", "enabled": True,
     "perm": "AccesoAppConfig", "modulo": "Apps",
     "desc": "Catálogo de apps: tipo, endpoint y credenciales."},
]


def tab_por_id(tab_id):
    """Devuelve la definición del módulo, o None si el id no existe."""
    for t in TABS:
        if t["id"] == tab_id:
            return t
    return None


def is_cookie_secure(handler):
    """
    Indica si la cookie debe llevar el flag Secure.
    - Detrás de Cloudflare (cloudflared envía X-Forwarded-Proto: https) → sí.
    - Por LAN con http://IP:8200 → no, para no romper el login.
    """
    proto = (handler.headers.get("X-Forwarded-Proto") or "").lower().split(",")[0].strip()
    return proto == "https"
