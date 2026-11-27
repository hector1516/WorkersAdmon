"""
tests/test_api_ops.py — API JSON de Asistencia, Correo y Triaje
===============================================================
Pruebas de `panel/api_ops.py` con dobles: no tocan SQL Server, ni el worker de
correo, ni supervisord.

Cubre lo que un cambio de consulta o de forma rompería en silencio:

  * Asistencia devuelve 4 KPIs y una fila por persona, con y sin registros.
  * Correo nunca expone la contraseña SMTP (solo `valor:"" / tiene_valor`).
  * Triaje lista los pendientes con la forma del contrato y su resumen.
  * Una acción desconocida responde 400, no 500.

OJO con el orden de `unittest discover`: este archivo NO importa `panel` a nivel
de módulo. `panel.config` fija sus rutas al importarse, y los demás tests del
panel arman su propio sandbox ANTES de importarlo. Los imports y los dobles se
instalan en `setUpModule` y se restauran en `tearDownModule` (mismo patrón que
`tests/test_api.py`).

Ejecución:  python -m unittest tests.test_api_ops -v
"""
import datetime
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

_MODULOS = {}
_SESIONES = {}

USER = {
    "id": 7, "email": "ana@ecc-sa.com.mx", "nombre": "Ana Pruebas",
    "nickname": "ana",
    "perms": {"AccesoConfiguracion": True, "AccesoDeteccionRed": True,
              "AccesoUsuarios": True},
}

# ── datos de doble ───────────────────────────────────────────────────────────
_ASIS = [
    {"UsuarioNombre": "Ana", "TurnoNombre": "Operativo",
     "HoraEntradaEsperada": datetime.time(9, 0),
     "HoraSalidaEsperada": datetime.time(18, 30),
     "EntradaPiso": datetime.time(8, 57), "EntradaTecho": datetime.time(9, 0),
     "SalidaPiso": datetime.time(18, 33), "SalidaTecho": datetime.time(18, 36),
     "HoraEntradaReal": datetime.time(9, 0),
     "HoraSalidaReal": datetime.time(18, 33),
     "Estado": "CALCULADA", "MinutosTarde": 0, "MinutosAntes": 0},
    {"UsuarioNombre": "Luis", "TurnoNombre": "Operativo",
     "HoraEntradaEsperada": datetime.time(9, 0),
     "HoraSalidaEsperada": datetime.time(18, 30),
     "EntradaPiso": None, "EntradaTecho": None, "HoraEntradaReal": None,
     "SalidaPiso": None, "SalidaTecho": None, "HoraSalidaReal": None,
     "Estado": "SIN_DATOS"},
]

_CORREO_CFG = {
    "smtp_server": "smtp.ecc-sa.com.mx", "port": 465,
    "username": "avisos@ecc-sa.com.mx", "password": "super-secreta-123",
    "use_ssl": True, "use_tls": False, "require_auth": True,
}

_BUZONES = [{
    "Id": 3, "Alias": "Ventas", "Email": "ventas@ecc-sa.com.mx",
    "ServidorIMAP": "imap.gmail.com", "PuertoIMAP": 993,
    "ServidorSMTP": "smtp.gmail.com", "PuertoSMTP": 587,
    "TipoAuth": "PASSWORD", "Estado": "ACTIVA", "CuantosUsan": 2,
    "NoLeidos": 5, "UltimoError": "", "UltimoSync": datetime.datetime(2026, 11, 27, 9, 0),
}]

_PENDIENTES = [{
    "Id": 11, "Creado": datetime.datetime(2026, 11, 27, 8, 30),
    "RemitenteEmail": "cliente@ejemplo.com",
    "Destinatario": "exempleado@ecc-sa.com.mx", "Categoria": "huerfano",
    "Confianza": 0.87, "DestinoSugerido": "ventas@ecc-sa.com.mx",
    "BorradorTexto": "Hola, te atiende Ventas.", "Motivo": "destinatario dado de baja",
    "CuentaEmail": "ventas@ecc-sa.com.mx", "Asunto": "Cotización pendiente",
}]


def _fake_authenticate(email, password):
    if (email or "").strip().lower() != USER["email"] or password != "clave":
        return None
    return dict(USER)


def _fake_create_token(email):
    token = f"tok_{len(_SESIONES) + 1}"
    _SESIONES[token] = email
    return token


def _fake_validate(token):
    email = _SESIONES.get(token)
    return dict(USER) if email else None


def _fake_delete(token):
    return 1 if _SESIONES.pop(token, None) else 0


def _instalar_dobles():
    from panel import db, db_correo
    from mailbox_worker import triaje

    def guardar(modulo, nombres):
        _MODULOS.setdefault(modulo, {})
        for nombre in nombres:
            _MODULOS[modulo][nombre] = getattr(modulo, nombre)

    guardar(db, ["authenticate", "create_session_token",
                 "validate_session_token", "delete_session_token",
                 "log_activity", "get_asistencia_fecha",
                 "get_all_usuario_turnos", "get_ultimo_escaneo",
                 "calcular_y_guardar_asistencias_fecha", "get_email_config",
                 "save_email_config"])
    guardar(db_correo, ["listar_cuentas"])
    guardar(triaje, ["pendientes", "resumen"])

    db.authenticate = _fake_authenticate
    db.create_session_token = _fake_create_token
    db.validate_session_token = _fake_validate
    db.delete_session_token = _fake_delete
    db.log_activity = lambda *a, **k: True
    db.get_asistencia_fecha = lambda fecha, uid=None: [dict(a) for a in _ASIS]
    db.get_all_usuario_turnos = lambda: []
    db.get_ultimo_escaneo = lambda: {"ultimo": None, "equipos": 0}
    db.calcular_y_guardar_asistencias_fecha = lambda fecha=None: {
        "guardados": 2, "por_estado": {"CALCULADA": 1, "SIN_DATOS": 1},
        "sin_turno": 0, "afirmables": 1}
    db.get_email_config = lambda: dict(_CORREO_CFG)
    db.save_email_config = lambda *a, **k: (True, "")

    db_correo.listar_cuentas = lambda: [dict(c) for c in _BUZONES]

    triaje.pendientes = lambda limite=100: [dict(p) for p in _PENDIENTES]
    triaje.resumen = lambda: {"nueva": 1, "aprobada": 3, "rechazada": 2,
                              "huerfanos_mes": 4, "bloqueos": 1,
                              "auto_rechazados_mes": 0, "sospechosos_mes": 0}


def _restaurar():
    for modulo, funciones in _MODULOS.items():
        for nombre, original in funciones.items():
            setattr(modulo, nombre, original)
    _MODULOS.clear()


def setUpModule():
    global HTTPD, PORT, ROOT
    ROOT = tempfile.mkdtemp(prefix="panel_api_ops_")
    os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(ROOT, "data"))

    from panel.server import Handler

    _instalar_dobles()
    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.2)


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
    """Cliente HTTP mínimo con cookies y cabecera CSRF (igual que test_api)."""

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
        code, _, _ = self.get("/api/panel/me")
        assert code == 401, f"/me sin sesión → {code}"
        code, _, texto = self.post_json(
            "/api/panel/login",
            {"email": USER["email"], "password": "clave"},
            csrf=self.cookie("panel_csrf"))
        assert code == 200, f"login → {code}: {texto[:200]}"
        return json.loads(texto)["csrf"]


class ApiOpsTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        _SESIONES.clear()

    # ── Asistencia ───────────────────────────────────────────────────────────
    def test_01_asistencia_con_filas(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/asistencia?dia=2026-11-26")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)

        self.assertEqual(datos["dia"], "2026-11-26")
        self.assertEqual(len(datos["kpis"]), 4)
        kpis = {k["label"]: k["value"] for k in datos["kpis"]}
        self.assertEqual(kpis["Presentes"], 1)
        self.assertEqual(kpis["Entradas"], 1)
        self.assertEqual(kpis["Salidas"], 1)
        self.assertEqual(kpis["Sin registro"], 1)

        self.assertEqual(len(datos["filas"]), 2)
        ana = datos["filas"][0]
        self.assertEqual(ana["persona"], "Ana")
        self.assertEqual(ana["estado"], "Normal")
        self.assertEqual(ana["tone"], "ok")
        self.assertIn("08:57", ana["entrada"])
        self.assertIn("Operativo", ana["detalle"])
        self.assertEqual(datos["filas"][1]["estado"], "Sin datos")

    def test_02_asistencia_sin_filas(self):
        from panel import db
        original = db.get_asistencia_fecha
        db.get_asistencia_fecha = lambda fecha, uid=None: []
        try:
            c = Client()
            c.entrar()
            code, _, texto = c.get("/api/panel/asistencia?dia=2026-11-25")
            self.assertEqual(code, 200)
            datos = json.loads(texto)
            self.assertEqual(datos["filas"], [])
            self.assertTrue(all(k["value"] == 0 for k in datos["kpis"]))
        finally:
            db.get_asistencia_fecha = original

    # ── Correo ───────────────────────────────────────────────────────────────
    def test_03_correo_oculta_secretos(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/correo")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)

        self.assertNotIn("super-secreta-123", texto)
        self.assertEqual(datos["config"]["password"]["valor"], "")
        self.assertTrue(datos["config"]["password"]["tiene_valor"])
        self.assertEqual(datos["config"]["smtp_server"], "smtp.ecc-sa.com.mx")

        self.assertEqual(len(datos["cuentas"]), 1)
        self.assertEqual(datos["cuentas"][0]["estado"], "Configurado")

        self.assertEqual(len(datos["buzones"]), 1)
        buzon = datos["buzones"][0]
        self.assertEqual(buzon["nombre"], "Ventas")
        self.assertEqual(buzon["estado"], "ACTIVA")
        self.assertTrue(buzon["ultimo_sync"].startswith("2026-11-27T09:00"))

    # ── Triaje ───────────────────────────────────────────────────────────────
    def test_04_triaje_lista(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/triaje")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)

        self.assertEqual(len(datos["pendientes"]), 1)
        p = datos["pendientes"][0]
        self.assertEqual(p["id"], 11)
        self.assertEqual(p["de"], "cliente@ejemplo.com")
        self.assertEqual(p["asunto"], "Cotización pendiente")
        self.assertTrue(p["fecha"].startswith("2026-11-27T08:30"))
        self.assertEqual(p["estado"], "NUEVA")
        self.assertEqual(datos["resumen"]["nueva"], 1)
        self.assertEqual(datos["resumen"]["bloqueos"], 1)

    # ── acciones inválidas ───────────────────────────────────────────────────
    def test_05_accion_invalida_400(self):
        c = Client()
        csrf = c.entrar()

        code, _, texto = c.post_json(
            "/api/panel/triaje", {"accion": "borrar", "id": 11}, csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("Acción no válida", json.loads(texto)["error"])

        code, _, texto = c.post_json(
            "/api/panel/asistencia", {"accion": "borrar"}, csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("Acción no válida", json.loads(texto)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
