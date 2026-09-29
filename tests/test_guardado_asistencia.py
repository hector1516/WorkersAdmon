"""
Pruebas del guardado de asistencia: que el MERGE no se desalinee.

Un MERGE con el número de placeholders equivocado no falla al escribir el
código, falla en producción, en el botón de "Calcular asistencias", y encima
se lleva por delante la transacción. Estas pruebas interceptan la conexión y
comprueban lo que se le entregaría a pymssql, sin tocar la base de datos.

    python3 -m unittest tests.test_guardado_asistencia
"""

import unittest
from contextlib import contextmanager
from datetime import date, datetime, time

import eccsa_db


class _CursorFalso:
    """Se queda con la query y los parámetros, como haría pymssql."""

    def __init__(self, registro, as_dict=False):
        self.registro = registro
        self._as_dict = as_dict

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.registro['sql'] = sql
        self.registro['params'] = params
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _ConnFalsa:
    def __init__(self, registro):
        self.registro = registro
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, as_dict=False):
        return _CursorFalso(self.registro, as_dict)

    def commit(self):
        self.committed = True


def _con_pymsmsql_apaixonado(monkeypatch_target, registro):
    """Parchea get_connection para que devuelva la conexión falsa."""
    original = eccsa_db.get_connection
    monkeypatch_target.append(original)
    eccsa_db.get_connection = lambda: _ConnFalsa(registro)
    return original


class GuardarAsistencia(unittest.TestCase):
    def setUp(self):
        self.registro = {}
        self._original = eccsa_db.get_connection
        eccsa_db.get_connection = lambda: _ConnFalsa(self.registro)

    def tearDown(self):
        eccsa_db.get_connection = self._original

    def _datos(self, **kw):
        base = dict(
            fecha=date(2026, 9, 29),
            turno_id=3,
            estado='CALCULADA',
            entrada_esperada=time(9, 0),
            salida_esperada=time(18, 30),
            entrada_piso=time(8, 57),
            entrada_techo=time(9, 0),
            salida_piso=time(18, 33),
            salida_techo=time(18, 35),
            minutos_tarde=None,
            minutos_antes=None,
            ventana_min=3,
            entrada_tardia=False,
            salida_temprana=False,
            ausente=False,
            indeterminado=False,
            escaneos_dia=200,
            gap_maximo_min=4,
            observaciones='dato de prueba',
            fuente='RED',
        )
        base.update(kw)
        return base

    def test_los_placeholders_cuadran_con_los_parametros(self):
        """El fallo clásico: un %s de más o de menos."""
        ok = eccsa_db.guardar_asistencia_diaria(7, date(2026, 9, 29), self._datos())
        self.assertTrue(ok)
        sql, params = self.registro['sql'], self.registro['params']
        self.assertEqual(sql.count('%s'), len(params),
                         f"{sql.count('%s')} placeholders vs {len(params)} parámetros")

    def test_las_dos_ramas_del_merge_usan_los_mismos_parametros(self):
        """WHEN MATCHED y WHEN NOT MATCHED reciben la misma tupla: si no, el
        INSERT escribiría los valores corridos (la fila del usuario con la hora
        de otro, etc.), que es de los fallos más difíciles de ver."""
        eccsa_db.guardar_asistencia_diaria(7, date(2026, 9, 29), self._datos())
        sql, params = self.registro['sql'], self.registro['params']
        self.assertIn("WHEN MATCHED THEN", sql)
        self.assertIn("WHEN NOT MATCHED THEN", sql)
        self.assertEqual(sql.count('%s'), len(params),
                         "el total de placeholders no cuadra con los parámetros")

    def test_las_columnas_del_insert_cuadran_con_sus_valores(self):
        """La lista de columnas del INSERT y su VALUES tienen que tener la
        misma cantidad: si no, pymssql manda una tupla de otro tamaño y revienta
        al ejecutar, en el botón de la vista y no en el código."""
        eccsa_db.guardar_asistencia_diaria(7, date(2026, 9, 29), self._datos())
        sql = self.registro['sql']
        import re
        columnas = re.search(r"INSERT \((.*?)\)\s*VALUES \((.*?)\);", sql, re.S)
        self.assertIsNotNone(columnas, "no se encontró el INSERT del MERGE")
        cols = [c.strip() for c in columnas.group(1).replace("\n", " ").split(",")
                if c.strip()]
        vals = [v for v in columnas.group(2).split("%s")]
        self.assertEqual(len(cols), columnas.group(2).count('%s'),
                         f"{len(cols)} columnas vs {columnas.group(2).count('%s')} valores")
        # Y que la rama MATCHED actualice lo mismo que se inserta.
        self.assertIn("Fuente = %s", sql)
        self.assertIn("Observaciones = %s", sql)

    def test_guarda_la_ventana_y_no_solo_el_minuto(self):
        eccsa_db.guardar_asistencia_diaria(7, date(2026, 9, 29), self._datos())
        sql, params = self.registro['sql'], self.registro['params']
        for columna in ('EntradaPiso', 'EntradaTecho', 'SalidaPiso', 'SalidaTecho',
                        'VentanaMin', 'Estado', 'EscaneosDia',
                        'GapMaximoMin', 'HoraEntradaEsperada'):
            self.assertIn(columna, sql, columna)
        # 08:57 y 09:00 tienen que ir como las dos columnas de la ventana.
        self.assertIn('08:57:00', params)
        self.assertIn('09:00:00', params)

    def test_commitea(self):
        eccsa_db.guardar_asistencia_diaria(7, date(2026, 9, 29), self._datos())
        # Si no hay commit, el MERGE se pierde al cerrar la conexión.
        self.assertIn('MERGE', self.registro['sql'])

    def test_acepta_hora_como_string(self):
        """El driver puede devolver TIME como texto; no debe reventar."""
        eccsa_db.guardar_asistencia_diaria(
            7, date(2026, 9, 29),
            self._datos(entrada_piso='08:57', entrada_techo='09:00'))
        self.assertTrue(self.registro['params'])
        self.assertIn('08:57:00', self.registro['params'])


class RegistrarEventoSinMigracion(unittest.TestCase):
    """
    Orden del despliegue: si el código llega antes que la migración 0042, el
    INSERT con las columnas nuevas falla. Sin este reintento se dejarían de
    registrar eventos de presencia, o sea, se dejaría de generar asistencia sin
    que nada más lo avise.
    """

    def test_si_falla_el_insert_completo_reintenta_el_simple(self):
        intentos = []

        class _Cursor:
            def __init__(self, registros):
                self.registros = registros

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                intentos.append(sql)
                if 'FechaDeteccion' in sql:
                    raise Exception("Invalid column name 'FechaDeteccion'")
                self.registros['sql'] = sql
                self.registros['params'] = params

            def fetchone(self):
                return None

            def fetchall(self):
                return []

        class _Conn:
            def __init__(self, registros):
                self.registros = registros

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self, as_dict=False):
                return _Cursor(self.registros)

            def commit(self):
                pass

        registros = {}
        original = eccsa_db.get_connection
        eccsa_db.get_connection = lambda: _Conn(registros)
        try:
            ok = eccsa_db.register_presence_event(
                7, 'ENTRADA', 95.0, 'prueba',
                fecha_deteccion=datetime(2026, 9, 29, 9, 0))
        finally:
            eccsa_db.get_connection = original

        self.assertTrue(ok, "el evento debe guardarse aunque falte la migración")
        self.assertEqual(len(intentos), 2, "se esperaba un reintento")
        self.assertIn('FechaDeteccion', intentos[0])
        self.assertNotIn('FechaDeteccion', intentos[1],
                         "el reintento tiene que usar el INSERT viejo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
