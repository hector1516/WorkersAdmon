"""
panel/server.py — Servidor HTTP del panel de control
=====================================================
Routing (HTML puro, formularios POST + Post/Redirect/Get, sin JS obligatorio):

  GET  /healthz                        → "ok" (sondeo, sin auth)
  GET  /api/status                     → JSON de estado (sin secretos, sin auth)
  GET  /login                          → formulario de acceso
  POST /login                          → autentica y crea la sesión (HUB_Sessions)
  POST /logout                         → cierra sesión
  GET  /                               → pestaña Workers (requiere sesión + permiso)
  GET  /workers/<nombre>/logs          → logs de un programa
  POST /workers/<nombre>/enable|disable|restart → acción + redirección
  GET  /configuracion                  → pestaña Configuración (por worker)
  POST /configuracion/<nombre>         → guarda env + HUB_Config de un worker
  POST /configuracion/<nombre>/probar  → prueba un token (bot de Telegram)
  GET  /notificaciones | /apps         → marcador de fase (pestañas aún no libres)

Seguridad: cookie `ecsa_token` (HttpOnly), CSRF de doble envío, límite de
intentos de login, cabeceras anti-clickjacking y permiso `AccesoConfiguracion`.
"""
import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth, config, db, envconf, spec, workers
from .templates import esc, forbidden_page, login_page, page
from .views import config as config_view
from .views import workers as workers_view

MAX_BODY = 64 * 1024          # límite del cuerpo de un POST
LOG_LINES = 300               # líneas de log mostradas

_FUTURE_TABS = {
    "notificaciones": ("🔔 Notificaciones",
                       "Fase C — Telegram, Push, SMTP e IA con pruebas de envío.",
                       "notificaciones"),
    "apps": ("⚙️ Apps",
             "Fase D — catálogo de claves de configuración por app "
             "(HUB, admon, Field y futuras).", "apps"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkersPanel/1.0"

    def log_message(self, fmt, *args):
        """Silencioso: los logs propios viven en /var/log/supervisor/status_web.log"""
        pass

    # ── respuestas ───────────────────────────────────────────────────────────
    def _send(self, code, body, ctype, extra=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        headers = list(extra) + [
            ("Content-Type", f"{ctype}; charset=utf-8"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "same-origin"),
        ]
        if getattr(self, "_set_csrf", None):
            headers.append(("Set-Cookie", auth.csrf_cookie(
                self._set_csrf, config.is_cookie_secure(self))))
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _html(self, text, code=200, extra=()):
        self._send(code, text, "text/html", extra)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2),
                   "application/json")

    def _redirect(self, url, extra=()):
        self._send(303, "", "text/html", [("Location", url)] + list(extra))

    # ── utilidades de request ────────────────────────────────────────────────
    def _parts(self):
        parts = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parts.path).rstrip("/") or "/"
        return path, urllib.parse.parse_qs(parts.query, keep_blank_values=True)

    def _read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return urllib.parse.parse_qs(raw.decode("utf-8", "replace"),
                                          keep_blank_values=True)
        except Exception:
            return {}

    def _csrf(self):
        """Token de doble envío: reusa la cookie si existe, si no lo genera."""
        token = auth.read_cookie(self.headers.get("Cookie"), config.CSRF_COOKIE)
        if not token:
            token = auth.csrf_token()
            self._set_csrf = token
        return token

    def _flash(self, query):
        return (query.get("ok", [""])[0] or "",
                query.get("err", [""])[0] or "")

    def _client_ip(self):
        return auth.client_ip(self)

    # ── gate de acceso ───────────────────────────────────────────────────────
    def _user(self):
        """Usuario con sesión válida o None."""
        user, _token = auth.current_user(self)
        return user

    def _require(self):
        """
        Devuelve (user, None) si hay sesión + permiso de panel;
        si no, responde (None, respuesta_ya_enviada=True) y devuelve (None, True).
        """
        user = self._user()
        if not user:
            self._redirect("/login")
            return None, True
        if not auth.has_perm(user, config.PANEL_PERMISSION):
            self._html(forbidden_page(
                "Tu usuario no tiene el permiso AccesoConfiguracion para "
                "entrar al panel de workers. Pídeselo al administrador del HUB."))
            return None, True
        return user, False

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self):
        path, query = self._parts()

        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/api/status":
            try:
                return self._json(workers.build_status())
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
        if path == "/login":
            if self._user():
                return self._redirect("/")
            return self._html(login_page(csrf=self._csrf()))
        if path == "/logout":
            return self._redirect("/login")

        if path == "/":
            user, done = self._require()
            if done:
                return
            status = workers.build_status()
            ok, err = self._flash(query)
            return self._html(workers_view.render(
                status, user, flash_ok=ok, flash_err=err, csrf=self._csrf()))

        if path.startswith("/workers/") and path.endswith("/logs"):
            user, done = self._require()
            if done:
                return
            name = path[len("/workers/"):-len("/logs")]
            text, error = workers.tail_log(name, LOG_LINES)
            return self._html(workers_view.logs_page(name, text, error, user))

        if path == "/configuracion":
            user, done = self._require()
            if done:
                return
            ok, err = self._flash(query)
            return self._html(config_view.render(
                workers.build_status(), user, flash_ok=ok, flash_err=err,
                csrf=self._csrf()))

        if path in ("/notificaciones", "/apps"):
            user, done = self._require()
            if done:
                return
            title, desc, tab = _FUTURE_TABS[path.strip("/")]
            body = (f'<div class="panel"><div class="empty">'
                    f'<div style="font-size:1.6rem">🚧</div>'
                    f'<h2 style="margin:8px 0">{esc(title)}</h2>'
                    f'<p>{esc(desc)}</p>'
                    f'<p class="muted">Esta pestaña se habilita en una fase '
                    f'siguiente; el código ya está preparado para ella.</p>'
                    f'<a href="/">← Volver a Workers</a></div></div>')
            return self._html(page(tab, body, user=user))

        return self._send(404, "No encontrado", "text/plain")

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        path, _query = self._parts()
        form = self._read_form()

        if path == "/login":
            return self._post_login(form)
        if path == "/logout":
            return self._post_logout()

        # Toda el resto exige sesión + CSRF
        if not auth.check_csrf(self, form):
            return self._html(page("error",
                '<div class="panel"><div class="empty">⚠️ Token de seguridad '
                'inválido o caducado. <a href="/">Recarga la página</a> e '
                'intenta de nuevo.</div></div>'), 403)
        user, done = self._require()
        if done:
            return

        if path.startswith("/workers/"):
            rest = path[len("/workers/"):]
            name, _, action = rest.partition("/")
            return self._worker_action(user, name, action)

        if path.startswith("/configuracion/"):
            rest = path[len("/configuracion/"):]
            name, _, action = rest.partition("/")
            return self._config_action(user, name, action, form)

        return self._send(404, "No encontrado", "text/plain")

    # ── acciones ─────────────────────────────────────────────────────────────
    def _post_login(self, form):
        ip = self._client_ip()
        if not auth.login_allowed(ip):
            return self._html(login_page(locked=True, csrf=self._csrf()), 429)

        if not auth.check_csrf(self, form):
            return self._html(login_page(
                error="Sesión de formulario caducada, vuelve a intentarlo.",
                csrf=self._csrf()), 403)

        email = (form.get("email", [""])[0] or "").strip().lower()
        password = form.get("password", [""])[0] or ""
        if not auth.valid_email(email) or not password:
            return self._html(login_page(error="Correo o contraseña no válidos.",
                                         csrf=self._csrf()))

        user = db.authenticate(email, password)
        if not user:
            auth.register_login_failure(ip)
            time.sleep(0.6)                       # frena la fuerza bruta
            return self._html(login_page(error="Correo o contraseña incorrectos.",
                                         csrf=self._csrf()))

        token = db.create_session_token(user["email"])
        if not token:
            return self._html(login_page(
                error="No se pudo crear la sesión (¿base de datos?).",
                csrf=self._csrf()), 500)
        auth.clear_login_failures(ip)
        db.log_activity(user["email"], "Panel Workers",
                        "Inicio de sesión en el panel")
        secure = config.is_cookie_secure(self)
        return self._redirect("/", extra=[
            ("Set-Cookie", auth.session_cookie(token, secure)),
            ("Set-Cookie", auth.csrf_cookie(self._csrf(), secure)),
        ])

    def _post_logout(self):
        token = auth.read_cookie(self.headers.get("Cookie"), config.COOKIE_NAME)
        user = self._user()
        if user:
            db.log_activity(user["email"], "Panel Workers",
                            "Cierre de sesión en el panel")
        db.delete_session_token(token)
        secure = config.is_cookie_secure(self)
        return self._redirect("/login", extra=[
            ("Set-Cookie", auth.clear_cookie(config.COOKIE_NAME, secure)),
            ("Set-Cookie", auth.clear_cookie(config.CSRF_COOKIE, secure)),
        ])

    def _worker_action(self, user, name, action):
        """Ejecuta enable/disable/restart y redirige con mensaje flash."""
        if not auth.has_perm(user, config.PANEL_PERMISSION):
            return self._html(forbidden_page(
                "Falta el permiso AccesoConfiguracion para cambiar workers."), 403)

        fn = {"enable": workers.enable,
              "disable": workers.disable,
              "restart": workers.restart}.get(action)
        if fn is None:
            return self._send(404, "Acción no válida", "text/plain")

        ok, output = fn(name)
        accion_txt = {"enable": "activado", "disable": "deshabilitado",
                      "restart": "reiniciado"}[action]
        if ok:
            db.log_activity(user["email"], "Panel Workers",
                            f"Worker '{name}' {accion_txt}")
            msg = f"Worker '{name}' {accion_txt}."
            if output:
                msg += " " + output.splitlines()[-1]
            url = "/?" + urllib.parse.urlencode({"ok": msg})
        else:
            url = "/?" + urllib.parse.urlencode(
                {"err": f"No se pudo {accion_txt} '{name}': {output or 'error'}"})
        return self._redirect(url)


    def _config_action(self, user, name, action, form):
        """
        Guarda la configuración de un worker (o ejecuta una prueba).
        Los valores secretos en blanco significan "no cambiar" y por eso
        nunca se registran en la bitácora ni en el mensaje flash.
        """
        if not auth.has_perm(user, config.PANEL_PERMISSION):
            return self._html(forbidden_page(
                "Falta el permiso AccesoConfiguracion para editar la "
                "configuración de los workers."), 403)
        if not spec.spec_for(name):
            return self._send(404, "Worker sin configuración declarada", "text/plain")

        if action == "probar":
            if name != "telegram_worker":
                return self._send(404, "Prueba no disponible", "text/plain")
            ok, msg = workers.test_telegram_bot()
            db.log_activity(user["email"], "Panel Workers",
                            f"Prueba de token de Telegram: {'OK' if ok else 'ERROR'}")
            url = "/configuracion?" + urllib.parse.urlencode(
                {"ok" if ok else "err": f"Token de Telegram: {msg}"})
            return self._redirect(url)

        if action:
            return self._send(404, "Acción no válida", "text/plain")

        env_updates, cfg_updates, error = _parse_config_form(name, form)
        if error:
            url = "/configuracion?" + urllib.parse.urlencode({"err": error})
            return self._redirect(url)

        detalles = []
        if env_updates:
            ok, out = envconf.write_env(name, env_updates)
            if not ok:
                url = "/configuracion?" + urllib.parse.urlencode(
                    {"err": f"No se pudo guardar la configuración de '{name}': "
                            f"{out or 'error'}"})
                return self._redirect(url)
            detalles.append(f"{len(env_updates)} variable(s) de entorno")
        if cfg_updates:
            ok, out = db.set_config_values(cfg_updates)
            if not ok:
                url = "/configuracion?" + urllib.parse.urlencode(
                    {"err": f"HUB_Config rechazó los valores: {out}"})
                return self._redirect(url)
            detalles.append(f"{len(cfg_updates)} clave(s) HUB_Config")

        if not detalles:
            msg = f"Sin cambios en '{name}' (campos vacíos)."
        else:
            msg = f"Configuración de '{name}' guardada: {' + '.join(detalles)}."
            db.log_activity(user["email"], "Panel Workers",
                            f"Config '{name}' actualizada: "
                            f"{', '.join(sorted(list(env_updates) + list(cfg_updates)))}")
        url = "/configuracion?" + urllib.parse.urlencode({"ok": msg})
        return self._redirect(url)


def _parse_config_form(name, form):
    """
    Valida el formulario de un worker.
    Devuelve (env_updates, hub_config_updates, error).
    Convierte/tacha según el tipo: números con rango, booleanos 0/1,
    secretos en blanco = sin cambios y límite de 500 caracteres de HUB_Config.
    """
    env_updates, cfg_updates, error = {}, {}, None
    for f in spec.editable_fields(name):
        raw = form.get(f["id"])
        if raw is None:                     # el campo no vino en el POST
            continue
        value = str(raw[0] or "").strip()
        tipo, origen = f["tipo"], f["origen"]
        key = f.get("clave") if origen == "hub_config" else f["id"]

        if tipo == "secret" and value == "":
            continue                        # no cambiar el secreto guardado

        if tipo in ("number", "hour", "minute"):
            if value == "":
                value = str(f.get("default", ""))
            try:
                number = int(value)
            except ValueError:
                error = f"{f['label']}: debe ser un número entero."
                break
            if f.get("min") is not None and number < f["min"]:
                error = f"{f['label']}: mínimo {f['min']}."
                break
            if f.get("max") is not None and number > f["max"]:
                error = f"{f['label']}: máximo {f['max']}."
                break
            value = str(number)

        if tipo == "bool" and value not in ("0", "1"):
            error = f"{f['label']}: valor no válido."
            break
        if len(value) > db.MAX_CONFIG_VALUE:
            error = (f"{f['label']}: demasiado largo "
                     f"({len(value)} > {db.MAX_CONFIG_VALUE}).")
            break

        if origen == "env":
            env_updates[key] = value
        else:
            cfg_updates[key] = value
    return env_updates, cfg_updates, error


def run_server(host="0.0.0.0", port=None):
    """Arranca el panel (lo invoca status_server.py bajo supervisord)."""
    port = port or config.PANEL_PORT
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[panel] Panel de control en http://{host}:{port}/  "
          f"(datos: {config.DATA_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
