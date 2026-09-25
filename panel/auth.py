"""
panel/auth.py — Sesiones, cookies, CSRF y control de acceso del panel
=====================================================================
Flujo:
  1. GET /login       → formulario (cookie de doble envío `panel_csrf`)
  2. POST /login      → db.authenticate() + db.create_session_token()
                        → cookie `ecsa_token` (HttpOnly, SameSite=Lax)
  3. Cada petición    → db.validate_session_token() → usuario + permisos
  4. POST (cualquier) → token CSRF de doble envío, comparado en tiempo constante

Los tokens viven en HUB_Sessions, por lo que una sesión iniciada en el HUB
(otro dominio) no se comparte automáticamente, pero el mecanismo es el mismo
y los tokens son intercambiables si se usan desde el mismo host.
"""
import hmac
import hashlib
import os
import re
import secrets
import time
import urllib.parse

from . import config, db

# ─── Intentos fallidos de login (memoria del proceso) ────────────────────────
_attempts = {}   # ip -> {"fails": int, "locked_until": float}


def login_allowed(ip):
    """False si la IP está bloqueada temporalmente por intentos fallidos."""
    info = _attempts.get(ip)
    return not (info and info.get("locked_until", 0) > time.time())


def register_login_failure(ip):
    """Suma un intento fallido y bloquea tras LOGIN_MAX_FAILS durante N segundos."""
    info = _attempts.setdefault(ip, {"fails": 0, "locked_until": 0})
    info["fails"] += 1
    if info["fails"] >= config.LOGIN_MAX_FAILS:
        info["locked_until"] = time.time() + config.LOGIN_LOCK_SECONDS
        info["fails"] = 0


def clear_login_failures(ip):
    _attempts.pop(ip, None)


# ─── Cookies ─────────────────────────────────────────────────────────────────
def read_cookie(header, name):
    """Extrae un valor de la cabecera Cookie."""
    if not header:
        return None
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return urllib.parse.unquote(v)
    return None


def make_cookie(name, value, max_age, secure, http_only=True):
    """Construye la cabecera Set-Cookie (Path=/, SameSite=Lax)."""
    cookie = (f"{name}={urllib.parse.quote(value, safe='')}; Path=/; "
              f"Max-Age={int(max_age)}; SameSite=Lax")
    if secure:
        cookie += "; Secure"
    if http_only:
        cookie += "; HttpOnly"
    return cookie


def clear_cookie(name, secure):
    return make_cookie(name, "", 0, secure)


def csrf_token():
    """Token de doble envío: aleatorio, persistido en cookie + campo oculto."""
    return secrets.token_urlsafe(24)


def check_csrf(handler, form):
    """Valida el CSRF de un POST (cookie == campo oculto, en tiempo constante)."""
    cookie = read_cookie(handler.headers.get("Cookie"), config.CSRF_COOKIE)
    field = form.get("csrf", [""])[0]
    if not cookie or not field:
        return False
    return hmac.compare_digest(cookie, field)


# ─── Sesión del usuario ──────────────────────────────────────────────────────
def current_user(handler):
    """Usuario activo a partir de la cookie ecsa_token (None si no hay sesión)."""
    token = read_cookie(handler.headers.get("Cookie"), config.COOKIE_NAME)
    if not token:
        return None, token
    return db.validate_session_token(token), token


def has_perm(user, perm):
    return bool(user and user.get("perms", {}).get(perm))


def session_cookie(token, secure):
    return make_cookie(config.COOKIE_NAME, token,
                       config.SESSION_DAYS * 86400, secure, http_only=True)


def csrf_cookie(token, secure):
    # No HttpOnly: el formulario lo reenvía como campo oculto (doble envío).
    return make_cookie(config.CSRF_COOKIE, token, 3600 * 12, secure,
                       http_only=False)


def client_ip(handler):
    """IP del cliente (para el límite de intentos)."""
    return (handler.headers.get("X-Real-IP")
            or (handler.client_address[0] if handler.client_address else "?"))


def redirect(url, extra_headers=()):
    """Construye una respuesta 303 (Post/Redirect/Get)."""
    return 303, [("Location", url)] + list(extra_headers), ""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def valid_email(email):
    return bool(email) and bool(_EMAIL_RE.match(email))
