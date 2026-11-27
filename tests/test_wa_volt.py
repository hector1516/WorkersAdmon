"""
Pruebas de los mensajes sueltos de Volt (wa_volt) y de su encaje en el bot.

Lo que se vigila:

  · cada `wa_volt_cada` interacciones del chat toca; antes no;
  · nunca más de un mensaje al día;
  · no repite la misma frase dos veces seguidas (ni tras reiniciar: el último
    registro es la memoria);
  · a un desconocido no se le habla;
  · el interruptor `wa_volt_activo` y las tres ausencias seguidas lo apagan;
  · con urgencias, facturas o cobranza `wa_bot` no lo agrega;
  · el saludo ya dice «Volt».

La base es falsa y el azar se fija; no se toca SQL Server ni WhatsApp.

    python -m unittest tests.test_wa_volt -v
"""
import os
import sys
import types
import unittest
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_bot  # noqa: E402
import wa_volt  # noqa: E402

# Jueves 26 nov 2026, 10:00 (hora de México).
HOY = datetime(2026, 11, 26, 10, 0)
MANANA = datetime(2026, 11, 27, 10, 0)

HECTOR = 7

TELEFONO = "5218123211516"
CHAT = "5218123211516@c.us"


# ─── Dobles ──────────────────────────────────────────────────────────────────

class BaseFalsa:
    """Conexión en memoria con las tablas que tocan wa_volt y wa_bot."""

    def __init__(self):
        self.usuarios = {HECTOR: (TELEFONO, True)}   # id -> (telefono, activo)
        self.mensajes = {CHAT: 0}                    # chat -> mensajes IN
        self.config = {}                             # Clave -> Valor
        self.recordatorios = []                      # dict(tipo, clave, uid, dia...)
        self.commits = 0
        self.rollbacks = 0
        self.cerrada = False

    def cursor(self, *a, **k):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        p = tuple(params or ())
        b = self.base
        self.una = None
        self.todas = []

        if "INSERT INTO HUB_WARECORDATORIOS" in q:
            tipo, clave, uid, dia, enviado, ok, detalle = p
            b.recordatorios.append({"tipo": tipo, "clave": clave, "uid": uid,
                                    "dia": dia, "enviado": enviado,
                                    "ok": bool(ok), "detalle": detalle})

        elif "FROM HUB_WARECORDATORIOS" in q and "ORDER BY ID DESC" in q:
            uid = p[0]
            filas = [r for r in b.recordatorios
                     if r["tipo"] == "VOLT" and r["uid"] == uid]
            self.una = (filas[-1]["detalle"],) if filas else None

        elif "FROM HUB_WARECORDATORIOS" in q:
            clave, uid, dia = p
            self.una = (sum(1 for r in b.recordatorios
                            if r["tipo"] == "VOLT" and r["clave"] == clave
                            and r["uid"] == uid and r["dia"] == dia),)

        elif "FROM HUB_CONFIG" in q:
            self.todas = [(clave, b.config[clave]) for clave in p
                          if clave in b.config]

        elif "FROM HUB_USUARIOSWHATSAPP" in q:
            uid = p[0]
            datos = b.usuarios.get(uid)
            self.una = (datos[0],) if datos and datos[1] else None

        elif "FROM HUB_WHATSAPPENTRANTE" in q:
            chat = p[0]
            self.una = (b.mensajes.get(chat, 0),)

        # Cualquier otra consulta (las que hace wa_persona detrás de wa_bot)
        # devuelve vacío: el dato del bot no depende de esta prueba.

    def fetchall(self):
        return self.todas

    def fetchone(self):
        return self.una

    def close(self):
        pass


def kb_falso():
    return {
        "empresa_telefono": "8181234567",
        "empresa_whatsapp": "528181234567",
        "direccion": "Av. Siempre Viva 123",
        "horario_texto": "lunes a viernes de 09:00 a 18:30",
        "servicios": ["Mantenimiento industrial"],
        "personal": [{"id": HECTOR, "nombre": "Héctor Peña",
                      "telefono": TELEFONO}],
        "ia": "0",
        "sarcasmo": "1",
    }


class Volt(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        self._azar = wa_volt.random.random
        self._elegir = wa_volt.random.choice
        wa_volt.random.random = lambda: 0.0        # siempre entra el azar
        wa_volt.random.choice = lambda opciones: opciones[0]
        self._ahora = wa_volt.now_mx
        wa_volt.now_mx = lambda: HOY
        # Hechos de persona doblados: solo interesa la bandera de ausencias.
        self.ausencias = False
        self._persona = wa_volt.wa_persona
        wa_volt.wa_persona = types.SimpleNamespace(
            hechos=lambda uid, conn=None: {"ausencias_seguidas": self.ausencias})

    def tearDown(self):
        wa_volt.random.random = self._azar
        wa_volt.random.choice = self._elegir
        wa_volt.now_mx = self._ahora
        wa_volt.wa_persona = self._persona

    # ── frecuencia ───────────────────────────────────────────────────────────
    def test_cada_doce_interacciones_toca(self):
        self.base.mensajes[CHAT] = 12
        self.assertTrue(wa_volt.toca(HECTOR, conn=self.base))
        frase = wa_volt.mensaje(HECTOR, conn=self.base)
        self.assertIn(frase, wa_volt.FRASES)

    def test_antes_de_doce_no_toca(self):
        self.base.mensajes[CHAT] = 11
        self.assertFalse(wa_volt.toca(HECTOR, conn=self.base))
        self.assertEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")

    def test_frecuencia_configurable(self):
        self.base.config = {"wa_volt_cada": "5"}
        self.base.mensajes[CHAT] = 5
        self.assertTrue(wa_volt.toca(HECTOR, conn=self.base))

    def test_el_azar_puede_callarlo(self):
        self.base.mensajes[CHAT] = 12
        wa_volt.random.random = lambda: 0.9        # fuera del 60 %
        self.assertFalse(wa_volt.toca(HECTOR, conn=self.base))

    # ── tope diario ──────────────────────────────────────────────────────────
    def test_solo_un_mensaje_al_dia(self):
        self.base.mensajes[CHAT] = 12
        self.assertNotEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")

        self.base.mensajes[CHAT] = 24
        self.assertEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")
        self.assertEqual(len(self.base.recordatorios), 1)

    def test_toca_no_gasta_el_tope(self):
        self.base.mensajes[CHAT] = 12
        self.assertTrue(wa_volt.toca(HECTOR, conn=self.base))
        self.assertTrue(wa_volt.toca(HECTOR, conn=self.base))
        self.assertEqual(self.base.recordatorios, [])

    def test_al_dia_siguiente_vuelve_a_tocar(self):
        self.base.mensajes[CHAT] = 12
        self.assertNotEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")
        wa_volt.now_mx = lambda: MANANA
        self.base.mensajes[CHAT] = 24
        self.assertNotEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")

    # ── frases ───────────────────────────────────────────────────────────────
    def test_no_repite_la_misma_frase(self):
        self.base.mensajes[CHAT] = 12
        primera = wa_volt.mensaje(HECTOR, conn=self.base)
        wa_volt.now_mx = lambda: MANANA
        self.base.mensajes[CHAT] = 24
        segunda = wa_volt.mensaje(HECTOR, conn=self.base)
        self.assertNotEqual(primera, segunda)
        self.assertIn(segunda, wa_volt.FRASES)

    # ── a quién no se le habla ───────────────────────────────────────────────
    def test_no_aplica_a_desconocidos(self):
        self.base.mensajes[CHAT] = 12
        self.assertEqual(wa_volt.mensaje(99, conn=self.base), "")
        self.assertEqual(wa_volt.mensaje(None, conn=self.base), "")
        self.assertEqual(wa_volt.mensaje(0, conn=self.base), "")

    def test_interruptor_apagado(self):
        self.base.config = {"wa_volt_activo": "0"}
        self.base.mensajes[CHAT] = 12
        self.assertFalse(wa_volt.toca(HECTOR, conn=self.base))
        self.assertEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")

    def test_tres_ausencias_lo_apagan(self):
        self.ausencias = True
        self.base.mensajes[CHAT] = 12
        self.assertEqual(wa_volt.mensaje(HECTOR, conn=self.base), "")


class EnElBot(unittest.TestCase):
    """El encaje en wa_bot: saludo con nombre y frase al final, sin IA."""

    def setUp(self):
        self.base = BaseFalsa()
        self.base.mensajes[CHAT] = 12
        self.kb = kb_falso()
        self._azar = wa_volt.random.random
        self._elegir = wa_volt.random.choice
        wa_volt.random.random = lambda: 0.0
        wa_volt.random.choice = lambda opciones: opciones[0]
        self._ahora = wa_volt.now_mx
        wa_volt.now_mx = lambda: HOY
        self._persona = wa_volt.wa_persona
        wa_volt.wa_persona = types.SimpleNamespace(
            hechos=lambda uid, conn=None: {"ausencias_seguidas": False})

    def tearDown(self):
        wa_volt.random.random = self._azar
        wa_volt.random.choice = self._elegir
        wa_volt.now_mx = self._ahora
        wa_volt.wa_persona = self._persona

    def responder(self, texto):
        return wa_bot.responder(CHAT, TELEFONO, texto, kb=self.kb,
                                conn=self.base)

    def test_el_saludo_dice_volt(self):
        r = self.responder("hola")
        self.assertIn("Soy Volt, el asistente de ECCSA", r)
        self.assertIn("Héctor", r)

    def test_la_frase_va_en_parrafo_aparte(self):
        r = self.responder("hola")
        parrafos = r.split("\n\n")
        self.assertGreaterEqual(len(parrafos), 2)
        self.assertIn(parrafos[-1], wa_volt.FRASES)
        self.assertIn("Héctor", parrafos[0])        # el dato sigue primero

    def test_urgencia_no_lleva_frase(self):
        r = self.responder("urgente, no funciona el sistema")
        for frase in wa_volt.FRASES:
            self.assertNotIn(frase, r)

    def test_factura_no_lleva_frase(self):
        r = self.responder("necesito una factura")
        for frase in wa_volt.FRASES:
            self.assertNotIn(frase, r)

    def test_respeta_los_800_caracteres(self):
        r = self.responder("hola")
        self.assertLessEqual(len(r), wa_bot.MAX_RESPUESTA)


if __name__ == "__main__":
    unittest.main()
