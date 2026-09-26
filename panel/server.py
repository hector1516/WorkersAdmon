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
  GET  /notificaciones                 → pestaña Notificaciones (Fase C)
  POST /notificaciones/<bloque>[/...]  → guarda / prueba (telegram|push|correo|ia)
  GET  /apps                           → pestaña Apps (catálogo por app)
  POST /apps                           → alta / editar / borrar / guardar valores

Seguridad: cookie `ecsa_token` (HttpOnly), CSRF de doble envío, límite de
intentos de login, cabeceras anti-clickjacking y permiso `AccesoConfiguracion`.
"""
import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth, config, db, envconf, probes, spec, workers
from .templates import esc, forbidden_page, login_page, page
from .views import apps as apps_view
from .views import config as config_view
from .views import notifications as notif_view
from .views import workers as workers_view

MAX_BODY = 64 * 1024          # límite del cuerpo de un POST
LOG_LINES = 300               # líneas de log mostradas



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

    def _logo(self):
        """Sirve el logo del panel (PNG) con caché de un día."""
        try:
            with open(config.LOGO_FILE, "rb") as fh:
                data = fh.read()
        except OSError:
            return self._send(404, "logo no encontrado", "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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
        if path == "/logo.png":
            return self._logo()
        if path == "/favicon.ico":
            return self._redirect("/logo.png")
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

        if path == "/notificaciones":
            user, done = self._require()
            if done:
                return
            ok, err = self._flash(query)
            return self._html(notif_view.render(
                user, flash_ok=ok, flash_err=err, csrf=self._csrf(),
                correo_prueba=query.get("para", [""])[0] or user.get("email", "")))

        if path == "/apps":
            user, done = self._require()
            if done:
                return
            if not auth.has_perm(user, "AccesoAppConfig"):
                return self._html(forbidden_page(
                    "La pestaña Apps requiere el permiso AccesoAppConfig en el "
                    "HUB. Pídeselo al administrador."), 403)
            ok, err = self._flash(query)
            return self._html(apps_view.render(
                user, flash_ok=ok, flash_err=err, csrf=self._csrf()))

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

        if path == "/apps":
            return self._apps_action(user, form)

        if path.startswith("/notificaciones/"):
            segs = [s for s in path[len("/notificaciones/"):].split("/") if s]
            return self._notif_action(user, segs, form)

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


    def _notif_action(self, user, segs, form):
        """Guarda o prueba un bloque de la pestaña Notificaciones."""
        bloque = (segs[0] if segs else "").strip()
        accion = segs[1] if len(segs) > 1 else "guardar"
        perm, perm_label = notif_view.PERMISOS.get(bloque, (None, ""))
        if perm and not auth.has_perm(user, perm):
            return self._html(forbidden_page(
                f"El bloque '{bloque}' requiere el permiso {perm_label}."), 403)

        handler = {
            ("telegram", "guardar"): self._notif_telegram_token,
            ("telegram", "probar"): self._notif_telegram_probar,
            ("telegram", "evento"): self._notif_telegram_evento,
            ("telegram", "destinatarios"): self._notif_telegram_dest,
            ("push", "guardar"): self._notif_push,
            ("push", "probar"): self._notif_push_probar,
            ("correo", "guardar"): self._notif_correo,
            ("correo", "probar"): self._notif_correo_probar,
            ("ia", "guardar"): self._notif_ia,
            ("ia", "probar"): self._notif_ia_probar,
        }.get((bloque, accion))
        if handler is None:
            return self._send(404, "Bloque/acción no válidos", "text/plain")
        return handler(user, form)

    # ── Apps (Fase D) ──────────────────────────────────────────────────────
    def _apps_action(self, user, form):
        """Dispatch de la pestaña Apps: los botones se distinguen por su name."""
        if not auth.has_perm(user, "AccesoAppConfig"):
            return self._html(forbidden_page(
                "La pestaña Apps requiere el permiso AccesoAppConfig."), 403)

        if form.get("add"):
            return self._apps_alta(user, form)
        if form.get("save_all"):
            return self._apps_guardar_todo(user, form)
        if form.get("edit"):
            return self._apps_editar(user, form)
        if form.get("del"):
            return self._apps_borrar(user, form)
        if form.get("clasificar"):
            return self._apps_clasificar(user, form)
        return self._send(404, "Acción no válida", "text/plain")

    def _apps_flash(self, message, err=False):
        url = "/apps?" + urllib.parse.urlencode({"err" if err else "ok": message})
        return self._redirect(url)

    def _apps_log(self, user, accion):
        db.log_activity(user["email"], "Panel Workers", accion)

    @staticmethod
    def _fila_app(fid):
        """Fila del catálogo con ese Id (None si ya no existe)."""
        try:
            fid = int(fid)
        except (TypeError, ValueError):
            return None
        for fila in db.get_config_catalog():
            if fila["Id"] == fid:
                return fila
        return None

    def _apps_alta(self, user, form):
        """Alta de una clave en el catálogo (+ valor inicial opcional)."""
        app = (form.get("app", [""])[0] or "").strip()
        clave = (form.get("clave", [""])[0] or "").strip()
        titulo = (form.get("titulo", [""])[0] or "").strip()
        desc = (form.get("descripcion", [""])[0] or "").strip()
        unidad = (form.get("unidad", [""])[0] or "").strip()
        tipo = (form.get("tipo", [""])[0] or "").strip()
        valor = (form.get("valor", [""])[0] or "")
        orden = (form.get("orden", ["0"])[0] or "0").strip()
        if not tipo:
            return self._apps_flash("Elige el tipo de la clave.", err=True)
        ok, err = db.add_catalog_item(app, clave, titulo, desc, tipo, unidad, orden)
        if not ok:
            return self._apps_flash(f"No se pudo agregar: {err}", err=True)
        if valor:
            okv, errv = db.set_config_values({clave: valor})
            if not okv:
                return self._apps_flash(
                    f"Catálogo creado, pero no se pudo escribir el valor: {errv}",
                    err=True)
        self._apps_log(user, f"Apps: clave '{clave}' agregada a {app}")
        return self._apps_flash(f"Clave '{clave}' agregada a {app}.")

    def _apps_guardar_todo(self, user, form):
        """Guarda de una vez todos los valores del formulario de una app."""
        app = (form.get("app", [""])[0] or "").strip()
        valores, claves = {}, []
        for fila in db.get_config_catalog():
            if fila["App"] != app:
                continue
            raw = form.get(f"val{fila['Id']}", [None])[0]
            if raw is None:
                continue                      # el campo no estaba en el form
            if fila["Tipo"] == "readonly":
                continue
            if fila["Tipo"] == "secret" and not str(raw).strip():
                continue                      # en blanco = no cambiar
            valores[fila["Clave"]] = raw
            claves.append(fila["Clave"])
        if not valores:
            return self._apps_flash("Sin cambios: no hay valores que guardar.")
        ok, err = db.set_config_values(valores)
        if not ok:
            return self._apps_flash(f"No se pudieron guardar: {err}", err=True)
        extra = ", ".join(claves[:5]) + ("…" if len(claves) > 5 else "")
        self._apps_log(user, f"Apps: {len(valores)} valor(es) de {app} "
                             f"guardados ({extra})")
        return self._apps_flash(f"{len(valores)} valor(es) de {app} guardados.")

    def _apps_editar(self, user, form):
        """Guarda título/tipo/descripción y el valor de UNA fila."""
        fid = (form.get("edit", [""])[0] or "").strip()
        fila = self._fila_app(fid)
        if not fila:
            return self._apps_flash("Esa clave ya no está en el catálogo.", err=True)
        titulo = (form.get(f"t{fid}", [fila["Titulo"]])[0] or "").strip()
        desc = (form.get(f"d{fid}", [fila["Descripcion"]])[0] or "").strip()
        tipo = (form.get(f"tipo{fid}", [fila["Tipo"]])[0] or fila["Tipo"]).strip()
        ok, err = db.update_catalog_item(fila["Id"], titulo, desc, tipo,
                                         fila["Unidad"], fila["Orden"])
        if not ok:
            return self._apps_flash(f"No se pudo guardar: {err}", err=True)
        raw = form.get(f"val{fid}", [None])[0]
        if (raw is not None and tipo != "readonly"
                and not (tipo == "secret" and not str(raw).strip())):
            okv, errv = db.set_config_values({fila["Clave"]: raw})
            if not okv:
                return self._apps_flash(
                    f"Metadatos guardados; el valor no: {errv}", err=True)
        self._apps_log(user, f"Apps: '{fila['Clave']}' ({fila['App']}) actualizada")
        return self._apps_flash(f"Clave '{fila['Clave']}' guardada.")

    def _apps_borrar(self, user, form):
        """Quita la fila del catálogo (HUB_Config conserva el valor)."""
        fid = (form.get("del", [""])[0] or "").strip()
        fila = self._fila_app(fid)
        if not fila:
            return self._apps_flash("Esa clave ya no está en el catálogo.", err=True)
        ok, err = db.delete_catalog_item(fila["Id"])
        if not ok:
            return self._apps_flash(f"No se pudo borrar: {err}", err=True)
        self._apps_log(user, f"Apps: '{fila['Clave']}' quitada del catálogo")
        return self._apps_flash(f"Clave '{fila['Clave']}' fuera del catálogo.")

    def _apps_clasificar(self, user, form):
        """Mueve una clave de 'sin clasificar' al catálogo de una app."""
        idx = (form.get("clasificar", [""])[0] or "").strip()
        clave = (form.get(f"k{idx}", [""])[0] or "").strip()
        app = (form.get(f"app{idx}", [""])[0] or "").strip()
        titulo = (form.get(f"tit{idx}", [""])[0] or "").strip() or clave
        tipo = (form.get(f"tipo{idx}", [""])[0] or "text").strip()
        if not clave:
            return self._apps_flash("No se reconoció la clave.", err=True)
        if not app:
            return self._apps_flash("Indica la app a la que pertenece la clave.",
                                    err=True)
        ok, err = db.add_catalog_item(app, clave, titulo, "", tipo, "", 0)
        if not ok:
            return self._apps_flash(f"No se pudo clasificar: {err}", err=True)
        self._apps_log(user, f"Apps: '{clave}' clasificada en {app}")
        return self._apps_flash(f"'{clave}' ahora vive en {app}.")

    # ── Telegram ───────────────────────────────────────────────────────────
    def _notif_telegram_token(self, user, form):
        token = (form.get("telegram_bot_token", [""])[0] or "").strip()
        if not token:
            return self._notif_flash("Sin cambios: el token quedó vacío (no se borra lo guardado).")
        ok, err = db.set_config_values({"telegram_bot_token": token})
        if not ok:
            return self._notif_flash(f"No se pudo guardar el token: {err}", err=True)
        self._notif_log(user, "Token de Telegram actualizado")
        return self._notif_flash("Token de Telegram guardado.")

    def _notif_telegram_probar(self, user, form):
        ok, msg = workers.test_telegram_bot()
        self._notif_log(user, f"Prueba de token de Telegram: {'OK' if ok else 'ERROR'}")
        return self._notif_flash(f"Token de Telegram: {msg}", err=not ok)

    def _notif_telegram_evento(self, user, form):
        eid = (form.get("IdEvento", [""])[0] or "").strip()
        plantilla = (form.get("PlantillaMensaje", [""])[0] or "")
        adjunto = (form.get("AdjuntarArchivo", ["0"])[0] or "0") == "1"
        activo = (form.get("Activo", ["0"])[0] or "0") == "1"
        if not eid:
            return self._notif_flash("Falta el evento.", err=True)
        ok, err = db.update_telegram_evento(eid, plantilla, adjunto, activo)
        if not ok:
            return self._notif_flash(f"No se pudo guardar el evento: {err}", err=True)
        self._notif_log(user, f"Plantilla del evento '{eid}' actualizada")
        return self._notif_flash(f"Evento '{eid}' guardado.")

    def _notif_telegram_dest(self, user, form):
        eid = (form.get("IdEvento", [""])[0] or "").strip()
        ids = [i for i in form.get("ids", []) if str(i).isdigit()]
        if not eid:
            return self._notif_flash("Falta el evento.", err=True)
        ok, err = db.set_telegram_destinatarios(eid, ids)
        if not ok:
            return self._notif_flash(f"No se pudieron guardar los destinatarios: {err}", err=True)
        self._notif_log(user, f"Destinatarios de '{eid}' actualizados ({len(ids)})")
        return self._notif_flash(f"Destinatarios de '{eid}' guardados ({len(ids)}).")

    # ── Push ───────────────────────────────────────────────────────────────
    def _notif_push(self, user, form):
        cfg = db.get_push_config()
        public = (form.get("VapidPublicKey", [""])[0] or "").strip()
        privada = (form.get("VapidPrivateKey", [""])[0] or "").strip()
        email = (form.get("VapidEmail", [""])[0] or "").strip()
        if not privada:
            privada = cfg.get("private", "")          # en blanco = no cambiar
        if not public:
            public = cfg.get("public", "")
        ok, err = db.save_push_config(public, privada, email)
        if not ok:
            return self._notif_flash(f"No se pudieron guardar las claves VAPID: {err}", err=True)
        self._notif_log(user, "Claves VAPID actualizadas")
        return self._notif_flash("Claves VAPID guardadas.")

    def _notif_push_probar(self, user, form):
        ok, msg = probes.probe_push()
        self._notif_log(user, f"Push de prueba: {'OK' if ok else 'ERROR'}")
        return self._notif_flash(msg, err=not ok)

    # ── Correo SMTP ────────────────────────────────────────────────────────
    def _notif_correo(self, user, form):
        puerto = (form.get("port", [""])[0] or "").strip()
        try:
            puerto = int(puerto)
            if not 1 <= puerto <= 65535:
                raise ValueError
        except ValueError:
            return self._notif_flash("El puerto debe estar entre 1 y 65535.", err=True)
        cfg = db.get_email_config()
        password = (form.get("password", [""])[0] or "").strip()
        if not password:
            password = cfg.get("password", "")        # en blanco = no cambiar
        ok, err = db.save_email_config(
            (form.get("smtp_server", [""])[0] or "").strip(),
            puerto,
            (form.get("username", [""])[0] or "").strip(),
            password,
            (form.get("use_ssl", ["0"])[0] or "0") == "1",
            (form.get("use_tls", ["0"])[0] or "0") == "1",
            (form.get("require_auth", ["0"])[0] or "0") == "1")
        if not ok:
            return self._notif_flash(f"No se pudo guardar la config SMTP: {err}", err=True)
        self._notif_log(user, "Configuración SMTP actualizada")
        return self._notif_flash("Configuración SMTP guardada.")

    def _notif_correo_probar(self, user, form):
        destino = (form.get("destino", [""])[0] or "").strip()
        ok, msg = probes.probe_smtp(db.get_email_config(), destino)
        self._notif_log(user, f"Prueba de correo a '{destino}': {'OK' if ok else 'ERROR'}")
        url = "/notificaciones?" + urllib.parse.urlencode(
            {("ok" if ok else "err"): msg, "para": destino})
        return self._redirect(url)

    # ── IA ─────────────────────────────────────────────────────────────────
    def _notif_ia(self, user, form):
        cfg = db.get_ai_config()
        api_key = (form.get("api_key", [""])[0] or "").strip()
        if not api_key:
            api_key = cfg.get("api_key", "")          # en blanco = no cambiar
        ok, err = db.save_ai_config(
            (form.get("provider", [""])[0] or "").strip() or "google_gemini",
            api_key,
            (form.get("model", [""])[0] or "").strip() or "gemini-2.0-flash")
        if not ok:
            return self._notif_flash(f"No se pudo guardar la config de IA: {err}", err=True)
        self._notif_log(user, "Configuración de IA actualizada")
        return self._notif_flash("Configuración de IA guardada.")

    def _notif_ia_probar(self, user, form):
        ok, msg = probes.probe_ai(db.get_ai_config())
        self._notif_log(user, f"Prueba de IA: {'OK' if ok else 'ERROR'}")
        return self._notif_flash(msg, err=not ok)

    # ── utilidades comunes ─────────────────────────────────────────────────
    def _notif_flash(self, message, err=False):
        url = "/notificaciones?" + urllib.parse.urlencode(
            {"err" if err else "ok": message})
        return self._redirect(url)

    def _notif_log(self, user, accion):
        db.log_activity(user["email"], "Panel Workers", accion)

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
