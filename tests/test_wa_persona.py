"""
Pruebas de los hechos y el sarcasmo por usuario (wa_persona.py).

Lo que se vigila:

  · que los hechos sean SOLO del propio usuario (vales, reportes, km, tickets);
  · que las llegadas tarde salgan del primer escaneo contra el turno;
  · que el sarcasmo use un hecho real y no invente cuando no hay datos;
  · que un insulto se detecte por palabra completa (no dentro de otra) y se
    responda con reconocimiento seco y un hecho real;
  · que no repita la frase anterior de la misma persona;
  · que con tres o más ausencias seguidas se calle (texto neutro);
  · que las cotizaciones solo cuenten si está en `wa_bot_cotizaciones`;
  · que la cache de 5 minutos evite repetir consultas y que `olvidar` la tire;
  · que un error de base no lance y deje los hechos en cero.

La conexión se dobla en memoria: no se toca SQL Server.

    python -m unittest tests.test_wa_persona -v
"""
import os
import sys
import unittest
from datetime import date, datetime, time as hora_del_dia, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_asistencia  # noqa: E402
import wa_persona  # noqa: E402

YO = 6
OTRO = 5
AHORA = datetime(2026, 11, 26, 10, 0)          # jueves 26-nov-2026
MES = datetime(2026, 11, 1)


class BaseFalsa:
    """Dobles de las tablas que lee `wa_persona` (y los km de `wa_consultas`)."""

    def __init__(self):
        self.nombres = {YO: "Héctor Salas"}
        self.vales = []          # (Id, IdSolicitante, Estatus, Fecha, Comprobante)
        self.reportes = []       # (Folio, Fecha, Cliente, Estatus, IdUsuario, Eliminado)
        self.tickets = []        # (IdUsuario, FechaRegistro)
        self.km = []             # (Kilometros, FechaHora, IdUsuario, IdAutomovil)
        self.cotizaciones = []   # (Autor, Fecha)
        self.config = None
        self.error = None
        self.consultas = 0

    def cursor(self):
        return CursorFalso(self)


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        base = self.base
        q = " ".join(str(sql).split()).upper()
        base.consultas += 1
        if base.error:
            raise base.error
        self.una, self.todas = None, []

        if "FROM HUB_SOLICITUDVALES" in q:
            yo = params[0]
            filas = [v for v in base.vales
                     if v[1] == yo and v[2] == "GENERADO" and not v[4]]
            self.una = (len(filas), min((v[3] for v in filas), default=None))

        elif "FROM REPORTESSERVICIO" in q:
            yo = params[0]
            filas = [r for r in base.reportes if r[4] == yo and not r[5]]
            filas.sort(key=lambda r: (r[1], r[0]), reverse=True)
            self.una = tuple(filas[0][:4]) if filas else None

        elif "FROM HUB_OXXOGASTICKETS" in q:
            yo, desde = params
            self.una = (len([t for t in base.tickets
                             if t[0] == yo and t[1] >= desde]),)

        elif "FROM HUB_REGISTROKILOMETROS" in q:
            yo, desde = params
            filas = [k for k in base.km if k[2] == yo and k[1] >= desde]
            if "GROUP BY IDAUTOMOVIL" in q:
                autos = {}
                for k in filas:
                    autos.setdefault(k[3], []).append(k)
                self.todas = [(auto, min(x[0] for x in ks), max(x[0] for x in ks),
                               len(ks)) for auto, ks in sorted(autos.items())]
            else:
                filas.sort(key=lambda k: k[1], reverse=True)
                self.una = (filas[0][0], filas[0][1]) if filas else None

        elif "FROM HUB_CONFIG" in q:
            self.una = (base.config,) if base.config is not None else None

        elif "FROM INDICEMATERIALES" in q:
            ids = params
            autor = next((n for i, n in base.nombres.items() if i == ids[0]), "")
            desde = ids[1]
            self.una = (len([c for c in base.cotizaciones
                             if c[0] == autor and c[1] >= desde]),)

        elif "FROM HUB_USERS WHERE ID = %S" in q:
            self.una = ((base.nombres.get(params[0]),)
                        if params[0] in base.nombres else None)

    def fetchone(self):
        return self.una

    def fetchall(self):
        return self.todas

    def close(self):
        return None


class PersonaBase(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()
        self._horario = wa_asistencia.horario_dia
        self._primeras = wa_asistencia._primera_por_dia
        self._ausencias = wa_asistencia._ausencias_seguidas
        self.dias = []

        def horario(conn, usuario_id, dia):
            return None if dia.weekday() >= 5 else \
                (hora_del_dia(9, 0), hora_del_dia(18, 30), 10)

        def primeras(conn, usuario_id, desde, hasta):
            return {dia: datetime.combine(dia, hora_del_dia(9, 40))
                    for dia in self.dias}

        wa_asistencia.horario_dia = horario
        wa_asistencia._primera_por_dia = primeras
        wa_asistencia._ausencias_seguidas = lambda conn, uid, hoy: False

    def tearDown(self):
        wa_asistencia.horario_dia = self._horario
        wa_asistencia._primera_por_dia = self._primeras
        wa_asistencia._ausencias_seguidas = self._ausencias
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()

    def hechos(self):
        return wa_persona.hechos(YO, conn=self.base, ahora=AHORA)

    def sarcasmo(self, datos=None):
        if datos is None:
            datos = self.hechos()
        return wa_persona.sarcasmo(YO, hechos=datos)


class Hechos(PersonaBase):
    def test_nombre_y_hechos_vacios(self):
        datos = self.hechos()
        self.assertEqual(datos["nombre"], "Héctor Salas")
        self.assertEqual(datos["vales"], {"total": 0, "mas_viejo": None})
        self.assertIsNone(datos["reporte"])
        self.assertEqual(datos["tardes"], 0)
        self.assertIsNone(datos["cotizaciones"])     # no autorizado

    def test_vales_sin_comprobante_y_el_mas_viejo(self):
        self.base.vales = [
            (1, YO, "GENERADO", datetime(2026, 10, 3), False),
            (2, YO, "GENERADO", datetime(2026, 11, 20), False),
            (3, YO, "GENERADO", datetime(2026, 11, 21), True),   # con ticket
            (4, OTRO, "GENERADO", datetime(2026, 11, 22), False),  # ajeno
        ]
        datos = self.hechos()
        self.assertEqual(datos["vales"]["total"], 2)
        self.assertEqual(datos["vales"]["mas_viejo"], datetime(2026, 10, 3))

    def test_ultimo_reporte_sin_eliminados(self):
        self.base.reportes = [
            ("RS-1", datetime(2026, 11, 24), "Cliente A", "FIRMADO", YO, False),
            ("RS-2", datetime(2026, 11, 25), "Cliente B", "BORRADOR", YO, True),
            ("RS-3", datetime(2026, 11, 25), "Cliente C", "FIRMADO", OTRO, False),
        ]
        datos = self.hechos()
        self.assertEqual(datos["reporte"]["folio"], "RS-1")
        self.assertEqual(datos["reporte"]["fecha"], datetime(2026, 11, 24))

    def test_kilometros_del_mes_y_ultimo_odometro(self):
        self.base.km = [
            (179000, datetime(2026, 11, 3), YO, 1),
            (179679, datetime(2026, 11, 25), YO, 1),
            (900000, datetime(2026, 10, 31), YO, 1),     # mes anterior
            (50000, datetime(2026, 11, 25), OTRO, 1),    # ajeno
        ]
        datos = self.hechos()
        self.assertEqual(datos["km_mes"], 679)
        self.assertEqual(datos["odometro"], 179679)

    def test_tickets_del_mes(self):
        self.base.tickets = [
            (YO, datetime(2026, 11, 4)),
            (YO, datetime(2026, 11, 19)),
            (YO, datetime(2026, 10, 30)),   # mes anterior
            (OTRO, datetime(2026, 11, 19)),
        ]
        self.assertEqual(self.hechos()["tickets_mes"], 2)

    def test_llegadas_tarde_contra_el_turno(self):
        # Últimos días hábiles antes del jueves 26: 25, 24, 23, 20 y 19.
        self.dias = [date(2026, 11, 25), date(2026, 11, 24),
                     date(2026, 11, 23), date(2026, 11, 20),
                     date(2026, 11, 19)]
        datos = self.hechos()
        self.assertEqual(datos["tardes_dias"], 5)
        self.assertEqual(datos["tardes"], 5)      # 09:40 contra 09:00 + 10

    def test_llegada_dentro_de_la_tolerancia_no_cuenta(self):
        self.dias = [date(2026, 11, 25)]
        wa_asistencia._primera_por_dia = lambda conn, uid, d, h: {
            date(2026, 11, 25): datetime(2026, 11, 25, 9, 5)}
        self.assertEqual(self.hechos()["tardes"], 0)

    def test_cotizaciones_solo_si_esta_autorizado(self):
        self.base.cotizaciones = [("Héctor Salas", datetime(2026, 11, 12))]
        self.assertIsNone(self.hechos()["cotizaciones"])

        self.base.config = "[6, 11]"
        wa_persona.olvidar()
        self.assertEqual(self.hechos()["cotizaciones"], 1)

    def test_cache_cinco_minutos(self):
        self.hechos()
        primera = self.base.consultas
        self.hechos()
        self.assertEqual(self.base.consultas, primera)

        wa_persona.olvidar(YO)
        self.hechos()
        self.assertGreater(self.base.consultas, primera)

    def test_error_de_base_no_lanza(self):
        self.base.error = RuntimeError("sin SQL Server")
        datos = self.hechos()
        self.assertEqual(datos["vales"]["total"], 0)
        self.assertEqual(datos["tardes"], 0)

    def test_usuario_falso_no_consulta(self):
        for falso in (0, None, "x"):
            datos = wa_persona.hechos(falso, conn=self.base)
            self.assertEqual(datos["vales"]["total"], 0)
            self.assertEqual(wa_persona.sarcasmo(falso, conn=self.base), "")
        self.assertEqual(self.base.consultas, 0)


class Sarcasmo(PersonaBase):
    def test_con_vales(self):
        self.base.vales = [(i, YO, "GENERADO", datetime(2026, 11, 20), False)
                           for i in (1, 2, 3)]
        frases = [self.sarcasmo(), self.sarcasmo()]
        self.assertTrue(any("coleccionando" in f for f in frases), frases)
        self.assertTrue(any("3 vales" in f for f in frases), frases)
        for frase in frases:
            self.assertTrue(frase)
            self.assertEqual(len(frase.splitlines()), 1)

    def test_con_llegadas_tarde(self):
        self.dias = [date(2026, 11, 25), date(2026, 11, 24),
                     date(2026, 11, 23), date(2026, 11, 20),
                     date(2026, 11, 19)]
        frases = {self.sarcasmo(), self.sarcasmo()}
        self.assertTrue(any("tarde" in f for f in frases), frases)
        for frase in frases:
            self.assertTrue(frase)

    def test_con_odometro(self):
        self.base.km = [(179679, datetime(2026, 11, 25), YO, 1)]
        frases = {self.sarcasmo(), self.sarcasmo()}
        self.assertTrue(any("179,679" in f for f in frases), frases)

    def test_sin_datos_no_inventa(self):
        self.assertEqual(self.sarcasmo(), "")

    def test_no_repite_la_frase_anterior(self):
        self.base.vales = [(1, YO, "GENERADO", datetime(2026, 11, 20), False),
                           (2, YO, "GENERADO", datetime(2026, 11, 21), False)]
        self.base.km = [(179679, datetime(2026, 11, 25), YO, 1)]
        datos = wa_persona.hechos(YO, conn=self.base)
        primera = wa_persona.sarcasmo(YO, hechos=datos)
        segunda = wa_persona.sarcasmo(YO, hechos=datos)
        self.assertTrue(primera)
        self.assertTrue(segunda)
        self.assertNotEqual(primera, segunda)

    def test_tres_ausencias_seguidas_callan_el_sarcasmo(self):
        self.base.vales = [(1, YO, "GENERADO", datetime(2026, 11, 20), False)]
        wa_asistencia._ausencias_seguidas = lambda conn, uid, hoy: True
        self.assertEqual(self.sarcasmo(), "")

    def test_sin_nombre_la_frase_sigue_siendo_una_linea(self):
        self.base.nombres = {}
        self.base.vales = [(1, YO, "GENERADO", datetime(2026, 11, 20), False)]
        frase = self.sarcasmo()
        self.assertTrue(frase)
        self.assertNotIn("\n", frase)


class Insultos(unittest.TestCase):
    """Detección de insultos y respuesta seca con un hecho propio."""

    def test_detecta_insulto_con_y_sin_acentos(self):
        for texto in ("esta bien eso bot menso, son cada lunes",
                      "CÁLLATE", "no sirves", "pinche bot", "me cae mal"):
            self.assertTrue(wa_persona.es_insulto(texto), texto)

    def test_no_dispara_dentro_de_otra_palabra(self):
        for texto in ("inmenso trabajo", "mensos no son", "pensamiento",
                      "el bot es bueno", "", None):
            self.assertFalse(wa_persona.es_insulto(texto), repr(texto))

    def test_con_hecho_real_remata_con_el(self):
        datos = {"usuario_id": YO, "nombre": "Héctor Salas",
                 "vales": {"total": 3, "mas_viejo": datetime(2026, 11, 2)},
                 "ausencias_seguidas": False}
        wa_persona._ULTIMA.clear()
        r = wa_persona.respuesta_insulto(
            {"id": YO, "nombre": "Héctor Salas"}, datos, "bot menso")
        self.assertTrue(r.startswith("Acepto la queja"), r)
        self.assertIn("vale", r)
        self.assertNotIn("\n", r)

    def test_sin_hechos_responde_seco(self):
        r = wa_persona.respuesta_insulto(
            {"id": YO, "nombre": "Héctor Salas"}, {}, "menso")
        self.assertIn("Acepto la queja, Héctor", r)
        self.assertIn(wa_persona.CIERRE_SECO, r)

    def test_con_roast_apagado_solo_reconoce(self):
        datos = {"usuario_id": YO, "nombre": "Héctor Salas",
                 "vales": {"total": 3, "mas_viejo": None}}
        r = wa_persona.respuesta_insulto(
            {"id": YO, "nombre": "Héctor Salas"}, datos, "menso",
            con_roast=False)
        self.assertEqual(r, "Acepto la queja, Héctor.")


if __name__ == "__main__":
    unittest.main()
