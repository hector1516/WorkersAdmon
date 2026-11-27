"""
Consultas internas de solo lectura del bot de WhatsApp
======================================================

`responder(norm, usuario_id, conn=None, conn_quotely=None)` contesta datos
PROPIOS del usuario registrado (vales, reportes, tickets, kilómetros y
cotizaciones). Nunca crea ni modifica nada y nunca consulta datos de otra
persona.

Contrato:

    responder(norm, usuario_id, conn=None, conn_quotely=None) -> str | None

- `norm` es el mensaje ya normalizado por `wa_bot`; aquí se vuelve a normalizar
  por si llega crudo.
- `usuario_id` es el `HUB_Users.Id` del que escribe. Si es falso, no se toca la
  base y se devuelve `None`.
- `conn=None` usa `eccsa_db.get_connection()` (base del HUB); los tests pasan
  una conexión falsa, así que este módulo no importa SQL Server al cargarse.
- `conn_quotely=None` abre la base `quotely` (cotizaciones de materiales). Si
  se pasa una conexión falsa, NO se abre la real.
- `None` significa "no hay una consulta interna clara": el bot sigue su flujo
  normal.
- Cualquier error de base devuelve un texto corto y se registra en el log; este
  motor nunca lanza.

Orden de temas (la primera coincidencia gana): vales, reportes, tickets,
kilómetros, cotizaciones.

    python -m unittest tests.test_wa_consultas -v
"""
import json
import re
import unicodedata
from datetime import date, datetime

from tz_mx import now_mx

MENSAJE_ERROR = "No pude consultar eso en este momento; intenta más tarde."
MENSAJE_COTIZACIONES_CERRADAS = ("Las cotizaciones solo las consulta el equipo "
                                 "autorizado; te canalizo con Priscila.")
MENSAJE_SIN_COTIZACIONES = "No hay cotizaciones de materiales registradas."

CLAVE_COTIZACIONES = "wa_bot_cotizaciones"
IDS_COTIZACIONES_DEFECTO = (11, 4, 2, 9, 3, 10)

MESES = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")

# Orden de temas definido por el dueño: la primera coincidencia gana.
PALABRAS = (
    ("vales", ("vale", "vales", "comprobante", "gasolina")),
    ("reportes", ("reporte", "reportes", "servicio")),
    ("tickets", ("ticket", "tickets", "oxxo")),
    ("kilometros", ("kilometro", "kilometros", "km", "recorrido")),
    ("cotizaciones", ("cotizacion", "cotizaciones", "cotiza", "folio")),
)

_FOLIO_RE = re.compile(
    r"\b(?:folio|cotizaciones|cotizacion|cotiza)\b\s*"
    r"(?:numero|num\.?|no\.?|#|:)?\s*"
    r"([a-z0-9]{2,12}(?:\s*-\s*[a-z0-9]{1,12})?)\b")

SQL_VALES = """
    SELECT TOP 3 v.Id, v.MontoUnit, v.FechaSolicitud, COUNT(*) OVER () AS Total
    FROM HUB_SolicitudVales v
    WHERE v.IdSolicitante = %s
      AND v.Estatus = 'GENERADO'
      AND NOT EXISTS (SELECT 1 FROM HUB_OxxoGasTickets t
                      WHERE t.IdSolicitudVale = v.Id)
    ORDER BY v.FechaSolicitud DESC, v.Id DESC
"""

SQL_REPORTES = """
    SELECT TOP {n} r.Folio, r.Fecha, r.Cliente, r.Estatus
    FROM ReportesServicio r
    JOIN ReportesServicioTecnicos t ON t.IdReporte = r.IdReporte
    WHERE t.IdUsuario = %s AND ISNULL(r.Eliminado, 0) = 0
    ORDER BY r.Fecha DESC, r.IdReporte DESC
"""

SQL_TICKETS = """
    SELECT TOP 3 FolioTicket, Descripcion, FechaRegistro, Estacion
    FROM HUB_OxxoGasTickets
    WHERE IdUsuario = %s
    ORDER BY FechaRegistro DESC, Id DESC
"""

SQL_KM_RESUMEN = """
    SELECT SUM(Kilometros) AS Total, COUNT(*) AS Registros
    FROM HUB_RegistroKilometros
    WHERE IdUsuario = %s AND FechaHora >= %s
"""

SQL_KM_ULTIMO = """
    SELECT TOP 1 Kilometros, FechaHora
    FROM HUB_RegistroKilometros
    WHERE IdUsuario = %s AND FechaHora >= %s
    ORDER BY FechaHora DESC, Id DESC
"""

SQL_CONFIG = "SELECT Valor FROM HUB_Config WHERE Clave = %s"

# Las cotizaciones del bot son las de MATERIALES y viven en otra base de datos,
# `quotely` (app Quotely), no en ECCSA_Admon.
SQL_COT_LISTA = """
    SELECT TOP 5 i.Folio, i.Empresa, i.Fecha, COALESCE(p.Total, 0) AS Total
    FROM tab_indice i
    LEFT JOIN (SELECT Folio, SUM(Cantidad * PrecioUnitario) AS Total
               FROM tab_partidas GROUP BY Folio) p ON p.Folio = i.Folio
    ORDER BY i.Fecha DESC, i.Folio DESC
"""

SQL_COT_INDICE = """
    SELECT TOP 1 i.Folio, i.Empresa, i.Contacto, i.Fecha,
           COALESCE(u.Usuario, '') AS Elaboro
    FROM tab_indice i
    LEFT JOIN tab_usuarios u ON u.ID = i.Usuario
    WHERE UPPER(LTRIM(RTRIM(i.Folio))) = %s
    ORDER BY i.Fecha DESC, i.Folio DESC
"""

SQL_COT_TOTAL = """
    SELECT SUM(Cantidad * PrecioUnitario) AS Total
    FROM tab_partidas
    WHERE UPPER(LTRIM(RTRIM(Folio))) = %s
"""

SQL_COT_PARTIDAS = """
    SELECT TOP 3 Partida, Cantidad, UME, Descripcion, PrecioUnitario,
           Cantidad * PrecioUnitario AS Importe
    FROM tab_partidas
    WHERE UPPER(LTRIM(RTRIM(Folio))) = %s
    ORDER BY Partida
"""


# ─── Texto ───────────────────────────────────────────────────────────────────

def _normalizar(texto):
    """Minúsculas, sin acentos y con espacios colapsados."""
    txt = unicodedata.normalize("NFD", str(texto or ""))
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return " ".join(txt.lower().split())


def _detectar(texto):
    """
    Tema de la consulta interna (o None). Antes de buscar, se quitan frases
    como «vale la pena» para que no se confundan con «vales».
    """
    limpio = re.sub(r"\b(?:mas\s+)?vale\s+la\s+pena\b", " ", texto)
    limpio = re.sub(r"\bmas\s+vale\b", " ", limpio)
    for tema, palabras in PALABRAS:
        for palabra in palabras:
            if re.search(r"\b" + re.escape(palabra) + r"\b", limpio):
                return tema
    return None


def _folio(texto):
    """Folio capturado del mensaje, limpio (solo letras y números, mayúsculas)."""
    m = _FOLIO_RE.search(texto)
    if not m:
        return ""
    return re.sub(r"[^A-Z0-9]", "", m.group(1).upper())


def _dinero(valor):
    try:
        return f"${float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _numero(valor):
    try:
        return f"{float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _a_fecha(valor):
    """Convierte lo que devuelva la base (datetime, date o texto) a datetime."""
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    txt = str(valor or "").strip()
    if not txt:
        return None
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt[:19], formato)
        except ValueError:
            continue
    return None


def _fecha(valor):
    """Fecha corta en español: «26 nov 2026»."""
    momento = _a_fecha(valor)
    if momento is None:
        return "sin fecha"
    return f"{momento.day} {MESES[momento.month - 1]} {momento.year}"


# ─── Acceso a la base ────────────────────────────────────────────────────────

def _filas(conn, sql, params):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _una(conn, sql, params):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass


# ─── Vales ───────────────────────────────────────────────────────────────────

def _respuesta_vales(conn, usuario_id):
    filas = _filas(conn, SQL_VALES, (usuario_id,))
    if not filas:
        return "No tienes vales pendientes."

    try:
        total = int(filas[0][3] or len(filas))
    except (IndexError, TypeError, ValueError):
        total = len(filas)

    lineas = []
    for fila in filas[:3]:
        lineas.append(f"- Vale {fila[0]} · {_dinero(fila[1])} · "
                      f"{_fecha(fila[2])}")

    if total == 1:
        encabezado = "Tienes 1 vale pendiente de comprobante."
    else:
        encabezado = (f"Tienes {total} vales pendientes de comprobante.")
    return encabezado + " Los más recientes:\n" + "\n".join(lineas)


# ─── Reportes ────────────────────────────────────────────────────────────────

def _respuesta_reportes(conn, usuario_id, texto):
    es_ultimo = bool(re.search(r"\b(?:ultimo|ultima|reciente)\b", texto))
    limite = 1 if es_ultimo else 5
    filas = _filas(conn, SQL_REPORTES.format(n=limite), (usuario_id,))
    if not filas:
        return "No tienes reportes de servicio registrados."

    if es_ultimo:
        fila = filas[0]
        folio = str(fila[0] or "").strip() or "(sin folio)"
        return (f"Tu último reporte: {folio} · {_fecha(fila[1])} · "
                f"{str(fila[2] or '').strip() or 'sin cliente'} · "
                f"{str(fila[3] or '').strip() or 'sin estatus'}.")

    lineas = []
    for fila in filas:
        folio = str(fila[0] or "").strip() or "(sin folio)"
        lineas.append(f"- {folio} · {_fecha(fila[1])} · "
                      f"{str(fila[2] or '').strip() or 'sin cliente'} · "
                      f"{str(fila[3] or '').strip() or 'sin estatus'}")
    return f"Tus últimos {len(filas)} reportes:\n" + "\n".join(lineas)


# ─── Tickets ─────────────────────────────────────────────────────────────────

def _respuesta_tickets(conn, usuario_id):
    filas = _filas(conn, SQL_TICKETS, (usuario_id,))
    if not filas:
        return "No tienes tickets de OxxoGas registrados."

    lineas = []
    for fila in filas:
        descripcion = str(fila[1] or "").strip()
        estacion = str(fila[3] or "").strip()
        detalle = " · ".join(p for p in (estacion, descripcion) if p)
        linea = f"- {str(fila[0] or '').strip() or '(sin folio)'} · " \
                f"{_fecha(fila[2])}"
        if detalle:
            linea += f" · {detalle}"
        lineas.append(linea)
    return f"Tus últimos {len(filas)} tickets de OxxoGas:\n" + "\n".join(lineas)


# ─── Kilómetros ──────────────────────────────────────────────────────────────

def _respuesta_km(conn, usuario_id):
    ahora = now_mx()
    desde = datetime(ahora.year, ahora.month, 1)

    resumen = _una(conn, SQL_KM_RESUMEN, (usuario_id, desde))
    total = resumen[0] if resumen else None
    registros = int((resumen[1] if resumen else 0) or 0)
    if not registros:
        return "No tienes kilómetros registrados este mes."

    ultimo = _una(conn, SQL_KM_ULTIMO, (usuario_id, desde))
    texto = (f"Este mes llevas {_numero(total)} km en {registros} "
             f"{'registro' if registros == 1 else 'registros'}.")
    if ultimo:
        texto += (f" Último: {_numero(ultimo[0])} km el {_fecha(ultimo[1])}.")
    return texto


# ─── Cotizaciones ────────────────────────────────────────────────────────────

def _ids_autorizados(conn):
    """Ids permitidos desde `HUB_Config`; si falta o está rota, el defecto."""
    fila = _una(conn, SQL_CONFIG, (CLAVE_COTIZACIONES,))
    if not fila:
        return set(IDS_COTIZACIONES_DEFECTO)
    try:
        datos = json.loads(str(fila[0] or ""))
    except (TypeError, ValueError):
        return set(IDS_COTIZACIONES_DEFECTO)
    if not isinstance(datos, list):
        return set(IDS_COTIZACIONES_DEFECTO)
    try:
        ids = {int(dato) for dato in datos}
    except (TypeError, ValueError):
        return set(IDS_COTIZACIONES_DEFECTO)
    return ids or set(IDS_COTIZACIONES_DEFECTO)


def _abrir_quotely():
    """
    Conexión a la base `quotely` (app Quotely, cotizaciones de materiales).

    La conexión del HUB apunta a `ECCSA_Admon`, así que aquí se copia su
    configuración cambiando solo el nombre de la base.
    """
    import eccsa_db
    import pymssql

    cfg = dict(eccsa_db.DB_CONFIG)
    cfg["database"] = "quotely"
    return pymssql.connect(**cfg, login_timeout=30, timeout=60)


def _partidas(conn_quotely, folio):
    """Primeras 3 partidas de una cotización (cantidad, descripción, precios)."""
    lineas = []
    for fila in _filas(conn_quotely, SQL_COT_PARTIDAS, (folio,)):
        cantidad = fila[1]
        unidad = str(fila[2] or "").strip()
        descripcion = str(fila[3] or "").strip()
        cantidad_texto = str(cantidad if cantidad is not None else "")
        lineas.append(f"- {cantidad_texto} {unidad} · {descripcion} · "
                      f"{_dinero(fila[4])} c/u · {_dinero(fila[5])}")
    return lineas


def _respuesta_cotizacion_folio(conn_quotely, folio):
    fila = _una(conn_quotely, SQL_COT_INDICE, (folio,))
    if not fila:
        return f"No encontré la cotización {folio}."

    total = _una(conn_quotely, SQL_COT_TOTAL, (folio,))
    partidas = _partidas(conn_quotely, folio)

    texto = (f"Cotización {str(fila[0] or '').strip()}: "
             f"{str(fila[1] or '').strip() or 'sin empresa'}")
    contacto = str(fila[2] or "").strip()
    if contacto:
        texto += f" · Contacto: {contacto}"
    texto += f" · {_fecha(fila[3])}"
    elaboro = str(fila[4] or "").strip()
    if elaboro:
        texto += f" · Elaboró: {elaboro}"
    texto += f" · Total: {_dinero(total[0] if total else 0)}."

    if partidas:
        texto += "\nPartidas:\n" + "\n".join(partidas)
    else:
        texto += "\nSin partidas registradas."
    return texto


def _respuesta_cotizaciones_lista(conn_quotely):
    filas = _filas(conn_quotely, SQL_COT_LISTA, ())
    if not filas:
        return MENSAJE_SIN_COTIZACIONES

    lineas = []
    for fila in filas:
        lineas.append(f"- {str(fila[0] or '').strip() or '(sin folio)'} · "
                      f"{str(fila[1] or '').strip() or 'sin empresa'} · "
                      f"{_fecha(fila[2])} · {_dinero(fila[3])}")
    return (f"Últimas {len(filas)} cotizaciones de materiales:\n"
            + "\n".join(lineas))


def _respuesta_cotizaciones(conn, usuario_id, texto, conn_quotely):
    if usuario_id not in _ids_autorizados(conn):
        return MENSAJE_COTIZACIONES_CERRADAS
    if conn_quotely is None:
        conn_quotely = _abrir_quotely()
    folio = _folio(texto)
    if folio:
        return _respuesta_cotizacion_folio(conn_quotely, folio)
    return _respuesta_cotizaciones_lista(conn_quotely)


# ─── Entrada ─────────────────────────────────────────────────────────────────

def responder(norm, usuario_id, conn=None, conn_quotely=None):
    """
    Contesta una consulta interna de solo lectura, o `None` si el mensaje no
    pide ninguna. No lanza: los errores de base se devuelven como texto.
    """
    try:
        propio = int(usuario_id)
    except (TypeError, ValueError):
        return None
    if propio <= 0:
        return None

    texto = _normalizar(norm)
    if not texto:
        return None
    tema = _detectar(texto)
    if tema is None:
        return None

    try:
        if conn is None:
            import eccsa_db
            conn = eccsa_db.get_connection()

        if tema == "vales":
            return _respuesta_vales(conn, propio)
        if tema == "reportes":
            return _respuesta_reportes(conn, propio, texto)
        if tema == "tickets":
            return _respuesta_tickets(conn, propio)
        if tema == "kilometros":
            return _respuesta_km(conn, propio)
        return _respuesta_cotizaciones(conn, propio, texto, conn_quotely)
    except Exception as exc:
        print(f"[wa_consultas] {tema}: {exc}")
        return MENSAJE_ERROR
