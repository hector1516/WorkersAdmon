"""
tests/test_ui_ops.py — las tres pantallas de operación con el sistema Apple
============================================================================
Asistencia, Triaje y Correo se rediseñaron con las clases de `panel/apple.css`
y los iconos de `panel/icons.py` (ver `panel/DESIGN.md`). Estas pruebas fijan lo
que un rediseño puede romper sin que nadie lo note:

  * el resumen de Asistencia son `.kpi` y el detalle son filas `.list__row`;
  * Triaje lista los pendientes en `.list` con acciones `.btn--sm` y filtros
    `.segmented`;
  * Correo pinta el estado como badge y los buzones en `.list`;
  * los formularios conservan sus `name` (rutas y POST de siempre);
  * ningún `<form>` queda anidado dentro de otro (HTML lo invalida).

Es prueba de HTML puro con dobles: no toca SQL Server, ni el worker, ni el
servidor HTTP. Se fija `WORKERS_DATA_DIR` antes de importar `panel.config` para
que el paquete no cree carpetas de datos en el repo (mismo patrón que
`tests/test_ui.py`).

Ejecución:  python -m unittest tests.test_ui_ops -v
"""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_ROMPER = tempfile.mkdtemp(prefix="panel_ops_")
os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(_ROMPER, "data"))

USER = {"nombre": "Admin", "email": "admin@ecc-sa.com.mx", "perms": {}}


def _sin_form_anidado(html):
    """True si cada `<form>` cierra antes de abrir el siguiente."""
    abiertos = 0
    resto = html
    while True:
        i = resto.find("<form")
        j = resto.find("</form>")
        if i < 0 and j < 0:
            return abiertos == 0
        if i >= 0 and (j < 0 or i < j):
            if abiertos:
                return False
            abiertos += 1
            resto = resto[i + 5:]
        else:
            if not abiertos:
                return False
            abiertos -= 1
            resto = resto[j + 7:]


# ── Asistencia ────────────────────────────────────────────────────────────────

_ASIS = [
    {"UsuarioNombre": "Ana", "TurnoNombre": "Operativo",
     "HoraEntradaEsperada": __import__("datetime").time(9, 0),
     "HoraSalidaEsperada": __import__("datetime").time(18, 30),
     "EntradaPiso": __import__("datetime").time(8, 57),
     "EntradaTecho": __import__("datetime").time(9, 0),
     "SalidaPiso": __import__("datetime").time(18, 33),
     "SalidaTecho": __import__("datetime").time(18, 36),
     "HoraEntradaReal": __import__("datetime").time(9, 0),
     "HoraSalidaReal": __import__("datetime").time(18, 33),
     "VentanaMin": 3, "EscaneosDia": 270, "GapMaximoMin": 3,
     "Estado": "CALCULADA", "Observaciones": ""},
    {"UsuarioNombre": "Beto", "TurnoNombre": "Operativo",
     "HoraEntradaEsperada": __import__("datetime").time(9, 0),
     "HoraSalidaEsperada": __import__("datetime").time(18, 30),
     "EntradaPiso": None, "EntradaTecho": None,
     "SalidaPiso": None, "SalidaTecho": None,
     "HoraEntradaReal": None, "HoraSalidaReal": None,
     "VentanaMin": None, "EscaneosDia": 0, "GapMaximoMin": None,
     "Estado": "SIN_DATOS", "Observaciones": "el escaner no cubrio el dia"},
]


class AsistenciaUI(unittest.TestCase):
    def render(self):
        import panel.views.asistencia as vista
        import panel.db as db

        orig = (db.get_asistencia_fecha, db.get_all_usuario_turnos,
                db.get_ultimo_escaneo)
        db.get_asistencia_fecha = lambda fecha, user_id=None: [dict(a) for a in _ASIS]
        db.get_all_usuario_turnos = lambda: [
            {"IdUsuario": 1, "UsuarioNombre": "Ana"},
            {"IdUsuario": 2, "UsuarioNombre": "Beto"}]
        db.get_ultimo_escaneo = lambda: {"ultimo": None, "equipos": 0}
        try:
            return vista.render(USER, csrf="tok123")
        finally:
            (db.get_asistencia_fecha, db.get_all_usuario_turnos,
             db.get_ultimo_escaneo) = orig

    def test_pinta_kpis_y_filas(self):
        html = self.render()
        # Cuatro cifras del día: presentes, entradas, salidas, sin registro.
        self.assertEqual(html.count('class="kpi"'), 4)
        for etiqueta in ("Presentes", "Entradas", "Salidas", "Sin registro"):
            self.assertIn(etiqueta, html)
        # Una fila por persona, con la ventana y las columnas esperada/real.
        self.assertGreaterEqual(html.count('class="list__row"'), 2)
        self.assertIn('class="list__main"', html)
        self.assertIn("Ana", html)
        self.assertIn("Beto", html)
        self.assertIn("Entrada esp.", html)
        self.assertIn("Salida esp.", html)
        self.assertIn("08:57–09:00 ±3 min", html)
        # El estado es un badge, con palabra (no solo color).
        self.assertIn(">Normal</span>", html)
        self.assertIn(">Sin datos</span>", html)

    def test_iconos_y_controles(self):
        html = self.render()
        self.assertIn("<svg", html)
        self.assertIn('aria-hidden="true"', html)
        # Consultar es GET (no escribe) y conserva sus campos.
        self.assertIn('method="get" action="/asistencia"', html)
        self.assertIn('name="fecha"', html)
        self.assertIn('name="usuario"', html)
        # Calcular es POST con CSRF.
        self.assertIn('method="post" action="/asistencia"', html)
        self.assertIn('name="csrf" value="tok123"', html)
        self.assertIn('name="accion" value="calcular"', html)
        self.assertIn("Calcular y guardar", html)
        self.assertNotIn("🔄", html)
        self.assertNotIn("📅", html)

    def test_formularios_no_anidados(self):
        self.assertTrue(_sin_form_anidado(self.render()))


# ── Triaje ────────────────────────────────────────────────────────────────────

_PEND = [{
    "Id": 11, "Categoria": "huerfano", "Confianza": 0.91,
    "RemitenteEmail": "cliente@fuera.com", "CuentaEmail": "ventas@ecc-sa.com.mx",
    "Destinatario": "jefe@ecc-sa.com.mx", "DestinoSugerido": "ana@ecc-sa.com.mx",
    "Motivo": "huerfano: sospecha 3 enlaces", "Creado": "2026-11-25 08:10",
    "Asunto": "Cotización pendiente", "BorradorTexto": "Hola, adjunto la cotización.",
}]


class TriajeUI(unittest.TestCase):
    def render(self, pendientes=None):
        from mailbox_worker import triaje as t
        import panel.views.triaje as vista

        orig = (t.resumen, t.pendientes, t.listar_bloqueos)
        t.resumen = lambda: {"nueva": 1, "aprobada": 2, "rechazada": 1,
                             "huerfanos_mes": 4, "bloqueos": 2,
                             "auto_rechazados_mes": 9, "sospechosos_mes": 1}
        t.pendientes = lambda n: [dict(p) for p in
                                  (pendientes if pendientes is not None else _PEND)]
        t.listar_bloqueos = lambda: [
            {"Id": 3, "Tipo": "email", "Valor": "spam@molesto.com",
             "Origen": "manual", "Motivo": "rebotes"}]
        try:
            return vista.render(USER, csrf="tok123")
        finally:
            (t.resumen, t.pendientes, t.listar_bloqueos) = orig

    def test_lista_con_acciones(self):
        html = self.render()
        self.assertIn('class="list"', html)
        self.assertIn('class="list__row"', html)
        self.assertIn("cliente@fuera.com", html)
        self.assertIn("Cotización pendiente", html)
        self.assertIn("2026-11-25 08:10", html)
        # Las cuatro acciones del correo pendiente, como botones chicos.
        for valor in ("aprobar", "rechazar", "bloquear_remitente",
                      "bloquear_dominio"):
            self.assertIn(f'value="{valor}"', html)
        self.assertIn("btn--sm", html)
        self.assertIn('name="csrf" value="tok123"', html)
        self.assertIn('name="id" value="11"', html)

    def test_filtros_segmentados_y_bloqueos(self):
        html = self.render()
        self.assertIn('class="segmented"', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn("Por revisar", html)
        self.assertIn("Bloqueos", html)
        self.assertIn('id="bloqueos"', html)
        self.assertIn('name="valor"', html)
        self.assertIn('name="tipo"', html)
        self.assertIn('name="lista"', html)
        self.assertIn('value="desbloquear"', html)
        self.assertIn("spam@molesto.com", html)

    def test_iconos_y_formularios(self):
        html = self.render()
        self.assertIn("<svg", html)
        self.assertNotIn("🛡️", html)
        self.assertTrue(_sin_form_anidado(html))

    def test_sin_pendientes_lo_dice(self):
        html = self.render(pendientes=[])
        self.assertIn("No hay nada por revisar", html)
        self.assertNotIn('id="por-revisar"', html)


# ── Correo ────────────────────────────────────────────────────────────────────

_CUENTAS = [
    {"Id": 1, "Alias": "Ventas", "Email": "ventas@ecc-sa.com.mx",
     "ServidorIMAP": "imap.gmail.com", "PuertoIMAP": 993,
     "ServidorSMTP": "smtp.gmail.com", "PuertoSMTP": 587,
     "TipoAuth": "PASSWORD", "Estado": "ACTIVA", "Color": "#FF6B00",
     "UltimoSync": None, "UltimoError": None, "VentanaDias": 90,
     "MaxMensajes": 5000, "CuantosUsan": 2, "NoLeidos": 7},
    {"Id": 2, "Alias": "Soporte", "Email": "soporte@ecc-sa.com.mx",
     "ServidorIMAP": "imap.gmail.com", "PuertoIMAP": 993,
     "ServidorSMTP": "smtp.gmail.com", "PuertoSMTP": 587,
     "TipoAuth": "PASSWORD", "Estado": "ERROR", "Color": "#EF4444",
     "UltimoSync": None, "UltimoError": "Invalid credentials",
     "VentanaDias": 90, "MaxMensajes": 5000, "CuantosUsan": 0, "NoLeidos": 0},
]
_USUARIOS = [{"Id": 7, "Nombre": "Juan"}, {"Id": 9, "Nombre": "Ana"}]


class CorreoUI(unittest.TestCase):
    def render(self, **kw):
        import panel.db_correo as db
        import panel.views.correo as vista

        orig = (db.listar_cuentas, db.usuarios_activos,
                db.usuarios_con_cuenta, db.obtener_cuenta)
        db.listar_cuentas = lambda: [dict(c) for c in _CUENTAS]
        db.usuarios_activos = lambda: [dict(u) for u in _USUARIOS]
        db.usuarios_con_cuenta = lambda i: [7] if int(i) == 1 else []
        db.obtener_cuenta = lambda i: next(
            (dict(c) for c in _CUENTAS if c["Id"] == int(i)), None)
        try:
            return vista.render(USER, csrf="tok123", **kw)
        finally:
            (db.listar_cuentas, db.usuarios_activos,
             db.usuarios_con_cuenta, db.obtener_cuenta) = orig

    def test_estado_de_cuenta(self):
        html = self.render()
        self.assertIn('class="card"', html)
        self.assertIn('class="badge badge--ok">Activa</span>', html)
        self.assertIn('class="badge badge--bad">Error</span>', html)
        self.assertIn("Último error", html)
        self.assertIn("Invalid credentials", html)
        self.assertIn("2 usuario(s)", html)
        self.assertIn("7 sin leer", html)

    def test_buzones_en_lista(self):
        html = self.render()
        self.assertIn('class="list"', html)
        self.assertIn('class="list__row"', html)
        self.assertIn("Ventas", html)
        self.assertIn("ventas@ecc-sa.com.mx", html)
        self.assertIn("IMAP imap.gmail.com:993", html)
        self.assertIn("btn--sm", html)

    def test_campos_y_password_en_blanco(self):
        html = self.render()
        self.assertIn("Nueva cuenta de correo", html)
        self.assertIn('type="password"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertIn('name="alias"', html)
        self.assertIn('name="email"', html)
        self.assertIn('name="servidor_imap"', html)
        self.assertIn('name="puerto_imap"', html)
        self.assertIn('name="servidor_smtp"', html)
        self.assertIn('name="puerto_smtp"', html)
        self.assertIn('name="tipo_auth"', html)
        self.assertIn('name="ventana_dias"', html)
        self.assertIn('name="max_mensajes"', html)
        # Un formulario NUNCA pinta la credencial guardada.
        self.assertNotIn("CredencialCifrada", html)

    def test_editar_no_cambia_password_si_va_vacia(self):
        html = self.render(editando=1)
        self.assertIn("Editar cuenta", html)
        self.assertNotIn("Nueva cuenta de correo", html)
        self.assertIn("(sin cambios)", html)
        self.assertIn("Dejala vacía para NO cambiar", html)

    def test_asignacion_y_formularios(self):
        html = self.render()
        self.assertIn('value="7" checked', html)
        self.assertIn('value="9"', html)
        self.assertIn('name="accion" value="asignar"', html)
        self.assertIn('name="accion" value="estado"', html)
        self.assertIn('name="accion" value="borrar"', html)
        self.assertTrue(_sin_form_anidado(html))
        self.assertNotIn("✉️", html)


if __name__ == "__main__":
    unittest.main()
