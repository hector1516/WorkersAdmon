"""
Pruebas de la lógica de asistencia (asistencia_core).

Son funciones puras: no tocan la base de datos, así que esto corre en un
segundo y en cualquier máquina. Cada prueba es un caso que pasó de verdad en
producción o que podría pasar.

    python3 -m unittest tests.test_asistencia
"""

import unittest
from datetime import date, datetime, time

from asistencia_core import (
    ESTADO_AUSENTE, ESTADO_CALCULADA, ESTADO_INDETERMINADO,
    ESTADO_NO_APLICA, ESTADO_SIN_DATOS, ESTADO_SALIDA_TEMPRANA, ESTADO_TARDE,
    POLITICA_PISO, POLITICA_PROMEDIO, POLITICA_TECHO,
    VEREDICTO_INDETERMINADO, VEREDICTO_OK, VEREDICTO_TARDE,
    aplicar_politica, clasificar_ventana, evaluar_dia, formatear_ventana,
    limpiar_hora,
)

HOY = date(2026, 9, 29)          # martes
ESCANEOS_OK = 200                # un día normal con el escáner sano


def _dia(**kw):
    """Un día normal de 9:00 a 18:30 con 15 min de tolerancia, y lo que pida."""
    base = dict(
        fecha=HOY,
        entrada_esperada=time(9, 0),
        salida_esperada=time(18, 30),
        tol_llegada_min=15,
        tol_salida_antes_min=5,
        escaneos_dia=ESCANEOS_OK,
    )
    base.update(kw)
    return base


class LimpiarHora(unittest.TestCase):
    def test_acepta_los_tres_que_puede_devolver_el_driver(self):
        self.assertEqual(limpiar_hora(time(9, 2)), time(9, 2))
        # pymssql a veces lo devuelve como datetime con la fecha de fondo.
        self.assertEqual(limpiar_hora(datetime(1899, 12, 30, 9, 2)), time(9, 2))
        self.assertEqual(limpiar_hora("09:02:30"), time(9, 2, 30))
        self.assertIsNone(limpiar_hora(None))
        self.assertIsNone(limpiar_hora("basura"))


class ClasificarVentana(unittest.TestCase):
    def test_llego_dentro_de_la_tolerancia_es_a_salvo(self):
        v = clasificar_ventana(time(8, 58), time(9, 1), time(9, 0), 15)
        self.assertEqual(v["veredicto"], VEREDICTO_OK)
        self.assertEqual(v["ancho_min"], 3)

    def test_la_ventana_que_cruza_la_tolerancia_es_indeterminada(self):
        # Llegó entre 9:10 y 9:20, tolerancia 15: puede ser puntual o 20 tarde.
        v = clasificar_ventana(time(9, 10), time(9, 20), time(9, 0), 15)
        self.assertEqual(v["veredicto"], VEREDICTO_INDETERMINADO)
        # Con PISO no se le cobra nada: la prueba es que a las 9:10 aún no había
        # llegado. Ese es el punto de la ventana.
        self.assertEqual(aplicar_politica(v, POLITICA_PISO)["aplicado"], 10)
        # Con TECHO sí se le cobran 20.
        self.assertEqual(aplicar_politica(v, POLITICA_TECHO)["aplicado"], 20)
        self.assertEqual(aplicar_politica(v, POLITICA_PROMEDIO)["aplicado"], 15)

    def test_ventana_entera_despues_de_la_tolerancia_es_tarde_afirmable(self):
        # 9:18 a 9:22 con tolerancia 15: la llegada más temprana posible ya es
        # 18 min tarde, o sea que no hay duda. Lo que sí cambia es el número que
        # se cobra: con PISO son 18 (el que le favorece), no 22.
        v = clasificar_ventana(time(9, 18), time(9, 22), time(9, 0), 15)
        self.assertEqual(v["veredicto"], VEREDICTO_TARDE)
        self.assertEqual(aplicar_politica(v, POLITICA_PISO)["aplicado"], 18)
        self.assertEqual(aplicar_politica(v, POLITICA_TECHO)["aplicado"], 22)

    def test_tarde_solo_cuando_el_piso_ya_pasa_la_tolerancia(self):
        # 9:16 a 9:19 con tolerancia 15: el piso ya es 16 tarde, no hay duda.
        v = clasificar_ventana(time(9, 16), time(9, 19), time(9, 0), 15)
        self.assertEqual(v["veredicto"], VEREDICTO_TARDE)
        self.assertEqual(aplicar_politica(v, POLITICA_TECHO)["aplicado"], 19)

    def test_salida_anticipada_afirmable_con_la_misma_formula_invertida(self):
        # Se fue entre 18:20 y 18:24, salida 18:30, tolerancia 5. Lo más
        # temprano que puede haberse ido son las 18:24 = 6 min antes, y 6 > 5:
        # es afirmable. Con la fórmula invertida y el orden bien puesto, el
        # extremo QUE FAVORECE (6) no se confunde con el que castiga (10).
        v = clasificar_ventana(time(18, 20), time(18, 24), time(18, 30), 5,
                               desfavorable=-1)
        self.assertEqual(v["veredicto"], VEREDICTO_TARDE)
        self.assertEqual(aplicar_politica(v, POLITICA_PISO)["aplicado"], 6)
        self.assertEqual(aplicar_politica(v, POLITICA_TECHO)["aplicado"], 10)

    def test_salida_que_cruza_la_tolerancia_es_indeterminada(self):
        """La banda existe también para la salida.

        Se fue entre 18:23 y 18:28 con tolerancia 5: pudo irse 2 min antes
        (nada) o 7 min antes (ya es salida temprana). Sin esto, toda salida
        anticipada se cobraba en su lectura más estricta.
        """
        v = clasificar_ventana(time(18, 23), time(18, 28), time(18, 30), 5,
                               desfavorable=-1)
        self.assertEqual(v["veredicto"], VEREDICTO_INDETERMINADO)
        self.assertEqual(aplicar_politica(v, POLITICA_PISO)["aplicado"], 2)

    def test_salida_muy_anticipada_es_afirmable(self):
        v = clasificar_ventana(time(17, 50), time(17, 52), time(18, 30), 5,
                               desfavorable=-1)
        self.assertEqual(v["veredicto"], VEREDICTO_TARDE)
        self.assertEqual(aplicar_politica(v, POLITICA_PISO)["aplicado"], 38)

    def test_ventana_que_cruza_la_medianoche_se_desempaqueta(self):
        """Turno 22:00–06:00: la ventana 23:58–00:02 es de la madrugada.

        Sin desempaquetar la medianoche, `techo (2) < piso (1438)` salía como
        una ventana de -1436 minutos y TODO el mundo parecía puntual.
        """
        v = clasificar_ventana(time(23, 58), time(0, 2), time(0, 0), 5,
                               desfavorable=-1)
        self.assertTrue(v["cruzando_medianoche"])
        self.assertEqual(v["ancho_min"], 4)
        # Pudo irse 2 min antes de las 00:00 o 2 min después: dentro de la
        # tolerancia de 5, o sea que no hay nada que cobrar.
        self.assertEqual(v["veredicto"], VEREDICTO_OK)

    def test_sin_horas_no_inventa_veredicto(self):
        v = clasificar_ventana(None, None, time(9, 0), 15)
        self.assertEqual(v["veredicto"], VEREDICTO_INDETERMINADO)
        self.assertIsNone(v["ancho_min"])


class EvaluarDia(unittest.TestCase):
    def test_escaneo_caido_es_sin_datos_y_no_ausente(self):
        """El falso positivo más caro: con el escáner caído no se sabe nada."""
        r = evaluar_dia(**_dia(escaneos_dia=0))
        self.assertEqual(r["estado"], ESTADO_SIN_DATOS)
        self.assertFalse(r["ausente"], "sin escaneos NO se marca ausente")
        self.assertIn("no se puede afirmar", r["observaciones"])

    def test_pocos_escaneos_tambien_es_sin_datos(self):
        r = evaluar_dia(**_dia(escaneos_dia=3, escaneos_minimos=20))
        self.assertEqual(r["estado"], ESTADO_SIN_DATOS)
        self.assertFalse(r["ausente"])

    def test_dia_sin_turno_es_no_aplica_y_no_ausente(self):
        """Un sábado sin turno no es una falta."""
        r = evaluar_dia(**_dia(hay_turno=False, entrada_esperada=None,
                               salida_esperada=None))
        self.assertEqual(r["estado"], ESTADO_NO_APLICA)
        self.assertFalse(r["ausente"])

    def test_escaneo_sano_sin_presencia_es_ausente_de_verdad(self):
        r = evaluar_dia(**_dia())
        self.assertEqual(r["estado"], ESTADO_AUSENTE)
        self.assertTrue(r["ausente"])

    def test_puntual_con_ventana(self):
        r = evaluar_dia(**_dia(entrada_piso=time(8, 57), entrada_techo=time(9, 0),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertEqual(r["estado"], ESTADO_CALCULADA)
        self.assertFalse(r["entrada_tardia"])
        self.assertEqual(r["ventana_min"], 3)
        self.assertEqual(r["minutos_tarde"], None)

    def test_tarde_afirmable(self):
        r = evaluar_dia(**_dia(entrada_piso=time(9, 16), entrada_techo=time(9, 19),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertEqual(r["estado"], ESTADO_TARDE)
        self.assertTrue(r["entrada_tardia"])
        self.assertEqual(r["minutos_tarde"], 16)

    def test_ventana_sobre_la_tolerancia_queda_indeterminada(self):
        r = evaluar_dia(**_dia(entrada_piso=time(9, 10), entrada_techo=time(9, 20),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertEqual(r["estado"], ESTADO_INDETERMINADO)
        self.assertFalse(r["entrada_tardia"], "no se le marca tarde sin poder probarlo")
        self.assertTrue(r["indeterminado"])
        self.assertEqual(r["minutos_indeterminados"], 10)

    def test_equipo_encendido_de_madrugada_no_es_una_entrada(self):
        """La caída de antes: el primer escaneo del día era a las 00:15 y el
        cálculo decía 'llegó a las 00:15', o sea puntual todo el día."""
        # El SQL filtra por la ventana del turno, así que el evento de las
        # 00:15 no llega; lo que se evalúa es la ausencia dentro de la ventana.
        r = evaluar_dia(**_dia(entrada_piso=None, entrada_techo=None))
        self.assertEqual(r["estado"], ESTADO_AUSENTE)
        self.assertIn("ventana del turno", r["observaciones"])

    def test_sin_salida_no_es_salida_temprana(self):
        r = evaluar_dia(**_dia(entrada_piso=time(8, 58), entrada_techo=time(9, 0)))
        self.assertFalse(r["salida_temprana"])
        self.assertEqual(r["estado"], ESTADO_CALCULADA)
        self.assertIn("sin salida registrada", r["observaciones"])

    def test_salida_temprana_afirmable(self):
        r = evaluar_dia(**_dia(entrada_piso=time(8, 58), entrada_techo=time(9, 0),
                               salida_piso=time(17, 50), salida_techo=time(17, 52)))
        self.assertEqual(r["estado"], ESTADO_SALIDA_TEMPRANA)
        self.assertTrue(r["salida_temprana"])
        self.assertEqual(r["minutos_antes"], 38)

    def test_evento_sin_evidencia_queda_marcado_incompleto(self):
        """Un evento viejo solo tiene FechaHora: se usa como punto, pero se
        dice que la evidencia está incompleta en vez de fingir un rango."""
        r = evaluar_dia(**_dia(entrada_piso=None, entrada_techo=time(9, 0),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertTrue(r["evidencia_incompleta"])
        self.assertIn("evidencia de escaneo", r["observaciones"])
        self.assertEqual(r["estado"], ESTADO_CALCULADA)

    def test_tolerancia_del_turno_se_usa_de_verdad(self):
        """Regresión: el cálculo comparaba contra el literal 15 y se leía la
        tolerancia del turno sin usarla. Con tolerancia 30 no debe marcar tarde."""
        r = evaluar_dia(**_dia(tol_llegada_min=30,
                               entrada_piso=time(9, 20), entrada_techo=time(9, 22),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertFalse(r["entrada_tardia"])
        self.assertEqual(r["estado"], ESTADO_CALCULADA)

    def test_tolerancia_cero_marca_tarde_a_los_3_minutos(self):
        r = evaluar_dia(**_dia(tol_llegada_min=0,
                               entrada_piso=time(9, 2), entrada_techo=time(9, 4),
                               salida_piso=time(18, 33), salida_techo=time(18, 35)))
        self.assertTrue(r["entrada_tardia"])
        self.assertEqual(r["minutos_tarde"], 2)


class Formateo(unittest.TestCase):
    def test_ventana_con_rango(self):
        self.assertEqual(formatear_ventana(time(8, 57), time(9, 0)),
                         "08:57–09:00 ±3 min")

    def test_ventana_exacta(self):
        self.assertEqual(formatear_ventana(time(9, 0), time(9, 0)),
                         "09:00–09:00 ±0 min")

    def test_ventana_con_un_solo_extremo(self):
        self.assertEqual(formatear_ventana(None, time(9, 0)), "09:00 ±?")

    def test_ventana_vacia(self):
        self.assertEqual(formatear_ventana(None, None), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
