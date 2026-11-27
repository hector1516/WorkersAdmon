"""
tests/test_eccsita.py — captura del grupo ECCSita y su API
==========================================================
Pruebas con dobles: no tocan SQL Server, ni OpenWA, ni la red.

Se vigila lo que duele si falla:

  · solo se guarda el grupo configurado (los demás siguen ignorados);
  · el eco del bot (`fromMe`) no entra, y el dedupe deja una sola fila;
  · un fallo al guardar el grupo no tumba el webhook (siempre 200);
  · la lista y el detalle tienen la forma del contrato y el 404 es 404;
  · generar arma la crónica determinista y deja Estado=RESUMIDA;
  · revisar deja Estado=REVISADA;
  · el export responde con su `Content-Disposition`, en md y en json;
  · un chatId que no termina en `@g.us` se rechaza;
  · el aviso de consentimiento sale UNA vez, y no sale sin grupo, con
    WhatsApp apagado ni con el interruptor apagado.

Ejecución:  python -m unittest tests.test_eccsita -v
"""
import datetime
import json
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from panel import api as panel_api  # noqa: E402
from panel import api_eccsita, db, eccsita, wa_webhook  # noqa: E402
import canales  # noqa: E402

TOKEN = "token-de-prueba-123"
GRUPO = "120363407857907731@g.us"
OTRO = "120363000000000000@g.us"
USER = {"id": 7, "email": "ana@ecc-sa.com.mx", "nombre": "Ana Pruebas",
        "perms": {"AccesoConfiguracion": True}}

AHORA = datetime.datetime(2026, 11, 24, 9, 30)   # martes de la semana 48


# ─── dobles de base para la captura ──────────────────────────────────────────
class BaseFalsa:
    def __init__(self):
        self.filas = []
        self.ids = set()
        self.rollbacks = 0


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        if q.startswith("SELECT 1 FROM HUB_GRUPOMENSAJES"):
            self.una = (1,) if (params[0], params[1]) in self.base.ids else None
        elif q.startswith("INSERT INTO HUB_GRUPOMENSAJES"):
            self.base.filas.append(params)
            self.base.ids.add((params[0], params[1]))
            self.una = None
        else:
            raise AssertionError(f"SQL inesperado: {q}")

    def fetchone(self):
        return self.una

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorFalso(self.base)

    def commit(self):
        pass

    def rollback(self):
        self.base.rollbacks += 1

    def close(self):
        pass


class HandlerFalso:
    """Doble del Handler: solo lo que usan el webhook y la API."""

    def __init__(self, path="/", command="GET", cuerpo=None, csrf=False):
        self.path = path
        self.command = command
        self.cuerpo = cuerpo or {}
        self.headers = {}
        if csrf:
            self.headers = {"Cookie": "panel_csrf=abc",
                            "X-CSRF-Token": "abc"}
        self.code = None
        self.payload = None
        self.sent = None

    def _read_json(self):
        return self.cuerpo

    def _json(self, obj, code=200, extra=()):
        self.code = code
        self.payload = obj

    def _send(self, code, body, ctype, extra=()):
        self.code = code
        self.sent = {"body": body, "ctype": ctype, "extra": list(extra)}


def mensaje(**extra):
    cuerpo = {"id": "g-1", "chatId": GRUPO, "author": "5218123211516@c.us",
              "notifyName": "Ana", "body": "hola equipo",
              "timestamp": 1790000000}
    cuerpo.update(extra)
    return cuerpo


class BaseConDobles(unittest.TestCase):
    """Restaura atributos que cada prueba sustituye (sin mock del proyecto)."""

    def setUp(self):
        self._previos = []

    def tearDown(self):
        for objeto, nombre, valor in reversed(self._previos):
            setattr(objeto, nombre, valor)

    def doblar(self, objeto, nombre, valor):
        self._previos.append((objeto, nombre, getattr(objeto, nombre)))
        setattr(objeto, nombre, valor)


# ─── captura en el webhook ───────────────────────────────────────────────────
class CapturaGrupo(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.base = BaseFalsa()
        self.cfg = {wa_webhook.CLAVE_TOKEN: TOKEN,
                    wa_webhook.CLAVE_GRUPO: GRUPO,
                    wa_webhook.CLAVE_CAPTURA: "1"}
        self.doblar(db, "get_config_values", lambda claves: dict(self.cfg))
        self.doblar(db, "get_connection", lambda: ConexionFalsa(self.base))
        # El roast del grupo (eccsita_perfiles) tiene sus propias pruebas; aquí
        # se dobla para que la captura no dependa de sus consultas.
        self.perfiles = types.ModuleType("eccsita_perfiles")
        self.perfiles.extraer = lambda conn, datos: 0
        self._previo_perfiles = sys.modules.get("eccsita_perfiles")
        sys.modules["eccsita_perfiles"] = self.perfiles

    def tearDown(self):
        super().tearDown()
        if self._previo_perfiles is None:
            sys.modules.pop("eccsita_perfiles", None)
        else:
            sys.modules["eccsita_perfiles"] = self._previo_perfiles

    def _atender(self, cuerpo):
        h = HandlerFalso(cuerpo=cuerpo)
        wa_webhook.atender(h, TOKEN)
        return h

    def test_guarda_el_grupo_configurado(self):
        h = self._atender(mensaje())
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "guardado")
        self.assertEqual(len(self.base.filas), 1)

        (chat, idm, autor, nombre, tipo, texto,
         fecha, semana, anio, crudo) = self.base.filas[0]
        self.assertEqual(chat, GRUPO)
        self.assertEqual(idm, "g-1")
        self.assertEqual(autor, "5218123211516@c.us")
        self.assertEqual(nombre, "Ana")
        self.assertEqual(tipo, "text")
        self.assertEqual(texto, "hola equipo")
        self.assertEqual(anio, 2026)
        self.assertEqual(semana, fecha.isocalendar()[1])
        self.assertIn("hola equipo", crudo)

    def test_otro_grupo_se_ignora(self):
        h = self._atender(mensaje(chatId=OTRO))
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_grupo_sin_configurar_se_ignora(self):
        self.cfg[wa_webhook.CLAVE_GRUPO] = ""
        h = self._atender(mensaje())
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_dedupe_por_id_deja_una_fila(self):
        self._atender(mensaje())
        h = self._atender(mensaje())
        self.assertEqual(h.payload["estado"], "duplicado")
        self.assertEqual(len(self.base.filas), 1)

    def test_fromme_no_se_guarda(self):
        h = self._atender(mensaje(fromMe=True))
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_mensaje_sin_id_se_ignora(self):
        h = self._atender(mensaje(id=None))
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_captura_apagada_no_guarda(self):
        self.cfg[wa_webhook.CLAVE_CAPTURA] = "0"
        h = self._atender(mensaje())
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_fallo_al_guardar_responde_200(self):
        def rompe(*args, **kwargs):
            raise RuntimeError("sin base")

        self.doblar(eccsita, "guardar_mensaje", rompe)
        h = self._atender(mensaje())
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "error")
        self.assertEqual(self.base.filas, [])

    def test_extrae_perfiles_del_mensaje_guardado(self):
        llamadas = []

        def extraer(conn, datos):
            llamadas.append(datos["id_mensaje"])
            return 1

        self.perfiles.extraer = extraer
        h = self._atender(mensaje(id="g-perfil"))
        self.assertEqual(h.payload["estado"], "guardado")
        self.assertEqual(llamadas, ["g-perfil"])

    def test_fallo_del_roast_no_rompe_la_captura(self):
        def rompe(conn, datos):
            raise RuntimeError("sin perfiles")

        self.perfiles.extraer = rompe
        h = self._atender(mensaje())
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "guardado")
        self.assertEqual(len(self.base.filas), 1)

    def test_imagen_se_guarda_con_su_tipo(self):
        self._atender(mensaje(id="g-img", type="image", mimetype="image/jpeg",
                              body=None, caption="la foto"))
        self.assertEqual(self.base.filas[0][4], "image")
        self.assertEqual(self.base.filas[0][5], "la foto")


# ─── almacenamiento y crónica ────────────────────────────────────────────────
MENSAJES = [
    {"AutorId": "a", "AutorNombre": "Ana", "Tipo": "text",
     "Texto": "buenos días a todos, hoy hay junta a las diez",
     "Fecha": datetime.datetime(2026, 11, 24, 9, 0)},
    {"AutorId": "a", "AutorNombre": "Ana", "Tipo": "text",
     "Texto": "ok",
     "Fecha": datetime.datetime(2026, 11, 24, 9, 5)},
    {"AutorId": "b", "AutorNombre": "Luis", "Tipo": "text",
     "Texto": "yo llevo el café y los panes para la junta de hoy",
     "Fecha": datetime.datetime(2026, 11, 25, 10, 0)},
]


class Almacen(BaseConDobles):
    def test_lista_semanas_arma_el_rango_iso(self):
        self.doblar(db, "_rows", lambda sql, params=(): [{
            "Anio": 2026, "Semana": 48, "Mensajes": 10, "Participantes": 3,
            "Inicio": None, "Fin": None, "Estado": None}])
        items = eccsita.listar_semanas(GRUPO)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mensajes"], 10)
        self.assertEqual(items[0]["participantes"], 3)
        self.assertEqual(items[0]["inicio"],
                         datetime.date.fromisocalendar(2026, 48, 1).isoformat())
        self.assertEqual(items[0]["fin"],
                         datetime.date.fromisocalendar(2026, 48, 7).isoformat())
        self.assertEqual(items[0]["estado"], "CAPTURADA")

    def test_detalle_sin_mensajes_ni_cronica_es_none(self):
        self.doblar(eccsita, "_fila_semana", lambda *a: None)
        self.doblar(eccsita, "_mensajes_semana", lambda *a: [])
        self.assertIsNone(eccsita.detalle_semana(GRUPO, 2026, 48))

    def test_resumen_basico_es_determinista(self):
        resumen, temas, personajes, momentos = eccsita.resumen_basico(
            MENSAJES, 2026, 48)
        self.assertIn("Semana 48 de 2026", resumen)
        self.assertIn("Hablaron:", resumen)
        self.assertIn("Conteo por persona", resumen)
        self.assertIn("Ana", resumen)
        self.assertEqual(temas, [])
        self.assertEqual(personajes[0], {"nombre": "Ana", "mensajes": 2})
        self.assertEqual(momentos[0]["autor"], "Luis")   # la cita más larga

    def test_generar_guarda_resumida_y_basico(self):
        guardado = {}

        def guardar(chat, anio, semana, mensajes, participantes,
                    resumen, temas, personajes, momentos):
            guardado.update({
                "chat": chat, "anio": anio, "semana": semana,
                "participantes": participantes, "resumen": resumen,
                "temas": temas, "personajes": personajes,
                "momentos": momentos})

        self.doblar(eccsita, "_mensajes_semana", lambda *a: list(MENSAJES))
        self.doblar(eccsita, "_guardar_semana", guardar)

        datos = eccsita.generar_basico(GRUPO, 2026, 48)
        self.assertEqual(datos["estado"], "RESUMIDA")
        self.assertEqual(datos["generadoPor"], "BASICO")
        self.assertEqual(datos["total"], 3)
        self.assertEqual(guardado["semana"], 48)
        self.assertEqual(guardado["participantes"], 2)
        self.assertEqual(guardado["temas"], [])
        self.assertIn("Luis", json.dumps(guardado["momentos"],
                                         ensure_ascii=False))

    def test_revisar_cambia_el_estado(self):
        estado = {"valor": "RESUMIDA"}
        self.doblar(eccsita, "_fila_semana", lambda *a: {
            "Estado": estado["valor"], "Resumen": "crónica", "Temas": "[]",
            "Personajes": "[]", "Momentos": "[]", "GeneradoPor": "BASICO",
            "FechaGeneracion": AHORA})
        self.doblar(eccsita, "_mensajes_semana", lambda *a: [])

        def ejecutar(sql, params=()):
            if "REVISADA" in str(sql).upper():
                estado["valor"] = "REVISADA"
                return 1
            return 0

        self.doblar(db, "_execute", ejecutar)
        datos = eccsita.marcar_revisada(GRUPO, 2026, 48)
        self.assertEqual(datos["estado"], "REVISADA")


# ─── API ─────────────────────────────────────────────────────────────────────
SEMANA = {
    "anio": 2026, "semana": 48, "inicio": "2026-11-23", "fin": "2026-11-29",
    "total": 3, "participantes": 2, "estado": "RESUMIDA",
    "generadoPor": "BASICO", "fechaGeneracion": AHORA.isoformat(),
    "resumen": "Semana 48 de 2026\n3 mensajes de 2 personas.",
    "temas": [], "personajes": [{"nombre": "Ana", "mensajes": 2}],
    "momentos": [{"autor": "Luis", "texto": "yo llevo el café",
                  "fecha": AHORA.isoformat()}],
    "mensajes": [{"autor": "Ana", "texto": "hola", "tipo": "text",
                  "fecha": AHORA.isoformat()}],
    "personas": [{"nombre": "Ana", "mensajes": 2}],
}


class Api(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.cfg = {"grupo": GRUPO, "captura": True}
        self.doblar(panel_api, "_sesion", lambda h: (dict(USER), None))
        self.doblar(eccsita, "config", lambda: dict(self.cfg))
        self.doblar(api_eccsita, "_log", lambda *a: None)

    def _get(self, path):
        h = HandlerFalso(path=path)
        self.assertTrue(panel_api.atender(h))
        return h

    def _post(self, path, cuerpo):
        h = HandlerFalso(path=path, command="POST", cuerpo=cuerpo, csrf=True)
        self.assertTrue(panel_api.atender(h))
        return h

    def test_lista_semanas(self):
        self.doblar(eccsita, "listar_semanas", lambda chat, limite: [{
            "anio": 2026, "semana": 48, "inicio": "2026-11-23",
            "fin": "2026-11-29", "mensajes": 3, "participantes": 2,
            "estado": "CAPTURADA"}])
        h = self._get("/api/panel/eccsita/semanas")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["grupo"], GRUPO)
        self.assertTrue(h.payload["configurado"])
        self.assertEqual(h.payload["items"][0]["semana"], 48)

    def test_lista_sin_grupo_configurado(self):
        self.cfg["grupo"] = ""
        h = self._get("/api/panel/eccsita/semanas")
        self.assertEqual(h.payload, {"items": [], "grupo": "",
                                     "configurado": False})

    def test_detalle_404(self):
        self.doblar(eccsita, "detalle_semana", lambda *a: None)
        h = self._get("/api/panel/eccsita/semanas/2026/48")
        self.assertEqual(h.code, 404)

    def test_detalle_devuelve_mensajes_y_personas(self):
        self.doblar(eccsita, "detalle_semana", lambda *a: dict(SEMANA))
        h = self._get("/api/panel/eccsita/semanas/2026/48")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["mensajes"][0]["autor"], "Ana")
        self.assertEqual(h.payload["personas"][0]["nombre"], "Ana")

    def test_generar_por_http_cambia_estado(self):
        guardado = {}

        def guardar(chat, anio, semana, mensajes, participantes,
                    resumen, temas, personajes, momentos):
            guardado.update({"estado": "RESUMIDA", "personajes": personajes})

        self.doblar(eccsita, "_mensajes_semana", lambda *a: list(MENSAJES))
        self.doblar(eccsita, "_guardar_semana", guardar)
        h = self._post("/api/panel/eccsita/semanas/2026/48/generar", {})
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "RESUMIDA")
        self.assertEqual(h.payload["generadoPor"], "BASICO")
        self.assertEqual(guardado["estado"], "RESUMIDA")

    def test_revisar_por_http(self):
        self.doblar(eccsita, "marcar_revisada",
                    lambda *a: dict(SEMANA, estado="REVISADA"))
        h = self._post("/api/panel/eccsita/semanas/2026/48/revisar", {})
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "REVISADA")

    def test_export_md_con_disposition(self):
        self.doblar(eccsita, "detalle_semana", lambda *a: dict(SEMANA))
        h = self._get("/api/panel/eccsita/semanas/2026/48/export?formato=md")
        self.assertEqual(h.code, 200)
        self.assertIn("# ECCSita", h.sent["body"])
        self.assertEqual(h.sent["ctype"], "text/markdown")
        cabeceras = dict(h.sent["extra"])
        self.assertIn("attachment", cabeceras["Content-Disposition"])
        self.assertIn("eccsita-2026-s48.md",
                      cabeceras["Content-Disposition"])

    def test_export_json_con_disposition(self):
        self.doblar(eccsita, "detalle_semana", lambda *a: dict(SEMANA))
        h = self._get("/api/panel/eccsita/semanas/2026/48/export?formato=json")
        self.assertEqual(h.code, 200)
        self.assertEqual(json.loads(h.sent["body"])["semana"], 48)
        self.assertIn("eccsita-2026-s48.json",
                      dict(h.sent["extra"])["Content-Disposition"])

    def test_export_formato_invalido(self):
        self.doblar(eccsita, "detalle_semana", lambda *a: dict(SEMANA))
        h = self._get("/api/panel/eccsita/semanas/2026/48/export?formato=xml")
        self.assertEqual(h.code, 400)

    def test_chatid_invalido_se_rechaza(self):
        h = self._post("/api/panel/eccsita/grupo",
                       {"chatId": "5218123211516@c.us"})
        self.assertEqual(h.code, 400)

    def test_chatid_valido_se_guarda(self):
        guardados = []
        self.doblar(eccsita, "set_grupo",
                    lambda chat: (guardados.append(chat) or True, ""))
        h = self._post("/api/panel/eccsita/grupo", {"chatId": GRUPO})
        self.assertEqual(h.code, 200)
        self.assertEqual(guardados, [GRUPO])

    def test_grupo_get(self):
        h = self._get("/api/panel/eccsita/grupo")
        self.assertEqual(h.payload, {"chatId": GRUPO, "configurado": True})

    def test_grupos_con_openwa_caido_no_truena(self):
        import types
        falso = types.ModuleType("openwa_client")
        falso.listar_grupos = lambda *a, **k: (False, "sin conexión")
        previo = sys.modules.get("openwa_client")
        sys.modules["openwa_client"] = falso
        try:
            h = self._get("/api/panel/eccsita/grupos")
        finally:
            if previo is None:
                sys.modules.pop("openwa_client", None)
            else:
                sys.modules["openwa_client"] = previo
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["items"], [])
        self.assertIn("sin conexión", h.payload["error"])

    def test_aviso_desde_el_panel(self):
        self.doblar(eccsita, "enviar_aviso",
                    lambda: (True, "Aviso de consentimiento enviado."))
        h = self._post("/api/panel/eccsita/aviso", {})
        self.assertEqual(h.code, 200)
        self.assertTrue(h.payload["ok"])

    def test_aviso_ya_enviado_es_400(self):
        self.doblar(eccsita, "enviar_aviso",
                    lambda: (False, "El aviso de consentimiento ya se mandó "
                                    "a ese grupo."))
        h = self._post("/api/panel/eccsita/aviso", {})
        self.assertEqual(h.code, 400)
        self.assertIn("ya se mandó", h.payload["error"])


# ─── aviso de consentimiento (modulo) ───────────────────────────────────────
class AvisoConsentimiento(BaseConDobles):
    """Dobles de la config, del canal y de OpenWA: no se manda nada real."""

    def setUp(self):
        super().setUp()
        self.cfg = {"grupo": GRUPO, "aviso": True, "aviso_enviado": ""}
        self.enviados = []
        self.guardado = {}
        self.doblar(eccsita, "config", lambda: dict(self.cfg))
        self.doblar(canales, "activo", lambda canal: True)
        self.doblar(db, "set_config_values", self._guardar_valores)

        falso = types.ModuleType("openwa_client")
        falso.enviar_texto = self._enviar
        self._previo_openwa = sys.modules.get("openwa_client")
        sys.modules["openwa_client"] = falso

    def tearDown(self):
        super().tearDown()
        if self._previo_openwa is None:
            sys.modules.pop("openwa_client", None)
        else:
            sys.modules["openwa_client"] = self._previo_openwa

    def _enviar(self, chat, texto):
        self.enviados.append((chat, texto))
        return True, "enviado"

    def _guardar_valores(self, valores):
        self.guardado.update(valores)
        return True, ""

    def test_manda_una_sola_vez(self):
        ok, detalle = eccsita.enviar_aviso()
        self.assertTrue(ok, detalle)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(self.enviados[0][0], GRUPO)
        self.assertEqual(self.enviados[0][1], eccsita.AVISO_CONSENTIMIENTO)
        self.assertIn(eccsita.CLAVE_AVISO_ENVIADO, self.guardado)

        self.cfg["aviso_enviado"] = self.guardado[eccsita.CLAVE_AVISO_ENVIADO]
        ok, detalle = eccsita.enviar_aviso()
        self.assertFalse(ok)
        self.assertIn("ya se mandó", detalle)
        self.assertEqual(len(self.enviados), 1)

    def test_whatsapp_apagado_no_manda(self):
        self.doblar(canales, "activo", lambda canal: False)
        ok, detalle = eccsita.enviar_aviso()
        self.assertFalse(ok)
        self.assertIn("WhatsApp está apagado", detalle)
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.guardado, {})

    def test_sin_grupo_no_manda(self):
        self.cfg["grupo"] = ""
        ok, detalle = eccsita.enviar_aviso()
        self.assertFalse(ok)
        self.assertIn("grupo", detalle)
        self.assertEqual(self.enviados, [])

    def test_aviso_apagado_no_manda(self):
        self.cfg["aviso"] = False
        ok, detalle = eccsita.enviar_aviso()
        self.assertFalse(ok)
        self.assertEqual(self.enviados, [])

    def test_fallo_de_openwa_no_marca_enviado(self):
        def falla(chat, texto):
            return False, "sin sesión"

        self.doblar(sys.modules["openwa_client"], "enviar_texto", falla)
        ok, detalle = eccsita.enviar_aviso()
        self.assertFalse(ok)
        self.assertIn("sin sesión", detalle)
        self.assertEqual(self.guardado, {})


if __name__ == "__main__":
    unittest.main()
