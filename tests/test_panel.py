"""
tests/test_panel.py — Smoke test del panel de control (Fase A)
==============================================================
No necesita SQL Server ni supervisord reales:
  * `panel.db` se reemplaza por un doble en memoria (login/sesión/bitácora).
  * `supervisorctl` se reemplaza por un script falso que imprime un estado
    plausible y registra los comandos recibidos.
  * Los scripts enable/disable_worker del repo se copian al sandbox con las
    rutas reescritas, de modo que se ejercita el camino real de activación.

Ejecución:  python tests/test_panel.py
"""
import http.cookiejar
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path  # noqa: E402
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ── Entorno simulado: hay que fijarlo ANTES de importar panel.config ─────────
ROOT = tempfile.mkdtemp(prefix="panel_test_")
os.environ.update({
    "WORKERS_DATA_DIR": os.path.join(ROOT, "data"),
    "WORKERS_AVAILABLE_DIR": os.path.join(ROOT, "available"),
    "WORKERS_BIN_DIR": os.path.join(ROOT, "bin"),
    "SUPERVISOR_CONF": os.path.join(ROOT, "supervisord.conf"),
    "SUPERVISOR_LOG_DIR": os.path.join(ROOT, "logs"),
    "SUPERVISORCTL": os.path.join(ROOT, "supervisorctl"),
    "SUPERVISOR_PROGRAM_DIR": os.path.join(ROOT, "conf.d"),
    "STATUS_PORT": "0",
    "STATUS_TITLE": "Workers Admon (test)",
})

FAKE_STATUS = os.path.join(ROOT, "fake_status.txt")
FAKE_CALLS = os.path.join(ROOT, "fake_calls.log")


def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


# supervisorctl falso: acepta `-c <conf> <comando>` como el verdadero
_write(os.path.join(ROOT, "supervisorctl"), """#!/bin/sh
[ "$1" = "-c" ] && shift 2
CMD="$1"; shift
echo "$CMD $*" >> "${FAKE_SUPERVISOR_LOG:-/dev/null}"
case "$CMD" in
  status) cat "${FAKE_STATUS_FILE}" 2>/dev/null ;;
  restart|start|stop) echo "$*: restarted" ;;
  reread|update) echo "No process updates" ;;
  *) echo "comando desconocido"; exit 1 ;;
esac
""", 0o755)

_write(FAKE_STATUS, "status_web  RUNNING   pid 101, uptime 0:02:10\n"
                    "demo_on     RUNNING   pid 202, uptime 0:07:44\n")
_write(os.path.join(ROOT, "supervisord.conf"), "[supervisord]\n")

# Dos workers de prueba: uno habilitado y otro solo disponible
for name in ("demo_on", "demo_off"):
    _write(os.path.join(ROOT, "available", f"{name}.conf"),
           f"[program:{name}]\ncommand=/bin/true\nautostart=true\n")
_write(os.path.join(ROOT, "conf.d", "demo_on.conf"),
       open(os.path.join(ROOT, "available", "demo_on.conf"), encoding="utf-8").read())
_write(os.path.join(ROOT, "data", "workers_enabled.txt"), "demo_on\n")

# Scripts del repo con las rutas del sandbox
for script in ("enable_worker", "disable_worker"):
    src = Path(os.path.join(REPO, "docker", "bin", script)).read_text()
    src = (src
           .replace("/app/docker/conf.d.available", os.path.join(ROOT, "available"))
           .replace("/data/workers_enabled.txt", os.path.join(ROOT, "data", "workers_enabled.txt"))
           .replace("/etc/supervisor/conf.d", os.path.join(ROOT, "conf.d"))
           .replace("supervisorctl ", f'"{os.environ["SUPERVISORCTL"]}" '))
    _write(os.path.join(ROOT, "bin", script), src, 0o755)

# Dos workers que SÍ tienen configuración declarada en panel/spec.py
_write(os.path.join(ROOT, "available", "bing_worker.conf"),
       "[program:bing_worker]\ncommand=/bin/true\nautostart=true\n"
       "environment=PYTHONUNBUFFERED=\"1\"\n")
_write(os.path.join(ROOT, "conf.d", "bing_worker.conf"),   # habilitado
       open(os.path.join(ROOT, "available", "bing_worker.conf"), encoding="utf-8").read())
_write(os.path.join(ROOT, "available", "tipo_cambio_worker.conf"),
       "[program:tipo_cambio_worker]\ncommand=/bin/true\nautostart=false\n")

# Log de un worker (para la página de logs)
_write(os.path.join(ROOT, "logs", "demo_on.log"),
       "linea 1: worker arrancado\nlinea 2: procesando\nlinea 3: listo\n")

os.environ["FAKE_STATUS_FILE"] = FAKE_STATUS
os.environ["FAKE_SUPERVISOR_LOG"] = FAKE_CALLS

from panel import auth, config, db, server, workers  # noqa: E402
from panel.server import Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# ── Doble en memoria para la capa de datos ───────────────────────────────────
USERS = {
    "admin@ecc-sa.com.mx": {"password": "s3cret", "nombre": "Admin Panel",
                            "permiso": True},
    "sinpermiso@ecc-sa.com.mx": {"password": "s3cret", "nombre": "Sin Permiso",
                                 "permiso": False},
}
_SESSIONS = {}     # token -> email
_ACTIVITY = []
_TOKENS = {"n": 0}


def _fake_user(email, permiso, nombre):
    return {"id": 1, "email": email, "nombre": nombre, "nickname": "",
            "perms": {config.PANEL_PERMISSION: permiso,
                      "AccesoTelegram": permiso, "AccesoAppConfig": permiso,
                      "AccesoConfigurarCorreo": permiso, "AccesoConfigAI": permiso,
                      "AccesoUsuarios": permiso, "AccesoVM": False,
                      "AccesoEdicionBD": False}}


def fake_authenticate(email, password):
    rec = USERS.get((email or "").strip().lower())
    if not rec or rec["password"] != password:
        return None
    return _fake_user((email or "").strip().lower(), rec["permiso"], rec["nombre"])


def fake_create_token(email):
    _TOKENS["n"] += 1
    token = f"tok_{_TOKENS['n']}"
    _SESSIONS[token] = email
    return token


def fake_validate(token):
    email = _SESSIONS.get(token)
    if not email:
        return None
    rec = USERS.get(email)
    if not rec:
        return None
    return _fake_user(email, rec["permiso"], rec["nombre"])


def fake_delete_token(token):
    return 1 if _SESSIONS.pop(token, None) else 0


def fake_activity(usuario, modulo, accion):
    _ACTIVITY.append((usuario, modulo, accion))
    return True


_CONFIG = {"tipo_cambio_usd": "17.1409"}       # doble de HUB_Config


def fake_get_config_values(claves):
    return {c: _CONFIG[c] for c in claves if c in _CONFIG}


def fake_set_config_values(valores):
    for clave, valor in valores.items():
        if len(str(valor)) > db.MAX_CONFIG_VALUE:
            return False, f"'{clave}': valor demasiado largo"
        _CONFIG[clave] = str(valor)
    return True, ""


db.authenticate = fake_authenticate
db.create_session_token = fake_create_token
db.validate_session_token = fake_validate
db.delete_session_token = fake_delete_token
db.log_activity = fake_activity
db.get_config_values = fake_get_config_values
db.set_config_values = fake_set_config_values

# ── Servidor en hilo aparte ──────────────────────────────────────────────────
HTTPD = None
PORT = None


def setUpModule():
    global HTTPD, PORT
    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.2)


def tearDownModule():
    if HTTPD:
        HTTPD.shutdown()
        HTTPD.server_close()
    shutil.rmtree(ROOT, ignore_errors=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No sigue las 303: el test quiere ver el código y la cabecera Location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Cliente HTTP mínimo con cookies (login/CSRF) y soporte de formularios."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())

    def _req(self, url, data=None, method=None):
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            url, data=body,
            method=method or ("POST" if data is not None else "GET"))
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")

    def get(self, path):
        return self._req(f"http://127.0.0.1:{PORT}{path}")

    def post(self, path, **fields):
        return self._req(f"http://127.0.0.1:{PORT}{path}", data=fields)

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    def csrf(self):
        return self.cookie("panel_csrf") or ""

    def login(self, email, password, expect_ok=True):
        code, _, html = self.get("/login")
        assert code == 200, f"GET /login → {code}"
        code, _, html = self.post("/login", email=email, password=password,
                                  csrf=self.csrf())
        if expect_ok:
            assert code == 303, f"login {email} → {code}: {html[:300]}"
        return code, html


class PanelTest(unittest.TestCase):
    maxDiff = None

    # ── endpoints públicos ───────────────────────────────────────────────────
    def test_01_healthz_y_status(self):
        code, _, body = Client().get("/healthz")
        self.assertEqual((code, body), (200, "ok"))
        code, _, body = Client().get("/api/status")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertIn("totals", data)
        names = [p["name"] for p in data["programs"]]
        self.assertIn("demo_on", names)
        self.assertIn("demo_off", names)
        self.assertEqual(data["totals"]["available"], 4)

    def test_02_sin_sesion_redirige_a_login(self):
        code, headers, _ = Client().get("/")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_03_login_y_tabla(self):
        c = Client()
        code, _, html = c.get("/login")
        self.assertEqual(code, 200)
        self.assertIn("csrf", html)
        self.assertTrue(c.cookie("panel_csrf"), "falta la cookie panel_csrf")
        c.login("admin@ecc-sa.com.mx", "s3cret")
        self.assertTrue(c.cookie("ecsa_token"), "falta la cookie ecsa_token")
        code, _, html = c.get("/")
        self.assertEqual(code, 200)
        self.assertIn("demo_on", html)
        self.assertIn("Admin Panel", html)
        self.assertIn('name="csrf"', html, "los formularios deben llevar CSRF")

    def test_04_login_malas_credenciales_y_bloqueo(self):
        c = Client()
        for _ in range(config.LOGIN_MAX_FAILS):
            code, html = c.login("admin@ecc-sa.com.mx", "mala", expect_ok=False)
            self.assertEqual(code, 200)
            self.assertIn("incorrectos", html)
        code, _ = c.login("admin@ecc-sa.com.mx", "mala", expect_ok=False)
        self.assertEqual(code, 429, "debe bloquear tras varios intentos")
        auth._attempts.clear()   # el resto de tests comparte la misma IP

    def test_05_permiso_faltante(self):
        c = Client()
        c.login("sinpermiso@ecc-sa.com.mx", "s3cret")
        code, _, html = c.get("/")
        self.assertEqual(code, 200)
        self.assertIn("AccesoConfiguracion", html)

    # ── acciones sobre workers ───────────────────────────────────────────────
    def _logged(self):
        c = Client()
        c.login("admin@ecc-sa.com.mx", "s3cret")
        return c

    def test_06_activar_worker(self):
        open(FAKE_CALLS, "w").close()
        c = self._logged()
        code, headers, _ = c.post("/workers/demo_off/enable", csrf=c.csrf())
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote(headers.get("Location", "")))
        self.assertTrue(os.path.exists(
            os.path.join(ROOT, "conf.d", "demo_off.conf")), "no se copió la conf")
        self.assertIn("demo_off", Path(
            os.path.join(ROOT, "data", "workers_enabled.txt")).read_text())
        self.assertIn("reread", Path(FAKE_CALLS).read_text())

    def test_07_deshabilitar_worker(self):
        c = self._logged()
        code, headers, _ = c.post("/workers/demo_on/disable", csrf=c.csrf())
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote(headers.get("Location", "")))
        self.assertNotIn("demo_on", Path(
            os.path.join(ROOT, "data", "workers_enabled.txt")).read_text())

    def test_08_reiniciar_worker(self):
        open(FAKE_CALLS, "w").close()
        c = self._logged()
        code, headers, _ = c.post("/workers/demo_on/restart", csrf=c.csrf())
        self.assertEqual(code, 303)
        self.assertIn("restart demo_on", Path(FAKE_CALLS).read_text())

    def test_09_post_sin_csrf_rechazado(self):
        c = self._logged()
        code, _, _ = c.post("/workers/demo_on/restart", csrf="")
        self.assertEqual(code, 403)

    def test_10_panel_protegido(self):
        c = self._logged()
        code, headers, _ = c.post("/workers/status_web/disable", csrf=c.csrf())
        self.assertEqual(code, 303)
        loc = urllib.parse.unquote(headers.get("Location", ""))
        self.assertIn("err=", loc, "status_web debe rechazarse con mensaje de error")
        code, _, body = c.get("/api/status")
        names = {p["name"]: p for p in json.loads(body)["programs"]}
        self.assertTrue(names["status_web"]["enabled"])

    def test_11_pagina_de_logs(self):
        c = self._logged()
        code, _, html = c.get("/workers/demo_on/logs")
        self.assertEqual(code, 200)
        self.assertIn("linea 3: listo", html)
        code, _, html = c.get("/workers/no_existe/logs")
        self.assertEqual(code, 200)
        self.assertIn("no hay log", html)

    def test_12_pestanas_futuras(self):
        c = self._logged()
        for path, marca in (("/notificaciones", "Fase C"), ("/apps", "Fase D")):
            code, _, html = c.get(path)
            self.assertEqual(code, 200)
            self.assertIn(marca, html)

    def test_13_logout(self):
        c = self._logged()
        code, _, _ = c.post("/logout")
        self.assertEqual(code, 303)
        _SESSIONS.clear()          # el logout borra la sesión en la capa fake
        code, headers, _ = c.get("/")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_14_bitacora(self):
        mods = [a[1] for a in _ACTIVITY]
        self.assertIn("Panel Workers", mods)

    # ── pestaña Configuración ───────────────────────────────────────────────
    def test_15_pagina_configuracion(self):
        c = self._logged()
        code, _, html = c.get("/configuracion")
        self.assertEqual(code, 200)
        self.assertIn("bing_worker", html)
        self.assertIn("Intervalo de sincronización", html)
        self.assertIn("HUB_CONFIG", html)
        self.assertIn("ENTORNO", html)
        self.assertIn("CÓDIGO", html)
        # el valor de HUB_Config (tipo_cambio_usd) sí se muestra: es readonly
        self.assertIn("17.1409", html)
        self.assertIn('name="csrf"', html)
        # la pestaña ya está habilitada en la barra
        self.assertIn("/configuracion", html)

    def test_16_guardar_variable_entorno(self):
        c = self._logged()
        code, headers, _ = c.post("/configuracion/bing_worker", csrf=c.csrf(),
                                  CRON_BING_INTERVAL="7200", CRON_BING_MAX="9")
        self.assertEqual(code, 303)
        loc = urllib.parse.unquote(headers.get("Location", ""))
        self.assertIn("ok=", loc, loc)
        conf = Path(os.path.join(ROOT, "available", "bing_worker.conf")).read_text()
        self.assertIn('CRON_BING_INTERVAL="7200"', conf)
        self.assertIn('CRON_BING_MAX="9"', conf)
        self.assertIn('PYTHONUNBUFFERED="1"', conf, "conserva la base del conf")
        # el conf ACTIVO (lo usa supervisord) también quedó parcheado
        active = Path(os.path.join(ROOT, "conf.d", "bing_worker.conf")).read_text()
        self.assertIn('CRON_BING_INTERVAL="7200"', active)
        # persistido en el volumen para sobrevivir a un rebuild
        overlay = json.loads(Path(
            os.path.join(ROOT, "data", "worker_env.json")).read_text())
        self.assertEqual(overlay["bing_worker"]["CRON_BING_INTERVAL"], "7200")
        # supervisord recargó la sección
        self.assertIn("reread", Path(FAKE_CALLS).read_text())

    def test_17_guardar_hub_config(self):
        c = self._logged()
        code, headers, _ = c.post("/configuracion/tipo_cambio_worker",
                                  csrf=c.csrf(), CRON_TC_HORA="7",
                                  CRON_TC_MIN="15", banxico_token="TOK-PRUEBA-1")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote(headers.get("Location", "")))
        self.assertEqual(_CONFIG.get("banxico_token"), "TOK-PRUEBA-1")
        conf = Path(os.path.join(ROOT, "available",
                                 "tipo_cambio_worker.conf")).read_text()
        self.assertIn('CRON_TC_HORA="7"', conf)
        self.assertIn('CRON_TC_MIN="15"', conf)

    def test_18_secreto_en_blanco_no_cambia(self):
        c = self._logged()
        code, headers, _ = c.post("/configuracion/tipo_cambio_worker",
                                  csrf=c.csrf(), CRON_TC_HORA="6",
                                  banxico_token="")          # en blanco = sin cambios
        self.assertEqual(code, 303)
        self.assertEqual(_CONFIG.get("banxico_token"), "TOK-PRUEBA-1",
                         "un secreto en blanco no debe borrar lo guardado")

    def test_19_validacion_de_numeros(self):
        c = self._logged()
        # no numérico
        code, headers, _ = c.post("/configuracion/bing_worker", csrf=c.csrf(),
                                  CRON_BING_INTERVAL="abc")
        self.assertEqual(code, 303)
        self.assertIn("err=", urllib.parse.unquote(headers.get("Location", "")))
        conf = Path(os.path.join(ROOT, "available", "bing_worker.conf")).read_text()
        self.assertNotIn('CRON_BING_INTERVAL="abc"', conf)
        # fuera de rango (min 60)
        code, headers, _ = c.post("/configuracion/bing_worker", csrf=c.csrf(),
                                  CRON_BING_INTERVAL="5")
        self.assertEqual(code, 303)
        self.assertIn("mínimo 60",
                          urllib.parse.unquote_plus(headers.get("Location", "")))

    def test_20_config_requiere_sesion_y_csrf(self):
        c = Client()                       # sin sesión
        code, headers, _ = c.get("/configuracion")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")
        c = self._logged()
        code, _, _ = c.post("/configuracion/bing_worker", csrf="",
                            CRON_BING_INTERVAL="9000")
        self.assertEqual(code, 403)

    def test_21_apply_all_sobrevive_rebuild(self):
        # Simula un rebuild: los confs vuelven al original sin overrides
        _write(os.path.join(ROOT, "available", "bing_worker.conf"),
               "[program:bing_worker]\ncommand=/bin/true\n")
        from panel import envconf
        applied, errors = envconf.apply_all()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(applied, 1)
        conf = Path(os.path.join(ROOT, "available", "bing_worker.conf")).read_text()
        self.assertIn('CRON_BING_INTERVAL="7200"', conf,
                      "apply_all debe reaplicar el overlay del volumen")

    def test_22_bitacora_de_configuracion(self):
        c = self._logged()
        c.post("/configuracion/bing_worker", csrf=c.csrf(), CRON_BING_MAX="12")
        acciones = [a[2] for a in _ACTIVITY]
        self.assertTrue(any("Config 'bing_worker' actualizada" in a
                            for a in acciones), acciones)
        # jamás se registra el valor de un secreto en la bitácora
        self.assertFalse(any("TOK-PRUEBA-1" in a for a in acciones))

    def test_23_prueba_token_telegram_sin_token(self):
        c = self._logged()
        code, headers, _ = c.post("/configuracion/telegram_worker/probar",
                                  csrf=c.csrf())
        self.assertEqual(code, 303)
        loc = urllib.parse.unquote(headers.get("Location", ""))
        self.assertIn("err=", loc, "sin token debe reportar error")
        self.assertIn("telegram_bot_token", loc)

    def test_24_worker_sin_spec_es_404(self):
        c = self._logged()
        code, _, _ = c.post("/configuracion/demo_off", csrf=c.csrf(), x="1")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
