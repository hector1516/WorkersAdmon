"""
tests/test_api_wa.py — API JSON de WhatsApp (bandeja de conversaciones)
=======================================================================
No necesita SQL Server ni OpenWA: se doblan `panel.db` (conversaciones,
mensajes, estado y salida) y `openwa_client` (un módulo falso en `sys.modules`),
así que el envío nunca sale a la red.

Se vigila lo que la vista ya cuidaba:

  * la lista y el hilo devuelven las claves del contrato;
  * tomar y cerrar cambian el estado del chat;
  * enviar llama al cliente UNA vez y deja la fila OUT;
  * si el cliente rechaza el envío, no se guarda nada;
  * el secreto de OpenWA jamás aparece en el JSON.

Mismo cuidado que `test_api.py` con el orden de `unittest discover`: este
archivo NO importa `panel` a nivel de módulo (los imports viven en
`setUpModule`, ya durante la ejecución).

Ejecución:  python -m unittest tests.test_api_wa -v
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
import types
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
_ORIGINALES = {}
_SESIONES = {}
_ENVIADOS = []       # (chat, texto) que llegaron al cliente falso
_GUARDADOS = []      # (chat, texto) que intentó guardar la API
_CONV = {}           # chat -> fila de HUB_WhatsappConversaciones
_MENSAJES = {}       # chat -> [filas de HUB_WhatsappEntrante]

API_KEY = "clave-openwa-secreta-123"

CHAT = "5218123456789@c.us"
AHORA = datetime.datetime(2026, 11, 27, 10, 0, 0)


def _usuario(email="ana@ecc-sa.com.mx"):
    return {
        "id": 7, "email": email, "nombre": "Ana Pruebas", "nickname": "ana",
        "perms": {"AccesoConfiguracion": True, "AccesoWhatsapp": True,
                  "AccesoTelegram": True, "AccesoAppConfig": True},
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


# ── dobles de la bandeja ─────────────────────────────────────────────────────
def _sembrar_conversaciones():
    _CONV.clear()
    _MENSAJES.clear()
    _ENVIADOS.clear()
    _GUARDADOS.clear()
    _CONV[CHAT] = {"ChatId": CHAT, "Estado": "BOT", "AsignadoA": None,
                   "Asignado": None, "Ultima": AHORA, "Nombre": "Hector",
                   "Telefono": "5218123456789", "Texto": "hola desde el campo"}
    _MENSAJES[CHAT] = [
        {"Id": 1, "Direccion": "IN", "Texto": "hola desde el campo",
         "Fecha": AHORA, "Nombre": "Hector", "Telefono": "5218123456789",
         "Estado": "RECIBIDO"},
    ]


def _fake_conversaciones(limite=100):
    filas = sorted(_CONV.values(), key=lambda c: c.get("Ultima") or AHORA,
                   reverse=True)
    return filas[:int(limite)]


def _fake_conversacion(chat_id):
    fila = _CONV.get(str(chat_id or ""))
    return dict(fila) if fila else None


def _fake_mensajes(chat_id, limite=200):
    return list(_MENSAJES.get(str(chat_id or ""), []))[-int(limite):]


def _fake_set_estado(chat_id, estado, asignado_a=None):
    fila = _CONV.get(str(chat_id or ""))
    if not fila or estado not in ("BOT", "HUMANO", "CERRADA"):
        return 0
    fila["Estado"] = estado
    fila["AsignadoA"] = asignado_a
    fila["Asignado"] = "Ana Pruebas" if asignado_a else None
    return 1


def _fake_guardar_salida(chat_id, texto, telefono=None, nombre=None,
                         fecha=None, estado="ENVIADO"):
    _GUARDADOS.append((chat_id, texto))
    _MENSAJES.setdefault(chat_id, []).append({
        "Id": 99, "Direccion": "OUT", "Texto": texto,
        "Fecha": fecha or AHORA, "Nombre": None,
        "Telefono": telefono, "Estado": estado})
    return True


def _fake_openwa_config():
    return {"api_key": API_KEY, "base_url": "http://openwa:2785",
            "session_id": "principal"}


def _fake_enviar_texto(chat_id, texto, *args, **kwargs):
    _ENVIADOS.append((chat_id, texto))
    return True, "enviado"


def _fake_enviar_falla(chat_id, texto, *args, **kwargs):
    _ENVIADOS.append((chat_id, texto))
    return False, "OpenWA no respondió"


def _fake_grupos(base=None, clave=None, session_id=None, sesion=None):
    return True, [{"id": "1203630@g.us", "nombre": "Cuadrilla Norte",
                   "participantes": 5}]


def _montar_openwa(enviar=None):
    """Módulo `openwa_client` falso: nunca se habla con WhatsApp."""
    modulo = types.ModuleType("openwa_client")
    modulo.enviar_texto = enviar or _fake_enviar_texto
    modulo.listar_grupos = _fake_grupos
    modulo.normalizar_chat_ids = lambda texto: (
        [t.strip() + "@c.us" for t in (texto or "").split(",") if t.strip()], [])
    modulo.obtener_config = _fake_openwa_config
    if "openwa_client" not in _ORIGINALES:
        _ORIGINALES["openwa_client"] = sys.modules.get("openwa_client")
    sys.modules["openwa_client"] = modulo
    return modulo


def _instalar_dobles():
    from panel import db

    def guardar(modulo, nombres):
        _MODULOS.setdefault(modulo, {})
        for nombre in nombres:
            _MODULOS[modulo][nombre] = getattr(modulo, nombre)

    guardar(db, ["authenticate", "create_session_token",
                 "validate_session_token", "delete_session_token",
                 "log_activity", "get_openwa_config", "get_wa_conversaciones",
                 "get_wa_conversacion", "get_wa_mensajes", "set_wa_estado",
                 "guardar_wa_salida"])
    db.authenticate = _fake_authenticate
    db.create_session_token = _fake_create_token
    db.validate_session_token = _fake_validate
    db.delete_session_token = _fake_delete
    db.log_activity = lambda usuario, modulo, accion: True
    db.get_openwa_config = _fake_openwa_config
    db.get_wa_conversaciones = _fake_conversaciones
    db.get_wa_conversacion = _fake_conversacion
    db.get_wa_mensajes = _fake_mensajes
    db.set_wa_estado = _fake_set_estado
    db.guardar_wa_salida = _fake_guardar_salida


def _restaurar():
    for modulo, funciones in _MODULOS.items():
        for nombre, original in funciones.items():
            setattr(modulo, nombre, original)
    _MODULOS.clear()
    original = _ORIGINALES.pop("openwa_client", None)
    if original is None:
        sys.modules.pop("openwa_client", None)
    else:
        sys.modules["openwa_client"] = original


def setUpModule():
    global HTTPD, PORT, ROOT
    from panel.server import Handler

    ROOT = tempfile.mkdtemp(prefix="panel_api_wa_test_")
    _sembrar_conversaciones()
    _instalar_dobles()
    _montar_openwa()
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
            {"email": "ana@ecc-sa.com.mx", "password": "clave"},
            csrf=self.cookie("panel_csrf"))
        assert code == 200, f"login → {code}: {texto[:200]}"
        return json.loads(texto)["csrf"]


class ApiWaTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        _SESIONES.clear()
        _sembrar_conversaciones()
        _montar_openwa()
        from panel.views import notifications as notif_view
        notif_view._GRUPOS_CACHE.update({"cuando": 0.0, "grupos": None})

    # ── lectura ──────────────────────────────────────────────────────────────
    def test_01_lista_conversaciones(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/wa/conversaciones")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)
        self.assertEqual(len(datos["items"]), 1)
        item = datos["items"][0]
        for clave in ("chat", "nombre", "telefono", "estado", "asignado",
                      "ultima", "ultimo_texto"):
            self.assertIn(clave, item)
        self.assertEqual(item["chat"], CHAT)
        self.assertEqual(item["nombre"], "Hector")
        self.assertEqual(item["estado"], "BOT")
        self.assertIsNone(item["asignado"])
        self.assertEqual(item["ultimo_texto"], "hola desde el campo")
        self.assertTrue(item["ultima"].startswith("2026-11-27"))

    def test_02_detalle_con_mensajes(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get(f"/api/panel/wa/conversaciones/{CHAT}")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)
        self.assertEqual(datos["chat"], CHAT)
        self.assertEqual(datos["estado"], "BOT")
        self.assertEqual(datos["telefono"], "5218123456789")
        self.assertEqual(len(datos["mensajes"]), 1)
        self.assertEqual(datos["mensajes"][0]["dir"], "IN")
        self.assertEqual(datos["mensajes"][0]["texto"], "hola desde el campo")

    def test_03_chat_inexistente_404(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/wa/conversaciones/no-existe")
        self.assertEqual(code, 404)
        self.assertIn("no existe", json.loads(texto)["error"])

    def test_04_grupos_de_openwa(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/whatsapp?bloque=grupos")
        self.assertEqual(code, 200, texto[:300])
        datos = json.loads(texto)
        self.assertEqual(datos["items"][0]["id"], "1203630@g.us")
        self.assertEqual(datos["items"][0]["nombre"], "Cuadrilla Norte")
        self.assertNotIn("error", datos)

    def test_05_grupos_sin_openwa_200_con_error(self):
        import sys as _sys
        modulo = _sys.modules["openwa_client"]
        original = modulo.listar_grupos
        modulo.listar_grupos = lambda *a, **k: (False, "sin conexión")
        try:
            c = Client()
            c.entrar()
            code, _, texto = c.get("/api/panel/whatsapp?bloque=grupos")
            self.assertEqual(code, 200)
            datos = json.loads(texto)
            self.assertEqual(datos["items"], [])
            self.assertIn("sin conexión", datos["error"])
        finally:
            modulo.listar_grupos = original

    # ── secreto ──────────────────────────────────────────────────────────────
    def test_06_secreto_nunca_en_json(self):
        c = Client()
        c.entrar()
        code, _, texto = c.get("/api/panel/whatsapp?bloque=conexion")
        self.assertEqual(code, 200)
        self.assertNotIn(API_KEY, texto)
        datos = json.loads(texto)
        self.assertEqual(datos["api_key"], {"valor": "", "tiene_valor": True})
        self.assertEqual(datos["session_id"], "principal")

    # ── acciones ─────────────────────────────────────────────────────────────
    def test_07_tomar_cambia_estado(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/tomar", csrf=csrf)
        self.assertEqual(code, 200, texto[:200])
        self.assertEqual(json.loads(texto)["estado"], "HUMANO")

        code, _, texto = c.get(f"/api/panel/wa/conversaciones/{CHAT}")
        datos = json.loads(texto)
        self.assertEqual(datos["estado"], "HUMANO")
        self.assertEqual(datos["asignado"], "Ana Pruebas")

    def test_08_cerrar_cambia_estado(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/cerrar", csrf=csrf)
        self.assertEqual(code, 200, texto[:200])
        self.assertEqual(json.loads(texto)["estado"], "CERRADA")
        self.assertEqual(_CONV[CHAT]["Estado"], "CERRADA")

    def test_09_enviar_llama_al_cliente_y_guarda_out(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/enviar",
            {"texto": "  allá vamos  "}, csrf=csrf)
        self.assertEqual(code, 200, texto[:300])
        self.assertEqual(_ENVIADOS, [(CHAT, "allá vamos")])
        self.assertEqual(_GUARDADOS, [(CHAT, "allá vamos")])

        _, _, texto = c.get(f"/api/panel/wa/conversaciones/{CHAT}")
        mensajes = json.loads(texto)["mensajes"]
        self.assertEqual(mensajes[-1]["dir"], "OUT")
        self.assertEqual(mensajes[-1]["texto"], "allá vamos")

    def test_10_enviar_sin_texto_400_y_no_manda(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/enviar", {"texto": "  "},
            csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("Escribe el texto", json.loads(texto)["error"])
        self.assertEqual(_ENVIADOS, [])
        self.assertEqual(_GUARDADOS, [])

    def test_11_si_openwa_falla_no_guarda(self):
        _montar_openwa(enviar=_fake_enviar_falla)
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/enviar", {"texto": "hola"},
            csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("No se pudo enviar", json.loads(texto)["error"])
        self.assertEqual(_GUARDADOS, [], "no se guarda una salida que no salió")

    def test_12_accion_invalida_400(self):
        c = Client()
        csrf = c.entrar()
        code, _, texto = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/borrar", csrf=csrf)
        self.assertEqual(code, 400)
        self.assertIn("Acción no válida", json.loads(texto)["error"])

    def test_13_csrf_ausente_403(self):
        c = Client()
        c.entrar()
        code, _, _ = c.post_json(
            f"/api/panel/wa/conversaciones/{CHAT}/tomar")
        self.assertEqual(code, 403)
        self.assertEqual(_CONV[CHAT]["Estado"], "BOT")

    def test_14_sin_sesion_401(self):
        code, _, _ = Client().get("/api/panel/wa/conversaciones")
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
