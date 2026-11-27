"""
tests/test_spa.py — el panel servido como SPA (corte final)
===========================================================
Un `dist` de mentira en un directorio temporal: el servidor debe entregar el
index del SPA en las rutas del frontend, los assets con caché larga, la API
como JSON y las vistas de Python solo bajo /clasico/....

Ejecución:  python tests/test_spa.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

HTTPD = None
PORT = None
ROOT = None
_ORIGINALES = ()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get(path, accept="text/html"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", headers={"Accept": accept})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        resp = opener.open(req, timeout=10)
        return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def _post(path):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=b"", method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        resp = opener.open(req, timeout=10)
        return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def _dist_falso(destino):
    """dist mínimo: index con el script del build y un asset con hash."""
    assets = os.path.join(destino, "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(destino, "index.html"), "w", encoding="utf-8") as fh:
        fh.write('<!doctype html><html><body><div id="app"></div>'
                 '<script type="module" src="/assets/app-abc123.js"></script>'
                 "</body></html>")
    with open(os.path.join(assets, "app-abc123.js"), "w",
              encoding="utf-8") as fh:
        fh.write("console.log('panel');")


def setUpModule():
    global HTTPD, PORT, ROOT, _ORIGINALES
    from panel import config
    from panel.server import Handler

    ROOT = tempfile.mkdtemp(prefix="panel_spa_test_")
    dist = os.path.join(ROOT, "dist")
    _dist_falso(dist)
    _ORIGINALES = (config.UI_DIST_DIR, config.UI_INDEX_FILE)
    config.UI_DIST_DIR = dist
    config.UI_INDEX_FILE = os.path.join(dist, "index.html")

    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.2)


def tearDownModule():
    global HTTPD
    if HTTPD:
        HTTPD.shutdown()
        HTTPD.server_close()
    from panel import config
    config.UI_DIST_DIR, config.UI_INDEX_FILE = _ORIGINALES
    shutil.rmtree(ROOT, ignore_errors=True)


class SpaTest(unittest.TestCase):

    def test_01_raiz_sirve_el_index(self):
        code, headers, texto = _get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn('type="module"', texto)
        self.assertIn("/assets/app-abc123.js", texto)
        # index.html nunca se cachea: un deploy nuevo se ve de inmediato.
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_02_asset_cache_larga(self):
        code, headers, texto = _get("/assets/app-abc123.js", accept="*/*")
        self.assertEqual(code, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"),
                         "public, max-age=31536000, immutable")
        self.assertIn("console.log", texto)

    def test_03_asset_inexistente_404_no_index(self):
        code, _, texto = _get("/assets/no-existe.js", accept="*/*")
        self.assertEqual(code, 404)
        self.assertNotIn("<html", texto.lower())

    def test_04_asset_fuera_de_dist_no_se_sirve(self):
        code, _, _ = _get("/assets/%2e%2e/%2e%2e/server.py", accept="*/*")
        self.assertEqual(code, 404)

    def test_05_api_sigue_siendo_json(self):
        code, headers, texto = _get("/api/panel/me", accept="application/json")
        self.assertEqual(code, 401)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("error", json.loads(texto))
        self.assertNotIn("<html", texto.lower())

    def test_06_ruta_desconocida_sin_extension_es_index(self):
        code, headers, texto = _get("/whatsapp/123")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("/assets/app-abc123.js", texto)

    def test_07_archivo_desconocido_404(self):
        code, _, _ = _get("/robots.txt", accept="*/*")
        self.assertEqual(code, 404)

    def test_08_clasico_responde_ssr(self):
        code, headers, _ = _get("/clasico/estado")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_09_clasico_login_sigue_en_python(self):
        code, _, texto = _get("/clasico/login")
        self.assertEqual(code, 200)
        self.assertIn('name="csrf"', texto)

    def test_10_logout_cierra_cookie_y_redirige(self):
        code, headers, _ = _post("/logout")
        self.assertEqual(code, 303)
        self.assertEqual(headers.get("Location"), "/login")
        self.assertIn("ecsa_token=", headers.get("Set-Cookie", ""))

    def test_11_healthz_y_pwa_en_la_raiz(self):
        code, _, texto = _get("/healthz", accept="*/*")
        self.assertEqual((code, texto), (200, "ok"))
        code, headers, _ = _get("/sw.js", accept="*/*")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Service-Worker-Allowed"), "/")
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        code, headers, _ = _get("/manifest.webmanifest", accept="*/*")
        self.assertEqual(code, 200)
        self.assertIn("manifest+json", headers.get("Content-Type", ""))

    def test_12_sin_dist_503_claro(self):
        from panel import config
        original = (config.UI_DIST_DIR, config.UI_INDEX_FILE)
        config.UI_DIST_DIR = os.path.join(ROOT, "no_existe")
        config.UI_INDEX_FILE = os.path.join(ROOT, "no_existe", "index.html")
        try:
            code, headers, texto = _get("/")
            self.assertEqual(code, 503)
            self.assertIn("text/plain", headers.get("Content-Type", ""))
            self.assertIn("no está compilado", texto)
            self.assertNotIn("Traceback", texto)
            # la API no depende del build
            code, _, _ = _get("/api/panel/me", accept="application/json")
            self.assertEqual(code, 401)
        finally:
            config.UI_DIST_DIR, config.UI_INDEX_FILE = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
