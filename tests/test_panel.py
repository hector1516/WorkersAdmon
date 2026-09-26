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
import base64
import http.cookiejar
import importlib
import json
import os
import re
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
    # Solo Telegram: no debe poder tocar Correo ni IA
    "solo_telegram@ecc-sa.com.mx": {"password": "s3cret", "nombre": "Solo Telegram",
                                    "permiso": True,
                                    "solo": {"AccesoConfiguracion", "AccesoTelegram"}},
    # Panel + Apps: entra a /apps aunque el resto de pestañas quede grisado
    "solo_app@ecc-sa.com.mx": {"password": "s3cret", "nombre": "Solo App",
                               "permiso": True,
                               "solo": {"AccesoConfiguracion", "AccesoAppConfig"}},
    # Panel SIN Apps: la pestaña se oculta y /apps responde 403
    "sin_app@ecc-sa.com.mx": {"password": "s3cret", "nombre": "Sin App",
                              "permiso": True,
                              "solo": {"AccesoConfiguracion"}},
}
_SESSIONS = {}     # token -> email
_ACTIVITY = []
_TOKENS = {"n": 0}


def _fake_user(email, permiso, nombre, solo=None):
    todos = {config.PANEL_PERMISSION: permiso,
             "AccesoTelegram": permiso, "AccesoAppConfig": permiso,
             "AccesoConfigurarCorreo": permiso, "AccesoConfigAI": permiso,
             "AccesoUsuarios": permiso, "AccesoVM": False,
             "AccesoEdicionBD": False}
    if solo is not None:
        todos = {k: (k in solo) for k in todos}
    return {"id": 1, "email": email, "nombre": nombre, "nickname": "",
            "perms": todos}


def fake_authenticate(email, password):
    rec = USERS.get((email or "").strip().lower())
    if not rec or rec["password"] != password:
        return None
    return _fake_user((email or "").strip().lower(), rec["permiso"], rec["nombre"],
                      rec.get("solo"))


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
    return _fake_user(email, rec["permiso"], rec["nombre"], rec.get("solo"))


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

# ── Doble del catálogo por app (HUB_ConfigCatalogo, Fase D) ──────────────────
_CATALOGO = [
    {"Id": 1, "App": "HUB", "Clave": "tipo_cambio_usd",
     "Titulo": "Tipo de cambio USD/MXN", "Descripcion": "Lo escribe el worker",
     "Tipo": "readonly", "Unidad": "", "Orden": 10},
    {"Id": 2, "App": "HUB", "Clave": "telegram_bot_token",
     "Titulo": "Token del bot de Telegram", "Descripcion": "",
     "Tipo": "secret", "Unidad": "", "Orden": 30},
    {"Id": 3, "App": "HUB", "Clave": "net_scan_interval_seg",
     "Titulo": "Intervalo de escaneo de red", "Descripcion": "",
     "Tipo": "number", "Unidad": "seg", "Orden": 52},
    {"Id": 4, "App": "Field", "Clave": "field_avisos_rep_1",
     "Titulo": "Aviso de reporte Field #1", "Descripcion": "",
     "Tipo": "text", "Unidad": "", "Orden": 10},
    {"Id": 5, "App": "admon", "Clave": "asistencia_calcular_auto",
     "Titulo": "Asistencia: cálculo automático", "Descripcion": "",
     "Tipo": "bool", "Unidad": "", "Orden": 20},
]
_CATALOGO_BASE = [dict(f) for f in _CATALOGO]     # semilla para setUp
_CATALOGO_OK = {"valor": True}                    # ¿existe la tabla 0036?
_PROXIMO_ID = {"n": 6}


def fake_catalog_exists():
    return _CATALOGO_OK["valor"]


def fake_get_catalog():
    """Misma forma de fila que la real: Valor/Actualizado llegan del JOIN."""
    if not _CATALOGO_OK["valor"]:
        return []
    filas = []
    for f in _CATALOGO:
        r = dict(f)
        r["Valor"] = _CONFIG.get(f["Clave"], "")
        r["Actualizado"] = None
        filas.append(r)
    return filas


def fake_all_config_values():
    return dict(_CONFIG)


def fake_add_catalog(app, clave, titulo, descripcion="", tipo="text",
                     unidad="", orden=0):
    # reutiliza el validador REAL para que los mensajes coincidan con producción
    app = str(app or "").strip()
    clave = str(clave or "").strip()
    error = db._validar_item(app, clave, titulo, tipo, descripcion, unidad, orden)
    if error:
        return False, error
    previa = [f["App"] for f in _CATALOGO if f["Clave"] == clave]
    if previa:
        return False, f"la clave '{clave}' ya está catalogada en {previa[0]}"
    _CATALOGO.append({"Id": _PROXIMO_ID["n"], "App": app, "Clave": clave,
                      "Titulo": str(titulo).strip(),
                      "Descripcion": str(descripcion or "").strip(),
                      "Tipo": tipo, "Unidad": str(unidad or "").strip(),
                      "Orden": int(orden)})
    _PROXIMO_ID["n"] += 1
    return True, ""


def fake_update_catalog(item_id, titulo, descripcion="", tipo="text",
                        unidad="", orden=0):
    error = db._validar_item("x", "x", titulo, tipo, descripcion, unidad, orden)
    if error:
        return False, error
    for f in _CATALOGO:
        if f["Id"] == int(item_id):
            f.update({"Titulo": str(titulo).strip(),
                      "Descripcion": str(descripcion or "").strip(),
                      "Tipo": tipo, "Unidad": str(unidad or "").strip(),
                      "Orden": int(orden)})
            return True, ""
    return False, "la clave ya no existe en el catálogo"


def fake_delete_catalog(item_id):
    for f in list(_CATALOGO):
        if f["Id"] == int(item_id):
            _CATALOGO.remove(f)
            return True, ""
    return False, "la clave ya no existe en el catálogo"


# ── Doble de HUB_Passkeys + verificación (Fase: login con passkey) ───────────
_PASSKEYS = {
    "cred-admin-1": {"Id": 7, "IdUsuario": 1, "CredentialId": "cred-admin-1",
                     "PublicKey": "pk-admin", "SignCount": 10,
                     "RpId": "ecc-sa.com.mx"},
    "cred-legacy-1": {"Id": 8, "IdUsuario": 1, "CredentialId": "cred-legacy-1",
                      "PublicKey": "pk-legacy", "SignCount": 3,
                      "RpId": "field.ecc-sa.com.mx"},
}
_SIGNCOUNT = {}


def fake_passkey_by_credential(cred_id):
    row = _PASSKEYS.get(cred_id)
    return dict(row) if row else None


def fake_update_sign_count(passkey_id, sign_count):
    _SIGNCOUNT[passkey_id] = sign_count
    return True


def fake_user_by_id(user_id):
    if user_id == 1:
        return _fake_user("admin@ecc-sa.com.mx", True, "Admin Panel")
    return None


db.get_passkey_by_credential = fake_passkey_by_credential
db.update_passkey_sign_count = fake_update_sign_count
db.get_user_by_id = fake_user_by_id

# El chequeo criptográfico real (webauthn) queda fuera de los tests: aquí se
# sustituye por un resultado válido; la lógica alrededor SÍ se prueba.
from panel import webauthn as _webauthn  # noqa: E402


class _VerificacionOK:
    new_sign_count = 42


_webauthn._verify_assertion = lambda *a, **k: _VerificacionOK()

db.config_catalog_exists = fake_catalog_exists
db.get_config_catalog = fake_get_catalog
db.get_all_config_values = fake_all_config_values
db.add_catalog_item = fake_add_catalog
db.update_catalog_item = fake_update_catalog
db.delete_catalog_item = fake_delete_catalog

# ── Doble de notificaciones (HUB_Telegram* / HUB_Push* / HUB_EmailConfig /
#    HUB_AIConfig) y sondas de red ───────────────────────────────────────────
_TG = {
    "eventos": [{"IdEvento": "KILOMETROS", "Nombre": "Registro de Kilometros",
                 "PlantillaMensaje": "*{Automovil}* — {Kilometros} km",
                 "AdjuntarArchivo": 0, "Activo": 1}],
    "dest": {"KILOMETROS": [1]},
    "metricas": {"vinculados": 3, "eventos": 1, "destinatarios": 1,
                 "pendientes": 2, "fallados": 0, "enviados": 40},
}
_PUSH = {"public": "BPUB", "private": "BPRIV", "email": "a@b.c",
         "updated": "2026-01-01", "subs": [{"Endpoint": "https://x", "P256dhKey": "k",
                                            "AuthKey": "a", "UserEmail": "u@e"}]}
_EMAIL = {"smtp_server": "smtp.example.com", "port": 465, "username": "robot@x",
          "password": "old-pass", "use_ssl": True, "use_tls": False,
          "require_auth": True}
_AI = {"provider": "google_gemini", "api_key": "AIza-old", "model": "gemini-3.5-flash-lite"}
_USERS_ACTIVE = [{"Id": 1, "Nombre": "Admin", "Email": "admin@ecc-sa.com.mx"},
                 {"Id": 3, "Nombre": "Otro", "Email": "otro@ecc-sa.com.mx"}]

db.get_telegram_eventos = lambda: [dict(e) for e in _TG["eventos"]]
db.get_telegram_destinatarios = lambda eid: list(_TG["dest"].get(eid, []))
db.get_active_users = lambda: list(_USERS_ACTIVE)
db.telegram_metrics = lambda: dict(_TG["metricas"])


def fake_update_evento(eid, plantilla, adjunto, activo):
    for e in _TG["eventos"]:
        if e["IdEvento"] == eid:
            e["PlantillaMensaje"] = plantilla
            e["AdjuntarArchivo"] = int(bool(adjunto))
            e["Activo"] = int(bool(activo))
            return True, ""
    return False, "no existe"


def fake_set_dest(eid, ids):
    _TG["dest"][eid] = list(ids)
    return True, ""


db.update_telegram_evento = fake_update_evento
db.set_telegram_destinatarios = fake_set_dest
db.get_push_config = lambda: dict(_PUSH)


def fake_save_push(pub, priv, mail):
    _PUSH.update({"public": pub, "private": priv, "email": mail})
    return True, ""


db.save_push_config = fake_save_push
db.get_push_subscriptions = lambda: list(_PUSH["subs"])
db.remove_push_subscription = lambda ep: 1
db.get_email_config = lambda: dict(_EMAIL)


def fake_save_email(server, port, username, password, use_ssl, use_tls, require_auth):
    _EMAIL.update({"smtp_server": server, "port": port, "username": username,
                   "password": password, "use_ssl": use_ssl, "use_tls": use_tls,
                   "require_auth": require_auth})
    return True, ""


db.save_email_config = fake_save_email
db.get_ai_config = lambda: dict(_AI)


def fake_save_ai(prov, key, model):
    _AI.update({"provider": prov, "api_key": key, "model": model})
    return True, ""


db.save_ai_config = fake_save_ai

# Sondas de red: fuera de la red en los tests
from panel import probes as _probes, workers as _workers  # noqa: E402
_probes.probe_smtp = lambda cfg, dest: (True, f"Correo enviado a {dest}.")
_probes.probe_ai = lambda cfg: (True, "3 modelos disponibles.")
_probes.probe_push = lambda: (True, "Push enviado a 1 suscriptor(es).")
def fake_test_bot():
    # Imita al real: sin token en HUB_Config devuelve error con el nombre de la clave
    if not (_CONFIG.get("telegram_bot_token") or "").strip():
        return False, "No hay token guardado en HUB_Config (telegram_bot_token)."
    return True, "Bot @prueba responde correctamente."


_workers.test_telegram_bot = fake_test_bot

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
            body = urllib.parse.urlencode(data, doseq=True).encode()
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

    def get_bytes(self, path):
        """GET que devuelve los bytes crudos (PNG, etc.)."""
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}")
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.getcode(), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def post(self, path, **fields):
        return self._req(f"http://127.0.0.1:{PORT}{path}", data=fields)

    def post_json(self, path, obj, origin=None):
        """POST con cuerpo JSON (endpoints /passkey/*)."""
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}",
            data=json.dumps(obj).encode("utf-8"), headers=headers, method="POST")
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")

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

    def setUp(self):
        # el catálogo es estado compartido: cada test arranca de la semilla base
        _CATALOGO[:] = [dict(f) for f in _CATALOGO_BASE]
        _PROXIMO_ID["n"] = 6
        _CATALOGO_OK["valor"] = True

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
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        self.assertTrue(os.path.exists(
            os.path.join(ROOT, "conf.d", "demo_off.conf")), "no se copió la conf")
        self.assertIn("demo_off", Path(
            os.path.join(ROOT, "data", "workers_enabled.txt")).read_text())
        self.assertIn("reread", Path(FAKE_CALLS).read_text())

    def test_07_deshabilitar_worker(self):
        c = self._logged()
        code, headers, _ = c.post("/workers/demo_on/disable", csrf=c.csrf())
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
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
        loc = urllib.parse.unquote_plus(headers.get("Location", ""))
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

    def test_12_pestanas_reales(self):
        c = self._logged()
        code, _, html = c.get("/apps")
        self.assertEqual(code, 200)
        self.assertNotIn("Fase D", html)     # ya es la pestaña real
        self.assertIn('id="app-alta"', html)
        code, _, html = c.get("/notificaciones")
        self.assertEqual(code, 200)
        self.assertNotIn("Fase C", html)     # ya es la pestaña real

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
        loc = urllib.parse.unquote_plus(headers.get("Location", ""))
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
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
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
        self.assertIn("err=", urllib.parse.unquote_plus(headers.get("Location", "")))
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
        loc = urllib.parse.unquote_plus(headers.get("Location", ""))
        self.assertIn("err=", loc, "sin token debe reportar error")
        self.assertIn("telegram_bot_token", loc)

    def test_24_worker_sin_spec_es_404(self):
        c = self._logged()
        code, _, _ = c.post("/configuracion/demo_off", csrf=c.csrf(), x="1")
        self.assertEqual(code, 404)


    # ── descripción de cada worker ──────────────────────────────────────────
    def test_25_descripcion_de_cada_worker(self):
        c = self._logged()
        # pestaña Workers: párrafo plegado "ℹ️ Qué hace"
        code, _, html = c.get("/")
        self.assertEqual(code, 200)
        self.assertIn("Qué hace", html)
        self.assertIn("Descarga los wallpapers de Bing", html)
        self.assertIn("ℹ️ Qué hace", html)
        # pestaña Configuración: el mismo texto en la tarjeta
        code, _, html = c.get("/configuracion")
        self.assertEqual(code, 200)
        self.assertIn("regenera los PDFs del día anterior", html)
        # /api/status la expone para monitoreo
        code, _, body = c.get("/api/status")
        progs = {p["name"]: p for p in json.loads(body)["programs"]}
        self.assertTrue(progs["bing_worker"]["descripcion"])
        self.assertTrue(progs["bing_worker"]["desc"])
        # todo el catálogo queda documentado
        from panel import workers as W
        for nombre, meta in W.CATALOGO.items():
            self.assertTrue(meta.get("descripcion"), f"falta descripción: {nombre}")
            self.assertTrue(meta.get("desc"), f"falta desc corta: {nombre}")

    # ── pestaña Notificaciones (Fase C) ─────────────────────────────────────
    def test_26_pagina_notificaciones(self):
        c = self._logged()
        code, _, html = c.get("/notificaciones")
        self.assertEqual(code, 200)
        for trozo in ("Bot de Telegram", "Push Web", "Correo SMTP", "IA (Gemini)",
                      "HUB_TelegramEventos", "KILOMETROS", 'name="ids"',
                      "Usuarios que reciben esta alerta", "Clave privada VAPID"):
            self.assertIn(trozo, html)
        self.assertIn('name="csrf"', html)

    def test_27_guardar_token_de_telegram(self):
        c = self._logged()
        code, headers, _ = c.post("/notificaciones/telegram", csrf=c.csrf(),
                                  telegram_bot_token="SECRET-TG-123")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        self.assertEqual(_CONFIG.get("telegram_bot_token"), "SECRET-TG-123")
        _, _, html = c.get("/notificaciones")
        self.assertNotIn("SECRET-TG-123", html, "el token jamás se pinta en el HTML")
        self.assertIn("(guardado)", html)
        acciones = [a[2] for a in _ACTIVITY]
        self.assertTrue(any("Token de Telegram actualizado" in a for a in acciones))
        self.assertFalse(any("SECRET-TG-123" in a for a in acciones),
                         "la bitácora no debe registrar secretos")
        # en blanco = no cambia
        c.post("/notificaciones/telegram", csrf=c.csrf(), telegram_bot_token="")
        self.assertEqual(_CONFIG.get("telegram_bot_token"), "SECRET-TG-123")

    def test_28_prueba_de_token_de_telegram(self):
        c = self._logged()
        code, headers, _ = c.post("/notificaciones/telegram/probar", csrf=c.csrf())
        self.assertEqual(code, 303)
        loc = urllib.parse.unquote_plus(headers.get("Location", ""))
        self.assertIn("ok=", loc, loc)
        self.assertIn("responde", loc)

    def test_29_evento_y_destinatarios(self):
        c = self._logged()
        code, headers, _ = c.post("/notificaciones/telegram/evento", csrf=c.csrf(),
                                  IdEvento="KILOMETROS",
                                  PlantillaMensaje="Nuevo {Kilometros} km",
                                  AdjuntarArchivo="1", Activo="0")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        ev = _TG["eventos"][0]
        self.assertEqual(ev["PlantillaMensaje"], "Nuevo {Kilometros} km")
        self.assertEqual((ev["AdjuntarArchivo"], ev["Activo"]), (1, 0))
        # destinatarios (multiselect)
        code, headers, _ = c.post("/notificaciones/telegram/destinatarios",
                                  csrf=c.csrf(), IdEvento="KILOMETROS", ids="3")
        self.assertEqual(code, 303)
        self.assertEqual(_TG["dest"]["KILOMETROS"], ["3"])
        # sin selección → limpia
        c.post("/notificaciones/telegram/destinatarios", csrf=c.csrf(),
               IdEvento="KILOMETROS")
        self.assertEqual(_TG["dest"]["KILOMETROS"], [])

    def test_30_guardar_push_y_correo(self):
        c = self._logged()
        code, headers, _ = c.post("/notificaciones/push", csrf=c.csrf(),
                                  VapidPublicKey="BPUB-2", VapidPrivateKey="",
                                  VapidEmail="nuevo@ecc-sa.com.mx")
        self.assertEqual(code, 303)
        self.assertEqual(_PUSH["public"], "BPUB-2")
        self.assertEqual(_PUSH["private"], "BPRIV", "clave privada en blanco = no cambiar")
        # puerto fuera de rango
        code, headers, _ = c.post("/notificaciones/correo", csrf=c.csrf(),
                                  smtp_server="smtp.nuevo", port="99999",
                                  username="u", password="", use_ssl="1",
                                  use_tls="0", require_auth="1")
        self.assertEqual(code, 303)
        self.assertIn("err=", urllib.parse.unquote_plus(headers.get("Location", "")))
        self.assertEqual(_EMAIL["smtp_server"], "smtp.example.com")
        # guardado válido con contraseña en blanco
        code, headers, _ = c.post("/notificaciones/correo", csrf=c.csrf(),
                                  smtp_server="smtp.nuevo", port="587",
                                  username="robot", password="", use_ssl="0",
                                  use_tls="1", require_auth="1")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        self.assertEqual(_EMAIL["smtp_server"], "smtp.nuevo")
        self.assertEqual(_EMAIL["port"], 587)
        self.assertEqual(_EMAIL["password"], "old-pass", "conserva la contraseña")
        acciones = [a[2] for a in _ACTIVITY]
        self.assertFalse(any("old-pass" in a for a in acciones))

    def test_31_guardar_ia_y_pruebas_de_sonda(self):
        c = self._logged()
        code, headers, _ = c.post("/notificaciones/ia", csrf=c.csrf(),
                                  provider="google_gemini", api_key="",
                                  model="gemini-3.5-flash-lite")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        self.assertEqual(_AI["api_key"], "AIza-old", "key en blanco = no cambiar")
        self.assertEqual(_AI["model"], "gemini-3.5-flash-lite")
        # las tres sondas (falsas) responden OK
        for bloque in ("ia", "correo"):
            data = {"csrf": c.csrf()}
            if bloque == "correo":
                data["destino"] = "prueba@ecc-sa.com.mx"
            code, headers, _ = c.post(f"/notificaciones/{bloque}/probar", **data)
            self.assertEqual(code, 303, bloque)
            self.assertIn("ok=", urllib.parse.unquote_plus(headers.get("Location", "")))
        code, headers, _ = c.post("/notificaciones/push/probar", csrf=c.csrf())
        self.assertEqual(code, 303)
        self.assertIn("suscriptor", urllib.parse.unquote_plus(headers.get("Location", "")))
        code, headers, _ = c.post("/notificaciones/correo/probar", csrf=c.csrf(),
                                  destino="prueba@ecc-sa.com.mx")
        self.assertIn("prueba@ecc-sa.com.mx",
                      urllib.parse.unquote_plus(headers.get("Location", "")),
                      "el destino debe volver en la URL (se recarga la página)")
        self.assertIn("para=", headers.get("Location", ""))

    def test_32_bloques_según_permiso(self):
        c = Client()
        c.login("solo_telegram@ecc-sa.com.mx", "s3cret")
        code, _, html = c.get("/notificaciones")
        self.assertEqual(code, 200)
        self.assertIn("Bot de Telegram", html)
        self.assertIn("🔒 Requiere el permiso", html)
        self.assertNotIn('name="smtp_server"', html, "sin permiso no hay formulario")
        # y el POST correspondiente se rechaza
        code, _, _ = c.post("/notificaciones/correo", csrf=c.csrf(),
                            smtp_server="x", port="25")
        self.assertEqual(code, 403)
        code, _, _ = c.post("/notificaciones/telegram", csrf=c.csrf(),
                            telegram_bot_token="OtroToken")
        self.assertEqual(code, 303, "Telegram sí le está permitido")

    def test_33_notificaciones_requiere_sesion_y_csrf(self):
        c = Client()
        code, headers, _ = c.get("/notificaciones")
        self.assertEqual((code, headers.get("Location")), (303, "/login"))
        c = self._logged()
        code, _, _ = c.post("/notificaciones/telegram", csrf="",
                            telegram_bot_token="X")
        self.assertEqual(code, 403)
        code, _, _ = c.post("/notificaciones/no_existe", csrf=c.csrf())
        self.assertEqual(code, 404)


    # ── pestaña Apps: catálogo de configuración por app (Fase D) ────────────
    def test_34_pagina_apps(self):
        _CONFIG.setdefault("telegram_bot_token", "SECRET-TG-123")
        c = self._logged()
        code, _, html = c.get("/apps")
        self.assertEqual(code, 200)
        for trozo in ('id="app-HUB"', 'id="app-Field"', 'id="app-admon"',
                      'id="app-libres"', 'id="app-alta"', "Sin clasificar",
                      "Nueva clave", "tipo_cambio_usd", "net_scan_interval_seg",
                      "field_avisos_rep_1", "asistencia_calcular_auto",
                      'name="save_all"', 'name="add"', 'name="clasificar"',
                      "Claves catalogadas", "Secretos protegidos", "(guardado)",
                      "HUB_ConfigCatalogo", "banxico_token", 'href="/apps"',
                      "config central por app"):
            self.assertIn(trozo, html, f"falta {trozo!r}")
        self.assertNotIn(_CONFIG["telegram_bot_token"], html,
                         "el valor de un secreto jamás se pinta")

    def test_35_apps_requiere_permiso(self):
        # con AccesoAppConfig: entra y la pestaña está activa
        c = Client()
        c.login("solo_app@ecc-sa.com.mx", "s3cret")
        code, _, html = c.get("/apps")
        self.assertEqual(code, 200)
        code, _, home = c.get("/")
        self.assertIn('href="/apps"', home)
        # sin AccesoAppConfig: 403 y la pestaña queda gris en la barra
        c2 = Client()
        c2.login("sin_app@ecc-sa.com.mx", "s3cret")
        code, _, html = c2.get("/apps")
        self.assertEqual(code, 403)
        self.assertIn("AccesoAppConfig", html)
        code, _, home = c2.get("/")
        self.assertEqual(code, 200)
        self.assertIn('title="Requiere el permiso AccesoAppConfig"', home)
        self.assertNotIn('href="/apps"', home, "la pestaña no debe enlazarse")
        code, _, _ = c2.post("/apps", csrf=c2.csrf(), grupo="alta", add="1",
                             app="HUB", clave="x", titulo="X", tipo="text",
                             unidad="", orden="1", valor="", descripcion="")
        self.assertEqual(code, 403)

    def test_36_alta_de_clave(self):
        c = self._logged()
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="alta", add="1",
                                  app="admon", clave="nueva_clave",
                                  titulo="Nueva clave", tipo="text", unidad="",
                                  orden="100", valor="hola", descripcion="prueba")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers["Location"]))
        self.assertEqual(_CONFIG.get("nueva_clave"), "hola", "valor inicial")
        self.assertTrue(any(f["Clave"] == "nueva_clave" and f["App"] == "admon"
                            for f in _CATALOGO), "no quedó en el catálogo")
        self.assertTrue(any("nueva_clave" in accion for _, _, accion in _ACTIVITY),
                        "falta la bitácora")

        def err(**campos):
            base = dict(csrf=c.csrf(), grupo="alta", add="1", app="HUB",
                        clave="clave_nueva", titulo="T", tipo="text", unidad="",
                        orden="1", valor="", descripcion="")
            base.update(campos)
            code, headers, _ = c.post("/apps", **base)
            self.assertEqual(code, 303)
            return urllib.parse.unquote_plus(headers["Location"])

        self.assertIn("Elige el tipo", err(tipo=""))
        self.assertIn("ya está catalogada", err(clave="tipo_cambio_usd"))
        self.assertIn("la app solo puede llevar", err(app="Mi App"))
        self.assertIn("la clave solo puede llevar", err(clave="clave mala"))

    def test_37_guardar_valores_de_una_app(self):
        _CONFIG["telegram_bot_token"] = "SECRET-TG-123"
        _CONFIG["tipo_cambio_usd"] = "17.1409"
        c = self._logged()
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", app="HUB",
                                  save_all="1", val1="17.9", val2="", val3="300")
        self.assertEqual(code, 303)
        loc = urllib.parse.unquote_plus(headers["Location"])
        self.assertIn("ok=", loc)
        self.assertIn("1 valor", loc, "solo cambió el número")
        self.assertEqual(_CONFIG["net_scan_interval_seg"], "300")
        self.assertEqual(_CONFIG["telegram_bot_token"], "SECRET-TG-123",
                         "el secreto en blanco no se modifica")
        self.assertEqual(_CONFIG["tipo_cambio_usd"], "17.1409",
                         "el tipo readonly no se modifica")
        self.assertTrue(any("Apps: 1 valor(es) de HUB" in accion
                            for _, _, accion in _ACTIVITY), "falta la bitácora")
        # sin campos de valor no hay nada que guardar
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", app="Field",
                                  save_all="1")
        self.assertIn("Sin cambios", urllib.parse.unquote_plus(headers["Location"]))

    def test_38_editar_y_borrar_fila(self):
        _CONFIG["field_avisos_rep_1"] = "RS-0"
        _CONFIG["telegram_bot_token"] = "TOKEN-SECRETO-8"
        c = self._logged()
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", edit="4",
                                  t4="Aviso nuevo", d4="descripción larga",
                                  tipo4="text", val4="RS-9")
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers["Location"]))
        fila = [f for f in _CATALOGO if f["Id"] == 4][0]
        self.assertEqual(fila["Titulo"], "Aviso nuevo")
        self.assertEqual(fila["Descripcion"], "descripción larga")
        self.assertEqual(_CONFIG["field_avisos_rep_1"], "RS-9")

        # el secreto en blanco conserva su valor guardado
        code, _, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", edit="2",
                            t2="Token del bot", d2="", tipo2="secret", val2="")
        self.assertEqual(code, 303)
        self.assertEqual(_CONFIG["telegram_bot_token"], "TOKEN-SECRETO-8")

        # borrar solo saca la fila del catálogo: HUB_Config conserva el valor
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", **{"del": "4"})
        self.assertEqual(code, 303)
        self.assertNotIn("field_avisos_rep_1", [f["Clave"] for f in _CATALOGO])
        self.assertEqual(_CONFIG["field_avisos_rep_1"], "RS-9")
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="cat", **{"del": "4"})
        self.assertEqual(code, 303)
        self.assertIn("err=", urllib.parse.unquote_plus(headers["Location"]),
                      "la segunda vez debe avisar que ya no existe")

    def test_39_clasificar_clave_libre(self):
        _CONFIG.setdefault("banxico_token", "BANX-TOK")
        c = self._logged()
        libres = sorted(k for k in _CONFIG
                        if k not in {f["Clave"] for f in _CATALOGO})
        i = libres.index("banxico_token")
        code, headers, _ = c.post(
            "/apps", csrf=c.csrf(), grupo="libre", clasificar=str(i),
            **{f"k{i}": "banxico_token", f"app{i}": "HUB", f"tit{i}": "",
               f"tipo{i}": "secret"})
        self.assertEqual(code, 303)
        self.assertIn("ok=", urllib.parse.unquote_plus(headers["Location"]))
        fila = [f for f in _CATALOGO if f["Clave"] == "banxico_token"]
        self.assertEqual(len(fila), 1)
        self.assertEqual(fila[0]["App"], "HUB")
        self.assertEqual(fila[0]["Titulo"], "banxico_token", "título de respaldo")
        self.assertEqual(fila[0]["Tipo"], "secret")
        # sin app destino no se puede clasificar
        libres = sorted(k for k in _CONFIG
                        if k not in {f["Clave"] for f in _CATALOGO})
        code, headers, _ = c.post("/apps", csrf=c.csrf(), grupo="libre",
                                  clasificar="0", **{"k0": libres[0], "app0": "",
                                                     "tit0": "X", "tipo0": "text"})
        self.assertIn("err=", urllib.parse.unquote_plus(headers["Location"]))

    def test_40_apps_sin_migracion(self):
        _CATALOGO_OK["valor"] = False
        try:
            c = self._logged()
            code, _, html = c.get("/apps")
            self.assertEqual(code, 200)
            self.assertIn("Falta la migración 0036", html)
            self.assertIn("HUB_ConfigCatalogo", html)
            self.assertNotIn('name="save_all"', html, "sin tabla no hay formularios")
            self.assertNotIn('name="add"', html)
            self.assertIn("tipo_cambio_usd", html, "queda en modo consulta")
        finally:
            _CATALOGO_OK["valor"] = True

    def test_41_apps_requiere_sesion_y_csrf(self):
        c = Client()
        code, headers, _ = c.get("/apps")
        self.assertEqual((code, headers.get("Location")), (303, "/login"))
        c = self._logged()
        code, _, _ = c.post("/apps", csrf="", grupo="cat", save_all="1")
        self.assertEqual(code, 403)
        code, _, _ = c.post("/apps", csrf=c.csrf())
        self.assertEqual(code, 404, "POST sin acción conocida → 404")

    # ── Login con passkey (WebAuthn) ─────────────────────────────────────────
    ORIGIN_OK = "https://worker.ecc-sa.com.mx"

    def _begin(self, c):
        """Helper: emite el challenge y devuelve (state, challenge)."""
        if not c.cookie("panel_csrf"):
            c.get("/login")
        code, _, body = c.post_json("/passkey/begin",
                                    {"csrf": c.csrf()}, origin=self.ORIGIN_OK)
        self.assertEqual(code, 200, body)
        data = json.loads(body)
        self.assertTrue(data["ok"], data)
        return data["state"], data["options"]["challenge"]

    def test_42_login_con_passkey_ui(self):
        c = Client()
        code, _, html = c.get("/login")
        self.assertEqual(code, 200)
        for trozo in ('id="pk" hidden', 'id="pk_btn"', "Entrar con passkey",
                      "/passkey/begin", "/passkey/finish", "o con contraseña",
                      "worker.ecc-sa.com.mx"):
            self.assertIn(trozo, html, f"falta {trozo!r}")
        self.assertTrue(c.cookie("panel_csrf"), "falta la cookie panel_csrf")

    def test_43_passkey_begin(self):
        c = Client()
        # sin CSRF → 403
        code, _, _ = c.post_json("/passkey/begin", {}, origin=self.ORIGIN_OK)
        self.assertEqual(code, 403)
        # con CSRF → options + state firmado
        state, challenge = self._begin(c)
        self.assertIn(".", state, "el state debe venir firmado")
        self.assertTrue(challenge and len(challenge) > 20)
        self.assertRegex(challenge, r"^[A-Za-z0-9_-]+$")
        # acción desconocida
        code, _, _ = c.post_json("/passkey/no_existe", {"csrf": c.csrf()},
                                 origin=self.ORIGIN_OK)
        self.assertEqual(code, 404)

    def test_44_passkey_finish_crea_sesion(self):
        c = Client()
        state, _ = self._begin(c)
        cred = {"id": "cred-admin-1", "rawId": "cred-admin-1",
                "type": "public-key",
                "response": {"clientDataJSON": "e30", "authenticatorData": "AA",
                             "signature": "AA",
                             "userHandle": base64.urlsafe_b64encode(
                                 b"1").rstrip(b"=").decode()}}
        code, headers, body = c.post_json(
            "/passkey/finish",
            {"state": state, "credential": cred, "csrf": c.csrf()},
            origin=self.ORIGIN_OK)
        self.assertEqual(code, 200, body)
        data = json.loads(body)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["redirect"], "/")
        self.assertTrue(c.cookie("ecsa_token"), "falta la cookie de sesión")
        # la sesión funciona en las páginas del panel
        code, _, home = c.get("/")
        self.assertEqual(code, 200)
        self.assertIn("Admin Panel", home)
        # se actualizó el sign count y quedó la bitácora
        self.assertEqual(_SIGNCOUNT.get(7), 42)
        self.assertTrue(any("passkey" in accion for _, _, accion in _ACTIVITY),
                        "falta la bitácora")

    def test_45_passkey_errores(self):
        c = Client()
        state, _ = self._begin(c)

        def finish(**kw):
            payload = {"state": state, "credential": {"id": "cred-admin-1"},
                       "csrf": c.csrf()}
            payload.update(kw)
            code, _, body = c.post_json("/passkey/finish", payload,
                                        origin=kw.pop("_origin", self.ORIGIN_OK)
                                        if "_origin" in kw else self.ORIGIN_OK)
            return code, body

        # credencial desconocida
        code, body = finish(credential={"id": "nai"})
        self.assertEqual(code, 401)
        self.assertIn("no reconocida", body)
        # passkey legacy de otro subdominio → mensaje claro, no niebla
        code, body = finish(credential={"id": "cred-legacy-1"})
        self.assertEqual(code, 401)
        self.assertIn("field.ecc-sa.com.mx", body)
        self.assertIn("solo funciona", body)
        # state basura
        code, body = finish(state="aaa.bbb")
        self.assertEqual(code, 401)
        self.assertIn("Desaf", body)
        # sin CSRF
        code, _, body = c.post_json("/passkey/finish",
                                    {"state": state,
                                     "credential": {"id": "cred-admin-1"}},
                                    origin=self.ORIGIN_OK)
        self.assertEqual(code, 403)
        # origin no permitido (http://otro-dominio)
        code, body = finish(_origin="http://intruso.ejemplo.com")
        self.assertEqual(code, 401)
        self.assertIn("Origen no permitido", body)

    def test_46_helpers_webauthn(self):
        # estados firmados: correcto / alterado / propósito equivocado
        state, challenge = _webauthn._issue_state("wk_login")
        self.assertEqual(_webauthn._read_state(state, "wk_login"), challenge)
        with self.assertRaises(ValueError):
            _webauthn._read_state("x" + state[1:], "wk_login")
        with self.assertRaises(ValueError):
            _webauthn._read_state(state, "otro_proposito")
        with self.assertRaises(ValueError):
            _webauthn._read_state("", "wk_login")
        # allowlist de origins
        self.assertTrue(_webauthn.is_allowed_origin("https://worker.ecc-sa.com.mx"))
        self.assertTrue(_webauthn.is_allowed_origin("https://ecc-sa.com.mx"))
        self.assertTrue(_webauthn.is_allowed_origin("http://127.0.0.1:8200"))
        self.assertFalse(_webauthn.is_allowed_origin("http://worker.ecc-sa.com.mx"))
        self.assertFalse(_webauthn.is_allowed_origin("https://intruso.com"))
        self.assertFalse(_webauthn.is_allowed_origin(""))
        # base64url con y sin padding
        self.assertEqual(_webauthn._from_b64url("AQID"), b"\x01\x02\x03")
        self.assertEqual(_webauthn._from_b64url("AQI"), b"\x01\x02")

    # ── PWA (manifest, service worker, iconos, offline) ──────────────────────
    def test_47_pwa_manifest(self):
        c = Client()
        code, headers, body = c.get("/manifest.webmanifest")
        self.assertEqual(code, 200)
        self.assertIn("manifest+json", headers.get("Content-Type", ""))
        data = json.loads(body)
        self.assertEqual(data["name"], "Workers Admon · ECCSA")
        self.assertEqual(data["short_name"], "Workers")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data["background_color"], "#0F172A")
        self.assertEqual(data["theme_color"], "#FF6B00")
        sizes = {i["sizes"] for i in data["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertTrue(any(i.get("purpose") == "maskable" for i in data["icons"]))
        rutas = {t["url"] for t in data["shortcuts"]}
        self.assertEqual(rutas, {"/", "/configuracion", "/notificaciones", "/apps"})
        # el manifest nunca se sirve con caché eterna
        self.assertIn("max-age", headers.get("Cache-Control", ""))

    def test_48_pwa_service_worker(self):
        c = Client()
        code, headers, body = c.get("/sw.js")
        self.assertEqual(code, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Service-Worker-Allowed"), "/")
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        # REGLA DURA de Field: solo GET, jamás respondWith(fetch(request))
        self.assertIn("request.method !== 'GET'", body)
        self.assertNotIn("respondWith(fetch(request))", body)
        # jamás cachear datos vivos ni login/passkey
        for trozo in ("'/api/status'", "'/healthz'", "'/login'", "'/passkey/'",
                      "'/offline'", "'/manifest.webmanifest'"):
            self.assertIn(trozo, body, f"falta {trozo}")

    def test_49_pwa_iconos(self):
        c = Client()
        for nombre, size in (("icon-192x192.png", 192), ("icon-512x512.png", 512),
                             ("icon-maskable-512.png", 512),
                             ("apple-touch-icon.png", 180)):
            code, headers, data = c.get_bytes(f"/icons/{nombre}")
            self.assertEqual(code, 200, nombre)
            self.assertEqual(headers.get("Content-Type"), "image/png", nombre)
            self.assertTrue(data.startswith(b"\x89PNG"), f"{nombre} no es PNG")
            self.assertIn("max-age", headers.get("Cache-Control", ""))
        # fuera del whitelist → 404 (nada de archivos arbitrarios)
        code, _, _ = c.get("/icons/secretos_local.py")
        self.assertEqual(code, 404)
        code, _, _ = c.get("/icons/icon-999x999.png")
        self.assertEqual(code, 404)
        code, _, _ = c.get("/icons/../secretos_local.py")
        self.assertEqual(code, 404)

    def test_50_pwa_head_y_offline(self):
        head = ('<link rel="manifest" href="/manifest.webmanifest">',
                '<meta name="theme-color" content="#0F172A">',
                'rel="apple-touch-icon"',
                "navigator.serviceWorker.register('/sw.js')",
                'viewport-fit=cover')
        # login (sin sesión)
        c = Client()
        _, _, html = c.get("/login")
        for trozo in head:
            self.assertIn(trozo, html, f"login sin {trozo}")
        # página autenticada
        c.login("admin@ecc-sa.com.mx", "s3cret")
        _, _, html = c.get("/")
        for trozo in head:
            self.assertIn(trozo, html, f"panel sin {trozo}")
        # offline: accesible sin sesión y sin datos del usuario
        code, _, html = c.get("/offline")
        self.assertEqual(code, 200)
        self.assertIn("Sin conexión", html)
        self.assertIn("serviceWorker", html)
        self.assertNotIn("Admin Panel", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
