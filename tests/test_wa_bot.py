"""
Pruebas del motor de respuestas del bot publico de WhatsApp (wa_bot.py).

Lo que se vigila:

  · que el desconocido reciba un saludo formal y SIN chiste;
  · que al registrado se le llame por su nombre, se le diga que puede hacer y
    se le agregue UN chiste;
  · que la urgencia y factura/cobranza apaguen el humor;
  · que abierto/cerrado dependa de la hora inyectada (sin depender del reloj);
  · que direccion + mapa, telefonos del equipo y canalizacion salgan con el
    responsable correcto;
  · que precios y datos de clientes se rechacen y se canalicen;
  · que lo vacio, los emojis sueltos y los grupos no se contesten.

La base se dobla en memoria: no se toca SQL Server.

    python -m unittest tests.test_wa_bot -v
"""
import os
import sys
import unittest
from datetime import datetime
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_bot  # noqa: E402
import eccsa_db  # noqa: E402

CHAT = "5218123211516@c.us"
DESCONOCIDO = "528100000000"
REGISTRADO = "528111111111"          # Daniel Perez
DIRECCION = "Av. Constitucion 123, Monterrey, NL"

# Lunes 23-nov-2026 10:00, sabado 21-nov-2026 10:00, domingo 22-nov-2026 12:00.
LUNES_10 = datetime(2026, 11, 23, 10, 0)
LUNES_19 = datetime(2026, 11, 23, 19, 0)
SABADO_10 = datetime(2026, 11, 21, 10, 0)
SABADO_14 = datetime(2026, 11, 21, 14, 0)
DOMINGO_12 = datetime(2026, 11, 22, 12, 0)


def kb_falso(**extra):
    kb = {
        "empresa_telefono": "8181234567",
        "empresa_whatsapp": "528181234567",
        "direccion": DIRECCION,
        "horario_texto": wa_bot.HORARIO_TEXTO,
        "horario_lv": "09:00 a 18:30",
        "horario_sab": "09:30 a 13:30",
        # ventas -> Daniel y Alejandro; facturas -> Rosy; default -> Priscila.
        "rutas": {"ventas": [1, 2], "facturas": [3], "default": [4]},
        "servicios": ["Mantenimiento industrial",
                      "Proyectos de automatizacion"],
        "personal": [
            {"id": 1, "nombre": "Daniel Perez", "telefono": REGISTRADO},
            {"id": 2, "nombre": "Alejandro Ruiz", "telefono": "528122222222"},
            {"id": 3, "nombre": "Rosy Lopez", "telefono": "528133333333"},
            {"id": 4, "nombre": "Priscila Gomez", "telefono": "528144444444"},
            {"id": 5, "nombre": "Héctor Salas", "telefono": "528155555555"},
        ],
    }
    kb.update(extra)
    return kb


def chistes_en(texto):
    return [chiste for chiste in wa_bot.CHISTES if chiste in (texto or "")]


class BotWa(unittest.TestCase):
    def setUp(self):
        self.kb = kb_falso()

    def responder(self, texto, telefono=DESCONOCIDO, ahora=None, kb=None):
        return wa_bot.responder(CHAT, telefono, texto,
                                kb=self.kb if kb is None else kb, ahora=ahora)

    # ── desconocido ──────────────────────────────────────────────────────────
    def test_desconocido_saluda_formal_sin_chiste(self):
        r = self.responder("hola")
        self.assertIsNotNone(r)
        self.assertIn("asistente de ECCSA", r)
        self.assertIn("horario", r.lower())
        self.assertIn("dirección", r.lower())
        self.assertIn("teléfonos", r.lower())
        self.assertEqual(chistes_en(r), [])

    def test_desconocido_no_recibe_humor_ni_preguntando_datos(self):
        r = self.responder("hola, ¿me ayudas con los servicios?")
        self.assertEqual(chistes_en(r), [])

    # ── registrado ───────────────────────────────────────────────────────────
    def test_registrado_nombre_capacidades_y_un_chiste(self):
        r = self.responder("hola", telefono=REGISTRADO)
        self.assertIn("Daniel", r)
        bajo = r.lower()
        for capacidad in ("horario", "dirección", "teléfonos", "canaliz"):
            self.assertIn(capacidad, bajo)
        self.assertIn("tickets", bajo)
        self.assertEqual(len(chistes_en(r)), 1)

    def test_humor_varia_entre_mensajes_seguidos(self):
        primero = self.responder("hola", telefono=REGISTRADO)
        segundo = self.responder("hola", telefono=REGISTRADO)
        chiste1 = chistes_en(primero)
        chiste2 = chistes_en(segundo)
        self.assertEqual(len(chiste1), 1)
        self.assertEqual(len(chiste2), 1)
        self.assertNotEqual(chiste1[0], chiste2[0])

    def test_urgencia_apaga_el_humor(self):
        r = self.responder("urgente, no funciona el sistema",
                           telefono=REGISTRADO)
        self.assertIsNotNone(r)
        self.assertEqual(chistes_en(r), [])
        self.assertIn("Héctor", r)

    def test_factura_apaga_el_humor_y_canaliza_a_rosy(self):
        r = self.responder("necesito una factura", telefono=REGISTRADO)
        self.assertEqual(chistes_en(r), [])
        self.assertIn("Rosy", r)

    # ── horario ──────────────────────────────────────────────────────────────
    def test_horario_abierto_lunes(self):
        r = self.responder("¿están abiertos?", ahora=LUNES_10)
        self.assertIn("abierto", r.lower())
        self.assertIn("18:30", r)

    def test_horario_cerrado_lunes_de_noche(self):
        r = self.responder("¿a qué hora abren?", ahora=LUNES_19)
        self.assertIn("cerrado", r.lower())

    def test_horario_sabado_en_la_manana_abierto(self):
        r = self.responder("horario", ahora=SABADO_10)
        self.assertIn("abierto", r.lower())
        self.assertIn("13:30", r)

    def test_horario_sabado_tarde_cerrado(self):
        r = self.responder("horario", ahora=SABADO_14)
        self.assertIn("cerrado", r.lower())

    def test_horario_domingo_cerrado(self):
        r = self.responder("¿están abiertos?", ahora=DOMINGO_12)
        self.assertIn("cerrado", r.lower())

    # ── datos publicos ───────────────────────────────────────────────────────
    def test_direccion_y_enlace_de_mapa(self):
        r = self.responder("¿dónde están?")
        self.assertIn(DIRECCION, r)
        self.assertIn("https://maps.google.com/?q=", r)
        self.assertIn(quote(DIRECCION, safe=""), r)

    def test_sin_direccion_no_inventa_y_manda_al_telefono(self):
        kb = kb_falso(direccion="")
        r = self.responder("¿dónde están?", kb=kb)
        self.assertIn("No tengo cargada la dirección exacta", r)
        self.assertIn("8181234567", r)

    def test_telefono_de_alguien_del_equipo(self):
        r = self.responder("¿me das el teléfono de Rosy?")
        self.assertIn("Rosy", r)
        self.assertIn("528133333333", r)

    def test_telefonos_del_equipo_completos(self):
        r = self.responder("teléfonos del equipo")
        for persona in self.kb["personal"]:
            self.assertIn(persona["nombre"], r)
            self.assertIn(persona["telefono"], r)

    def test_servicios(self):
        r = self.responder("¿qué servicios ofrecen?")
        self.assertIn("Mantenimiento industrial", r)

    # ── canalizacion ─────────────────────────────────────────────────────────
    def test_ventas_canaliza_a_daniel_y_alejandro(self):
        r = self.responder("quiero información de ventas y proyectos")
        self.assertIn("Daniel", r)
        self.assertIn("Alejandro", r)
        self.assertIn("528122222222", r)

    def test_facturas_canaliza_a_rosy(self):
        r = self.responder("tengo una duda de facturas")
        self.assertIn("Rosy", r)
        self.assertIn("528133333333", r)

    def test_desconocido_que_pide_hablar_canaliza_a_priscila(self):
        r = self.responder("quiero hablar con alguien")
        self.assertIn("Priscila", r)
        self.assertIn("528144444444", r)

    def test_sistemas_canaliza_a_hector(self):
        r = self.responder("tengo un problema con sistemas")
        self.assertIn("Héctor", r)
        self.assertIn("528155555555", r)

    # ── lo que nunca se comparte ─────────────────────────────────────────────
    def test_precios_se_rechazan_y_se_canalizan(self):
        r = self.responder("¿me pasas los precios?")
        self.assertTrue(r.startswith("No puedo compartir eso;"),
                        f"respuesta inesperada: {r!r}")
        self.assertIn("Daniel", r)

    def test_datos_de_cliente_se_rechazan(self):
        r = self.responder("necesito los datos de cliente de otro cliente")
        self.assertTrue(r.startswith("No puedo compartir eso;"))
        self.assertIn("Priscila", r)

    # ── lo que se ignora ─────────────────────────────────────────────────────
    def test_texto_vacio_no_contesta(self):
        for vacio in ("", "   ", "\n\t", None):
            self.assertIsNone(self.responder(vacio), repr(vacio))

    def test_solo_emojis_no_contesta(self):
        self.assertIsNone(self.responder("😀🔥👍"))
        self.assertIsNone(self.responder("??!!!"))

    def test_grupo_no_contesta(self):
        self.assertIsNone(wa_bot.responder("1234567890@g.us", REGISTRADO,
                                           "hola", kb=self.kb))

    def test_mensaje_largo_pide_resumen(self):
        r = self.responder("a" * (wa_bot.MAX_ENTRADA + 1))
        self.assertIn("resume", r.lower())

    # ── menu ─────────────────────────────────────────────────────────────────
    def test_sin_entender_ofrece_menu_numerado(self):
        r = self.responder("xyz")
        for numero in ("1.", "2.", "3.", "4.", "5."):
            self.assertIn(numero, r)
        self.assertIn("número", r.lower())

    def test_opcion_numerica_del_menu(self):
        r = self.responder("2", ahora=LUNES_10)
        self.assertIn(DIRECCION, r)

    # ── base de conocimiento ─────────────────────────────────────────────────
    def test_cargar_kb_sin_base_devuelve_defaults(self):
        original = eccsa_db.get_connection
        wa_bot._kb_cache_reset()
        try:
            eccsa_db.get_connection = lambda: (_ for _ in ()).throw(
                RuntimeError("sin SQL Server"))
            kb = wa_bot.cargar_kb()
        finally:
            eccsa_db.get_connection = original
            wa_bot._kb_cache_reset()

        self.assertEqual(kb["servicios"], [])
        self.assertEqual(kb["personal"], [])
        self.assertEqual(kb["rutas"], {})
        self.assertIn("09:00", kb["horario_texto"])
        self.assertIn("empresa_telefono", kb)


if __name__ == "__main__":
    unittest.main()
