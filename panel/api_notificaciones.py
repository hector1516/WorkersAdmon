"""
panel/api_notificaciones.py — API JSON de la pestaña Notificaciones
====================================================================
Los mismos cuatro canales de `panel/views/notifications.py` (Telegram, push,
correo e IA) expuestos como JSON para el SPA.

Lectura: devuelve las claves que pinta la vista y deja los secretos como
`{valor: "", tiene_valor: bool}` — igual que el HTML, nunca el valor.

Guardado: reusa las funciones de `panel.db` y las sondas de `panel.probes` con
las reglas del POST del panel SSR:

  * secreto en blanco = no cambiar (nunca se borra por omisión);
  * el puerto SMTP se valida antes de escribir;
  * `probar` sí manda (Telegram, push, correo, IA), por eso el SPA no lo llama
    solo: queda detrás de un botón que la persona pulsa.

Rutas:
  GET  /api/panel/notificaciones?bloque=conexion|telegram|push|correo|ia
  GET  /api/panel/notificaciones/<bloque>          (alias de la query)
  POST /api/panel/notificaciones/<bloque>          {accion: save|probar, valores}
"""
import logging
import urllib.parse

from . import api as _api
from . import auth, db, probes, workers
from .views import notifications as _view

log = logging.getLogger("panel.api.notificaciones")

BLOQUES = ("conexion", "telegram", "push", "correo", "ia")


# ── utilidades ───────────────────────────────────────────────────────────────
def _query(handler):
    """Query string de la petición (la ruta llega sin query a la función)."""
    q = urllib.parse.urlsplit(getattr(handler, "path", "") or "").query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items() if v}


def _secreto(valor):
    """Forma pública de un secreto: nunca el valor, solo si hay uno."""
    return {"valor": "", "tiene_valor": bool((valor or "").strip())}


def _permiso(bloque):
    _, perm = _view.PERMISOS.get(bloque, (None, ""))
    return perm or None


def _puede(user, bloque):
    perm = _permiso(bloque)
    if not perm:
        return True
    return bool(auth.has_perm(user, perm))


def _iso(valor):
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat(timespec="seconds")
    return str(valor)


def _texto(valores, clave, defecto=""):
    """Campo de texto del JSON (tolera bool y None sin escribir "None")."""
    valor = valores.get(clave, defecto)
    if isinstance(valor, bool):
        return "1" if valor else "0"
    return str(valor if valor is not None else defecto).strip()


def _si(valores, clave, defecto=False):
    """Bool tolerante: acepta true, 1, "1", "true", "on"."""
    if clave not in valores:
        return defecto
    valor = valores.get(clave)
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on")
    return bool(valor)


def _log(user, accion):
    """Bitácora: es un extra, nunca tumba el guardado."""
    try:
        db.log_activity((user or {}).get("email"), "Panel Workers", accion)
    except Exception:
        log.warning("no se pudo registrar en la bitácora: %s", accion)


def _sin_permiso(bloque):
    return 403, {"error": f"Requiere el permiso {_permiso(bloque)}."}


# ── lectura ──────────────────────────────────────────────────────────────────
def _conexion(user):
    """Resumen de los cuatro canales, como las KPIs de la vista."""
    metricas = db.telegram_metrics() or {}
    subs = db.get_push_subscriptions() or []
    correo = db.get_email_config() or {}
    ia = db.get_ai_config() or {}
    tg = (db.get_config_values(["telegram_bot_token"]) or {})
    canales = [
        {"id": "telegram", "nombre": "Telegram",
         "configurado": bool((tg.get("telegram_bot_token") or "").strip()),
         "permiso": _permiso("telegram"), "puede": _puede(user, "telegram")},
        {"id": "push", "nombre": "Push web",
         "configurado": bool((db.get_push_config() or {}).get("public")),
         "permiso": _permiso("push"), "puede": _puede(user, "push")},
        {"id": "correo", "nombre": "Correo",
         "configurado": bool((correo.get("smtp_server") or "").strip()),
         "permiso": _permiso("correo"), "puede": _puede(user, "correo")},
        {"id": "ia", "nombre": "IA",
         "configurado": bool((ia.get("api_key") or "").strip()),
         "permiso": _permiso("ia"), "puede": _puede(user, "ia")},
    ]
    return {
        "bloque": "conexion",
        "resumen": {
            "vinculados": metricas.get("vinculados", 0),
            "eventos": metricas.get("eventos", 0),
            "suscriptores": len(subs),
            "en_cola": metricas.get("pendientes", 0),
        },
        "canales": canales,
    }


def _telegram(user):
    cfg = db.get_config_values(["telegram_bot_token"]) or {}
    m = db.telegram_metrics() or {}
    return {
        "bloque": "telegram", "permiso": _permiso("telegram"),
        "puede": True,
        "token": _secreto(cfg.get("telegram_bot_token")),
        "resumen": {
            "vinculados": m.get("vinculados", 0),
            "eventos": m.get("eventos", 0),
            "destinatarios": m.get("destinatarios", 0),
            "pendientes": m.get("pendientes", 0),
            "fallados": m.get("fallados", 0),
            "enviados": m.get("enviados", 0),
        },
    }


def _push(user):
    cfg = db.get_push_config() or {}
    subs = db.get_push_subscriptions() or []
    return {
        "bloque": "push", "permiso": _permiso("push"), "puede": True,
        "vapid_publica": cfg.get("public") or "",
        "vapid_privada": _secreto(cfg.get("private")),
        "email": cfg.get("email") or "",
        "suscriptores": len(subs),
        "actualizado": _iso(cfg.get("updated")),
    }


def _correo(user):
    cfg = db.get_email_config() or {}
    return {
        "bloque": "correo", "permiso": _permiso("correo"), "puede": True,
        "smtp_server": cfg.get("smtp_server") or "",
        "port": int(cfg.get("port") or 465),
        "username": cfg.get("username") or "",
        "password": _secreto(cfg.get("password")),
        "use_ssl": bool(cfg.get("use_ssl")),
        "use_tls": bool(cfg.get("use_tls")),
        "require_auth": bool(cfg.get("require_auth")),
    }


def _ia(user):
    cfg = db.get_ai_config() or {}
    return {
        "bloque": "ia", "permiso": _permiso("ia"), "puede": True,
        "provider": cfg.get("provider") or "google_gemini",
        "api_key": _secreto(cfg.get("api_key")),
        "model": cfg.get("model") or "gemini-2.0-flash",
    }


_LEER = {
    "conexion": _conexion,
    "telegram": _telegram,
    "push": _push,
    "correo": _correo,
    "ia": _ia,
}


def _get(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    bloque = (params.get("bloque") or _query(handler).get("bloque")
              or "conexion").strip().lower()
    if bloque not in BLOQUES:
        return 400, {"error": "Bloque no válido."}
    return 200, _LEER[bloque](user)


# ── guardado ─────────────────────────────────────────────────────────────────
def _guardar_telegram(user, valores):
    token = _texto(valores, "telegram_bot_token") or _texto(valores, "token")
    if not token:
        return 200, {"ok": True,
                     "mensaje": "Sin cambios: el token quedó vacío (no se "
                                "borra lo guardado)."}
    ok, err = db.set_config_values({"telegram_bot_token": token})
    if not ok:
        return 400, {"error": f"No se pudo guardar el token: {err}"}
    _log(user, "Token de Telegram actualizado")
    return 200, {"ok": True, "mensaje": "Token de Telegram guardado."}


def _guardar_push(user, valores):
    cfg = db.get_push_config() or {}
    publica = (_texto(valores, "VapidPublicKey")
               or _texto(valores, "vapid_publica") or cfg.get("public") or "")
    privada = (_texto(valores, "VapidPrivateKey")
               or _texto(valores, "vapid_privada") or cfg.get("private") or "")
    email = _texto(valores, "VapidEmail") or _texto(valores, "email")
    ok, err = db.save_push_config(publica, privada, email)
    if not ok:
        return 400, {"error": f"No se pudieron guardar las claves VAPID: {err}"}
    _log(user, "Claves VAPID actualizadas")
    return 200, {"ok": True, "mensaje": "Claves VAPID guardadas."}


def _guardar_correo(user, valores):
    try:
        puerto = int(_texto(valores, "port", "465"))
        if not 1 <= puerto <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return 400, {"error": "El puerto debe estar entre 1 y 65535."}
    cfg = db.get_email_config() or {}
    password = _texto(valores, "password") or cfg.get("password") or ""
    ok, err = db.save_email_config(
        _texto(valores, "smtp_server"), puerto, _texto(valores, "username"),
        password, _si(valores, "use_ssl", True), _si(valores, "use_tls"),
        _si(valores, "require_auth", True))
    if not ok:
        return 400, {"error": f"No se pudo guardar la config SMTP: {err}"}
    _log(user, "Configuración SMTP actualizada")
    return 200, {"ok": True, "mensaje": "Configuración SMTP guardada."}


def _guardar_ia(user, valores):
    cfg = db.get_ai_config() or {}
    api_key = _texto(valores, "api_key") or cfg.get("api_key") or ""
    ok, err = db.save_ai_config(
        _texto(valores, "provider") or "google_gemini",
        api_key,
        _texto(valores, "model") or "gemini-2.0-flash")
    if not ok:
        return 400, {"error": f"No se pudo guardar la config de IA: {err}"}
    _log(user, "Configuración de IA actualizada")
    return 200, {"ok": True, "mensaje": "Configuración de IA guardada."}


_GUARDAR = {
    "telegram": _guardar_telegram,
    "push": _guardar_push,
    "correo": _guardar_correo,
    "ia": _guardar_ia,
}


# ── pruebas de un clic ───────────────────────────────────────────────────────
def _probar_telegram(user, valores):
    ok, msg = workers.test_telegram_bot()
    _log(user, f"Prueba de token de Telegram: {'OK' if ok else 'ERROR'}")
    return 200, {"ok": bool(ok), "mensaje": f"Token de Telegram: {msg}"}


def _probar_push(user, valores):
    ok, msg = probes.probe_push()
    _log(user, f"Push de prueba: {'OK' if ok else 'ERROR'}")
    return 200, {"ok": bool(ok), "mensaje": msg}


def _probar_correo(user, valores):
    destino = (_texto(valores, "destino") or _texto(valores, "para")
               or (user or {}).get("email") or "")
    if not destino:
        return 400, {"error": "Falta el correo de destino de la prueba."}
    ok, msg = probes.probe_smtp(db.get_email_config(), destino)
    _log(user, f"Prueba de correo a '{destino}': {'OK' if ok else 'ERROR'}")
    return 200, {"ok": bool(ok), "mensaje": msg}


def _probar_ia(user, valores):
    ok, msg = probes.probe_ai(db.get_ai_config())
    _log(user, f"Prueba de IA: {'OK' if ok else 'ERROR'}")
    return 200, {"ok": bool(ok), "mensaje": msg}


_PROBAR = {
    "telegram": _probar_telegram,
    "push": _probar_push,
    "correo": _probar_correo,
    "ia": _probar_ia,
}


# ── POST ─────────────────────────────────────────────────────────────────────
def _post(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error

    bloque = (params.get("bloque") or "").strip().lower()
    if bloque not in _GUARDAR:
        return 404, {"error": "Bloque no válido."}
    if not _puede(user, bloque):
        return _sin_permiso(bloque)

    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    accion = str(cuerpo.get("accion") or "").strip().lower()
    valores = cuerpo.get("valores") or {}
    if not isinstance(valores, dict):
        return 400, {"error": "Los valores deben ser un objeto."}

    if accion == "save":
        return _GUARDAR[bloque](user, valores)
    if accion == "probar":
        return _PROBAR[bloque](user, valores)
    return 400, {"error": "Acción no válida."}


# ── registro ─────────────────────────────────────────────────────────────────
_api.registrar("GET", r"/api/panel/notificaciones", _get)
_api.registrar("GET", r"/api/panel/notificaciones/(?P<bloque>[a-z]+)", _get)
_api.registrar("POST", r"/api/panel/notificaciones/(?P<bloque>[a-z]+)", _post)
