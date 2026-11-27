"""
panel/api.py — API JSON del panel para el frontend Svelte
=========================================================
Contrato en `panel_ui/API.md`. Python responde JSON bajo `/api/panel/...`; el
SPA compilado (`panel_ui/dist`) lo sirve `panel/server.py` y las vistas SSR
viven bajo `/clasico/...` como respaldo de emergencia.

Cómo se engancha:

  * `atender(handler)` se llama al principio de `Handler.do_GET` y
    `Handler.do_POST`. Devuelve True si ya respondió (y False para que siga el
    routing del servidor).
  * Solo intercepta `/api/panel/...`. Nada más: las rutas del SPA y las
    estáticas las resuelve `panel/server.py`.

Reglas:

  * Misma sesión que el panel: cookie `ecsa_token` + `HUB_Sessions` (panel/auth.py).
  * CSRF de doble envío: cookie `panel_csrf` == cabecera `X-CSRF-Token`, en TODO
    POST, comparado con `hmac.compare_digest`. Sin token → 403.
  * Errores siempre `{"error": "texto para la persona"}`, en español, con el
    código HTTP correcto. Un fallo inesperado devuelve 500 y se registra en el
    log; nunca se propaga al servidor.
  * Nada de secretos en el JSON (ni `openwa_api_key`, ni claves de
    `HUB_EmailConfig`, ni contraseñas).
  * Sin dependencias nuevas: stdlib + pymssql (por `panel.db`).

Para agregar rutas desde otro módulo: al final de este archivo se importan los
`panel/api_*.py` que existan, y cualquiera puede llamar a
`registrar(metodo, patron, funcion)`. La función recibe `(handler, params,
cuerpo)` y devuelve `(codigo, objeto_json)`.
"""
import collections
import datetime
import hmac
import importlib
import logging
import os
import pkgutil
import re
import urllib.parse

from . import auth, config, db, workers
from .views import workers as workers_view

log = logging.getLogger("panel.api")

LOG_LINES = 300                 # líneas de log que devuelve /logs/{nombre}
_LATIDO_VIEJO = datetime.timedelta(hours=24)

# (metodo, patron_compilado, funcion)
RUTAS = []

# Últimas lecturas de /estado para los gráficos del tablero. Vive en memoria del
# proceso: no había histórico en la base y no se inventa una tabla para un
# gráfico. El primer punto es el valor de ahora; luego uno por consulta (12).
_SERIES = collections.deque(maxlen=12)


# ── registro de rutas ────────────────────────────────────────────────────────
def registrar(metodo, patron, funcion):
    """Registra una ruta de la API.

    `patron` es una regex sin ancla (se compara con fullmatch contra la ruta ya
    normalizada, sin query ni barra final). Los grupos con nombre llegan a la
    función como `params`.
    """
    RUTAS.append(((metodo or "GET").upper(), re.compile(patron), funcion))


# ── punto de entrada ─────────────────────────────────────────────────────────
def atender(handler):
    """
    Atiende la petición si es de la API o del SPA. True = ya respondió.

    Se llama antes del routing SSR. Se decide primero, sin efectos, si la ruta
    es nuestra: un handler de prueba (o cualquier objeto sin `path`) no debe
    convertirse en un 500. Cualquier error inesperado DENTRO de nuestras rutas
    se registra y se responde 500 con el mensaje genérico.
    """
    try:
        path = urllib.parse.unquote(
            urllib.parse.urlsplit(getattr(handler, "path", "") or "").path
        ).rstrip("/") or "/"
    except Exception:
        return False

    metodo = (getattr(handler, "command", "GET") or "GET").upper()
    es_api = path == "/api/panel" or path.startswith("/api/panel/")
    if not es_api:
        return False

    try:
        return _atender_api(handler, metodo, path)
    except Exception:
        log.exception("error inesperado atendiendo %s %s", metodo, path)
        try:
            _responder(handler, 500, {
                "error": "No se pudo completar la operación."})
        except Exception:
            log.exception("no se pudo ni responder el error")
        return True


def _atender_api(handler, metodo, path):
    """Despacha /api/panel/...: CSRF, ruta, sesión (cada función) y respuesta."""
    cuerpo = handler._read_json() if metodo == "POST" else {}

    if metodo == "POST" and not _csrf_ok(handler):
        _responder(handler, 403, {
            "error": "Token de seguridad inválido o caducado. Recarga la "
                     "página e intenta de nuevo."})
        return True

    funcion, params = None, {}
    ruta_con_otro_metodo = False
    for r_metodo, patron, r_funcion in RUTAS:
        coincidencia = patron.fullmatch(path)
        if not coincidencia:
            continue
        if r_metodo == metodo:
            funcion, params = r_funcion, coincidencia.groupdict()
            break
        ruta_con_otro_metodo = True

    if funcion is None:
        if ruta_con_otro_metodo:
            _responder(handler, 405, {"error": "Método no permitido."})
        else:
            _responder(handler, 404, {"error": "No encontrado."})
        return True

    try:
        codigo, objeto = funcion(handler, params or {}, cuerpo)
    except Exception:
        log.exception("error en %s %s", metodo, path)
        codigo, objeto = 500, {"error": "No se pudo completar la operación."}
    _responder(handler, codigo, objeto)
    return True


# ── respuestas y CSRF ────────────────────────────────────────────────────────
def _responder(handler, codigo, objeto):
    """Responde JSON y suelta las cookies que la ruta haya dejado pendientes."""
    extra = tuple(getattr(handler, "_api_cookies", ()) or ())
    if hasattr(handler, "_api_cookies"):
        del handler._api_cookies
    if objeto is None:
        handler._send(codigo, "", "application/json", extra)
    else:
        handler._json(objeto, codigo, extra)


def _cookie(handler, valor):
    """Agrega una cabecera Set-Cookie a la respuesta de la ruta actual."""
    pendientes = list(getattr(handler, "_api_cookies", ()) or ())
    pendientes.append(("Set-Cookie", valor))
    handler._api_cookies = pendientes


def _csrf_ok(handler):
    """Doble envío: cookie panel_csrf == cabecera X-CSRF-Token (constante)."""
    cookie = auth.read_cookie(handler.headers.get("Cookie"), config.CSRF_COOKIE)
    enviado = (handler.headers.get("X-CSRF-Token") or "").strip()
    if not cookie or not enviado:
        return False
    return hmac.compare_digest(cookie, enviado)


# ── sesión ───────────────────────────────────────────────────────────────────
def _usuario(handler):
    user, _token = auth.current_user(handler)
    return user


def _user_json(user):
    """Forma pública del usuario: nunca permisos internos ni contraseñas."""
    perms = user.get("perms") or {}
    return {
        "id": user.get("id"),
        "nombre": user.get("nombre") or "",
        "email": user.get("email") or "",
        "nickname": user.get("nickname") or "",
        "permisos": [k for k, v in perms.items() if v],
    }


def _sesion(handler):
    """(user, None) con sesión y permiso de panel; (None, (codigo, error))."""
    user = _usuario(handler)
    if not user:
        return None, (401, {"error": "Sin sesión. Inicia sesión de nuevo."})
    if not auth.has_perm(user, config.PANEL_PERMISSION):
        return None, (403, {"error": "Tu usuario no tiene el permiso "
                                     "AccesoConfiguracion para usar el panel."})
    return user, None


# ── auth ─────────────────────────────────────────────────────────────────────
def _me(handler, params, cuerpo):
    user = _usuario(handler)
    # Aunque no haya sesión se emite (o reusa) la cookie CSRF: el formulario de
    # acceso del SPA la necesita para su POST /login.
    csrf = handler._csrf()
    if not user:
        return 401, {"error": "Sin sesión.", "csrf": csrf}
    return 200, {"user": _user_json(user), "csrf": csrf}


def _login(handler, params, cuerpo):
    ip = auth.client_ip(handler)
    if not auth.login_allowed(ip):
        return 429, {"error": "Demasiados intentos. Espera un minuto."}

    email = str(cuerpo.get("email") or "").strip().lower()
    password = str(cuerpo.get("password") or "")
    if not auth.valid_email(email) or not password:
        return 400, {"error": "Correo o contraseña no válidos."}

    user = db.authenticate(email, password)
    if not user:
        auth.register_login_failure(ip)
        return 401, {"error": "Correo o contraseña incorrectos."}

    token = db.create_session_token(user["email"])
    if not token:
        return 500, {"error": "No se pudo crear la sesión (¿base de datos?)."}

    auth.clear_login_failures(ip)
    db.log_activity(user["email"], "Panel Workers",
                    "Inicio de sesión en el panel (API)")

    secure = config.is_cookie_secure(handler)
    _cookie(handler, auth.session_cookie(token, secure))
    csrf = handler._csrf()
    return 200, {"user": _user_json(user), "csrf": csrf}


def _logout(handler, params, cuerpo):
    token = auth.read_cookie(handler.headers.get("Cookie"), config.COOKIE_NAME)
    user = _usuario(handler)
    if user:
        db.log_activity(user.get("email"), "Panel Workers",
                        "Cierre de sesión en el panel (API)")
    db.delete_session_token(token)

    secure = config.is_cookie_secure(handler)
    _cookie(handler, auth.clear_cookie(config.COOKIE_NAME, secure))
    _cookie(handler, auth.clear_cookie(config.CSRF_COOKIE, secure))
    return 204, None


# ── estado ───────────────────────────────────────────────────────────────────
def _kpis(status, viejos):
    t = status.get("totals") or {}
    return [
        {"label": "Corriendo", "value": t.get("running", 0),
         "hint": f'de {t.get("total", 0)} programas', "tone": "ok",
         "icon": "play"},
        {"label": "Con error", "value": t.get("errors", 0),
         "hint": "FATAL o reintentando",
         "tone": "bad" if t.get("errors") else "", "icon": "alert"},
        {"label": "Detenidos", "value": t.get("stopped", 0),
         "hint": f'{t.get("enabled", 0)} habilitados',
         "tone": "warn" if t.get("stopped") else "", "icon": "pause"},
        {"label": "Latidos viejos", "value": len(viejos),
         "hint": "más de 24 h sin latir",
         "tone": "warn" if viejos else "", "icon": "clock"},
    ]


def _worker_json(e):
    """Un worker del tablero: estado, latido, vigencia y descripción."""
    return {
        "nombre": e.get("name") or "",
        "estado": e.get("state") or "",
        "latido": workers_view.latido_texto(e),
        "viejo": workers_view.worker_viejo(e),
        "descripcion": e.get("descripcion") or "",
        "desc": e.get("desc") or "",
        "app": e.get("app") or "",
        "cadencia": e.get("cadencia") or "",
        "habilitado": bool(e.get("enabled")),
        "corriendo": bool(e.get("running")),
        "protegido": bool(e.get("protected")),
        "log": bool(e.get("log_ok")),
    }


def _grupos(status):
    """Workers agrupados por app, como los pinta el tablero."""
    grupos = {}
    for e in status.get("programs") or []:
        app = (e.get("app") or "").strip() or "Sin app"
        grupos.setdefault(app, []).append(e)
    return {"grupos": [
        {"nombre": app,
         "items": [_worker_json(e) for e in
                   sorted(items, key=lambda w: w.get("name") or "")]}
        for app, items in sorted(grupos.items())
    ]}


def _estado(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    status = workers.build_status()
    t = status.get("totals") or {}
    viejos = workers_view.latidos_viejos(status.get("programs") or [])
    avisos = workers_view.avisos_datos(status, workers.shell_check(),
                                       workers.versiones_check())

    # Series para los gráficos: sin histórico persistido, el primer punto es el
    # valor actual y cada consulta agrega uno (máximo 12).
    _SERIES.append((t.get("running", 0), t.get("errors", 0)))

    return 200, {
        "kpis": _kpis(status, viejos),
        "series": {
            "corriendo": [p[0] for p in _SERIES],
            "error": [p[1] for p in _SERIES],
        },
        "workers": _grupos(status),
        "avisos": avisos,
        "actualizado": datetime.datetime.now().isoformat(timespec="seconds"),
        "zona": status.get("timezone") or "",
        "supervisor_error": status.get("supervisor_error"),
    }


# ── logs ─────────────────────────────────────────────────────────────────────
def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def _ultimo_log(e):
    """Última escritura del log (o el último latido si no hay archivo)."""
    ruta = workers.log_path(e.get("name") or "")
    try:
        if ruta:
            return _iso(datetime.datetime.fromtimestamp(os.path.getmtime(ruta)))
    except OSError:
        pass
    return _iso(workers.parse_dt(e.get("last_run")))


def _logs(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    status = workers.build_status()
    items = []
    for e in sorted(status.get("programs") or [], key=lambda w: w.get("name") or ""):
        items.append({
            "nombre": e.get("name") or "",
            "estado": e.get("state") or "",
            "ultimo": _ultimo_log(e),
            "descripcion": e.get("descripcion") or "",
            "desc": e.get("desc") or "",
            "log": bool(e.get("log_ok")),
        })
    return 200, {"items": items}


def _log_texto(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    nombre = (params.get("nombre") or "").strip()
    ruta = workers.log_path(nombre)
    if not ruta:
        return 400, {"error": "Nombre de worker no válido."}
    if not os.path.exists(ruta):
        return 404, {"error": f"No hay log para '{nombre}'."}

    texto, fallo = workers.tail_log(nombre, LOG_LINES)
    if fallo:
        log.error("no se pudo leer el log de %s: %s", nombre, fallo)
        return 500, {"error": "No se pudo leer el log."}
    return 200, {"nombre": nombre, "texto": texto}


# ── acciones sobre workers ───────────────────────────────────────────────────
_ACCIONES = {
    # accion: (infinitivo para el error, participio para el éxito, función)
    "start": ("arrancar", "arrancado", "start"),
    "stop": ("detener", "detenido", "stop"),
    "restart": ("reiniciar", "reiniciado", "restart"),
    "enable": ("activar", "activado", "enable"),
    "disable": ("desactivar", "deshabilitado", "disable"),
}


def _accion_worker(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    nombre = (params.get("nombre") or "").strip()
    accion = (params.get("accion") or "").strip().lower()
    if accion not in _ACCIONES:
        return 400, {"error": "Acción no válida."}
    if not workers._NAME_RE.match(nombre or ""):
        return 400, {"error": "Nombre de worker no válido."}

    infinitivo, participio, funcion = _ACCIONES[accion]
    ok, salida = getattr(workers, funcion)(nombre)
    if not ok:
        return 400, {"error": f"No se pudo {infinitivo} '{nombre}': "
                              f"{salida or 'error'}"}

    db.log_activity(user["email"], "Panel Workers",
                    f"Worker '{nombre}' {participio}")
    detalle = (salida or "").strip().splitlines()
    return 200, {
        "ok": True,
        "nombre": nombre,
        "accion": accion,
        "mensaje": f"Worker '{nombre}' {participio}.",
        "detalle": detalle[-1] if detalle else "",
    }


# ── rutas del primer corte ───────────────────────────────────────────────────
registrar("GET", r"/api/panel/me", _me)
registrar("POST", r"/api/panel/login", _login)
registrar("POST", r"/api/panel/logout", _logout)
registrar("GET", r"/api/panel/estado", _estado)
registrar("GET", r"/api/panel/logs", _logs)
registrar("GET", r"/api/panel/logs/(?P<nombre>[a-z0-9_]{2,40})", _log_texto)
registrar("POST",
          r"/api/panel/workers/(?P<nombre>[a-z0-9_]{2,40})/(?P<accion>[a-z]+)",
          _accion_worker)


# Otros módulos de la API (config, apps, avisos, notificaciones, whatsapp...)
# se registran solos: basta con que existan como panel/api_<nombre>.py.
def _cargar_extensiones():
    for info in pkgutil.iter_modules([os.path.dirname(os.path.abspath(__file__))]):
        if not info.name.startswith("api_"):
            continue
        try:
            importlib.import_module(f".{info.name}", __package__)
        except Exception:
            log.exception("no se pudo cargar el módulo %s", info.name)


_cargar_extensiones()
