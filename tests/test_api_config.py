"""
tests/test_api_config.py — API JSON de Configuración, Apps y Avisos
==================================================================
Sin SQL Server, sin supervisord y sin servidor HTTP: se importa
`panel.api_config` dentro de `setUpModule` (igual que `test_api.py`, para no
fijar las rutas de `panel.config` antes que `test_panel`) y se doblan las
funciones que tocan BD, entorno y workers.

Ejecución:  python tests/test_api_config.py
"""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

api_config = None
USER = {"id": 1, "email": "ana@ecc-sa.com.mx", "nombre": "Ana",
        "perms": {"AccesoConfiguracion": True, "AccesoAppConfig": True}}

CATALOGO = [
    {"Id": 1, "App": "HUB", "Clave": "tipo_cambio_usd",
     "Titulo": "Tipo de cambio", "Descripcion": "Lo escribe el worker",
     "Tipo": "readonly", "Unidad": "", "Orden": 10, "Valor": "17.14"},
    {"Id": 2, "App": "HUB", "Clave": "telegram_bot_token",
     "Titulo": "Token de Telegram", "Descripcion": "",
     "Tipo": "secret", "Unidad": "", "Orden": 20, "Valor": "SECRETO-APPS"},
    {"Id": 3, "App": "admon", "Clave": "asistencia_calcular_auto",
     "Titulo": "Cálculo automático", "Descripcion": "",
     "Tipo": "bool", "Unidad": "", "Orden": 20, "Valor": "1"},
]


def setUpModule():
    global api_config
    import tempfile
    raiz = tempfile.mkdtemp(prefix="panel_api_config_")
    os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(raiz, "data"))
    from panel import api_config as modulo
    api_config = modulo


class _Dobles(unittest.TestCase):
    """Guarda y restaura las funciones que cada prueba sustituye."""

    def setUp(self):
        self._guardados = []

    def tearDown(self):
        for obj, nombre, original in reversed(self._guardados):
            setattr(obj, nombre, original)

    def doblar(self, obj, nombre, valor):
        self._guardados.append((obj, nombre, getattr(obj, nombre)))
        setattr(obj, nombre, valor)

    def sesion(self):
        """Sesión ya autenticada (sin tocar `panel.auth`)."""
        self.doblar(api_config, "_sesion", lambda _h: (USER, None))
        self.doblar(api_config, "_sesion_apps", lambda _h: (USER, None))


class ConfigTest(_Dobles):
    def test_get_oculta_secretos(self):
        self.sesion()
        self.doblar(api_config.workers, "build_status",
                    lambda: {"programs": [], "totals": {}})
        self.doblar(api_config.envconf, "read_env", lambda _w: {})
        self.doblar(api_config.db, "get_config_values",
                    lambda _claves: {"banxico_token": "SECRETO-CONFIG"})

        codigo, datos = api_config._config_get(None, {}, {})
        self.assertEqual(codigo, 200)
        self.assertNotIn("SECRETO-CONFIG", json.dumps(datos))

        worker = next(w for w in datos["workers"]
                      if w["nombre"] == "tipo_cambio_worker")
        campo = next(c for g in worker["grupos"] for c in g["campos"]
                     if c["clave"] == "banxico_token")
        self.assertEqual(campo["tipo"], "secret")
        self.assertEqual(campo["valor"], "")
        self.assertTrue(campo["tiene_valor"])
        self.assertEqual(worker["estado"], "NO_HABILITADO")

    def test_post_guarda_con_el_merge_existente(self):
        self.sesion()
        guardado = {}
        self.doblar(api_config.db, "set_config_values",
                    lambda valores: guardado.update(valores) or (True, ""))
        self.doblar(api_config.db, "log_activity", lambda *a: True)

        codigo, datos = api_config._config_post(
            None, {}, {"valores": {"banxico_token": "NUEVO"}})
        self.assertEqual(codigo, 200, datos)
        self.assertTrue(datos["ok"])
        self.assertEqual(guardado, {"banxico_token": "NUEVO"})

    def test_post_error_de_validacion_400(self):
        self.sesion()
        self.doblar(api_config.db, "set_config_values", lambda _v: (True, ""))
        codigo, datos = api_config._config_post(
            None, {}, {"worker": "bing_worker",
                       "valores": {"CRON_BING_INTERVAL": "no-es-numero"}})
        self.assertEqual(codigo, 400)
        self.assertIn("número entero", datos["error"])

    def test_post_sin_valores_400(self):
        self.sesion()
        codigo, datos = api_config._config_post(None, {}, {})
        self.assertEqual(codigo, 400)
        self.assertIn("valores", datos["error"])


class AppsTest(_Dobles):
    def _catalogo(self):
        self.sesion()
        self.doblar(api_config.db, "config_catalog_exists", lambda: True)
        self.doblar(api_config.db, "get_config_catalog",
                    lambda: [dict(f) for f in CATALOGO])
        self.doblar(api_config.db, "get_all_config_values",
                    lambda: {"suelta_1": "valor-que-no-se-muestra"})

    def test_get_lista_por_app_y_no_expone_secretos(self):
        self._catalogo()
        codigo, datos = api_config._apps_get(None, {}, {})
        self.assertEqual(codigo, 200)
        self.assertEqual([a["clave"] for a in datos["apps"]], ["HUB", "admon"])
        hub = datos["apps"][0]
        secreto = next(c for c in hub["claves"]
                       if c["clave"] == "telegram_bot_token")
        self.assertEqual(secreto["valor"], "")
        self.assertTrue(secreto["tiene_valor"])
        self.assertNotIn("SECRETO-APPS", json.dumps(datos))

        libre = next(s for s in datos["sin_clasificar"]
                     if s["clave"] == "suelta_1")
        self.assertNotIn("valor", libre, "sin clasificar no muestra valor")

    def test_post_clasifica(self):
        self._catalogo()
        llamadas = []
        self.doblar(api_config.db, "add_catalog_item",
                    lambda *a: llamadas.append(a) or (True, ""))
        self.doblar(api_config.db, "log_activity", lambda *a: True)

        codigo, datos = api_config._apps_post(
            None, {}, {"accion": "clasificar", "clave": "suelta_1",
                       "app": "HUB", "tipo": "text"})
        self.assertEqual(codigo, 200, datos)
        self.assertTrue(datos["ok"])
        self.assertEqual(llamadas[0][:2], ("HUB", "suelta_1"))

    def test_post_clasificar_sin_app_400(self):
        self._catalogo()
        codigo, datos = api_config._apps_post(
            None, {}, {"accion": "clasificar", "clave": "suelta_1", "app": ""})
        self.assertEqual(codigo, 400)
        self.assertIn("app", datos["error"])

    def test_post_accion_desconocida_400(self):
        self._catalogo()
        codigo, datos = api_config._apps_post(None, {}, {"accion": "borrar"})
        self.assertEqual(codigo, 400)
        self.assertIn("Acción no válida", datos["error"])


class AvisosTest(_Dobles):
    def test_get_avisos_y_contexto(self):
        self.sesion()
        self.doblar(api_config.workers, "build_status", lambda: {
            "programs": [{"name": "demo", "state": "RUNNING", "app": "HUB"}],
            "totals": {"running": 1, "total": 1},
            "timezone": "UTC", "supervisor_error": None,
        })
        self.doblar(api_config.workers, "shell_check",
                    lambda: {"ok": True, "shell_version": "1.0",
                             "revisado": "hoy"})
        self.doblar(api_config.workers, "versiones_check",
                    lambda: {"ok": True, "revisado": "hoy"})

        codigo, datos = api_config._avisos_get(None, {}, {})
        self.assertEqual(codigo, 200)
        self.assertGreaterEqual(len(datos["avisos"]), 2)
        self.assertIn("Shell", {a["titulo"] for a in datos["avisos"]})
        self.assertTrue(all("detalle" in a and "ok" in a
                            for a in datos["avisos"]))
        self.assertEqual(datos["contexto"]["zona"], "UTC")
        self.assertEqual(datos["grupos"][0]["nombre"], "HUB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
