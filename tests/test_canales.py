"""
tests/test_canales.py — Interruptores maestros de canales
==========================================================
Sin SQL Server: la lectura/escritura de HUB_Config se dobla con conexiones en
memoria. Los envíos reales se comprueban con el canal apagado (no deben tocar
HTTP ni pywebpush).

Ejecución:  python tests/test_canales.py
"""
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import canales  # noqa: E402
import eccsa_db  # noqa: E402
import openwa_client as ow  # noqa: E402


class _Cursor:
    """Cursor con una fila fija y, si se pide, registro de lo ejecutado."""

    def __init__(self, fila=None, error=None, registro=None):
        self.fila = fila
        self.error = error
        self.registro = registro
        self.params = None

    def execute(self, sql, params=()):
        if self.error:
            raise self.error
        self.params = params
        if self.registro is not None:
            self.registro.append((sql, params))

    def fetchone(self):
        return self.fila

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    """Conexión mínima con commit y un cursor reutilizable."""

    def __init__(self, cursor):
        self.cur = cursor
        self.commit_llamado = False

    def cursor(self, as_dict=False):
        return self.cur

    def commit(self):
        self.commit_llamado = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _con_fila(fila):
    conn = _Conn(_Cursor(fila))
    return conn


class Lectura(unittest.TestCase):
    def setUp(self):
        canales._cache.clear()

    def _leer(self, fila):
        conn = _con_fila(fila)
        with mock.patch.object(canales, "_db_configurada", return_value=True), \
             mock.patch.object(canales, "_conexion", return_value=conn):
            return canales.activo("telegram")

    def test_sin_fila_el_canal_esta_activo(self):
        self.assertTrue(self._leer(None))

    def test_valor_uno_esta_activo(self):
        self.assertTrue(self._leer({"Valor": "1"}))

    def test_valor_cero_apaga(self):
        self.assertFalse(self._leer({"Valor": "0"}))

    def test_valor_vacio_no_deja_mudo_al_sistema(self):
        self.assertTrue(self._leer({"Valor": ""}))

    def test_lectura_rota_devuelve_true(self):
        with mock.patch.object(canales, "_leer",
                               side_effect=RuntimeError("BD caída")):
            self.assertTrue(canales.activo("whatsapp"))

    def test_cache_no_repite_la_consulta(self):
        with mock.patch.object(canales, "_db_configurada", return_value=True), \
             mock.patch.object(canales, "_leer",
                               return_value=False) as leer:
            self.assertFalse(canales.activo("push"))
            self.assertFalse(canales.activo("push"))
        self.assertEqual(leer.call_count, 1)

    def test_estado_devuelve_los_tres_canales(self):
        with mock.patch.object(canales, "activo", side_effect=lambda c: c != "push"):
            self.assertEqual(canales.estado(),
                             {"telegram": True, "whatsapp": True, "push": False})


class EnviosApagados(unittest.TestCase):
    def setUp(self):
        canales._cache.clear()

    def test_whatsapp_apagado_no_hace_http(self):
        class Sesion:
            llamadas = 0

            def post(self, *a, **k):
                Sesion.llamadas += 1
                raise AssertionError("no debió salir a la red")

        with mock.patch("canales.activo", return_value=False):
            ok, detalle = ow.enviar_texto(
                "521@c.us", "Hola", base="http://openwa:2785",
                clave="k", session_id="s", sesion=Sesion())
        self.assertEqual((ok, detalle), (True, "canal apagado"))
        self.assertEqual(Sesion.llamadas, 0)

    def test_telegram_apagado_no_consulta_ni_manda(self):
        with mock.patch("canales.activo", return_value=False), \
             mock.patch.object(eccsa_db, "get_telegram_config") as cfg:
            ok, detalle = eccsa_db.send_telegram_message(1, "hola")
        self.assertEqual((ok, detalle), (True, "canal apagado"))
        cfg.assert_not_called()

    def test_push_apagado_no_llama_a_pywebpush(self):
        with mock.patch("canales.activo", return_value=False), \
             mock.patch.object(eccsa_db, "get_vapid_keys") as vapid:
            self.assertEqual(
                eccsa_db.send_push_notification_to_users(
                    "t", "m", ["a@b.com"]), (1, 0))
            self.assertEqual(
                eccsa_db.send_push_notification("t", "m"), (1, 0))
        vapid.assert_not_called()


class Guardado(unittest.TestCase):
    def setUp(self):
        canales._cache.clear()

    def test_guardar_hace_merge_y_refresca_la_cache(self):
        registro = []
        conn = _Conn(_Cursor(registro=registro))
        with mock.patch.object(canales, "_conexion", return_value=conn):
            canales.guardar("telegram", False)
        self.assertTrue(conn.commit_llamado)
        self.assertIn("MERGE HUB_Config", registro[0][0])
        self.assertIn("canal_telegram_activo", registro[0][1])
        self.assertIn("0", registro[0][1])
        self.assertFalse(canales.activo("telegram"))


class ApiPost(unittest.TestCase):
    def setUp(self):
        canales._cache.clear()

    def test_post_cambia_el_valor_con_doble_de_bd(self):
        # Importar panel aquí y no al inicio: `panel.config` fija rutas y otros
        # tests del panel arman su sandbox durante el descubrimiento.
        from panel import api_canales

        registro = []
        conn = _Conn(_Cursor(registro=registro))
        with mock.patch.object(api_canales.api, "_sesion",
                               return_value=({"email": "ana@eccsa.mx"}, None)), \
             mock.patch.object(api_canales.api.db, "log_activity"), \
             mock.patch.object(canales, "_conexion", return_value=conn):
            codigo, objeto = api_canales._guardar(
                None, {}, {"canal": "telegram", "activo": False})

        self.assertEqual(codigo, 200, objeto)
        self.assertFalse(objeto["activo"])
        self.assertFalse(canales.activo("telegram"))
        self.assertIn("0", registro[0][1])

    def test_post_rechaza_canal_desconocido(self):
        from panel import api_canales

        with mock.patch.object(api_canales.api, "_sesion",
                               return_value=({"email": "ana@eccsa.mx"}, None)):
            codigo, objeto = api_canales._guardar(
                None, {}, {"canal": "fax", "activo": True})
        self.assertEqual(codigo, 400)
        self.assertIn("error", objeto)


if __name__ == "__main__":
    unittest.main()
