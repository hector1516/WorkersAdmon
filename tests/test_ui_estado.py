"""
tests/test_ui_estado.py — Estado sin scroll y Logs con el sistema Apple
=====================================================================
Renderiza las dos vistas con dobles simples (sin SQL Server, sin supervisord y
sin tocar /var/log): `panel.views.workers.render()` y `logs_index()` son
funciones puras una vez que se sustituyen las lecturas.

Ejecución:  python -m unittest tests.test_ui_estado -v
"""
import datetime
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Antes de importar panel.config: get_data_dir() crea carpetas.
_ROMPER = tempfile.mkdtemp(prefix="panel_estado_")
os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(_ROMPER, "data"))

from panel import workers as pw                    # noqa: E402
from panel.views import workers as wv              # noqa: E402

USER = {"nombre": "Admin", "email": "admin@ecc-sa.com.mx", "perms": {}}


def _hace(horas):
    return (pw.now() - datetime.timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")


def _programa(nombre, estado="RUNNING", habilitado=True, latido=1.0,
              log_ok=True, protegido=False):
    return {
        "name": nombre, "state": estado, "enabled": habilitado,
        "pid": 10, "uptime_seconds": 60, "available": True,
        "last_run": _hace(latido) if latido is not None else None,
        "detail": "", "count": 1, "started_at": None, "running": estado == "RUNNING",
        "desc": "", "descripcion": "", "cadencia": "", "app": "HUB",
        "protected": protegido, "log_ok": log_ok,
    }


def _status(programas=None):
    programas = list(programas or [])
    return {
        "title": "Workers Admon",
        "now": "2026-11-26 12:00:00",
        "timezone": "UTC",
        "data_dir": "/data",
        "supervisor_error": None,
        "programs": programas,
        "totals": {
            "running": sum(1 for e in programas if e["state"] == "RUNNING"),
            "stopped": sum(1 for e in programas
                           if e["state"] in ("STOPPED", "EXITED", "NO_HABILITADO")),
            "errors": sum(1 for e in programas
                          if e["state"] in ("FATAL", "BACKOFF")),
            "enabled": sum(1 for e in programas if e["enabled"]),
            "available": len(programas),
            "total": len(programas),
        },
    }


CON_DATOS = [
    _programa("avisos", "RUNNING", latido=0.2),
    _programa("demo_on", "RUNNING", latido=30),      # latido viejo
    _programa("demo_off", "STOPPED", habilitado=False, latido=None, log_ok=False),
    _programa("status_web", "FATAL", protegido=True),
]


class _Base(unittest.TestCase):
    """Deja las lecturas externas en dobles: sin red, sin disco, sin BD."""

    def setUp(self):
        self._parches = [
            mock.patch.object(pw, "shell_check", return_value={
                "ok": True, "shell_version": "1.10.1", "app_version": "1.10.1",
                "revisado": "2026-11-26"}),
            mock.patch.object(pw, "versiones_check", return_value={
                "ok": True, "revisado": "2026-11-26"}),
            mock.patch.object(wv, "tail_log",
                              lambda nombre, lineas=120: ("linea 1\nERROR algo\n", None)),
            mock.patch.object(wv, "_log_bd",
                              return_value=('<div class="card"><div class="wcard-name">'
                                            'Eventos de los workers</div>'
                                            '<div class="muted" style="font-size:.85rem">'
                                            'Guardados en la base.</div></div>')),
        ]
        for p in self._parches:
            p.start()

    def tearDown(self):
        for p in self._parches:
            p.stop()


class EstadoTest(_Base):
    def test_dashboard_con_datos(self):
        html = wv.render(_status(CON_DATOS), USER)
        self.assertIn('class="dash dash--estado"', html)
        self.assertGreaterEqual(html.count('class="kpi'), 4)
        self.assertIn('aria-current="page"', html)       # nav: Estado activo
        self.assertIn("<svg", html)                      # iconos en línea
        self.assertIn('aria-hidden="true"', html)
        # Los nombres de los workers y sus formularios siguen ahí.
        self.assertIn("demo_on", html)
        self.assertIn('action="/workers/demo_on/restart"', html)
        self.assertIn('name="csrf"', html)
        self.assertIn("<span>Logs</span>", html)

    def test_raiz_sin_scroll_de_pagina(self):
        html = wv.render(_status(CON_DATOS), USER)
        # El alto y la rejilla viven en apple.css, no en un style="" del HTML.
        raiz = re.search(r'<div class="dash dash--estado"([^>]*)>', html)
        self.assertIsNotNone(raiz, "el tablero debe ser .dash--estado")
        self.assertNotIn("style=", raiz.group(1))
        self.assertIn(".dash--estado", html)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", html)
        self.assertIn("overflow: hidden", html)
        # Las listas SÍ scrollean dentro de su tarjeta.
        self.assertIn("dashx__scroll", html)
        self.assertIn("overflow: auto", html)

    def test_dashboard_vacio_no_revienta(self):
        html = wv.render(_status([]), USER)
        self.assertIn('class="dash dash--estado"', html)
        self.assertGreaterEqual(html.count('class="kpi'), 4)
        self.assertIn('class="empty"', html)
        self.assertIn("No hay workers", html)

    def test_kpis_derivados(self):
        status = _status(CON_DATOS)
        html = wv.render(status, USER)
        self.assertIn("Latidos viejos", html)
        self.assertIn("Avisos", html)
        # Hay un latido de 30 h y un worker en FATAL.
        avisos = wv._avisos(status, pw.shell_check(), pw.versiones_check())
        self.assertTrue(any(n == "warn" and "latido" in t for n, t in avisos))
        self.assertTrue(any(n == "bad" and "status_web" in t for n, t in avisos))


class LogsTest(_Base):
    def test_indice_es_una_lista(self):
        html = wv.logs_index(_status(CON_DATOS), USER)
        self.assertIn('<ul class="list', html)
        self.assertIn("Último latido", html)
        self.assertIn('href="/workers/avisos/logs"', html)
        self.assertIn("Ver log", html)
        # Un worker sin log no ofrece enlace roto.
        self.assertNotIn('href="/workers/demo_off/logs"', html)
        self.assertIn("sin log", html)
        # Eventos de la base: la tabla (si hay filas) va dentro de .table-wrap.
        self.assertIn(".table-wrap", html)
        if "<table" in html:
            self.assertIn("table-wrap", html)
        self.assertIn('class="badge', html)

    def test_indice_vacio(self):
        html = wv.logs_index(_status([]), USER)
        self.assertIn('class="empty"', html)
        self.assertIn("Todavía no hay workers", html)

    def test_log_crudo_solo_el_pre_scrollea(self):
        html = wv.logs_page("demo_on", "linea 1\nlinea 2", None, USER,
                            estado="RUNNING")
        self.assertIn('<pre class="log">linea 1\nlinea 2</pre>', html)
        self.assertIn("← Volver a Estado", html)
        self.assertIn("demo_on", html)
        self.assertIn("EN EJECUCIÓN", html)
        # La cabecera no repite formularios.
        self.assertNotIn("<form", html)
        # El <pre> es el único bloque con scroll propio.
        self.assertIn("dashx__log", html)

    def test_log_crudo_con_error(self):
        html = wv.logs_page("demo_on", "", "no hay log para 'demo_on'", USER)
        self.assertIn("no hay log", html)
        self.assertIn('class="empty"', html)
        self.assertNotIn('<pre class="log">', html)


if __name__ == "__main__":
    unittest.main()
