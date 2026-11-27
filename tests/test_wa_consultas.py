"""
Pruebas de las consultas internas del bot de WhatsApp (wa_consultas.py).

Lo que se vigila:

  · que solo se devuelvan datos del propio usuario;
  · que los vales pendientes sean GENERADO y SIN comprobante, con su total;
  · que el último reporte y la lista ignoren los eliminados;
  · que los kilómetros sumen solo el mes en curso;
  · que las cotizaciones respeten la lista autorizada y no inventen nada;
  · que «vale la pena» no dispare la consulta de vales;
  · que un error de base devuelva el texto amable y no una excepción;
  · que un usuario falso no toque la base.

La conexión se dobla en memoria: no se toca SQL Server.

    python -m unittest tests.test_wa_consultas -v
"""
import os
import sys
import unittest
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_consultas  # noqa: E402

AHORA = datetime(2026, 11, 26, 10, 0)
YO = 7
AUTORIZADO = 11


def _coincide(folio, params):
    """Mismo criterio que la consulta: exacto o terminación '...-digitos'."""
    valor = str(folio).upper()
    for param in params:
        candidato = str(param).upper()
        if "%" in candidato:
            if valor.endswith(candidato.replace("%", "")):
                return True
        elif valor == candidato:
            return True
    return False


class BaseFalsa:
    def __init__(self):
        self.vales = []        # (Id, MontoUnit, FechaSolicitud, IdSolicitante, Estatus)
        self.comprobantes = set()
        self.reportes = []     # (Folio, Fecha, Cliente, Estatus, IdUsuario, Eliminado)
        self.tickets = []      # (Folio, Descripcion, FechaRegistro, Estacion, IdUsuario)
        self.km = []           # (Kilometros, FechaHora, IdUsuario)
        self.config = None
        self.servproy = []     # (Folio, IdCliente, Contacto, Total, Fecha, Elaboro)
        self.calc = []         # (FolioCalculo, IdCliente, Total, Fecha)
        self.error = None
        self.sqls = []
        self.consultas = 0


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        base = self.base
        q = " ".join(str(sql).split()).upper()
        base.sqls.append((q, tuple(params)))
        base.consultas += 1
        if base.error:
            raise base.error

        if "FROM HUB_CONFIG" in q:
            self.una = (base.config,) if base.config is not None else None

        elif "FROM HUB_SOLICITUDVALES" in q:
            yo = params[0]
            filas = [v for v in base.vales
                     if v[3] == yo and v[4] == "GENERADO"
                     and v[0] not in base.comprobantes]
            filas.sort(key=lambda v: (v[2], v[0]), reverse=True)
            self.todas = [(v[0], v[1], v[2], len(filas)) for v in filas[:3]]

        elif "FROM REPORTESSERVICIO" in q:
            yo = params[0]
            filas = [r for r in base.reportes if r[4] == yo and not r[5]]
            filas.sort(key=lambda r: (r[1], r[0]), reverse=True)
            limite = 1 if "TOP 1" in q else 5
            self.todas = [(r[0], r[1], r[2], r[3]) for r in filas[:limite]]

        elif "FROM HUB_OXXOGASTICKETS" in q:
            yo = params[0]
            filas = [t for t in base.tickets if t[4] == yo]
            filas.sort(key=lambda t: (t[2], t[0]), reverse=True)
            self.todas = [(t[0], t[1], t[2], t[3]) for t in filas[:3]]

        elif "FROM HUB_REGISTROKILOMETROS" in q:
            yo, desde = params
            filas = [k for k in base.km if k[2] == yo and k[1] >= desde]
            if "SUM(KILOMETROS)" in q:
                if filas:
                    self.una = (sum(k[0] for k in filas), len(filas))
                else:
                    self.una = (None, 0)
            else:
                filas.sort(key=lambda k: k[1], reverse=True)
                self.una = (filas[0][0], filas[0][1]) if filas else None

        elif "FROM INDICE_COTIZACIONESSERVPROY" in q:
            if "TOP 1" in q:
                self.una = next((r for r in base.servproy
                                 if _coincide(r[0], params)), None)
            else:
                filas = sorted(base.servproy, key=lambda r: r[4], reverse=True)
                self.todas = filas[:5]

        elif "FROM INDICE_CALCULOCOTIZACION" in q:
            if "TOP 1" in q:
                self.una = next((r for r in base.calc
                                 if _coincide(r[0], params)), None)
            else:
                filas = sorted(base.calc, key=lambda r: r[3], reverse=True)
                self.todas = filas[:5]

        else:
            raise AssertionError(f"SQL inesperado: {q}")

    def fetchone(self):
        return self.una

    def fetchall(self):
        return self.todas

    def close(self):
        pass


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorFalso(self.base)


class ConsultasWa(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        self.conn = ConexionFalsa(self.base)
        self._ahora = wa_consultas.now_mx
        wa_consultas.now_mx = lambda: AHORA

    def tearDown(self):
        wa_consultas.now_mx = self._ahora

    def responder(self, texto, usuario=YO):
        return wa_consultas.responder(texto, usuario, conn=self.conn)

    # ── vales ────────────────────────────────────────────────────────────────
    def _vales(self):
        doc = datetime(2026, 11, 5)
        self.base.vales = [
            (101, 500.0, datetime(2026, 11, 20), YO, "GENERADO"),
            (102, 750.25, datetime(2026, 11, 25), YO, "GENERADO"),
            (103, 300.0, datetime(2026, 11, 26), YO, "GENERADO"),
            (104, 900.0, datetime(2026, 11, 1), YO, "GENERADO"),
            (105, 100.0, datetime(2026, 11, 10), YO, "APROBADO"),
            (999, 123.0, doc, 8, "GENERADO"),
        ]
        self.base.comprobantes = {104}

    def test_vales_pendientes_sin_comprobante(self):
        self._vales()
        r = self.responder("¿cuáles son mis vales pendientes?")
        self.assertIn("Tienes 3 vales pendientes", r)
        self.assertIn("Vale 103", r)
        self.assertIn("$300.00", r)
        self.assertIn("26 nov 2026", r)
        self.assertIn("Vale 101", r)
        self.assertNotIn("Vale 104", r)      # ya tiene comprobante
        self.assertNotIn("Vale 105", r)      # no está GENERADO
        self.assertNotIn("Vale 999", r)      # es de otro usuario

    def test_consulta_de_vales_filtra_estatus_y_comprobante(self):
        self._vales()
        self.responder("mis vales")
        sqls = [q for q, _ in self.base.sqls]
        self.assertTrue(any("ESTATUS = 'GENERADO'" in q and
                            "NOT EXISTS (SELECT 1 FROM HUB_OXXOGASTICKETS" in q
                            for q in sqls), sqls)

    def test_sin_vales(self):
        r = self.responder("vales pendientes")
        self.assertEqual(r, "No tienes vales pendientes.")

    def test_vale_la_pena_no_dispara_vales(self):
        self._vales()
        r = self.responder("más vale la pena preguntar antes")
        self.assertIsNone(r)
        self.assertEqual(self.base.consultas, 0)

    # ── reportes ─────────────────────────────────────────────────────────────
    def _reportes(self):
        self.base.reportes = [
            (1001, datetime(2026, 11, 20), "ACME", "Borrador", YO, 0),
            (1002, datetime(2026, 11, 25), "Beta", "Firmado", YO, 0),
            (1003, datetime(2026, 11, 21), "Eliminado SA", "Firmado", YO, 1),
            (1004, datetime(2026, 11, 15), "Otra", "Firmado", 8, 0),
        ]

    def test_ultimo_reporte(self):
        self._reportes()
        r = self.responder("¿cuál es mi último reporte?")
        self.assertIn("1002", r)
        self.assertIn("25 nov 2026", r)
        self.assertIn("Beta", r)
        self.assertIn("Firmado", r)
        self.assertNotIn("Eliminado SA", r)

    def test_mis_reportes_lista(self):
        self._reportes()
        r = self.responder("mis reportes")
        self.assertIn("Tus últimos 2 reportes", r)
        self.assertIn("1002", r)
        self.assertIn("1001", r)
        self.assertNotIn("Eliminado SA", r)
        self.assertNotIn("1004", r)

    # ── tickets ──────────────────────────────────────────────────────────────
    def test_tickets(self):
        self.base.tickets = [
            ("T-6", "Carga 10 L", datetime(2026, 11, 1), "Est 100", YO),
            ("T-7", "Carga 20 L", datetime(2026, 11, 5), "Est 100", YO),
            ("T-8", "Carga 30 L", datetime(2026, 11, 10), "Est 200", YO),
            ("T-9", "Carga 40 L", datetime(2026, 11, 26), "Est 200", YO),
            ("T-X", "De otro", datetime(2026, 11, 26), "Est 900", 8),
        ]
        r = self.responder("mis tickets de oxxo")
        self.assertIn("Tus últimos 3 tickets", r)
        self.assertIn("T-9", r)
        self.assertIn("Carga 40 L", r)
        self.assertNotIn("T-6", r)
        self.assertNotIn("T-X", r)

    # ── kilómetros ───────────────────────────────────────────────────────────
    def test_km_del_mes_suma(self):
        self.base.km = [
            (100.0, datetime(2026, 11, 1), YO),
            (250.5, datetime(2026, 11, 15), YO),
            (50.0, datetime(2026, 10, 31), YO),       # mes anterior
            (999.0, datetime(2026, 11, 20), 8),       # otro usuario
        ]
        r = self.responder("mis kilómetros del mes")
        self.assertIn("350.50 km", r)
        self.assertIn("2 registros", r)
        self.assertIn("Último: 250.50 km el 15 nov 2026", r)

    # ── cotizaciones ─────────────────────────────────────────────────────────
    def _autorizado(self):
        self.base.config = "[11, 4, 2]"
        self.base.servproy = [
            ("CSP-2026-0001", "ACME", "Juan Perez", 12345.0,
             datetime(2026, 11, 24), "Daniel"),
            ("C-1234", "Beta", "Ana", 500.0,
             datetime(2026, 11, 20), "Rosy"),
        ]
        self.base.calc = [
            ("CALC-2026-0007", "Gamma", 900.0, datetime(2026, 11, 25)),
        ]

    def test_cotizacion_por_folio_con_prefijo(self):
        self._autorizado()
        r = self.responder("cotización 0001", usuario=AUTORIZADO)
        self.assertIn("CSP-2026-0001", r)
        self.assertIn("$12,345.00", r)
        self.assertIn("24 nov 2026", r)
        self.assertIn("Daniel", r)

    def test_cotizacion_por_folio_corto(self):
        self._autorizado()
        r = self.responder("folio C-1234", usuario=AUTORIZADO)
        self.assertIn("C-1234", r)
        self.assertIn("$500.00", r)
        self.assertIn("Rosy", r)

    def test_cotizaciones_lista_vacia(self):
        self.base.config = "[11, 4, 2]"
        r = self.responder("cotizaciones", usuario=AUTORIZADO)
        self.assertEqual(r, "No hay cotizaciones registradas en el sistema.")

    def test_cotizaciones_lista_une_ambas_tablas_por_fecha(self):
        self._autorizado()
        r = self.responder("últimas cotizaciones", usuario=AUTORIZADO)
        lineas = [linea for linea in r.splitlines() if linea.startswith("- ")]
        self.assertEqual(len(lineas), 3)
        self.assertIn("CALC-2026-0007", lineas[0])   # 25 nov, la más nueva
        self.assertIn("CSP-2026-0001", lineas[1])

    def test_usuario_no_autorizado_a_cotizaciones(self):
        self._autorizado()
        r = self.responder("cotizaciones", usuario=YO)
        self.assertIn("equipo autorizado", r)
        self.assertIn("Priscila", r)
        self.assertFalse(any("INDICE_COTIZACIONES" in q or
                             "INDICE_CALCULOCOTIZACION" in q
                             for q, _ in self.base.sqls))

    def test_sin_config_manda_la_lista_por_defecto(self):
        self.base.config = None
        self.base.servproy = [("CSP-2026-0001", "ACME", "", 10.0,
                               datetime(2026, 11, 24), "Daniel")]
        r = self.responder("cotización CSP-2026-0001", usuario=AUTORIZADO)
        self.assertIn("CSP-2026-0001", r)

    # ── bordes ───────────────────────────────────────────────────────────────
    def test_sin_intencion_no_contesta(self):
        self.assertIsNone(self.responder("hola, ¿cómo están?"))
        self.assertEqual(self.base.consultas, 0)

    def test_usuario_falso_no_consulta(self):
        for falso in (0, None, "x"):
            self.assertIsNone(self.responder("mis vales", usuario=falso))
        self.assertEqual(self.base.consultas, 0)

    def test_error_de_base_devuelve_texto_amable(self):
        self.base.error = RuntimeError("sin SQL Server")
        r = self.responder("mis vales")
        self.assertEqual(r, wa_consultas.MENSAJE_ERROR)


class IntegracionConBot(unittest.TestCase):
    """wa_bot debe consultar solo a los registrados y antes de su flujo."""

    REGISTRADO = "528111111111"
    DESCONOCIDO = "528100000000"
    CHAT = "5218123211516@c.us"

    def setUp(self):
        import wa_bot
        self.wa_bot = wa_bot
        self.kb = {
            "empresa_telefono": "8181234567",
            "empresa_whatsapp": "",
            "direccion": "",
            "horario_texto": wa_bot.HORARIO_TEXTO,
            "horario_lv": "09:00 a 18:30",
            "horario_sab": "09:30 a 13:30",
            "rutas": {},
            "servicios": [],
            "personal": [{"id": 7, "nombre": "Daniel Perez",
                          "telefono": self.REGISTRADO}],
        }
        self._original = wa_consultas.responder
        self.llamadas = []

        def falso(norm, usuario_id, conn=None):
            self.llamadas.append((norm, usuario_id))
            return "Tienes 1 vale pendiente de comprobante."

        wa_consultas.responder = falso

    def tearDown(self):
        wa_consultas.responder = self._original

    def test_registrado_recibe_la_consulta_interna(self):
        r = self.wa_bot.responder(self.CHAT, self.REGISTRADO, "mis vales",
                                  kb=self.kb)
        self.assertIn("1 vale pendiente", r)
        self.assertEqual(self.llamadas, [("mis vales", 7)])

    def test_desconocido_no_consulta_la_base(self):
        self.wa_bot.responder(self.CHAT, self.DESCONOCIDO, "mis vales",
                              kb=self.kb)
        self.assertEqual(self.llamadas, [])


if __name__ == "__main__":
    unittest.main()
