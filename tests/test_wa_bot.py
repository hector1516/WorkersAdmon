"""
Pruebas del motor de respuestas del bot publico de WhatsApp (wa_bot.py).

Lo que se vigila:

  · que el desconocido reciba un saludo formal y SIN chiste;
  · que al registrado se le llame por su nombre, se le diga que puede hacer y
    se le agregue UN chiste;
  · que la urgencia y factura/cobranza apaguen el humor;
  · que un insulto al bot reciba reconocimiento seco y un hecho real propio,
    y que urgencia, factura, ausencias y desconocidos queden neutros;
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
import wa_consultas  # noqa: E402
import wa_persona  # noqa: E402
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
        # El humor es ocasional (50%); en las pruebas se fuerza para que el
        # resultado no dependa del azar.
        self._azar = wa_bot.random.random
        wa_bot.random.random = lambda: 0.0

    def tearDown(self):
        wa_bot.random.random = self._azar

    def responder(self, texto, telefono=DESCONOCIDO, ahora=None, kb=None):
        return wa_bot.responder(CHAT, telefono, texto,
                                kb=self.kb if kb is None else kb, ahora=ahora)

    # ── desconocido ──────────────────────────────────────────────────────────
    def test_desconocido_saluda_formal_sin_chiste(self):
        r = self.responder("hola")
        self.assertIsNotNone(r)
        self.assertIn("Volt, el asistente de ECCSA", r)
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
        self.assertEqual(kb["sarcasmo"], "1")


# ─── Kilometraje y sarcasmo ──────────────────────────────────────────────────
#
# Dobles de `HUB_Automoviles` y `HUB_RegistroKilometros`: el bot guarda la
# lectura de verdad, así que la prueba comprueba las filas, no solo el texto.

HECTOR = "528155555555"          # id 5 en `kb_falso`
HECTOR_ID = 5


class BaseKm:
    def __init__(self):
        self.autos = []           # (Id, MarcaModelo, Placas, IdUsuarioAsignado)
        self.registros = []       # [Id, IdAutomovil, Kilometros, IdUsuario]
        self.sqls = []
        self.commits = 0
        self._siguiente = 1

    def nuevo_id(self):
        self._siguiente += 1
        return self._siguiente

    def cursor(self):
        return CursorKm(self)

    def commit(self):
        self.commits += 1


class CursorKm:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        base = self.base
        q = " ".join(str(sql).split()).upper()
        base.sqls.append((q, tuple(params)))
        self.una, self.todas = None, []

        if "FROM HUB_AUTOMOVILES" in q:
            yo = params[0]
            filas = sorted((a for a in base.autos if a[3] == yo),
                           key=lambda a: a[0])
            self.una = (filas[0][0], filas[0][1], filas[0][2]) if filas else None
        elif "INSERT INTO HUB_REGISTROKILOMETROS" in q:
            auto, kilometros, yo = params
            base.registros.append([base.nuevo_id(), auto, kilometros, yo])
        elif "UPDATE HUB_REGISTROKILOMETROS" in q:
            kilometros, registro = params
            for fila in base.registros:
                if fila[0] == registro:
                    fila[2] = kilometros
        elif "FROM HUB_REGISTROKILOMETROS" in q:
            yo, auto = params
            filas = [f for f in base.registros
                     if f[3] == yo and f[1] == auto]
            self.una = (filas[-1][0],) if filas else None

    def fetchone(self):
        return self.una

    def fetchall(self):
        return self.todas

    def close(self):
        return None


class Kilometraje(unittest.TestCase):
    def setUp(self):
        self.kb = kb_falso()
        self.base = BaseKm()
        self.base.autos = [(7, "Nissan Versa", "ABC-123", HECTOR_ID)]
        self._azar = wa_bot.random.random
        wa_bot.random.random = lambda: 0.0
        self._sarcasmo = wa_persona.sarcasmo
        self.frases = []

    def tearDown(self):
        wa_bot.random.random = self._azar
        wa_persona.sarcasmo = self._sarcasmo
        wa_persona.olvidar()

    def sarcasmo_fijo(self, frase):
        def falso(usuario_id, hechos=None, conn=None):
            self.frases.append(usuario_id)
            return frase
        wa_persona.sarcasmo = falso

    def responder(self, texto, telefono=HECTOR, kb=None):
        return wa_bot.responder(CHAT, telefono, texto,
                                kb=self.kb if kb is None else kb,
                                conn=self.base)

    def test_numero_guarda_la_lectura_con_el_auto(self):
        self.sarcasmo_fijo("179,679 kilómetros. El auto va a pedir aumento.")
        r = self.responder("179679")
        self.assertIn("179,679", r)
        self.assertIn("Nissan Versa", r)
        self.assertIn("ABC-123", r)
        self.assertIn("El auto va a pedir aumento", r)
        self.assertEqual(len(self.base.registros), 1)
        self.assertEqual(self.base.registros[0][1:4], [7, 179679, HECTOR_ID])
        self.assertEqual(self.base.commits, 2)

    def test_numero_con_comas_tambien_cuenta(self):
        r = self.responder(" 179,679 ")
        self.assertIn("179,679", r)
        self.assertEqual(self.base.registros[0][2], 179679)

    def test_numero_de_hoy_se_actualiza_y_no_se_duplica(self):
        self.base.registros = [[1, 7, 179000, HECTOR_ID]]
        r = self.responder("179679")
        self.assertIn("Actualicé", r)
        self.assertEqual(len(self.base.registros), 1)
        self.assertEqual(self.base.registros[0][2], 179679)

    def test_numero_sin_auto_pide_el_menu(self):
        self.base.autos = []
        r = self.responder("179679")
        self.assertIn("¿179,679 qué, Héctor?", r)
        self.assertIn("vehículo asignado", r)
        self.assertIn(wa_bot.MENU_CORTO, r)
        self.assertEqual(self.base.registros, [])

    def test_numero_fuera_de_rango_no_guarda(self):
        r = self.responder("999")
        self.assertIn("rango", r)
        self.assertIn("No guardé nada", r)
        self.assertEqual(self.base.registros, [])

    def test_numero_de_desconocido_no_guarda_y_ofrece_menu(self):
        r = self.responder("179679", telefono=DESCONOCIDO)
        self.assertIn("1. Horario", r)
        self.assertEqual(self.base.registros, [])

    def test_numero_sin_interruptor_no_inventa_sarcasmo(self):
        kb = kb_falso(sarcasmo="0")
        r = self.responder("179679", kb=kb)
        self.assertIn("Registré", r)
        self.assertNotIn("aumento", r)
        self.assertEqual(self.frases, [])


class SarcasmoEnBot(unittest.TestCase):
    """El sarcasmo va al final, solo a registrados y respeta el interruptor."""

    FRASE = "Tres vales sin comprobante, Héctor. ¿Los estás coleccionando?"

    def setUp(self):
        self.kb = kb_falso()
        self.base = BaseKm()
        self._azar = wa_bot.random.random
        wa_bot.random.random = lambda: 0.0
        self._sarcasmo = wa_persona.sarcasmo
        self.llamadas = []

        def falso(usuario_id, hechos=None, conn=None):
            self.llamadas.append(usuario_id)
            return self.FRASE

        wa_persona.sarcasmo = falso

    def tearDown(self):
        wa_bot.random.random = self._azar
        wa_persona.sarcasmo = self._sarcasmo
        wa_persona.olvidar()

    def responder(self, texto, telefono=HECTOR, kb=None, ahora=None):
        return wa_bot.responder(CHAT, telefono, texto,
                                kb=self.kb if kb is None else kb,
                                ahora=ahora, conn=self.base)

    def test_mensaje_sin_intencion_usa_un_hecho_y_el_menu_corto(self):
        r = self.responder("Esta bien eso bot, son cada lunes")
        self.assertIn(self.FRASE, r)
        self.assertIn(wa_bot.MENU_CORTO, r)
        self.assertNotIn("1. Horario", r)      # ya no es el menú seco
        self.assertNotIn("¡Hola, Héctor!", r)  # ni el saludo genérico

    def test_el_dato_va_primero_y_el_sarcasmo_al_final(self):
        r = self.responder("¿están abiertos?", ahora=LUNES_10)
        self.assertTrue(r.startswith("Ahora estamos"), r)
        self.assertTrue(r.endswith(self.FRASE), r)

    def test_urgencia_apaga_el_sarcasmo(self):
        r = self.responder("urgente, no funciona el sistema")
        self.assertNotIn(self.FRASE, r)
        self.assertIn("Héctor", r)
        self.assertEqual(self.llamadas, [])

    def test_factura_apaga_el_sarcasmo(self):
        r = self.responder("necesito una factura")
        self.assertNotIn(self.FRASE, r)
        self.assertEqual(self.llamadas, [])

    def test_interruptor_apagado_no_agrega_nada(self):
        kb = kb_falso(sarcasmo="0")
        r = self.responder("Esta bien eso bot menso, son cada lunes", kb=kb)
        self.assertNotIn(self.FRASE, r)
        self.assertIn("Acepto la queja", r)
        self.assertNotIn(wa_bot.MENU_CORTO, r)
        self.assertEqual(self.llamadas, [])

    def test_desconocido_nunca_recibe_sarcasmo(self):
        r = self.responder("Esta bien eso bot menso, son cada lunes",
                           telefono=DESCONOCIDO)
        self.assertNotIn(self.FRASE, r)
        self.assertNotIn("Acepto la queja", r)
        self.assertIn("Entiendo la molestia", r)
        self.assertIn("Priscila", r)
        self.assertEqual(self.llamadas, [])

    def test_cotizacion_con_partidas_no_lleva_sarcasmo(self):
        original = wa_consultas.responder
        wa_consultas.responder = lambda norm, uid, conn=None: (
            "Cotizacion de materiales\nPartidas:\n- 1 PLC: 1,200.00")
        try:
            r = self.responder("cotizacion")
        finally:
            wa_consultas.responder = original
        self.assertIn("Partidas:", r)
        self.assertNotIn(self.FRASE, r)

    def test_el_sarcasmo_no_rebasa_los_800_caracteres(self):
        wa_persona.sarcasmo = lambda usuario_id, hechos=None, conn=None: (
            "x" * 900)
        r = self.responder("Esta bien eso bot menso, son cada lunes")
        self.assertLessEqual(len(r), wa_bot.MAX_RESPUESTA)
        self.assertNotIn("x" * 900, r)


class InsultoEnBot(unittest.TestCase):
    """Insulto al bot: reconocimiento seco y roast con un hecho propio."""

    MENSAJE = "Esta bien eso bot menso, son cada lunes"

    def setUp(self):
        self.kb = kb_falso()
        self.base = BaseKm()
        self._hechos = wa_persona.hechos
        self._azar = wa_bot.random.random
        wa_bot.random.random = lambda: 0.0
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()
        self.datos = {"usuario_id": HECTOR_ID, "nombre": "Héctor Salas",
                      "ausencias_seguidas": False}
        self.hechos_falsos()

    def tearDown(self):
        wa_persona.hechos = self._hechos
        wa_bot.random.random = self._azar
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()

    def hechos_falsos(self, **datos):
        base = dict(self.datos)
        base.update(datos)
        wa_persona.hechos = lambda usuario_id, conn=None, ahora=None: base
        return base

    def responder(self, texto, telefono=HECTOR, kb=None):
        return wa_bot.responder(CHAT, telefono, texto,
                                kb=self.kb if kb is None else kb,
                                conn=self.base)

    def test_insulto_con_datos_responde_con_hecho_real(self):
        self.hechos_falsos(vales={"total": 3,
                                  "mas_viejo": datetime(2026, 11, 2)})
        r = self.responder(self.MENSAJE)
        self.assertTrue(r.startswith("Acepto la queja"), r)
        self.assertIn("vale", r)
        self.assertIn("comprobante", r)

    def test_insulto_sin_datos_responde_seco(self):
        r = self.responder(self.MENSAJE)
        self.assertIn("Acepto la queja, Héctor", r)
        self.assertIn("Me dijiste", r)

    def test_menso_dentro_de_otra_palabra_no_dispara(self):
        r = self.responder("inmenso trabajo el de hoy")
        self.assertNotIn("Acepto la queja", r)

    def test_desconocido_recibe_trato_formal(self):
        r = self.responder(self.MENSAJE, telefono=DESCONOCIDO)
        self.assertIn("Entiendo la molestia", r)
        self.assertNotIn("Acepto la queja", r)
        self.assertIn("Priscila", r)

    def test_urgencia_desactiva_el_roast(self):
        r = self.responder("urgente, bot menso, no funciona el sistema")
        self.assertNotIn("Acepto la queja", r)
        self.assertIn("Héctor", r)

    def test_factura_desactiva_el_roast_y_canaliza(self):
        r = self.responder("necesito factura, bot menso")
        self.assertNotIn("Acepto la queja", r)
        self.assertIn("Rosy", r)

    def test_interruptor_apagado_solo_reconoce(self):
        self.hechos_falsos(vales={"total": 3,
                                  "mas_viejo": datetime(2026, 11, 2)})
        kb = kb_falso(sarcasmo="0")
        r = self.responder(self.MENSAJE, kb=kb)
        self.assertEqual(r, "Acepto la queja, Héctor.")
        self.assertNotIn("vales", r)
        self.assertNotIn(wa_persona.CIERRE_SECO, r)

    def test_tres_ausencias_seguidas_canalizan_neutro(self):
        self.hechos_falsos(ausencias_seguidas=True)
        r = self.responder(self.MENSAJE)
        self.assertNotIn("Acepto la queja", r)
        self.assertIn("Priscila", r)


if __name__ == "__main__":
    unittest.main()
