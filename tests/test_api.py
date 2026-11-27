"""
tests/test_api.py — Pruebas de la API JSON del panel (primer corte)
===================================================================
No necesita SQL Server ni supervisord: se doblan `panel.workers` (estado,
acciones y logs) y `panel.db` (login/sesión/bitácora) en memoria.

OJO con el orden de `unittest discover`: este archivo NO importa `panel` a nivel
de módulo. `panel.config` fija sus rutas al importarse, y los demás tests del
panel arman su propio sandbox ANTES de importarlo. Si este módulo se importara
primero, ese sandbox quedaría sin efecto y `test_panel` fallaría. Los imports y
los dobles se instalan en `setUpModule` (ya durante la ejecución) y se restauran
en `tearDownModule`.

Ejecución:  python tests/test_api.py
"""
import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

HTTPD = None
PORT = None
ROOT = None

# Módulos y funciones que este archivo sustituye (se restauran al terminar).
_MODULOS = {}
_ORIGINALES = {}
_HABLADOS = []          # acciones de workers pedidas por la API
_SESIONES = {}          # token -> email


def _usuario(email="ana@ecc-sa.com.mx"):
    return {
        "id": 7, "email": email, "nombre": "Ana Pruebas", "nickname": "ana",
        "perms": {"AccesoConfiguracion": True, "AccesoTelegram": False,
                  "AccesoAppConfig": True},
    }


def _fake_authenticate(email, password):
    if (email or "").strip().lower() != "ana@ecc-sa.com.mx" or password != "clave":
        return None
    return _usuario()


def _fake_create_token(email):
    token = f"tok_{len(_SESIONES) + 1}"
    _SESIONES[token] = email
    return token


def _fake_validate(token):
    email = _SESIONES.get(token)
    return _usuario(email) if email else None


def _fake_delete(token):
    return 1 if _SESIONES.pop(token, None) else 0


def _fake_activity(usuario, modulo, accion):
    return True


def _programa(nombre, estado, **extra):
    base = {
        "name": nombre, "state": estado, "pid": 1 if estado == "RUNNING" else None,
        "uptime_seconds": 60 if estado == "RUNNING" else None,
        "enabled": estado == "RUNNING", "available": True, "running":
            estado == "RUNNING", "last_run": None, "detail": "", "count": None,
        "started_at": None, "desc": f"Demo {nombre}", "descripcion":
            f"Worker de prueba {nombre}.", "cadencia": "cada 5 min",
        "app": "HUB", "protected": False, "log_ok": True,
    }
    base.update(extra)
    return base


def _fake_status():
    ahora = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "title": "Panel (test)", "now": ahora, "timezone": "UTC",
        "data_dir": "/data", "supervisor_error": None,
        "totals": {"running": 1, "stopped": 1, "errors": 0, "enabled": 1,
                   "available": 2, "total": 2},
        "programs": [
            _programa("demo_on", "RUNNING", last_run=ahora),
            _programa("demo_off", "STOPPED", enabled=False, log_ok=False,
                      last_run="2020-01-01 00:00:00", app="Field"),
        ],
    }


def _fake_accion(nombre):
    _HABLADOS.append(nombre)
    if nombre == "roto":
        return False, "el supervisor dijo que no"
    return True, "ok"


def _instalar_dobles():
    from panel import db, workers
    from panel.views import workers as workers_view

    def guardar(modulo, nombres):
        _MODULOS.setdefault(modulo, {})
        for nombre in nombres:
            _MODULOS[modulo][nombre] = getattr(modulo, nombre)

    guardar(db, ["authenticate", "create_session_token", "validate_session_token",
                 "delete_session_token", "log_activity"])
    guardar(workers, ["build_status", "shell_check", "versiones_check",
                      "log_path", "start", "stop", "restart", "enable", "disable"])

    db.authenticate = _fake_authenticate
    db.create_session_token = _fake_create_token
    db.validate_session_token = _fake_validate
    db.delete_session_token = _fake_delete
    db.log_activity = _fake_activity

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    workers.build_status = _fake_status
    workers.shell_check = lambda: {"ok": True, "shell_version": "9.9.9",
                                   "revisado": "hoy"}
    workers.versiones_check = lambda: {"ok": True, "revisado": "hoy"}
    workers.log_path = lambda nombre: (
        os.path.join(ROOT, "logs", f"{nombre}.log")
        if nombre and nombre.replace("_", "").isalnum() else None)
    for accion in ("start", "stop", "restart", "enable", "disable"):
        setattr(workers, accion, _fake_accion)

    # `workers_view` guarda referencias al MISMO módulo `workers`, así que los
    # dobles de arriba ya le aplican; no hay que tocar sus funciones puras.
    assert workers_view is not None


def _restaurar():
    for modulo, funciones in _MODULOS.items():
        for nombre, original in funciones.items():
            setattr(modulo, nombre, original)
    _MODULOS.clear()


def setUpModule():
    global HTTPD, PORT, ROOT
    from panel.server import Handler

    ROOT = tempfile.mkdtemp(prefix="panel_api_test_")
    with _abrir_creando(os.path.join(ROOT, "logs", "demo_on.log")) as fh:
        fh.write("linea 1: worker arrancado\nlinea 2: procesando\n")

    _instalar_dobles()
    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.2)


def _abrir_creando(ruta):
    """open(..., 'w') que antes crea el directorio (para el setUp)."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    return open(ruta, "w", encoding="utf-8")


def tearDownModule():
    global HTTPD
    if HTTPD:
        HTTPD.shutdown()
        HTTPD.server_close()
    _restaurar()
    shutil.rmtree(ROOT, ignore_errors=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Cliente HTTP mínimo con cookies y cabecera CSRF."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())

    def _abrir(self, req):
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.getcode(), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def get(self, path):
        code, headers, body = self._abrir(
            urllib.request.Request(f"http://127.0.0.1:{PORT}{path}"))
        return code, headers, body.decode("utf-8", "replace")

    def post_json(self, path, obj=None, csrf=None):
        headers = {"Content-Type": "application/json"}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}",
            data=json.dumps(obj or {}).encode("utf-8"),
            headers=headers, method="POST")
        code, cabeceras, body = self._abrir(req)
        return code, cabeceras, body.decode("utf-8", "replace")

    def cookie(self, nombre):
        for c in self.jar:
            if c.name == nombre:
                return c.value
        return None

    def entrar(self):
        """Pide /me (emite la cookie CSRF) y hace login por la API."""
        code, _, _ = self.get("/api/panel/me")
        assert code == 401, f"/me sin sesión → {code}"
        code, _, texto = self.post_json(
            "/api/panel/login",
            {"email": "ana@ecc-sa.com.mx", "password": "clave"},
            csrf=self.cookie("panel_csrf"))
        assert code == 200, f"login → {code}: {texto[:200]}"
        return json.loads(texto)["csrf"]


class ApiTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        _HABLADOS.clear()
        _SESIONES.clear()

    # ── sesión ───────────────────────────────────────────────────────────────
    def test_01_me_sin_sesion_401(self):
        c = Client()
        code, headers, texto = c.get("/api/panel/me")
        self.assertEqual(code, 401)
        self.assertIn("error", json.loads(texto))
        self.assertTrue(c.cookie("panel_csrf"),
                        "sin cookie CSRF el SPA no puede hacer login")

    def test_02_login_ok_y_me(self):
        c = Client()
        csrf = c.entrar()
        self.assertTrue(cookie_set(c, "ecsa_token"))
        self.assertTrue(csrf)
        code, _, texto = c.get("/api/panel/me")
        self.assertEqual(code, 200)
        datos = json.loads(texto)
        self.assertEqual(datos["user"]["email"], "ana@ecc-sa.com.mx")
        self.assertEqual(datos["csrf"], csrf)
        self.assertIn("AccesoConfiguracion", datos["user"]["permisos"])
        self.assertNotIn("perms", datos["user"])

    def test_03_login_mal_401(self):
        c = Client()
        c.get("/api/panel/me")
        code, _, texto = c.post_json(
            "/api/panel/login",
            {"email": "ana@ecc-sa.com.mx", "password": "mala"},
            csrf=c.cookie("panel_csrf"))
        self.assertEqual(code, 401)
        self.assertIn("incorrectos", json.loads(texto)["error"])
        self.assertIsNone(c.cookie("ecsa_token"))

    def test_04_logout_borra_sesion(self):
        c = Client()
        csrf = c.entrar()
        code, _, _ = c.post_json("/api/panel/logout", csrf=csrf)
        self.assertEqual(code, 204)
        self.assertEqual(_SESIONES, {})

    def test_05_csrf_ausente_403(self):
        c = Client()
        c.entrar()
        code, _, texto = c.post_json("/api/panel/workers/demo_on/restart")
        self.assertEqual(code, 403)
        self.assertIn("error", json.loads(texto))
        self.assertEqual(_HABLADOS, [])

    # ── estado ───────────────────────────────────────────────────────────────
    def test_06_estado_con_worker_corriendo_y_detenido(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/estado")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)

        self.assertEqual(len(datos["kpis"]), 4)
        self.assertEqual(datos["kpis"][0]["value"], 1)

        series = datos["series"]
        self.assertEqual(len(series["corriendo"]), len(series["error"]))
        self.assertGreaterEqual(len(series["corriendo"]), 1)
        self.assertEqual(series["corriendo"][-1], 1)

        grupos = {g["nombre"]: g["items"] for g in datos["workers"]["grupos"]}
        self.assertIn("HUB", grupos)
        por_nombre = {w["nombre"]: w for g in grupos.values() for w in g}
        self.assertEqual(por_nombre["demo_on"]["estado"], "RUNNING")
        self.assertFalse(por_nombre["demo_on"]["viejo"])
        self.assertEqual(por_nombre["demo_on"]["descripcion"],
                         "Worker de prueba demo_on.")
        self.assertEqual(por_nombre["demo_off"]["estado"], "STOPPED")
        self.assertTrue(por_nombre["demo_off"]["viejo"])

        self.assertTrue(datos["avisos"])
        self.assertTrue(all("detalle" in a for a in datos["avisos"]))

    # ── logs ─────────────────────────────────────────────────────────────────
    def test_07_logs_indice_y_texto(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/logs")
        self.assertEqual(code, 200)
        items = {i["nombre"]: i for i in json.loads(texto)["items"]}
        self.assertEqual(set(items), {"demo_on", "demo_off"})
        self.assertTrue(items["demo_on"]["log"])
        self.assertFalse(items["demo_off"]["log"])
        self.assertIn("ultimo", items["demo_on"])

        code, _, texto = c.get("/api/panel/logs/demo_on")
        self.assertEqual(code, 200)
        log = json.loads(texto)
        self.assertEqual(log["nombre"], "demo_on")
        self.assertIn("linea 1", log["texto"])

        code, _, texto = c.get("/api/panel/logs/demo_off")
        self.assertEqual(code, 404)
        self.assertIn("No hay log", json.loads(texto)["error"])

    # ── acciones ─────────────────────────────────────────────────────────────
    def test_08_accion_invalida_400(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            "/api/panel/workers/demo_on/borrar", csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("Acción no válida", json.loads(texto)["error"])

    def test_09_accion_valida(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            "/api/panel/workers/demo_on/restart", csrf=csrf)
        self.assertEqual(code, 200, texto[:200])
        self.assertEqual(_HABLADOS, ["demo_on"])
        datos = json.loads(texto)
        self.assertTrue(datos["ok"])
        self.assertIn("reiniciado", datos["mensaje"])

    def test_10_accion_que_falla_400(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            "/api/panel/workers/roto/start", csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("No se pudo arrancar", json.loads(texto)["error"])

    def test_11_sin_sesion_en_estado_401(self):
        code, _, _ = Client().get("/api/panel/estado")
        self.assertEqual(code, 401)

    def test_12_api_desconocida_404_json(self):
        code, headers, texto = Client().get("/api/panel/no_existe")
        self.assertEqual(code, 404)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("error", json.loads(texto))

    # ── SPA, respaldo SSR y build ausente ────────────────────────────────────
    def _dist_falso(self, contenido="<!doctype html><html><body>SPA</body></html>"):
        """Crea un dist mínimo y apunta config a él; devuelve (dir, originales)."""
        from panel import config
        dist = os.path.join(ROOT, "dist")
        os.makedirs(os.path.join(dist, "assets"), exist_ok=True)
        with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(contenido)
        originales = (config.UI_DIST_DIR, config.UI_INDEX_FILE)
        config.UI_DIST_DIR = dist
        config.UI_INDEX_FILE = os.path.join(dist, "index.html")
        return dist, originales

    def _restaurar_dist(self, originales):
        from panel import config
        config.UI_DIST_DIR, config.UI_INDEX_FILE = originales

    def test_13_fallback_spa_sirve_el_build(self):
        dist, originales = self._dist_falso()
        try:
            code, headers, texto = Client().get("/apps/123")
            self.assertEqual(code, 200)
            self.assertIn("text/html", headers.get("Content-Type", ""))
            self.assertIn("SPA", texto)
            self.assertEqual(headers.get("Cache-Control"), "no-store")
        finally:
            self._restaurar_dist(originales)

    def test_14_fallback_sin_build_503_claro(self):
        from panel import config
        originales = (config.UI_DIST_DIR, config.UI_INDEX_FILE)
        config.UI_DIST_DIR = os.path.join(ROOT, "no_existe", "dist")
        config.UI_INDEX_FILE = os.path.join(ROOT, "no_existe", "dist",
                                            "index.html")
        try:
            code, headers, texto = Client().get("/apps/123")
            self.assertEqual(code, 503)
            self.assertIn("text/plain", headers.get("Content-Type", ""))
            self.assertIn("no está compilado", texto)
            self.assertNotIn("Traceback", texto)
        finally:
            config.UI_DIST_DIR, config.UI_INDEX_FILE = originales

    def test_15_ssr_sigue_respondiendo_en_clasico(self):
        # Las vistas de Python quedan solo bajo /clasico/... (respaldo).
        code, headers, _ = Client().get("/clasico/estado")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")
        # La raíz ya es el SPA: /estado es una ruta del frontend.
        dist, originales = self._dist_falso()
        try:
            code, headers, _ = Client().get("/estado")
            self.assertEqual(code, 200)
            self.assertIn("text/html", headers.get("Content-Type", ""))
        finally:
            self._restaurar_dist(originales)


def cookie_set(cliente, nombre):
    return cliente.cookie(nombre) is not None


if __name__ == "__main__":
    unittest.main(verbosity=2)
