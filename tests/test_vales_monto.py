"""La regla del monto de los vales, sin navegador.

De aquí salieron dos incidentes: un vale por $5,709 (todo el saldo) y nueve de
$615, con la app y el aviso diciendo $500. Estas pruebas fijan la política
($500), la comparación contra lo emitido y la lectura del importe del listado.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oxxogas_vales_automation import (  # noqa: E402
    _leer_monto_emitido, _monto_coincide, _monto_permitido_de,
)

LISTADO = """
Vales
Filtros
856492448
24/11/2026
Importe original
$5,709.00
Saldo actual:
$5,709.00
Empresa: ECCSA Automation
Contacto: Ricardo
"""


class MontoPermitido(unittest.TestCase):
    def test_lee_el_valor_configurado(self):
        self.assertEqual(_monto_permitido_de("500"), 500.0)
        self.assertEqual(_monto_permitido_de("1500"), 1500.0)
        self.assertEqual(_monto_permitido_de("500,50"), 500.5)

    def test_si_falta_o_esta_mal_usa_500(self):
        self.assertEqual(_monto_permitido_de(None), 500.0)
        self.assertEqual(_monto_permitido_de(""), 500.0)
        self.assertEqual(_monto_permitido_de("basura"), 500.0)


class Coincide(unittest.TestCase):
    def test_igual_es_verdad(self):
        self.assertTrue(_monto_coincide(500, 500))
        self.assertTrue(_monto_coincide("500", 500))
        self.assertTrue(_monto_coincide(500.01, 500))  # un centavo

    def test_distinto_es_falso(self):
        self.assertFalse(_monto_coincide(5709, 500))
        self.assertFalse(_monto_coincide(615, 500))
        self.assertFalse(_monto_coincide(None, 500))
        self.assertFalse(_monto_coincide("x", 500))


class LeerMontoEmitido(unittest.TestCase):
    def test_saca_el_importe_del_folio(self):
        self.assertEqual(_leer_monto_emitido(LISTADO, "856492448"), 5709.0)

    def test_folio_que_no_esta_devuelve_none(self):
        self.assertIsNone(_leer_monto_emitido(LISTADO, "123456789"))

    def test_texto_vacio_no_revienta(self):
        self.assertIsNone(_leer_monto_emitido("", "856492448"))
        self.assertIsNone(_leer_monto_emitido(None, "856492448"))


if __name__ == "__main__":
    unittest.main()
