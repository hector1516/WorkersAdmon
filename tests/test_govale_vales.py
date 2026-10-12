"""
Pruebas del generador de vales QR (oxxogas_vales_automation).

Lo que se prueba aqui es una cadena de fallos que ya custo un vale real:

  1. Tras hacer clic en "Generar Vale" se tomaba un screenshot de depuracion
     SIN proteccion. Si ese screenshot se cuelga (30s de timeout, la pagina de
     resultado suele tardar), la excepcion se lleva por delante el resultado:
     el vale ya esta creado en Go Vale pero el sistema lo reporta como fallido
     y vuelve a PENDIENTE. O peor: se reintenta y se cobra dos veces.
  2. Al fallar, `revertir_solicitud_pendiente` pasaba la solicitud a PENDIENTE,
     que es justo lo que el worker NO vuelve a mirar (solo procesa APROBADO).
     El vale se perdia en silencio.

Estas pruebas usan una pagina falsa que falla cuando quiere: no hay navegador ni
red, asi que corren en un segundo.

    python3 -m unittest tests.test_govale_vales
"""

import sys
import types
import unittest

import oxxogas_vales_automation as ov


class PaginaFalsa:
    """Sustituto de la pagina de Playwright: falla donde le digan."""

    def __init__(self, falla_screenshot=False):
        self.falla_screenshot = falla_screenshot
        self.screenshots = []

    def screenshot(self, path=None, timeout=None):
        self.screenshots.append(path)
        if self.falla_screenshot:
            raise TimeoutError('Page.screenshot: Timeout 30000ms exceeded.')
        return b'png'


class CapturarDebug(unittest.TestCase):
    """El screenshot es una ayuda de depuracion, no puede romper nada."""

    def test_sin_fallo_devuelve_true_y_guarda_el_archivo(self):
        page = PaginaFalsa()
        self.assertTrue(ov._capturar_debug(page, 'govale_result'))
        self.assertEqual(page.screenshots, ['/tmp/govale_result.png'])

    def test_si_falla_no_revienta(self):
        page = PaginaFalsa(falla_screenshot=True)
        self.assertFalse(ov._capturar_debug(page, 'govale_result'))

    def test_el_timeout_es_corto_para_no_matar_la_operacion(self):
        # El default de Playwright son 30s: si el screenshot se cuelga 30s en
        # cada intento, el worker se atrasa y parece colgado.
        page = PaginaFalsa()
        vistos = {}

        def screenshot(path=None, timeout=None):
            vistos['timeout'] = timeout
            page.screenshots.append(path)

        page.screenshot = screenshot
        ov._capturar_debug(page, 'x')
        self.assertEqual(vistos['timeout'], 5000)

    def test_todas_las_capturas_pasan_por_el_ayudante(self):
        """
        Si alguien vuelve a escribir un page.screenshot pelado, esta prueba lo
        avisa. Es la que evita que el bug vuelva.
        """
        import inspect
        fuente = inspect.getsource(ov)
        # La unica llamada real a screenshot que debe existir es la del ayudante.
        llamadas = [l.strip() for l in fuente.splitlines()
                    if 'page.screenshot(' in l and 'timeout=timeout' not in l
                    and not l.strip().startswith('#')
                    and not l.strip().startswith(('Antes', 'Estas'))]
        self.assertEqual(llamadas, [],
                         f'capturas sin proteger (deben usar _capturar_debug): {llamadas}')
        # Y el modulo debe usar el ayudante en varios puntos, no en uno solo.
        self.assertGreaterEqual(fuente.count('_capturar_debug(page'), 4)


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def new_context(self, **kw):
        return _FakeContext()

    def close(self):
        self.closed = True


class _FakeContext:
    def new_page(self):
        return _FakePage()


class _FakePage:
    def __init__(self):
        self.gotos = []

    def on(self, *a, **kw):
        pass

    def goto(self, url, **kw):
        self.gotos.append((url, kw.get('wait_until')))
        raise TimeoutError('Page.goto: Timeout 45000ms exceeded.')


class _FakeChromium:
    def __init__(self, falla_launch=False):
        self.falla_launch = falla_launch

    def launch(self, **kw):
        if self.falla_launch:
            raise RuntimeError('no se pudo lanzar chromium')
        return _FakeBrowser()


class _FakePW:
    def __init__(self, falla_launch=False):
        self.chromium = _FakeChromium(falla_launch)
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeSync:
    def __init__(self, pw):
        self._pw = pw

    def start(self):
        return self._pw


class LifecyclePlaywright(unittest.TestCase):
    """
    Un `sync_playwright().start()` que no se detiene envenena el PROCESO: el
    siguiente `start()` falla con "It looks like you are using Playwright Sync
    API inside the asyncio loop" y el worker no se recupera hasta reiniciarlo.
    Le paso a `govale_vouchers_worker` el 2026-10-10 y dejo de sincronizar vales.
    """

    def _con_fake(self, pw):
        """Instala un playwright falso SIN importar el real (no esta en la imagen
        de tests): se meten los modulos en sys.modules y se restauran al salir."""
        mod = types.ModuleType('playwright.sync_api')
        mod.sync_playwright = lambda: _FakeSync(pw)
        pkg = types.ModuleType('playwright')
        pkg.sync_api = mod
        previos = {k: sys.modules.get(k) for k in ('playwright', 'playwright.sync_api')}
        sys.modules['playwright'] = pkg
        sys.modules['playwright.sync_api'] = mod
        return previos

    def _restaura(self, previos):
        for k, v in previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_si_el_goto_expira_se_suelta_playwright(self):
        pw = _FakePW()
        previos = self._con_fake(pw)
        try:
            with self.assertRaises(TimeoutError):
                ov._launch_browser_and_login('u', 'p')
        finally:
            self._restaura(previos)
        self.assertTrue(pw.stopped,
                        'pw.stop() no se llamo: el worker quedaria envenenado')

    def test_si_el_launch_falla_se_suelta_playwright(self):
        pw = _FakePW(falla_launch=True)
        previos = self._con_fake(pw)
        try:
            with self.assertRaises(RuntimeError):
                ov._launch_browser_and_login('u', 'p')
        finally:
            self._restaura(previos)
        self.assertTrue(pw.stopped)


class _El:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel

    def is_visible(self):
        return True

    def click(self):
        self.page.clicks.append(self.sel)


class _PageNaveg:
    """Pagina falsa que recuerda clicks y navegaciones."""

    def __init__(self):
        self.clicks = []
        self.gotos = []

    def query_selector(self, sel):
        return _El(self, sel)

    def goto(self, url, **kw):
        self.gotos.append(url)


class AbrirFormulario(unittest.TestCase):
    """La navegacion al formulario de Generar Vale."""

    def setUp(self):
        self._sleep = ov.time.sleep
        ov.time.sleep = lambda *a, **k: None

    def tearDown(self):
        ov.time.sleep = self._sleep

    def test_cierra_el_aviso_y_clickea_generar_vale(self):
        page = _PageNaveg()
        ov._abrir_formulario_generar_vale(page)
        # Primero el modal de "renovando plataforma", luego el formulario.
        self.assertTrue(any('Cerrar aviso' in c for c in page.clicks), page.clicks)
        self.assertTrue(any('Generar Vale' in c for c in page.clicks), page.clicks)
        self.assertEqual(page.gotos, [])   # no hizo falta el goto directo


class _CursorSol:
    def __init__(self, row):
        self.row = row

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ConnSol:
    def __init__(self, row):
        self.row = row

    def cursor(self, as_dict=False):
        return _CursorSol(self.row)

    def close(self):
        pass


class _DbEccsaFalso:
    def __init__(self, row):
        self.row = row

    def get_connection(self):
        return _ConnSol(self.row)

    def get_oxxogas_mapeo_by_usuario(self, uid):
        return {'ContactoOxxoGas': 'Victor'}


class _PageFormFail:
    """La navegacion "funciona" pero el formulario nunca aparece."""

    url = 'https://govale-digital.oxxogas.com/home'

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []

    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        raise TimeoutError('Page.wait_for_selector: Timeout 30000ms exceeded.')

    def screenshot(self, *a, **k):
        return b''

    def inner_text(self):
        return ''


class _Vacio:
    def close(self):
        pass

    def stop(self):
        pass


class Reintentable(unittest.TestCase):
    """Un fallo ANTES de generar no puede perder el vale."""

    ROW = {'Id': 28, 'IdSolicitante': 8, 'Placa': 'SAJ-722-B',
           'Descripcion': 'x', 'Cantidad': 1, 'MontoUnit': 500.0,
           'Solicitante': 'Victor', 'IdAutomovil': 9, 'Estatus': 'APROBADO'}

    def test_formulario_que_no_carga_es_reintentable(self):
        import sys
        import types as _types
        page = _PageFormFail()
        fake_db = _DbEccsaFalso(dict(self.ROW))
        previos = {k: sys.modules.get(k) for k in ('eccsa_db',)}
        sys.modules['eccsa_db'] = fake_db
        login_orig = ov._launch_browser_and_login
        ov._launch_browser_and_login = lambda u, p: (_Vacio(), None, page, {}, _Vacio())
        try:
            res = ov.crear_vale(28, user='u', pwd='p')
        finally:
            ov._launch_browser_and_login = login_orig
            for k, v in previos.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertIn('error', res)
        self.assertTrue(res.get('reintentable'),
                        'un fallo antes del click debe poder reintentarse')


class RetryEnWorker(unittest.TestCase):
    """`generar_vale_sincrono` deja APROBADO si el fallo es seguro."""

    class _DbFalso:
        def __init__(self):
            self.revertidas = []

        def get_govale_credentials(self):
            return {'user': 'u', 'password': 'p'}

        def revertir_solicitud_pendiente(self, sid):
            self.revertidas.append(sid)

    def _corre(self, resultado):
        import cron_vales_autogenerate as cv
        db = self._DbFalso()
        crear_orig, db_orig = cv.crear_vale, cv.db
        cv.db = db
        cv.crear_vale = lambda sid, user=None, pwd=None: resultado
        try:
            r = cv.generar_vale_sincrono(28)
        finally:
            cv.crear_vale, cv.db = crear_orig, db_orig
        return r, db.revertidas

    def test_fallo_seguro_no_revierte_a_pendiente(self):
        r, revertidas = self._corre({'error': 'No se pudo cargar el formulario',
                                     'reintentable': True})
        self.assertFalse(r)
        self.assertEqual(revertidas, [], 'no debía revertirse: se deja APROBADO')

    def test_fallo_tras_click_si_revierte(self):
        r, revertidas = self._corre({'error': 'No se pudo confirmar',
                                     'reintentable': False})
        self.assertFalse(r)
        self.assertEqual(revertidas, [28])


if __name__ == '__main__':
    unittest.main()
