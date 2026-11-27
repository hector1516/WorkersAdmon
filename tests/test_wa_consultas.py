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
        self.km = []           # (Kilometros, FechaHora, IdUsuario)
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


class BaseQuotely:
    """Dobles de la base `quotely` (cotizaciones de materiales)."""

    def __init__(self):
        self.indice = []       # (Folio, Empresa, Contacto, Fecha, Usuario)
        self.partidas = []     # (Folio, Partida, Cantidad, UME, Descripcion, PrecioUnitario)
        self.usuarios = {}     # ID -> nombre
        self.sqls = []
        self.consultas = 0


class CursorQuotely:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        base = self.base
        q = " ".join(str(sql).split()).upper()
        base.sqls.append((q, tuple(params)))
        base.consultas += 1

        if "FROM TAB_INDICE" in q:
            if "WHERE UPPER(LTRIM(RTRIM(I.FOLIO)))" in q:
                folio = str(params[0]).upper()
                fila = next((r for r in base.indice
                             if str(r[0]).strip().upper() == folio), None)
                if fila:
                    elaboro = base.usuarios.get(str(fila[4] or "").strip(), "")
                    self.una = (fila[0], fila[1], fila[2], fila[3], elaboro)
                else:
                    self.una = None
            else:
                filas = sorted(base.indice, key=lambda r: r[3], reverse=True)
                self.todas = []
                for fila in filas[:5]:
                    total = sum(p[2] * p[5] for p in base.partidas
                                if str(p[0]).strip().upper() ==
                                str(fila[0]).strip().upper())
                    self.todas.append((fila[0], fila[1], fila[3], total))

        elif "FROM TAB_PARTIDAS" in q:
            folio = str(params[0]).upper()
            filas = [p for p in base.partidas
                     if str(p[0]).strip().upper() == folio]
            if "SUM(CANTIDAD * PRECIOUNITARIO)" in q:
                self.una = (sum(p[2] * p[5] for p in filas),)
            else:
                filas.sort(key=lambda p: p[1])
                self.todas = [(p[1], p[2], p[3], p[4], p[5], p[2] * p[5])
                              for p in filas[:3]]
        else:
            raise AssertionError(f"SQL inesperado en quotely: {q}")

    def fetchone(self):
        return self.una

    def fetchall(self):
        return self.todas

    def close(self):
        pass


class ConexionQuotely:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorQuotely(self.base)


class ConsultasWa(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        self.conn = ConexionFalsa(self.base)
        self.qbase = BaseQuotely()
        self.quotely = ConexionQuotely(self.qbase)
        self._ahora = wa_consultas.now_mx
        wa_consultas.now_mx = lambda: AHORA

    def tearDown(self):
        wa_consultas.now_mx = self._ahora

    def responder(self, texto, usuario=YO):
        return wa_consultas.responder(texto, usuario, conn=self.conn,
                                      conn_quotely=self.quotely)

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

    # ── cotizaciones (materiales, base quotely) ──────────────────────────────
    def _cotizaciones(self):
        self.base.config = "[11, 4, 2]"
        self.qbase.usuarios = {"USR001": "Juan Perez", "USR002": "Luis Ruiz"}
        self.qbase.indice = [
            ("3K17WZ", "HEDESA Hermosillo", "Ana Lopez",
             datetime(2026, 5, 8), "USR001"),
            ("2I1AYL", "HEDESA Hermosillo", "Luis Ruiz",
             datetime(2026, 3, 31), "USR002"),
        ]
        self.qbase.partidas = [
            ("3K17WZ", 1, 2, "pza", "Tornillo 1/4", 10.0),
            ("3K17WZ", 2, 5, "pza", "Tuerca 1/4", 4.5),
            ("3K17WZ", 3, 1, "pza", "Arandela", 2.0),
            ("3K17WZ", 4, 3, "pza", "No debe salir", 99.0),
            ("2I1AYL", 1, 1, "pza", "Cable calibre 12", 100.0),
        ]

    def test_cotizaciones_materiales_lista(self):
        self._cotizaciones()
        r = self.responder("cotizaciones", usuario=AUTORIZADO)
        self.assertIn("Últimas 2 cotizaciones de materiales", r)
        self.assertIn("3K17WZ", r)
        self.assertIn("HEDESA Hermosillo", r)
        self.assertIn("$341.50", r)         # 20 + 22.5 + 2 + 297 (todas las partidas)
        self.assertIn("8 may 2026", r)
        self.assertIn("2I1AYL", r)

    def test_cotizacion_por_folio_con_partidas_y_total(self):
        self._cotizaciones()
        r = self.responder("cotización 3k17wz", usuario=AUTORIZADO)
        self.assertIn("Cotización 3K17WZ", r)
        self.assertIn("HEDESA Hermosillo", r)
        self.assertIn("Contacto: Ana Lopez", r)
        self.assertIn("8 may 2026", r)
        self.assertIn("Elaboró: Juan Perez", r)
        self.assertIn("Total: $341.50", r)  # 20 + 22.5 + 2 + 297
        self.assertIn("Tornillo 1/4", r)
        self.assertIn("$10.00 c/u", r)
        self.assertIn("$20.00", r)
        self.assertIn("Arandela", r)
        self.assertNotIn("No debe salir", r)   # solo las primeras 3 partidas

    def test_cotizacion_con_guiones_y_minusculas(self):
        self._cotizaciones()
        r = self.responder("folio 3K-17WZ", usuario=AUTORIZADO)
        self.assertIn("Cotización 3K17WZ", r)

    def test_cotizacion_inexistente(self):
        self._cotizaciones()
        r = self.responder("cotización ZZZ999", usuario=AUTORIZADO)
        self.assertEqual(r, "No encontré la cotización ZZZ999.")

    def test_cotizaciones_tabla_vacia(self):
        self.base.config = "[11, 4, 2]"
        r = self.responder("cotizaciones", usuario=AUTORIZADO)
        self.assertEqual(r, "No hay cotizaciones de materiales registradas.")

    def test_usuario_no_autorizado_a_cotizaciones(self):
        self._cotizaciones()
        r = self.responder("cotizaciones", usuario=YO)
        self.assertIn("equipo autorizado", r)
        self.assertIn("Priscila", r)
        self.assertEqual(self.qbase.consultas, 0)   # no se lee quotely

    def test_usa_la_conexion_quotely_falsa(self):
        self._cotizaciones()
        original = wa_consultas._abrir_quotely

        def prohibido():
            raise AssertionError("no debe abrir la conexión real a quotely")

        wa_consultas._abrir_quotely = prohibido
        try:
            r = self.responder("cotizaciones", usuario=AUTORIZADO)
        finally:
            wa_consultas._abrir_quotely = original
        self.assertIn("3K17WZ", r)
        self.assertGreater(self.qbase.consultas, 0)

    def test_sin_config_manda_la_lista_por_defecto(self):
        self.base.config = None
        self.qbase.indice = [
            ("3K17WZ", "HEDESA Hermosillo", "", datetime(2026, 5, 8), "USR001"),
        ]
        r = self.responder("cotización 3K17WZ", usuario=AUTORIZADO)
        self.assertIn("3K17WZ", r)

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
