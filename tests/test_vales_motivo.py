"""El motivo de un vale fallido tiene que llegar a la app con un código útil.

Antes el porqué solo vivía en el log del worker: en la app se veía la
solicitud atorada y nada más. Estas pruebas vigilan que el mensaje del
generador se clasifique en un código que Field sabe traducir.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cron_vales_autogenerate import _clasificar_motivo  # noqa: E402


class ClasificarMotivo(unittest.TestCase):
    def test_sin_saldo(self):
        self.assertEqual(
            _clasificar_motivo("Saldo insuficiente para generar vale."), "SIN_SALDO")

    def test_importe_no_escrito(self):
        self.assertEqual(
            _clasificar_motivo("No se pudo escribir el importe en el formulario"),
            "MONTO_NO_FIJADO")

    def test_kilometros(self):
        self.assertEqual(
            _clasificar_motivo("Los kilometros no avanzaron desde el ultimo vale"),
            "KM_MENORES")

    def test_sin_sesion(self):
        self.assertEqual(
            _clasificar_motivo("No se pudo iniciar sesión en Go Vale."),
            "GOVALE_SIN_SESION")

    def test_desconocido_no_queda_vacio(self):
        self.assertEqual(_clasificar_motivo(""), "REINTENTO")
        self.assertEqual(_clasificar_motivo(None), "REINTENTO")


if __name__ == "__main__":
    unittest.main()
