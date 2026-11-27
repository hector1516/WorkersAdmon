"""
tests/test_api_bot.py — Interruptores del bot de WhatsApp (Volt)
================================================================
Sin SQL Server ni servidor HTTP: se importa `panel.api_bot` dentro de
`setUpModule` (igual que `test_api.py`, para no fijar las rutas de
`panel.config` antes que `test_panel`) y se doblan las funciones de BD.

Ejecución:  python tests/test_api_bot.py
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

api_bot = None
USER = {"id": 1, "email": "ana@ecc-sa.com.mx", "nombre": "Ana",
        "perms": {"AccesoConfiguracion": True}}

CLAVES_ESPERADAS = {"sarcasmo", "roast_grupo", "volt", "volt_cada", "ia",
                    "asistencia"}


def setUpModule():
    global api_bot
    import tempfile
    raiz = tempfile.mkdtemp(prefix="panel_api_bot_")
    os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(raiz, "data"))
    from panel import api_bot as modulo
    api_bot = modulo


class _Dobles(unittest.TestCase):
    """Guarda y restaura las funciones que cada prueba sustituye."""

    def setUp(self):
        self._guardados = []
        self.guardado = {}
        self.doblar(api_bot, "_sesion", lambda _h: (USER, None))
        self.doblar(api_bot.db, "set_config_values",
                    lambda valores: self.guardado.update(valores) or (True, ""))
        self.doblar(api_bot.db, "get_config_values", lambda _claves: {})
        self.doblar(api_bot.db, "log_activity", lambda *a: True)

    def tearDown(self):
        for obj, nombre, original in reversed(self._guardados):
            setattr(obj, nombre, original)

    def doblar(self, obj, nombre, valor):
        self._guardados.append((obj, nombre, getattr(obj, nombre)))
        setattr(obj, nombre, valor)

    def leer(self):
        codigo, datos = api_bot._get(None, {}, {})
        self.assertEqual(codigo, 200)
        return datos


class GetTest(_Dobles):
    def test_devuelve_los_seis_con_sus_defaults(self):
        datos = self.leer()
        self.assertEqual(set(datos), CLAVES_ESPERADAS)
        self.assertIs(datos["sarcasmo"], True)
        self.assertIs(datos["roast_grupo"], True)
        self.assertIs(datos["volt"], True)
        self.assertEqual(datos["volt_cada"], 12)
        self.assertIs(datos["ia"], False)       # la IA viene apagada
        self.assertIs(datos["asistencia"], True)

    def test_lee_lo_guardado(self):
        self.doblar(api_bot.db, "get_config_values",
                    lambda _claves: {"wa_volt_cada": "20",
                                     "wa_bot_ia": "1",
                                     "wa_bot_sarcasmo": "0"})
        datos = self.leer()
        self.assertEqual(datos["volt_cada"], 20)
        self.assertIs(datos["ia"], True)
        self.assertIs(datos["sarcasmo"], False)


class PostTest(_Dobles):
    def test_guarda_booleano(self):
        codigo, datos = api_bot._post(
            None, {}, {"clave": "sarcasmo", "valor": 0})
        self.assertEqual(codigo, 200, datos)
        self.assertTrue(datos["ok"])
        self.assertEqual(self.guardado, {"wa_bot_sarcasmo": "0"})

    def test_guarda_numero(self):
        codigo, datos = api_bot._post(
            None, {}, {"clave": "wa_volt_cada", "valor": "25"})
        self.assertEqual(codigo, 200, datos)
        self.assertEqual(self.guardado, {"wa_volt_cada": "25"})

    def test_acepta_true_y_la_clave_larga(self):
        codigo, datos = api_bot._post(
            None, {}, {"clave": "wa_bot_ia", "valor": True})
        self.assertEqual(codigo, 200, datos)
        self.assertEqual(self.guardado, {"wa_bot_ia": "1"})

    def test_numero_fuera_de_rango_400(self):
        for valor in (0, 101):
            codigo, datos = api_bot._post(
                None, {}, {"clave": "volt_cada", "valor": valor})
            self.assertEqual(codigo, 400, datos)
        self.assertEqual(self.guardado, {})

    def test_numero_no_entero_400(self):
        codigo, datos = api_bot._post(
            None, {}, {"clave": "volt_cada", "valor": "doce"})
        self.assertEqual(codigo, 400)
        self.assertIn("número entero", datos["error"])

    def test_booleano_invalido_400(self):
        codigo, datos = api_bot._post(
            None, {}, {"clave": "sarcasmo", "valor": "quizá"})
        self.assertEqual(codigo, 400)
        self.assertIn("1 (sí) o 0 (no)", datos["error"])
        self.assertEqual(self.guardado, {})

    def test_clave_desconocida_o_secreto_400(self):
        for clave in ("inventada", "openwa_api_key", ""):
            codigo, datos = api_bot._post(
                None, {}, {"clave": clave, "valor": 1})
            self.assertEqual(codigo, 400, (clave, datos))
        self.assertEqual(self.guardado, {})

    def test_falta_valor_400(self):
        codigo, datos = api_bot._post(None, {}, {"clave": "sarcasmo"})
        self.assertEqual(codigo, 400)
        self.assertIn("valor", datos["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
