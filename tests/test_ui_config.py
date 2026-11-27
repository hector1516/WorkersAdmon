"""
tests/test_ui_config.py — Configuración, Apps y Avisos con el sistema Apple
===========================================================================
Comprueba el contrato visual de las tres vistas y, sobre todo, lo que no se
puede romper con un rediseño:

  * cada vista pinta tarjetas (`.card`), campos (`.field`) y un botón primario;
  * los valores secretos NO viajan al HTML;
  * los `name` de los POST son los mismos que antes;
  * no se anidan formularios (un `<form>` por bloque).

Sin base de datos ni servidor: los módulos que tocan BD se sustituyen por
dobles y `page()` se reemplaza por la identidad para revisar el cuerpo.
Ejecución:  python -m unittest tests.test_ui_config -v
"""
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Hay que fijarlo ANTES de importar panel.config: get_data_dir() crea carpetas.
_ROMPER = tempfile.mkdtemp(prefix="panel_uiconf_")
os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(_ROMPER, "data"))

import notif_dispatch as nd  # noqa: E402

from panel import spec  # noqa: E402
from panel.views import apps as apps_view  # noqa: E402
from panel.views import avisos as avisos_view  # noqa: E402
from panel.views import config as config_view  # noqa: E402

USER = {"nombre": "Admin", "email": "admin@ecc-sa.com.mx", "perms": {}}
SECRETO_CFG = "SECRETO-CONFIG-123"
SECRETO_APP = "SECRETO-APPS-456"

# Catálogo mínimo con los cinco tipos de valor.
CATALOGO = [
    {"Id": 1, "App": "HUB", "Clave": "tipo_cambio_usd",
     "Titulo": "Tipo de cambio USD/MXN", "Descripcion": "Lo escribe el worker",
     "Tipo": "readonly", "Unidad": "", "Orden": 10, "Valor": "17.1409"},
    {"Id": 2, "App": "HUB", "Clave": "telegram_bot_token",
     "Titulo": "Token del bot de Telegram", "Descripcion": "",
     "Tipo": "secret", "Unidad": "", "Orden": 20, "Valor": SECRETO_APP},
    {"Id": 3, "App": "HUB", "Clave": "net_scan_interval_seg",
     "Titulo": "Intervalo de escaneo de red", "Descripcion": "",
     "Tipo": "number", "Unidad": "seg", "Orden": 50, "Valor": "180"},
    {"Id": 4, "App": "admon", "Clave": "asistencia_calcular_auto",
     "Titulo": "Asistencia: cálculo automático", "Descripcion": "",
     "Tipo": "bool", "Unidad": "", "Orden": 20, "Valor": "1"},
]


def _page_identidad(active, body, **_kw):
    """page() real arma todo el layout; aquí solo interesa el cuerpo."""
    return body


def _no_anidados(caso, html):
    """Cuenta formularios abiertos y cerrados; nunca más de uno abierto."""
    profundidad = 0
    for m in re.finditer(r"<form\b|</form\s*>", html):
        if m.group(0).startswith("<form"):
            profundidad += 1
            caso.assertLessEqual(profundidad, 1, "formularios anidados")
        else:
            profundidad -= 1
            caso.assertGreaterEqual(profundidad, 0, "</form> sin apertura")
    caso.assertEqual(profundidad, 0, "formulario sin cerrar")


class ConfiguracionTest(unittest.TestCase):
    """Una tarjeta por worker, campos por origen y secretos en blanco."""

    def setUp(self):
        self._guardados = {}
        self._parchear(config_view, "page", _page_identidad)
        self._parchear(config_view.envconf, "read_env", lambda _w: {})
        self._parchear(config_view.envconf, "load_overrides", lambda: {})
        self._parchear(config_view.db, "get_config_values",
                        lambda claves: {"banxico_token": SECRETO_CFG})
        self._parchear(config_view.workers, "log_path", lambda _n: "")

    def _parchear(self, obj, nombre, valor):
        self._guardados[(obj, nombre)] = getattr(obj, nombre)
        setattr(obj, nombre, valor)

    def tearDown(self):
        for (obj, nombre), valor in self._guardados.items():
            setattr(obj, nombre, valor)

    def test_renderiza_tarjetas_campos_y_primario(self):
        html = config_view.render({"programs": []}, user=USER)
        self.assertIn('class="card"', html)
        self.assertIn('class="field cfg-field"', html)
        self.assertIn("btn--primary", html)
        # agrupación por origen, con su etiqueta
        for etiqueta in ("ENTORNO", "HUB_CONFIG", "CÓDIGO"):
            self.assertIn(etiqueta, html)
        self.assertIn('id="cfg-bing_worker"', html)

    def test_tarjeta_del_bot_de_whatsapp(self):
        """El pseudo-worker `wa_bot` tiene tarjeta propia, con sus seis claves."""
        html = config_view.render({"programs": []}, user=USER)
        self.assertIn('id="cfg-wa_bot"', html)
        self.assertIn("Bot de WhatsApp (Volt)", html)
        for clave in ("wa_bot_sarcasmo", "wa_roast_grupo", "wa_volt_activo",
                      "wa_volt_cada", "wa_bot_ia", "wa_asist_activo"):
            self.assertIn(f'name="{clave}"', html)
        self.assertIn('action="/configuracion/wa_bot"', html)

    def test_los_secretos_no_se_pintan(self):
        html = config_view.render({"programs": []}, user=USER)
        self.assertNotIn(SECRETO_CFG, html)
        # el campo existe, vacío y con autocompletado apagado
        self.assertIn('name="banxico_token"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertNotIn('value="' + SECRETO_CFG + '"', html)

    def test_nombres_de_post_y_un_boton_por_seccion(self):
        html = config_view.render({"programs": []}, user=USER)
        # los inputs conservan su nombre (el POST no cambia)
        self.assertIn('name="CRON_BING_INTERVAL"', html)
        self.assertIn('name="CRON_TC_HORA"', html)
        self.assertIn('action="/configuracion/bing_worker"', html)
        self.assertIn('action="/configuracion/telegram_worker/probar"', html)
        self.assertIn('name="csrf"', html)

    def test_un_formulario_por_bloque(self):
        html = config_view.render({"programs": []}, user=USER)
        con_campos = sum(1 for w in spec.all_workers() if spec.editable_fields(w))
        # más la tarjeta propia del bot (pseudo-worker fuera de all_workers())
        con_campos += 1 if spec.editable_fields("wa_bot") else 0
        con_prueba = sum(
            1 for w in spec.all_workers()
            if any(f.get("test") for f in spec.spec_for(w)))
        self.assertEqual(html.count("<form"), con_campos + con_prueba)
        self.assertEqual(html.count("</form>"), con_campos + con_prueba)
        _no_anidados(self, html)


class AppsTest(unittest.TestCase):
    """Tarjetas por app, claves con su tipo y guardado por bloque."""

    def setUp(self):
        self._guardados = {}
        self._parchear(apps_view, "page", _page_identidad)
        self._parchear(apps_view.db, "config_catalog_exists", lambda: True)
        self._parchear(apps_view.db, "get_config_catalog",
                        lambda: [dict(f) for f in CATALOGO])
        self._parchear(apps_view.db, "get_all_config_values",
                       lambda: {"suelta_1": "x"})

    def _parchear(self, obj, nombre, valor):
        self._guardados[(obj, nombre)] = getattr(obj, nombre)
        setattr(obj, nombre, valor)

    def tearDown(self):
        for (obj, nombre), valor in self._guardados.items():
            setattr(obj, nombre, valor)

    def test_renderiza_tarjetas_campos_y_primario(self):
        html = apps_view.render(user=USER)
        self.assertIn('class="card"', html)
        self.assertIn('class="field cfg-field"', html)
        self.assertIn("btn--primary", html)
        self.assertIn('id="app-HUB"', html)
        self.assertIn('id="app-admon"', html)

    def test_los_secretos_no_se_pintan(self):
        html = apps_view.render(user=USER)
        self.assertNotIn(SECRETO_APP, html)
        self.assertIn('name="val2"', html)          # el campo secreto existe
        self.assertIn('autocomplete="new-password"', html)
        # las claves sin clasificar tampoco muestran su valor
        self.assertIn("suelta_1", html)
        self.assertNotIn('value="x"', html)

    def test_las_filas_conservan_sus_nombres_de_post(self):
        html = apps_view.render(user=USER)
        for name in ('name="t1"', 'name="val2"', 'name="val3"', 'name="tipo1"',
                     'name="d1"',
                     'name="edit"', 'name="del"', 'name="save_all"',
                     'name="grupo"', 'name="app"', 'name="k0"',
                     'name="app0"', 'name="tit0"', 'name="tipo0"',
                     'name="clasificar"', 'name="clave"', 'name="add"',
                     'name="csrf"'):
            self.assertIn(name, html, f"falta {name}")
        # el número conserva inputmode
        self.assertIn('inputmode="numeric"', html)

    def test_un_formulario_por_bloque(self):
        html = apps_view.render(user=USER)
        # 2 apps + sin clasificar + alta
        self.assertEqual(html.count("<form"), 4)
        self.assertEqual(html.count("</form>"), 4)
        _no_anidados(self, html)


class AvisosTest(unittest.TestCase):
    """Lista de avisos con estado, destinatarios y horario."""

    def setUp(self):
        self._guardados = {}
        self._parchear(avisos_view, "page", _page_identidad)
        self._parchear(avisos_view.nd, "usuarios_con_suscripcion",
                       lambda **_kw: [])
        self._parchear(avisos_view.db, "get_config_values",
                       lambda claves: {c: "1" for c in claves
                                       if c in [f"avisos_push_{t.lower()}"
                                                for t in nd.TIPOS]})
        self._parchear(avisos_view.db, "get_suscripciones_por_app",
                       lambda: {nd.APP_ADMON: 2})
        self._parchear(avisos_view.db, "get_avisos_en_cola", lambda _a: 3)
        self._parchear(avisos_view.db, "get_plataformas_suscripciones",
                       lambda _a: [("iOS", 2)])
        self._parchear(avisos_view.db, "get_usuarios_con_permiso_sin_suscribir",
                       lambda _p, _a: 0)

    def _parchear(self, obj, nombre, valor):
        self._guardados[(obj, nombre)] = getattr(obj, nombre)
        setattr(obj, nombre, valor)

    def tearDown(self):
        for (obj, nombre), valor in self._guardados.items():
            setattr(obj, nombre, valor)

    def test_renderiza_tarjetas_campos_y_primario(self):
        html = avisos_view.render(user=USER)
        self.assertIn('class="card"', html)
        self.assertIn('class="field cfg-field"', html)
        self.assertIn("btn--primary", html)
        self.assertIn("Avisos en cola", html)
        self.assertIn("persona(s)", html)
        self.assertIn("lunes a viernes", html)
        self.assertIn("18:30", html)
        self.assertIn("Último resumen", html)

    def test_avisa_cuando_no_le_llega_a_nadie(self):
        html = avisos_view.render(user=USER)
        self.assertIn("No le llegaría a nadie", html)

    def test_un_interruptor_por_tipo(self):
        html = avisos_view.render(user=USER)
        self.assertEqual(html.count("<form"), len(nd.TIPOS))
        self.assertEqual(html.count("</form>"), len(nd.TIPOS))
        _no_anidados(self, html)
        for tipo in nd.TIPOS:
            self.assertIn(f'action="/avisos/{tipo}/', html)


if __name__ == "__main__":
    unittest.main()
