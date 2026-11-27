"""
Pruebas de la redaccion con IA del bot de WhatsApp (wa_redaccion.py).

Lo que se vigila:

  · que la IA este apagada por defecto (`wa_bot_ia` = 0) y que sin ApiKey
    tampoco se llame al proveedor;
  · que con 1 + ApiKey se reescriba el texto;
  · que si la IA cambia un numero, una liga o un emoji se descarte y quede el
    original;
  · que una excepcion o una demora devuelvan el original (nunca lanza);
  · que el prompt prohiba cambiar datos;
  · que `wa_bot.responder` sea identico al de antes con la IA apagada y use el
    texto mejorado con la IA prendida;
  · que cotizaciones con partidas, listas largas y textos de mas de 600
    caracteres no pasen por la IA.

El proveedor y la base se doblan: no hay red ni SQL Server.

    python -m unittest tests.test_wa_redaccion -v
"""
import os
import re
import sys
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_bot  # noqa: E402
import wa_consultas  # noqa: E402
import wa_redaccion  # noqa: E402
import eccsa_db  # noqa: E402

CHAT = "5218123211516@c.us"
DESCONOCIDO = "528100000000"
TEXTO = "Tu vale 450 pesos ya quedo listo."


def kb_falso(**extra):
    """kb minima: sin claves de IA, la redaccion esta apagada (default)."""
    kb = {
        "empresa_telefono": "8181234567",
        "empresa_whatsapp": "528181234567",
        "direccion": "Av. Constitucion 123, Monterrey, NL",
        "horario_texto": wa_bot.HORARIO_TEXTO,
        "horario_lv": "09:00 a 18:30",
        "horario_sab": "09:30 a 13:30",
        "rutas": {"ventas": [1], "default": [1]},
        "servicios": ["Mantenimiento industrial"],
        "personal": [{"id": 1, "nombre": "Daniel Perez",
                      "telefono": "528111111111"}],
        "ia": "0",
    }
    kb.update(extra)
    return kb


def kb_ia(**extra):
    """kb con la IA prendida y credenciales (sin tocar la base)."""
    return kb_falso(ia="1", ai_api_key="llave-de-prueba",
                    ai_model="gemini-de-prueba", **extra)


class RedaccionBase(unittest.TestCase):
    def setUp(self):
        self._azar = wa_bot.random.random
        wa_bot.random.random = lambda: 0.0
        wa_redaccion._cache_reset()
        self._timeout = wa_redaccion.TIMEOUT_S

    def tearDown(self):
        wa_redaccion.TIMEOUT_S = self._timeout
        wa_bot.random.random = self._azar
        wa_redaccion._cache_reset()


class IaActiva(RedaccionBase):
    def test_apagada_por_defecto_no_llama_al_proveedor(self):
        llamadas = []
        original = wa_redaccion._generar
        wa_redaccion._generar = lambda *a, **k: llamadas.append(a) or TEXTO
        try:
            kb = kb_falso(ai_api_key="llave", ai_model="modelo")
            self.assertFalse(wa_redaccion.ia_activa(kb))
            self.assertEqual(wa_redaccion.mejorar(TEXTO, kb), TEXTO)
        finally:
            wa_redaccion._generar = original
        self.assertEqual(llamadas, [])

    def test_prenda_con_apikey_llama_al_proveedor(self):
        # La ApiKey viene de HUB_AIConfig (se dobla get_ai_config).
        original = eccsa_db.get_ai_config
        eccsa_db.get_ai_config = lambda: {
            "provider": "google_gemini", "api_key": "llave",
            "model": "gemini-de-prueba"}
        llamadas = []
        original_gen = wa_redaccion._generar

        def falso(prompt, api_key, modelo):
            llamadas.append(prompt)
            return TEXTO.replace("ya quedo listo", "ya esta listo")

        wa_redaccion._generar = falso
        try:
            kb = kb_falso(ia="1")
            self.assertTrue(wa_redaccion.ia_activa(kb))
            nuevo = wa_redaccion.mejorar(TEXTO, kb)
        finally:
            eccsa_db.get_ai_config = original
            wa_redaccion._generar = original_gen
        self.assertEqual(len(llamadas), 1)
        self.assertIn("ya esta listo", nuevo)

    def test_sin_apikey_no_activa(self):
        kb = kb_falso(ia="1", ai_model="gemini-de-prueba")
        self.assertFalse(wa_redaccion.ia_activa(kb))

    def test_sin_modelo_no_activa(self):
        kb = kb_falso(ia="1", ai_api_key="llave")
        self.assertFalse(wa_redaccion.ia_activa(kb))


class ConservaDatos(RedaccionBase):
    def _mejorar_con(self, devuelto, texto=TEXTO, kb=None):
        original = wa_redaccion._generar
        wa_redaccion._generar = lambda *a, **k: devuelto
        try:
            return wa_redaccion.mejorar(texto, kb or kb_ia())
        finally:
            wa_redaccion._generar = original

    def test_numero_distinto_se_descarta(self):
        self.assertEqual(self._mejorar_con("Tu vale 540 pesos ya quedo listo."),
                         TEXTO)

    def test_liga_distinta_se_descarta(self):
        texto = "Estamos en la oficina. Mapa: https://maps.google.com/?q=abc"
        devuelto = "Estamos en la oficina. Mapa: https://maps.google.com/?q=xyz"
        self.assertEqual(self._mejorar_con(devuelto, texto=texto), texto)

    def test_emoji_faltante_se_descarta(self):
        texto = "Listo 👍, tu reporte ya esta."
        self.assertEqual(self._mejorar_con("Listo, tu reporte ya esta.",
                                           texto=texto), texto)

    def test_crecimiento_mayor_al_20_por_ciento_se_descarta(self):
        largo = TEXTO + " " + ("mucho texto amable " * 5)
        self.assertEqual(self._mejorar_con(largo), TEXTO)

    def test_respuesta_vacia_se_descarta(self):
        self.assertEqual(self._mejorar_con("   "), TEXTO)

    def test_mismo_numero_reordenado_se_acepta(self):
        devuelto = "Ya quedo listo tu vale de 450 pesos."
        self.assertEqual(self._mejorar_con(devuelto), devuelto)


class Fallas(RedaccionBase):
    def test_excepcion_del_proveedor_devuelve_original(self):
        original = wa_redaccion._generar

        def revienta(*a, **k):
            raise RuntimeError("sin cuota")

        wa_redaccion._generar = revienta
        try:
            self.assertEqual(wa_redaccion.mejorar(TEXTO, kb_ia()), TEXTO)
        finally:
            wa_redaccion._generar = original

    def test_timeout_devuelve_original(self):
        original = wa_redaccion._generar
        wa_redaccion.TIMEOUT_S = 0.05

        def tarda(*a, **k):
            time.sleep(0.3)
            return "Ya quedo tu vale."

        wa_redaccion._generar = tarda
        try:
            inicio = time.monotonic()
            self.assertEqual(wa_redaccion.mejorar(TEXTO, kb_ia()), TEXTO)
            self.assertLess(time.monotonic() - inicio, 0.25)
        finally:
            wa_redaccion._generar = original


class Prompt(RedaccionBase):
    def test_prompt_prohibe_cambiar_datos(self):
        prompt = wa_redaccion._prompt(TEXTO)
        self.assertIn("NO cambies ni agregues datos", prompt)
        self.assertIn("NO inventes", prompt)
        self.assertIn(TEXTO, prompt)

    def test_temperatura_baja(self):
        self.assertLessEqual(wa_redaccion.TEMPERATURA, 0.3)
        self.assertEqual(wa_redaccion.TIMEOUT_S, 8)


class QueSePule(RedaccionBase):
    def test_no_pule_cotizacion_con_partidas(self):
        texto = "Cotizacion de materiales\nPartidas:\n- 1 PLC: 1,200.00"
        self.assertFalse(wa_redaccion.se_pule(texto))

    def test_no_pule_lista_larga_de_vales(self):
        texto = "Tus ultimos 5 vales:\n" + "\n".join(
            f"- Vale {i}: 500.00" for i in range(1, 6))
        self.assertFalse(wa_redaccion.se_pule(texto))

    def test_no_pule_mas_de_600_caracteres(self):
        self.assertFalse(wa_redaccion.se_pule("a" * 601))

    def test_pule_un_mensaje_corto(self):
        self.assertTrue(wa_redaccion.se_pule("Hola, ¿qué necesitas?"))

    def test_mejorar_no_toca_lo_que_no_se_pule(self):
        llamadas = []
        original = wa_redaccion._generar
        wa_redaccion._generar = lambda *a, **k: llamadas.append(a) or "otro"
        texto = "Cotizacion\nPartidas:\n- 1 PLC: 1,200.00"
        try:
            self.assertEqual(wa_redaccion.mejorar(texto, kb_ia()), texto)
        finally:
            wa_redaccion._generar = original
        self.assertEqual(llamadas, [])


class BotConIa(RedaccionBase):
    def setUp(self):
        super().setUp()
        self._generar = wa_redaccion._generar

    def tearDown(self):
        wa_redaccion._generar = self._generar
        super().tearDown()

    def test_apagada_respuesta_igual_a_las_reglas(self):
        def no_llamar(*a, **k):
            raise AssertionError("la IA no debe llamarse con wa_bot_ia=0")

        wa_redaccion._generar = no_llamar
        kb = kb_falso(ai_api_key="llave", ai_model="modelo")
        reglas = wa_bot._responder_reglas(CHAT, DESCONOCIDO, "hola", kb=kb)
        r = wa_bot.responder(CHAT, DESCONOCIDO, "hola", kb=kb)
        self.assertEqual(r, reglas)

    def test_prendida_usa_el_texto_mejorado(self):
        def mayusculas(prompt, api_key, modelo):
            # El original va al final del prompt tras "Mensaje original:".
            texto = prompt.split("Mensaje original:", 1)[1].strip()
            return texto.upper()

        wa_redaccion._generar = mayusculas
        kb = kb_ia()
        r = wa_bot.responder(CHAT, DESCONOCIDO, "hola", kb=kb)
        self.assertIsNotNone(r)
        self.assertEqual(r, r.upper())
        self.assertNotEqual(r, wa_bot._responder_reglas(CHAT, DESCONOCIDO,
                                                        "hola", kb=kb))

    def test_prendida_y_proveedor_falla_responde_igual(self):
        def revienta(*a, **k):
            raise RuntimeError("sin red")

        wa_redaccion._generar = revienta
        kb = kb_ia()
        self.assertEqual(
            wa_bot.responder(CHAT, DESCONOCIDO, "hola", kb=kb),
            wa_bot._responder_reglas(CHAT, DESCONOCIDO, "hola", kb=kb))

    def test_no_contesta_nada_no_llama_a_la_ia(self):
        def no_llamar(*a, **k):
            raise AssertionError("no hay respuesta que pulir")

        wa_redaccion._generar = no_llamar
        self.assertIsNone(wa_bot.responder(CHAT, DESCONOCIDO, "", kb=kb_ia()))
        self.assertIsNone(wa_bot.responder("123@g.us", DESCONOCIDO, "hola",
                                           kb=kb_ia()))

    def test_cotizacion_con_partidas_no_pasa_por_ia(self):
        llamadas = []
        original = wa_consultas.responder
        wa_consultas.responder = lambda norm, uid, conn=None: (
            "Cotizacion de materiales\nPartidas:\n- 1 PLC: 1,200.00")

        def falso(*a, **k):
            llamadas.append(a)
            return "otro texto"

        wa_redaccion._generar = falso
        try:
            kb = kb_ia()
            r = wa_bot.responder(CHAT, "528111111111", "cotizacion", kb=kb)
        finally:
            wa_consultas.responder = original
        self.assertIn("Partidas:", r)
        self.assertEqual(llamadas, [])


class LineasVerbatim(RedaccionBase):
    """El texto con datos sale con los mismos digitos que entraron."""

    def test_digitos_del_original_presentes(self):
        nuevo = "Tu vale de 450 pesos ya quedo listo."
        original = wa_redaccion._generar
        wa_redaccion._generar = lambda *a, **k: nuevo
        try:
            r = wa_redaccion.mejorar(TEXTO, kb_ia())
        finally:
            wa_redaccion._generar = original
        self.assertEqual(set(re.findall(r"\d+", r)), set(re.findall(r"\d+", TEXTO)))


if __name__ == "__main__":
    unittest.main()
