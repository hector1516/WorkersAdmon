"""Tests para legends_core.py — ECCSA Legends (WorkersAdmon).

Verifica que los puntos, niveles y funciones core coinciden con lo documentado
en field/docs/LEGENDS_METRICS.md.
"""
import sys
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "api"))


class TestLegendsCore(unittest.TestCase):
    """Tests para el módulo compartido legends_core."""

    def setUp(self):
        from legends_core import METRICAS, NIVELES, NIVEL_POR_NOMBRE, ICONO_NIVEL
        self.METRICAS = METRICAS
        self.NIVELES = NIVELES
        self.NIVEL_POR_NOMBRE = NIVEL_POR_NOMBRE
        self.ICONO_NIVEL = ICONO_NIVEL

    def test_puntos_reporte_firmado(self):
        self.assertEqual(self.METRICAS["reporte_firmado"], 10)

    def test_puntos_kilometro(self):
        self.assertEqual(self.METRICAS["kilometro"], 5)

    def test_puntos_ticket_oxxogas(self):
        self.assertEqual(self.METRICAS["ticket_oxxogas"], 3)

    def test_puntos_vale_generado(self):
        self.assertEqual(self.METRICAS["vale_generado"], -2)

    def test_puntos_firma_remota(self):
        self.assertEqual(self.METRICAS["firma_remota"], 8)

    def test_puntos_racha_dia(self):
        self.assertEqual(self.METRICAS["racha_dia"], 3)

    def test_puntos_comida_reporte(self):
        self.assertEqual(self.METRICAS["comida_reporte"], -1)

    def test_puntos_servicio(self):
        self.assertEqual(self.METRICAS["servicio"], 0)

    def test_niveles_ordenados(self):
        mins = [n["min"] for n in self.NIVELES]
        self.assertEqual(mins, sorted(mins))

    def test_nivel_bronce_en_cero(self):
        self.assertEqual(self.NIVELES[0]["nombre"], "Bronce")
        self.assertEqual(self.NIVELES[0]["min"], 0)

    def test_nivel_por_nombre_completo(self):
        for n in self.NIVELES:
            self.assertIn(n["nombre"], self.NIVEL_POR_NOMBRE)

    def test_icono_por_nivel(self):
        for n in self.NIVELES:
            self.assertIn(n["nombre"], self.ICONO_NIVEL)

    def test_nivel_de_bronce(self):
        from legends_core import nivel_de
        result = nivel_de(0)
        self.assertEqual(result["nombre"], "Bronce")

    def test_nivel_de_diamante(self):
        from legends_core import nivel_de
        result = nivel_de(2000)
        self.assertEqual(result["nombre"], "Diamante")

    def test_nivel_de_leyenda(self):
        from legends_core import nivel_de
        result = nivel_de(5000)
        self.assertEqual(result["nombre"], "Leyenda")

    def test_siguiente_nivel(self):
        from legends_core import siguiente_nivel
        result = siguiente_nivel(0)
        self.assertIsNotNone(result)
        self.assertEqual(result["nombre"], "Plata")

    def test_progreso_nivel(self):
        from legends_core import progreso_nivel
        result = progreso_nivel(125)
        self.assertIn("pct", result)
        self.assertGreater(result["pct"], 0)

    def test_guia_no_vacia(self):
        from legends_core import guia
        result = guia()
        self.assertGreater(len(result), 0)

    def test_niveles_publicos(self):
        from legends_core import niveles_publicos
        result = niveles_publicos()
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()