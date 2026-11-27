"""
tests/test_ui.py — la base visual del panel (panel/DESIGN.md)
==============================================================
Comprueba el contrato del layout y de los iconos sin base de datos y sin
servidor: `page()` es una función pura que devuelve HTML, y `icono()` devuelve
SVG. Se fija el entorno antes de importar `panel.config` para que el paquete no
cree carpetas de datos en el repo (mismo patrón que tests/test_panel.py).

Ejecución:  python -m unittest tests.test_ui -v
"""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Hay que fijarlo ANTES de importar panel.config: get_data_dir() crea carpetas.
_ROMPER = tempfile.mkdtemp(prefix="panel_ui_")
os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(_ROMPER, "data"))

from panel import config, templates  # noqa: E402
from panel.icons import NOMBRES, icono  # noqa: E402

USER = {"nombre": "Admin", "email": "admin@ecc-sa.com.mx", "perms": {}}
# Usuario con todos los permisos: la barra inferior solo pinta enlace si puede
# abrir el destino (sin permiso sale apagado y sin href).
PERMS = {"AccesoWhatsapp": True, "AccesoAppConfig": True, "AccesoTelegram": True,
         "AccesoConfigurarCorreo": True, "AccesoConfigAI": True,
         "AccesoDeteccionRed": True, "AccesoUsuarios": True}
TODO = {"nombre": "Admin", "email": "admin@ecc-sa.com.mx", "perms": PERMS}


def _page(**kw):
    kw.setdefault("user", USER)
    return templates.page("estado", "<p>cuerpo de prueba</p>", **kw)


class LayoutTest(unittest.TestCase):
    def test_estructura_de_app(self):
        html = _page()
        self.assertIn('<div class="app">', html)
        self.assertIn('<aside class="rail">', html)
        self.assertIn('<main class="main">', html)
        self.assertIn('<header class="topbar">', html)
        self.assertIn('<div class="content">', html)

    def test_css_propio_y_sin_shell(self):
        html = _page()
        # El CSS viaja embebido (el panel no sirve /static) pero declarando su
        # origen: apple.css, no el shell.
        self.assertIn("apple.css", html)
        self.assertNotIn("shell.css", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)
        self.assertIn("--font: -apple-system", html)
        self.assertIn("--accent", html)

    def test_meta_de_la_pagina(self):
        html = _page()
        self.assertIn('name="viewport"', html)
        self.assertIn("viewport-fit=cover", html)
        self.assertIn('name="color-scheme" content="light dark"', html)
        self.assertIn('name="theme-color"', html)
        self.assertIn("prefers-color-scheme: light", html)
        self.assertIn("prefers-color-scheme: dark", html)

    def test_iconos_svg_en_linea(self):
        html = _page()
        self.assertIn("<svg", html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('stroke-width="1.75"', html)
        self.assertIn("currentColor", html)

    def test_destino_activo_lleva_aria_current(self):
        html = _page()
        # Estado es el activo y va marcado en las dos navegaciones.
        self.assertIn(
            '<a class="rail__link nav-item is-active" href="/estado" '
            'aria-current="page">', html)
        # Un destino inactivo no lleva el atributo.
        self.assertNotIn('href="/logs" aria-current', html)

    def test_barra_inferior_con_cinco_destinos_y_hoja_mas(self):
        html = templates.page("estado", "<p>x</p>", user=TODO)
        self.assertIn('<nav class="rail rail--bottom"', html)
        self.assertIn('<details class="rail__more"', html)
        self.assertIn('class="sheet rail__sheet"', html)
        for href in ("/estado", "/logs", "/whatsapp", "/notificaciones"):
            self.assertIn(f'href="{href}"', html)
        # El quinto destino de la barra es «Más».
        self.assertIn(">Más</span>", html)

    def test_marca_usuario_y_salir(self):
        html = _page()
        self.assertIn('class="logo-link" href="/"', html)
        self.assertIn(">Workers</span>", html)
        self.assertIn("Admin", html)
        self.assertIn('href="/logout"', html)
        self.assertIn("Salir", html)

    def test_flash_es_toast_en_el_flujo(self):
        html = _page(flash_ok="Guardado", flash_err="Falló la red")
        self.assertIn('class="toast toast--ok"', html)
        self.assertIn('class="toast toast--err"', html)
        self.assertIn("Guardado", html)
        self.assertIn("Falló la red", html)
        # Ya no hay banner del shell en el layout.
        self.assertNotIn('class="sync-header', html)

    def test_volver_aparece_fuera_de_la_home(self):
        html = _page()
        self.assertIn('class="btn btn-sm btn-secondary modulo-back', html)
        home = templates.page("inicio", "<p>menú</p>", user=USER)
        self.assertNotIn('class="btn btn-sm btn-secondary modulo-back', home)

    def test_titulo_y_subtitulo(self):
        html = _page(subtitle="datos en <code>/data</code>")
        self.assertIn('<h1 class="topbar__title">Estado</h1>', html)
        # El subtítulo conserva <code>: escaparlo mostraba la etiqueta literal.
        self.assertIn("datos en <code>/data</code>", html)

    def test_paginas_de_acceso_comparten_estilo(self):
        login = templates.login_page(csrf="x")
        offline = templates.offline_page()
        prohibido = templates.forbidden_page("Sin permiso")
        for html in (login, offline, prohibido):
            self.assertIn("apple.css", html)
            self.assertNotIn("fonts.googleapis.com", html)
            self.assertIn('name="viewport"', html)
        self.assertIn("auth__card", login)
        self.assertIn('class="app"', prohibido)      # sin permiso usa page()
        self.assertIn("Sin conexión", offline)
        self.assertIn("Sin permiso", prohibido)

    def test_login_conserva_formulario_y_passkey(self):
        html = templates.login_page(error="Credenciales", csrf="tok123")
        self.assertIn('action="/login"', html)
        self.assertIn('name="csrf" value="tok123"', html)
        self.assertIn('id="pk_btn"', html)
        self.assertIn("Credenciales", html)


class IconosTest(unittest.TestCase):
    def test_nombres_del_contrato(self):
        esperados = {
            "dashboard", "logs", "settings", "apps", "bell", "clock", "whatsapp",
            "chat", "users", "check", "alert", "refresh", "search", "chevron",
            "plus", "logout", "send", "trash", "play", "pause", "folder", "mail",
            "car", "ticket",
        }
        self.assertEqual(set(NOMBRES), esperados)

    def test_svg_de_whatsapp(self):
        svg = icono("whatsapp")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('aria-hidden="true"', svg)
        self.assertIn('stroke="currentColor"', svg)

    def test_tamano(self):
        self.assertIn('width="18"', icono("logs", 18))
        self.assertIn('width="20"', icono("logs"))
        self.assertIn('width="20"', icono("logs", "no-numero"))

    def test_desconocido_no_lanza(self):
        self.assertEqual(icono("nope"), "")
        self.assertEqual(icono(None), "")


if __name__ == "__main__":
    unittest.main()
