"""La sesión de Go Vale no puede tumbar al worker.

Dos procesos escriben el archivo (generador y sincronizador). Una escritura a
medias lo dejó con dos JSON pegados y el sincronizador murió en cada ciclo con
"Extra data: line 195": nunca llegaba a hacer login. Estas pruebas fijan que un
archivo roto se ignore y que el guardado sea atómico y verificable.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import govale_sesion as gs  # noqa: E402


class _ContextoFalso:
    """Imita lo único que se usa de Playwright: storage_state(path=...)."""

    def __init__(self, contenido):
        self.contenido = contenido

    def storage_state(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.contenido)


class SesionGoVale(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original = gs.SESION
        gs.SESION = os.path.join(self.dir, "govale_session.json")

    def tearDown(self):
        gs.SESION = self.original

    def _escribir(self, texto):
        with open(gs.SESION, "w", encoding="utf-8") as f:
            f.write(texto)

    def test_archivo_con_dos_json_se_ignora(self):
        # El caso real del 22-nov: dos escrituras pegadas.
        self._escribir(json.dumps({"cookies": [], "origins": []}) * 2)
        self.assertIsNone(gs.sesion_guardada())

    def test_archivo_valido_se_usa(self):
        self._escribir(json.dumps({"cookies": [], "origins": []}) + " " * 200)
        self.assertEqual(gs.sesion_guardada(), gs.SESION)

    def test_sin_archivo_no_revienta(self):
        self.assertIsNone(gs.sesion_guardada())

    def test_json_sin_cookies_no_sirve(self):
        self._escribir(json.dumps({"otra": "cosa"}) + " " * 200)
        self.assertIsNone(gs.sesion_guardada())

    def test_guardar_reemplaza_y_deja_json_legible(self):
        self._escribir('{"viejo": true}' + " " * 200)
        ctx = _ContextoFalso(json.dumps({"cookies": [1], "origins": []}))
        self.assertTrue(gs.guardar(ctx))
        with open(gs.SESION, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cookies"], [1])
        self.assertFalse(os.path.exists(gs.SESION + f".{os.getpid()}.tmp"))

    def test_guardar_no_destruye_lo_bueno_si_falla(self):
        bueno = json.dumps({"cookies": [1], "origins": []}) + " " * 200
        self._escribir(bueno)
        # El navegador escribe algo incompleto: no debe pisar el bueno.
        self.assertFalse(gs.guardar(_ContextoFalso('{"cookies": [2]')))
        with open(gs.SESION, encoding="utf-8") as f:
            self.assertEqual(f.read(), bueno)


if __name__ == "__main__":
    unittest.main()
