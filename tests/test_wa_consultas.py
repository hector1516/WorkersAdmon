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


class BaseFalsa:
    def __init__(self):
        self.vales = []        # (Id, MontoUnit, FechaSolicitud, IdSolicitante, Estatus)
        self.comprobantes = set()
        self.reportes = []     # (Folio, Fecha, Cliente, Estatus, IdUsuario, Eliminado)
        self.tickets = []      # (Folio, Descripcion, FechaRegistro, Estacion, IdUsuario)
        self.km = []           # (Kilometros, FechaHora, IdUsuario, IdAutomovil)
        self.materiales = []   # (Folio, IdCliente, Cliente, Contacto, Fecha, Autor,
                               #  Color, Descripcion, Nota)
        self.partidas_mat = []  # (Folio, Partida, Cantidad, Descripcion,
                                #  PrecioCompra, Factor, Flete)
        self.config = None
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
            if "GROUP BY IDAUTOMOVIL" in q:
                autos = {}
                for k in filas:
                    auto = k[3] if len(k) > 3 else 1
                    autos.setdefault(auto, []).append(k)
                self.todas = [(auto, min(k[0] for k in ks), max(k[0] for k in ks),
                               len(ks)) for auto, ks in sorted(autos.items())]
            else:
                filas.sort(key=lambda k: k[1], reverse=True)
                self.una = (filas[0][0], filas[0][1]) if filas else None

        elif "FROM INDICEMATERIALES I" in q:
            if "WHERE I.FOLIO = %S" in q:
                folio = int(params[0])
                fila = next((m for m in base.materiales if int(m[0]) == folio), None)
                self.una = tuple(fila) if fila else None
            else:
                filas = sorted(base.materiales, key=lambda m: (m[4], m[0]), reverse=True)
                self.todas = []
                for m in filas[:5]:
                    partidas = [p for p in base.partidas_mat if int(p[0]) == int(m[0])]
                    suma = sum((float(p[4]) * (1 + float(p[5]))) * p[2] for p in partidas)
                    flete = max((float(p[6] or 0) for p in partidas), default=0)
                    self.todas.append((m[0], m[1], m[2], m[3], m[4], m[5], m[6],
                                       suma, flete))

        elif "FROM PARTIDAS" in q:
            folio = int(params[0])
            filas = [p for p in base.partidas_mat if int(p[0]) == folio]
            if "COUNT(*)" in q:
                self.una = (len(filas),)
            elif "SUM(" in q:
                suma = sum((float(p[4]) * (1 + float(p[5]))) * p[2] for p in filas)
                flete = max((float(p[6] or 0) for p in filas), default=0)
                self.una = (suma, flete)
            else:
                filas.sort(key=lambda p: p[1])
                self.todas = [(p[1], p[2], p[3], float(p[4]), float(p[5]),
                               float(p[4]) * (1 + float(p[5])),
                               (float(p[4]) * (1 + float(p[5]))) * p[2],
                               (p[7] if len(p) > 7 else ""),
                               (p[8] if len(p) > 8 else 0),
                               (p[9] if len(p) > 9 else 0))
                              for p in filas[:10]]

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
            (100.0, datetime(2026, 11, 1), YO, 1),
            (250.0, datetime(2026, 11, 15), YO, 1),
            (50.0, datetime(2026, 10, 31), YO, 1),       # mes anterior
            (999.0, datetime(2026, 11, 20), 8, 1),       # otro usuario
        ]
        r = self.responder("mis kilómetros del mes")
        self.assertIn("150 km recorridos", r)
        self.assertIn("2 registros", r)
        self.assertIn("Último odómetro: 250 km el 15 nov 2026", r)

    # ── cotizaciones de materiales (IndiceMateriales + Partidas, Admon) ──────
    def _cotizaciones(self):
        self.base.config = "[11, 4, 2]"
        self.base.materiales = [
            (1329, "CL01", "HEDESA Hermosillo", "Ana Lopez",
             datetime(2026, 11, 25), "Daniel Quintanilla", 0, "PLC", ""),
            (1328, "CL02", "Parker Hannifin", "Luis Ruiz",
             datetime(2026, 11, 23), "Alejandro Mata", 2, "Sensores", ""),
        ]
        self.base.partidas_mat = [
            (1329, 1, 2, "Tornillo 1/4", 100.0, 0.5, 0.0, "Siemens", 5, 0.0),
            (1329, 2, 1, "Tuerca 1/4", 100.0, 0.0, 0.0, "", 0, 0.0),
            (1329, 3, 1, "Arandela", 40.0, 0.5, 0.0, "", 0, 18.5),
            (1329, 4, 1, "No debe salir", 999.0, 0.0, 0.0, "", 0, 0.0),
            (1328, 1, 1, "Cable calibre 12", 100.0, 0.0, 50.0, "", 0, 0.0),
        ]

    def test_cotizaciones_materiales_lista(self):
        self._cotizaciones()
        r = self.responder("cotizaciones", usuario=AUTORIZADO)
        self.assertIn("Últimas 2 cotizaciones de materiales", r)
        self.assertIn("1329", r)
        self.assertIn("HEDESA Hermosillo", r)
        self.assertIn("25 nov 2026", r)
        self.assertIn("$1,459.00", r)        # 300 + 100 + 60 + 999
        self.assertIn("PENDIENTE", r)
        self.assertIn("PAGADA", r)
        self.assertIn("1328", r)

    def test_cotizacion_por_folio_con_partidas_y_total(self):
        self._cotizaciones()
        r = self.responder("cotización 1329", usuario=AUTORIZADO)
        self.assertIn("Cotización 1329", r)
        self.assertIn("HEDESA Hermosillo", r)
        self.assertIn("Contacto: Ana Lopez", r)
        self.assertIn("25 nov 2026", r)
        self.assertIn("Elaboró: Daniel Quintanilla", r)
        self.assertIn("Estatus: PENDIENTE", r)
        self.assertIn("Subtotal partidas: $1,459.00", r)
        self.assertIn("Total: $1,459.00", r)
        self.assertIn("Tornillo 1/4", r)
        self.assertIn("costo $100.00", r)
        self.assertIn("factor 50%", r)
        self.assertIn("venta $150.00 c/u", r)
        self.assertIn("prov: Siemens", r)
        self.assertIn("entrega: 5 d", r)
        self.assertIn("dólar 18.50", r)
        self.assertIn("Arandela", r)

    def test_cotizacion_inexistente(self):
        self._cotizaciones()
        r = self.responder("cotización 9999", usuario=AUTORIZADO)
        self.assertEqual(r, "No encontré la cotización 9999.")

    def test_folio_con_letras_no_se_recorta_a_digitos(self):
        self._cotizaciones()
        r = self.responder("cotización 3K17WZ", usuario=AUTORIZADO)
        self.assertEqual(r, "No encontré la cotización 3K17WZ.")

    def test_cotizaciones_tabla_vacia(self):
        self.base.config = "[11, 4, 2]"
        r = self.responder("cotizaciones", usuario=AUTORIZADO)
        self.assertEqual(r, "No hay cotizaciones de materiales registradas.")

    def test_usuario_no_autorizado_a_cotizaciones(self):
        self._cotizaciones()
        r = self.responder("cotizaciones", usuario=YO)
        self.assertIn("equipo autorizado", r)
        self.assertIn("Priscila", r)
        self.assertFalse(any("INDICEMATERIALES" in q for q, _ in self.base.sqls))

    def test_sin_config_manda_la_lista_por_defecto(self):
        self.base.config = None
        self._cotizaciones()
        r = self.responder("cotización 1329", usuario=AUTORIZADO)
        self.assertIn("1329", r)

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
