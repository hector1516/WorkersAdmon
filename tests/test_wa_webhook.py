"""
Pruebas del receptor de mensajes entrantes de WhatsApp (panel/wa_webhook.py).

Lo que se vigila no es que el mensaje se vea bonito, sino las cosas que
duelen si fallan:

  · que un token equivocado responda 404 y no guarde nada;
  · que el eco del propio bot y los grupos no ensucien la tabla;
  · que un reintento de OpenWA (mismo IdMensaje) no duplique la fila;
  · que un mensaje normal deje su fila Y su conversacion en BOT.

La base se dobla en memoria: no se toca SQL Server.

    python3 -m unittest tests.test_wa_webhook
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from panel import wa_webhook  # noqa: E402

TOKEN = "token-de-prueba-123"
CHAT = "5218123211516@c.us"


class BaseFalsa:
    """Guarda filas y conversaciones para revisarlas desde el test."""

    def __init__(self):
        self.filas = []
        self.ids = set()
        self.conversaciones = {}
        self.rollbacks = 0


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        if q.startswith("SELECT 1 FROM HUB_WHATSAPPENTRANTE"):
            self.una = (1,) if params[0] in self.base.ids else None
        elif q.startswith("INSERT INTO HUB_WHATSAPPENTRANTE"):
            self.base.filas.append(params)
            if params[0]:
                self.base.ids.add(params[0])
            self.una = None
        elif q.startswith("MERGE HUB_WHATSAPPCONVERSACIONES"):
            chat, fecha = params[0], params[1]
            if chat in self.base.conversaciones:
                self.base.conversaciones[chat]["ultima"] = fecha
            else:
                self.base.conversaciones[chat] = {"estado": "BOT",
                                                  "ultima": fecha}
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
    """Doble del Handler del panel: solo lo que usa el webhook."""

    def __init__(self, cuerpo):
        self.cuerpo = cuerpo
        self.code = None
        self.payload = None

    def _read_json(self):
        return self.cuerpo if isinstance(self.cuerpo, dict) else {}

    def _json(self, obj, code=200, extra=()):
        self.code = code
        self.payload = obj


def mensaje(**extra):
    cuerpo = {"id": "wamid-1", "chatId": CHAT, "from": CHAT,
              "body": "hola", "timestamp": 1790000000}
    cuerpo.update(extra)
    return cuerpo


class WebhookWa(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        self._orig = (wa_webhook.db.get_config_values,
                      wa_webhook.db.get_connection)
        wa_webhook.db.get_config_values = lambda claves: {wa_webhook.CLAVE_TOKEN: TOKEN}
        wa_webhook.db.get_connection = lambda: ConexionFalsa(self.base)

    def tearDown(self):
        wa_webhook.db.get_config_values, wa_webhook.db.get_connection = self._orig
        wa_webhook._CACHE_LID.clear()

    def _atender(self, cuerpo, token=TOKEN):
        h = HandlerFalso(cuerpo)
        wa_webhook.atender(h, token)
        return h

    # ── token ────────────────────────────────────────────────────────────────
    def test_token_incorrecto_responde_404_y_no_guarda(self):
        h = self._atender(mensaje(), token="otro")
        self.assertEqual(h.code, 404)
        self.assertEqual(self.base.filas, [])
        self.assertEqual(self.base.conversaciones, {})

    def test_sin_token_configurado_tambien_es_404(self):
        wa_webhook.db.get_config_values = lambda claves: {}
        h = self._atender(mensaje())
        self.assertEqual(h.code, 404)
        self.assertEqual(self.base.filas, [])

    # ── lo que se ignora ─────────────────────────────────────────────────────
    def test_mensaje_propio_no_se_guarda(self):
        h = self._atender(mensaje(fromMe=True))
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    def test_grupo_no_se_guarda(self):
        h = self._atender(mensaje(**{"chatId": "120363407857907731@g.us",
                                     "from": "120363407857907731@g.us"}))
        self.assertEqual(h.payload["estado"], "ignorado")
        self.assertEqual(self.base.filas, [])

    # ── guardado ─────────────────────────────────────────────────────────────
    def test_duplicado_por_id_deja_una_sola_fila(self):
        self._atender(mensaje())
        h = self._atender(mensaje())
        self.assertEqual(h.payload["estado"], "duplicado")
        self.assertEqual(len(self.base.filas), 1)
        self.assertIn(CHAT, self.base.conversaciones)

    def test_mensaje_normal_guarda_fila_y_conversacion(self):
        h = self._atender(mensaje(nombre="Ana", body="buen dia"))
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["estado"], "guardado")
        self.assertEqual(len(self.base.filas), 1)

        idm, chat, tel, nombre, texto, fecha, crudo = self.base.filas[0]
        self.assertEqual(idm, "wamid-1")
        self.assertEqual(chat, CHAT)
        self.assertEqual(tel, "5218123211516")
        self.assertEqual(nombre, "Ana")
        self.assertEqual(texto, "buen dia")
        self.assertIsNotNone(fecha)
        self.assertIn("buen dia", crudo)
        self.assertEqual(self.base.conversaciones[CHAT]["estado"], "BOT")

    def test_cuerpo_anidado_en_data_se_entiende(self):
        h = self._atender({"event": "message", "data": mensaje(id="wamid-2")})
        self.assertEqual(h.payload["estado"], "guardado")
        self.assertEqual(self.base.filas[0][0], "wamid-2")

    # ── LID: WhatsApp manda un id largo en vez del telefono ──────────────────
    def test_nombre_del_autor_en_contacto_del_grupo(self):
        datos = wa_webhook._extraer(mensaje(
            **{"isGroup": True,
               "chatId": "120363407857907731@g.us",
               "from": "120363407857907731@g.us",
               "author": "247339298115756@lid",
               "contact": {"pushName": "Lic. Priscila Urbina"}}))
        self.assertEqual(datos["nombre"], "Lic. Priscila Urbina")
        self.assertIn("247339298115756", datos.get("autor_id") or "")

    def test_grupo_sin_contacto_usa_el_author_como_nombre(self):
        """Sin `contact` el autor no puede quedar NULL en HUB_GrupoMensajes."""
        datos = wa_webhook._extraer(mensaje(
            **{"isGroup": True,
               "chatId": "120363407857907731@g.us",
               "from": "120363407857907731@g.us",
               "author": "5218123211516@c.us"}))
        self.assertEqual(datos["autor_nombre"], "5218123211516@c.us")
        self.assertEqual(datos["autor_id"], "5218123211516@c.us")

    def test_lid_se_resuelve_al_telefono_real(self):
        import types

        falso = types.ModuleType("openwa_client")
        falso.telefono_de_contacto = lambda chat: "5218123211516"
        previo = sys.modules.get("openwa_client")
        sys.modules["openwa_client"] = falso
        try:
            datos = wa_webhook._extraer(mensaje(
                **{"chatId": "244534030106783@lid",
                   "from": "244534030106783@lid"}))
        finally:
            if previo is None:
                sys.modules.pop("openwa_client", None)
            else:
                sys.modules["openwa_client"] = previo
        self.assertEqual(datos["telefono"], "5218123211516")
        self.assertEqual(datos["chat_id"], "244534030106783@lid")

    def test_lid_sin_respuesta_conserva_el_id(self):
        import types

        falso = types.ModuleType("openwa_client")
        falso.telefono_de_contacto = lambda chat: ""
        previo = sys.modules.get("openwa_client")
        sys.modules["openwa_client"] = falso
        try:
            datos = wa_webhook._extraer(mensaje(
                **{"chatId": "244534030106783@lid",
                   "from": "244534030106783@lid"}))
        finally:
            if previo is None:
                sys.modules.pop("openwa_client", None)
            else:
                sys.modules["openwa_client"] = previo
        self.assertEqual(datos["telefono"], "244534030106783")

    # ── ruta pública, antes del gate ─────────────────────────────────────────
    def test_ruta_publica_atiende_antes_del_gate_de_sesion(self):
        from panel import server

        class SelfFalso(HandlerFalso):
            def __init__(self, path, cuerpo):
                super().__init__(cuerpo)
                self.path = path

            def _parts(self):
                return self.path, {}

            def _read_form(self):
                return {}

        yo = SelfFalso("/api/wa/entrante/" + TOKEN, mensaje(id="wamid-3"))
        server.Handler.do_POST(yo)
        self.assertEqual(yo.code, 200)
        self.assertEqual(len(self.base.filas), 1)


if __name__ == "__main__":
    unittest.main()
