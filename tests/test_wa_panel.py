"""
tests/test_wa_panel.py — Módulo WhatsApp: pestaña, bandeja y acciones
=====================================================================
Pruebas de `panel/views/whatsapp.py` + `panel/views/wa_conversaciones.py`
ejercitadas por HTTP, con dobles de la capa de datos: no requiere SQL Server ni
confía en la sesión del navegador de una persona.

Se mantiene aparte de `test_panel.py` para poder correrlo solo
(`python -m unittest tests.test_wa_panel -v`) con su propio servidor y sus
propios dobles, sin arrastrar el harness de supervisorctl.

Los dobles se instalan en `setUpModule` y se devuelven en `tearDownModule`, no
al importar: `unittest discover` importa TODOS los módulos antes de correr el
primero, así que pisar `panel.db` al importar rompería los dobles de los demás.

Ejecución:  python -m unittest tests.test_wa_panel -v
"""
import http.cookiejar
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ── Entorno simulado ANTES de importar panel.config ─────────────────────────
ROOT = tempfile.mkdtemp(prefix="wa_panel_test_")
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
for sub in ("data", "available", "bin", "logs", "conf.d"):
    os.makedirs(os.path.join(ROOT, sub), exist_ok=True)
open(os.path.join(ROOT, "supervisord.conf"), "w", encoding="utf-8").write(
    "[supervisord]\n")

from http.server import ThreadingHTTPServer  # noqa: E402

from panel import config, db, server  # noqa: E402
from panel.server import Handler  # noqa: E402

# ── Estado de los dobles ────────────────────────────────────────────────────
SESIONES = {}
CFG = {"openwa_base_url": "http://openwa:2785",
       "openwa_session_id": "20a37407", "openwa_api_key": "sk-secreta"}

USUARIOS = {
    "admin@ecc-sa.com.mx": {"pass": "s3cret", "nombre": "Admin Panel",
                            "wa": True},
    "sinwa@ecc-sa.com.mx": {"pass": "s3cret", "nombre": "Sin WhatsApp",
                            "wa": False},
}


def _usuario(email):
    rec = USUARIOS[email]
    perms = {config.PANEL_PERMISSION: True, "AccesoWhatsapp": rec["wa"],
             "AccesoTelegram": False, "AccesoAppConfig": False,
             "AccesoConfigurarCorreo": False, "AccesoConfigAI": False,
             "AccesoUsuarios": False}
    return {"id": 1, "email": email, "nombre": rec["nombre"], "nickname": "",
            "perms": perms}


def fake_authenticate(email, password):
    if email in USUARIOS and USUARIOS[email]["pass"] == password:
        return _usuario(email)
    return None


def fake_create_token(email):
    token = f"tok_{len(SESIONES) + 1}"
    SESIONES[token] = email
    return token


def fake_delete_token(token):
    return SESIONES.pop(token, None) is not None


def fake_validate(token):
    return _usuario(SESIONES[token]) if token in SESIONES else None


def fake_activity(usuario, modulo, accion):
    return True


def fake_get_config(claves):
    return {c: CFG[c] for c in claves if c in CFG}


def fake_set_config(valores):
    for clave, valor in valores.items():
        if len(str(valor)) > db.MAX_CONFIG_VALUE:
            return False, f"'{clave}': valor demasiado largo"
        CFG[clave] = str(valor)
    return True, ""


WA = {
    "eventos": [{"IdEvento": "KILOMETROS", "Nombre": "Kilómetros",
                 "Descripcion": "Al registrar el odómetro.",
                 "PlantillaMensaje": "{Kilometros} km", "AdjuntarArchivo": 0,
                 "Telefonos": "5218123211516", "Activo": 1}],
    "historial": [{"Id": 1, "IdEvento": "KILOMETROS",
                   "ChatId": "5218123211516@c.us", "Estado": "ENVIADO",
                   "Intentos": 1, "Error": None,
                   "Creado": "2026-11-26 10:00:00", "Enviado": None}],
}

CONV = {
    "conversaciones": [
        {"ChatId": "5218123211516@c.us", "Estado": "BOT", "AsignadoA": None,
         "Asignado": None, "Ultima": "2026-11-26 10:30:00",
         "Nombre": "Doña Mari", "Telefono": "5218123211516",
         "Texto": "¿Ya salió mi pedido de ayer?"},
        {"ChatId": "5218123211517@c.us", "Estado": "HUMANO", "AsignadoA": 1,
         "Asignado": "Admin Panel", "Ultima": "2026-11-26 09:05:00",
         "Nombre": "Taller López", "Telefono": "5218123211517",
         "Texto": "Gracias, ahí lo espero"},
    ],
    "mensajes": {
        "5218123211516@c.us": [
            {"Id": 1, "Direccion": "IN", "Texto": "Hola, buen día",
             "Fecha": "2026-11-26 10:00:00", "Nombre": "Doña Mari",
             "Telefono": "5218123211516", "Estado": "NUEVO"},
            {"Id": 2, "Direccion": "OUT", "Texto": "Buen día, ¿en qué le ayudo?",
             "Fecha": "2026-11-26 10:01:00", "Nombre": None,
             "Telefono": "5218123211516", "Estado": "ENVIADO"},
        ],
    },
    "salidas": [],
}


def _reset_conv():
    CONV["conversaciones"][0].update(Estado="BOT", AsignadoA=None,
                                     Asignado=None, Nombre="Doña Mari")
    CONV["conversaciones"][1].update(Estado="HUMANO", AsignadoA=1,
                                     Asignado="Admin Panel", Nombre="Taller López")
    CONV["salidas"][:] = []


def fake_openwa_config():
    return {"api_key": CFG.get("openwa_api_key", ""),
            "base_url": CFG.get("openwa_base_url", ""),
            "session_id": CFG.get("openwa_session_id", "")}


def fake_openwa_eventos():
    return [dict(e) for e in WA["eventos"]]


def fake_openwa_evento(eid):
    return next((dict(e) for e in WA["eventos"] if e["IdEvento"] == eid), None)


def fake_openwa_historial(limite=100, estado=None):
    return [dict(h) for h in WA["historial"]][:int(limite)]


def fake_openwa_resueltos(eid):
    return ([e.get("Telefonos") for e in WA["eventos"]
             if e["IdEvento"] == eid], [])


def fake_wa_conversaciones(limite=100):
    return [dict(c) for c in CONV["conversaciones"]][:int(limite)]


def fake_wa_conversacion(chat):
    return next((dict(c) for c in CONV["conversaciones"]
                 if c["ChatId"] == chat), None)


def fake_wa_mensajes(chat, limite=200):
    return [dict(m) for m in CONV["mensajes"].get(chat, [])]


def fake_wa_estado(chat, estado, asignado_a=None):
    for c in CONV["conversaciones"]:
        if c["ChatId"] == chat:
            c.update(Estado=estado, AsignadoA=asignado_a,
                     Asignado="Admin Panel" if asignado_a else None)
            return 1
    return 0


def fake_wa_salida(chat, texto, telefono=None, nombre=None, fecha=None,
                   estado="ENVIADO"):
    CONV["salidas"].append({"chat": chat, "texto": texto, "estado": estado})
    return True


DOBLES = {
    "authenticate": fake_authenticate,
    "create_session_token": fake_create_token,
    "delete_session_token": fake_delete_token,
    "validate_session_token": fake_validate,
    "log_activity": fake_activity,
    "get_config_values": fake_get_config,
    "set_config_values": fake_set_config,
    "get_openwa_config": fake_openwa_config,
    "get_openwa_eventos": fake_openwa_eventos,
    "get_openwa_evento": fake_openwa_evento,
    "get_openwa_historial": fake_openwa_historial,
    "get_openwa_ultimos_envios": lambda: {},
    "get_openwa_destinatarios_resueltos": fake_openwa_resueltos,
    "update_openwa_evento": lambda *a, **k: (True, ""),
    "get_wa_conversaciones": fake_wa_conversaciones,
    "get_wa_conversacion": fake_wa_conversacion,
    "get_wa_mensajes": fake_wa_mensajes,
    "set_wa_estado": fake_wa_estado,
    "guardar_wa_salida": fake_wa_salida,
}

_ORIGINALES = {}
HTTPD = None
PORT = None


def setUpModule():
    global HTTPD, PORT
    # Se guardan y se devuelven en tearDownModule: `discover` importa todos los
    # módulos antes de correr el primero, así que pisar `panel.db` al importar
    # dejaría rotos los dobles de test_panel.
    for nombre, fn in DOBLES.items():
        _ORIGINALES[nombre] = getattr(db, nombre)
        setattr(db, nombre, fn)
    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.2)


def tearDownModule():
    if HTTPD:
        HTTPD.shutdown()
        HTTPD.server_close()
    for nombre, fn in _ORIGINALES.items():
        setattr(db, nombre, fn)
    shutil.rmtree(ROOT, ignore_errors=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())

    def _req(self, path, data=None):
        body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}", data=body,
            method="POST" if data is not None else "GET")
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")

    def get(self, path):
        return self._req(path)

    def post(self, path, **campos):
        return self._req(path, data=campos)

    def csrf(self):
        for c in self.jar:
            if c.name == "panel_csrf":
                return c.value
        return ""

    def login(self, email, password):
        self.get("/login")
        code, _, _ = self.post("/login", email=email, password=password,
                               csrf=self.csrf())
        assert code == 303, f"login {email} → {code}"


def _logged(email="admin@ecc-sa.com.mx"):
    c = Client()
    c.login(email, "s3cret")
    return c


class WaPanelTest(unittest.TestCase):
    def setUp(self):
        _reset_conv()

    # ── módulo ───────────────────────────────────────────────────────────────
    def test_la_pestana_esta_en_tabs_y_en_la_barra(self):
        tabs = [t for t in config.TABS if t["id"] == "whatsapp"]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["href"], "/whatsapp")
        self.assertEqual(tabs[0]["perm"], "AccesoWhatsapp")
        _, _, home = _logged().get("/")
        self.assertIn('href="/whatsapp"', home)
        self.assertIn("WhatsApp", home)

    def test_sin_permiso_es_403(self):
        code, _, _ = _logged("sinwa@ecc-sa.com.mx").get("/whatsapp")
        self.assertEqual(code, 403)

    def test_sin_sesion_va_a_login(self):
        code, headers, _ = Client().get("/whatsapp")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_notificaciones_ya_no_pinta_whatsapp(self):
        _, _, html = _logged().get("/notificaciones")
        self.assertNotIn("OpenWA", html)

    def test_las_cinco_sub_pestanas(self):
        _, _, html = _logged().get("/whatsapp")
        for vista in ("conexion", "avisos", "grupos", "historial",
                      "conversaciones"):
            self.assertIn(f"/whatsapp?wa={vista}", html)

    def test_la_key_nunca_se_pinta(self):
        _, _, html = _logged().get("/whatsapp")
        self.assertNotIn("sk-secreta", html)
        self.assertIn('name="openwa_api_key" value=""', html)

    def test_vista_eventos_vieja_abre_avisos(self):
        _, _, html = _logged().get("/whatsapp?wa=eventos")
        self.assertIn("Avisos de WhatsApp", html)

    def test_enlace_viejo_de_notificaciones_redirige(self):
        code, headers, _ = _logged().get("/notificaciones?wa=avisos")
        self.assertEqual(code, 303)
        self.assertTrue(headers.get("Location", "").startswith("/whatsapp?"))

    # ── bandeja ──────────────────────────────────────────────────────────────
    def test_lista_con_contacto_mensaje_estado_y_asignado(self):
        code, _, html = _logged().get("/whatsapp?wa=conversaciones")
        self.assertEqual(code, 200)
        for trozo in ("Doña Mari", "5218123211516",
                      "¿Ya salió mi pedido de ayer?", "Taller López",
                      "badge badge--ok", "badge badge--warn",
                      "Admin Panel"):
            self.assertIn(trozo, html)

    def test_detalle_pinta_el_historial(self):
        code, _, html = _logged().get(
            "/whatsapp?wa=conversaciones&chat=5218123211516@c.us")
        self.assertEqual(code, 200)
        for trozo in ("Hola, buen día", "Buen día, ¿en qué le ayudo?",
                      'data-direccion="IN"', 'data-direccion="OUT"',
                      "Responder", "Tomar",
                      "Devolver al bot", "Cerrar"):
            self.assertIn(trozo, html)

    def test_escapa_el_texto_del_contacto(self):
        CONV["conversaciones"][0]["Nombre"] = "<script>alert(1)</script>"
        _, _, html = _logged().get("/whatsapp?wa=conversaciones")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    # ── acciones ─────────────────────────────────────────────────────────────
    def test_tomar_pone_humano_y_el_usuario(self):
        c = _logged()
        code, headers, _ = c.post("/whatsapp/conversaciones/tomar",
                                  csrf=c.csrf(), chat="5218123211516@c.us")
        self.assertEqual(code, 303)
        self.assertIn("chat=5218123211516", headers.get("Location", ""))
        conv = db.get_wa_conversacion("5218123211516@c.us")
        self.assertEqual(conv["Estado"], "HUMANO")
        self.assertEqual(conv["AsignadoA"], 1)

    def test_devolver_al_bot(self):
        c = _logged()
        c.post("/whatsapp/conversaciones/devolver", csrf=c.csrf(),
               chat="5218123211517@c.us")
        self.assertEqual(db.get_wa_conversacion("5218123211517@c.us")["Estado"],
                         "BOT")

    def test_cerrar(self):
        c = _logged()
        c.post("/whatsapp/conversaciones/cerrar", csrf=c.csrf(),
               chat="5218123211516@c.us")
        self.assertEqual(db.get_wa_conversacion("5218123211516@c.us")["Estado"],
                         "CERRADA")

    def test_sin_csrf_no_cambia(self):
        c = _logged()
        code, _, _ = c.post("/whatsapp/conversaciones/tomar",
                            chat="5218123211516@c.us")
        self.assertEqual(code, 403)
        self.assertEqual(db.get_wa_conversacion("5218123211516@c.us")["Estado"],
                         "BOT")

    def test_enviar_llama_a_openwa_y_guarda_out(self):
        import openwa_client
        previo = openwa_client.enviar_texto
        llamadas = []

        def falso(chat_id, texto, *a, **k):
            llamadas.append((chat_id, texto))
            return True, "ok"

        openwa_client.enviar_texto = falso
        try:
            c = _logged()
            code, headers, _ = c.post("/whatsapp/conversaciones/enviar",
                                      csrf=c.csrf(),
                                      chat="5218123211516@c.us",
                                      texto="Ya salió, le aviso.")
        finally:
            openwa_client.enviar_texto = previo
        self.assertEqual(code, 303)
        self.assertEqual(llamadas, [("5218123211516@c.us", "Ya salió, le aviso.")])
        self.assertEqual(len(CONV["salidas"]), 1)
        self.assertEqual(CONV["salidas"][0]["estado"], "ENVIADO")

    def test_enviar_sin_texto_no_llama_a_openwa(self):
        c = _logged()
        code, headers, _ = c.post("/whatsapp/conversaciones/enviar",
                                  csrf=c.csrf(), chat="5218123211516@c.us",
                                  texto="  ")
        self.assertEqual(code, 303)
        self.assertIn("err=", urllib.parse.unquote_plus(
            headers.get("Location", "")))
        self.assertEqual(CONV["salidas"], [])

    def test_accion_de_otro_chat_no_se_mezcla(self):
        c = _logged()
        c.post("/whatsapp/conversaciones/cerrar", csrf=c.csrf(),
               chat="5218123211516@c.us")
        self.assertEqual(db.get_wa_conversacion("5218123211517@c.us")["Estado"],
                         "HUMANO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
