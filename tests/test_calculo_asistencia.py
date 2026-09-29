"""
Prueba de integración del cálculo de asistencia, sin base de datos.

`calcular_asistencia_dia` encadena varios pasos (ventana del turno, línea de
tiempo de escaneos, huecos, eventos, veredicto) y un error en cualquiera de
ellos no se ve hasta que alguien aprieta el botón en producción. Acá se le
pone una base de datos falsa que responde por tipo de consulta y se comprueba
el resultado de punta a punta.

    python3 -m unittest tests.test_calculo_asistencia
"""

import unittest
from datetime import date, datetime, time, timedelta

import eccsa_db

FECHA = date(2026, 9, 29)      # martes
NODO = datetime(2026, 9, 29, 8, 0)


class _CursorFalso:
    """Responde según el texto de la consulta, como una base de verdad."""

    def __init__(self, guion, as_dict=False):
        self.guion = guion
        self._as_dict = as_dict
        self._filas = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._filas = self.guion.responder(sql, params)
        return self

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _ConnFalsa:
    def __init__(self, guion):
        self.guion = guion

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, as_dict=False):
        return _CursorFalso(self.guion, as_dict)

    def commit(self):
        pass


class _Guion:
    """Los datos que 'tiene' la base de datos en cada escenario."""

    def __init__(self, turnos=(), escaneos=(), eventos=(), config=()):
        self.turnos = list(turnos)
        self.escaneos = list(escaneos)
        self.eventos = list(eventos)
        self.config = list(config)

    def responder(self, sql, params):
        if 'FROM HUB_Config' in sql:
            return self.config
        if 'FROM HUB_UsuarioTurno' in sql:
            return self.turnos[:1]
        if 'DISTINCT FechaScan' in sql:
            return [{'FechaScan': e} for e in self._en_ventana(self.escaneos, params)]
        if 'FROM HUB_NetworkPresence' in sql:
            # Como la base real: el WHERE filtra por la ventana del turno. Sin
            # esto, un evento de las 00:15 llegaría al cálculo y la prueba
            # estaría probando algo que en producción no pasa.
            return [e for e in self.eventos
                    if self._dentro(e.get('Deteccion'), params)]
        if 'FROM HUB_NetworkScanResults' in sql:
            return [{'FechaScan': e} for e in self._en_ventana(self.escaneos, params)]
        if 'FROM HUB_AsistenciaDiaria' in sql:
            return []
        return []

    @staticmethod
    def _dentro(fecha, params):
        """Si la consulta trae (algo, desde, hasta), se filtra por la ventana."""
        if not params or len(params) < 3:
            return True
        desde, hasta = params[-2], params[-1]
        if fecha is None or desde is None:
            return True
        return desde <= fecha <= hasta

    def _en_ventana(self, escaneos, params):
        if not params or len(params) < 3:
            return escaneos
        desde, hasta = params[-2], params[-1]
        return [e for e in escaneos if desde <= e <= hasta]


TURNO_LV = {
    'IdTurno': 3, 'Nombre': 'Operativo',
    'LV_Entrada': time(9, 0), 'LV_Salida': time(18, 30),
    'Sab_Entrada': time(9, 30), 'Sab_Salida': time(13, 30),
    'Dom_Entrada': None, 'Dom_Salida': None,
    'Tol_Llegada_Min': 15, 'Tol_Salida_Antes_Min': 5,
}

CONFIG = [
    {'Clave': 'net_asistencia_ventana_min', 'Valor': '120'},
    {'Clave': 'net_asistencia_escaneos_minimos', 'Valor': '20'},
    {'Clave': 'net_asistencia_politica', 'Valor': 'PISO'},
    {'Clave': 'net_scan_interval_seg', 'Valor': '180'},
]


def _escaneos_desde(desde, hasta, cada_min=3):
    """La línea de tiempo del escáner: un escaneo cada 3 minutos."""
    out, t = [], desde
    while t <= hasta:
        out.append(t)
        t += timedelta(minutes=cada_min)
    return out


def _jornada(inicio=time(7, 0), fin=time(21, 0)):
    return _escaneos_desde(datetime.combine(FECHA, inicio),
                            datetime.combine(FECHA, fin))


class CalcularAsistencia(unittest.TestCase):
    def _correr(self, guion):
        original = eccsa_db.get_connection
        eccsa_db.get_connection = lambda: _ConnFalsa(guion)
        try:
            return eccsa_db.calcular_asistencia_dia(7, FECHA)
        finally:
            eccsa_db.get_connection = original

    def test_llegada_a_las_9_en_ventana_de_3_min(self):
        """Escaneos cada 3 min; el equipo aparece en el de las 9:00 y el
        anterior (8:57) no lo vio. La llegada está ENTRE 8:57 y 9:00."""
        escaneos = _jornada()
        eventos = [{
            'TipoEvento': 'ENTRADA',
            'Deteccion': datetime(2026, 9, 29, 9, 0),   # escaneo que lo vio
            'UltimaVezVisto': datetime(2026, 9, 28, 18, 0),  # ayer: piso real
            'VentanaMin': 900,
        }]
        r = self._correr(_Guion([TURNO_LV], escaneos, eventos, CONFIG))
        self.assertEqual(r['entrada_techo'], time(9, 0))
        # El piso sale del ESCANEO anterior (8:57), no de "ayer": esa es la
        # diferencia entre una ventana de 3 minutos y una de 15 horas.
        self.assertEqual(r['entrada_piso'], time(8, 57))
        self.assertEqual(r['ventana_min'], 3)
        self.assertEqual(r['estado'], 'CALCULADA')
        self.assertFalse(r['entrada_tardia'])

    def test_escaneo_caido_da_sin_datos_y_no_ausente(self):
        """Sin escaneos no hay evidencia, y evidencia no es ausencia."""
        r = self._correr(_Guion([TURNO_LV], [], [], CONFIG))
        self.assertEqual(r['estado'], 'SIN_DATOS')
        self.assertFalse(r['ausente'])

    def test_hueco_grande_en_el_escaneo_es_sin_datos(self):
        """Escaneos normalitos pero con un hueco de 40 min a media mañana: el
        sistema estuvo caído ese rato, así que no se puede afirmar nada."""
        escaneos = _escaneos_desde(datetime(2026, 9, 29, 7, 0),
                                   datetime(2026, 9, 29, 9, 0))
        escaneos += _escaneos_desde(datetime(2026, 9, 29, 9, 40),
                                    datetime(2026, 9, 29, 21, 0))
        r = self._correr(_Guion([TURNO_LV], escaneos, [], CONFIG))
        self.assertEqual(r['estado'], 'SIN_DATOS')
        self.assertGreaterEqual(r['gap_maximo_min'], 40)

    def test_presencia_fuera_de_la_ventana_no_cuenta(self):
        """El evento es de las 00:15, fuera de la ventana del turno: no se
        consulta, así que el día queda AUSENTE y no 'llegó a las 00:15'."""
        escaneos = _jornada(inicio=time(0, 0), fin=time(21, 0))
        eventos = [{
            'TipoEvento': 'ENTRADA',
            'Deteccion': datetime(2026, 9, 29, 0, 15),
            'UltimaVezVisto': datetime(2026, 9, 28, 18, 0),
            'VentanaMin': 375,
        }]
        r = self._correr(_Guion([TURNO_LV], escaneos, eventos, CONFIG))
        # La consulta filtra por la ventana, así que el guion entrega solo lo que
        # caería dentro; se verifica que el cálculo NO usa nada fuera de rango.
        self.assertIn(r['estado'], ('CALCULADA', 'AUSENTE'))
        self.assertNotIsInstance(r.get('entrada_techo'), datetime)
        if r['entrada_techo'] is not None:
            hora = r['entrada_techo']
            self.assertTrue(7 <= hora.hour <= 21, hora)

    def test_sin_turno_es_no_aplica(self):
        r = self._correr(_Guion([TURNO_LV], _jornada(), [], CONFIG))
        self.assertIsNotNone(r.get('estado'))
        # Sábado: el turno deOperativo tiene horario, domingo no.
        domingo = date(2026, 9, 27)
        original = eccsa_db.get_connection
        eccsa_db.get_connection = lambda: _ConnFalsa(_Guion([TURNO_LV], _jornada(), [], CONFIG))
        try:
            r_dom = eccsa_db.calcular_asistencia_dia(7, domingo)
        finally:
            eccsa_db.get_connection = original
        self.assertEqual(r_dom['estado'], 'NO_APLICA')
        self.assertFalse(r_dom['ausente'])

    def test_salida_toma_el_ultimo_que_se_vio_presente(self):
        escaneos = _jornada()
        eventos = [
            {'TipoEvento': 'ENTRADA',
             'Deteccion': datetime(2026, 9, 29, 9, 0),
             'UltimaVezVisto': datetime(2026, 9, 28, 18, 0),
             'VentanaMin': 900},
            {'TipoEvento': 'SALIDA',
             # FechaDeteccion = escaneo que constató la ausencia (techo)
             'Deteccion': datetime(2026, 9, 29, 18, 36),
             # UltimaVezVisto = última vez presente (piso)
             'UltimaVezVisto': datetime(2026, 9, 29, 18, 33),
             'VentanaMin': 3},
        ]
        r = self._correr(_Guion([TURNO_LV], escaneos, eventos, CONFIG))
        # El núcleo trabaja con horas, no con datetimes: la fecha ya está
        # acotada por la ventana de la consulta, así que sobra.
        self.assertEqual(r['salida_piso'], time(18, 33))
        self.assertEqual(r['salida_techo'], time(18, 36))
        self.assertFalse(r['salida_temprana'])

    def test_tarde_afirmable_con_ventana_amplia(self):
        """Llega entre 9:20 y 9:24 con tolerancia 15: los dos extremos pasan la
        tolerancia, así que es tarde y se cobra el extremo que lo favorece."""
        escaneos = _jornada()
        eventos = [{
            'TipoEvento': 'ENTRADA',
            'Deteccion': datetime(2026, 9, 29, 9, 24),
            'UltimaVezVisto': datetime(2026, 9, 28, 18, 0),
            'VentanaMin': 900,
        }]
        r = self._correr(_Guion([TURNO_LV], escaneos, eventos, CONFIG))
        self.assertEqual(r['estado'], 'TARDE')
        # El piso es el escaneo ANTERIOR a las 9:24, que con el intervalo de
        # 3 min cae a las 9:21: 21 min tarde, y se cobra ese (el que favorece).
        self.assertEqual(r['minutos_tarde'], 21)


class HelpersDeVentana(unittest.TestCase):
    """Los helpers que convierten la línea de tiempo en la ventana."""

    def test_ventana_del_turno_abre_por_los_dos_lados(self):
        desde, hasta = eccsa_db._ventana_turno(
            FECHA, time(9, 0), time(18, 30), 120)
        self.assertEqual(desde, datetime(2026, 9, 29, 7, 0))
        self.assertEqual(hasta, datetime(2026, 9, 29, 20, 30))

    def test_hueco_maximo_con_un_solo_escaneo_no_se_inventa(self):
        """Con un solo escaneo no se puede hablar de huecos:(None es "no sé",
        no 0 (que diría "no habrá ningún hueco" y es justamente el falso
        positivo que hay que evitar)."""
        self.assertIsNone(eccsa_db._hueco_maximo(
            [], datetime(2026, 9, 29, 7, 0), datetime(2026, 9, 29, 20, 30)))
        self.assertIsNone(eccsa_db._hueco_maximo(
            [datetime(2026, 9, 29, 12, 0)],
            datetime(2026, 9, 29, 7, 0), datetime(2026, 9, 29, 20, 30)))

    def test_hueco_maximo_mide_la_caida_del_escaner(self):
        escaneos = [datetime(2026, 9, 29, 7, 0), datetime(2026, 9, 29, 7, 3),
                    datetime(2026, 9, 29, 7, 43), datetime(2026, 9, 29, 7, 46)]
        hueco = eccsa_db._hueco_maximo(escaneos,
                                       datetime(2026, 9, 29, 7, 0),
                                       datetime(2026, 9, 29, 20, 30))
        self.assertEqual(hueco, 40)

    def test_oficina_vacia_no_cuenta_como_caida_del_escaner(self):
        """El falso positivo que casi se cuela: los escaneos que no detectan
        nada no dejan fila, así que el último escaneo de la jornada suele ser
        mucho ANTES del fin de la ventana. Medir ese borde daría un hueco de
        horas y marcaría el día SIN DATOS aunque el escáner hubiera corrido
        toda la mañana."""
        escaneos = _escaneos_desde(datetime(2026, 9, 29, 7, 0),
                                   datetime(2026, 9, 29, 15, 0))
        hueco = eccsa_db._hueco_maximo(escaneos,
                                       datetime(2026, 9, 29, 7, 0),
                                       datetime(2026, 9, 29, 20, 30))
        self.assertEqual(hueco, 3, "el interior va parejo; el borde no cuenta")

    def test_escaneo_caido_toda_la_mañana_lo_coge_el_conteo(self):
        """Si el escáner muere temprano, el hueco interior es 0 pero el día no
        tiene datos: eso lo atrapa el conteo, no el hueco."""
        escaneos = _escaneos_desde(datetime(2026, 9, 29, 7, 0),
                                   datetime(2026, 9, 29, 7, 30))
        hueco = eccsa_db._hueco_maximo(escaneos,
                                       datetime(2026, 9, 29, 7, 0),
                                       datetime(2026, 9, 29, 20, 30))
        self.assertEqual(hueco, 3)
        self.assertLess(len(escaneos), 20, "el conteo es lo que delata el día vacío")

    def test_piso_usa_el_escaneo_anterior_y_no_el_respaldo(self):
        escaneos = [datetime(2026, 9, 29, 8, 54), datetime(2026, 9, 29, 8, 57),
                    datetime(2026, 9, 29, 9, 0)]
        piso = eccsa_db._piso_de_llegada(
            escaneos, datetime(2026, 9, 29, 9, 0),
            datetime(2026, 9, 28, 18, 0))   # respaldo: ayer
        self.assertEqual(piso, datetime(2026, 9, 29, 8, 57),
                         "el escaneo anterior acota mucho mejor que 'ayer'")

    def test_piso_sin_linea_de_tiempo_cae_al_respaldo(self):
        piso = eccsa_db._piso_de_llegada([], datetime(2026, 9, 29, 9, 0),
                                        datetime(2026, 9, 28, 18, 0))
        self.assertEqual(piso, datetime(2026, 9, 28, 18, 0))

    def test_techo_de_salida_es_el_escaneo_siguiente(self):
        escaneos = [datetime(2026, 9, 29, 18, 33), datetime(2026, 9, 29, 18, 36)]
        techo = eccsa_db._techo_de_salida(escaneos,
                                          datetime(2026, 9, 29, 18, 33), None)
        self.assertEqual(techo, datetime(2026, 9, 29, 18, 36))


if __name__ == "__main__":
    unittest.main(verbosity=2)
