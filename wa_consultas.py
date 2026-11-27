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

SQL_KM_POR_AUTO = """
    SELECT IdAutomovil, MIN(Kilometros) AS Desde, MAX(Kilometros) AS Hasta,
           COUNT(*) AS Registros
    FROM HUB_RegistroKilometros
    WHERE IdUsuario = %s AND FechaHora >= %s
    GROUP BY IdAutomovil
"""

SQL_KM_ULTIMO = """
    SELECT TOP 1 Kilometros, FechaHora
    FROM HUB_RegistroKilometros
    WHERE IdUsuario = %s AND FechaHora >= %s
    ORDER BY FechaHora DESC, Id DESC
"""

SQL_CONFIG = "SELECT Valor FROM HUB_Config WHERE Clave = %s"

# Cotizaciones de materiales: las MISMAS tablas del módulo de Admon
# (`IndiceMateriales` + `Partidas`) en `ECCSA_Admon`. `Color` es el estatus
# (0 PENDIENTE, 1 ENTREGADA, 2 PAGADA) y el precio de venta sale de
# `PrecioCompraUnitario * (1 + Factor)`; el costo de compra NO se muestra.
SQL_COT_LISTA = """
    SELECT TOP 5 I.Folio, I.IdCliente,
           COALESCE((SELECT TOP 1 C.Cliente FROM Clientes C
                     WHERE C.IdCliente = I.IdCliente), '') AS Cliente,
           I.Contacto, I.Fecha, I.Autor, I.Color,
           COALESCE((SELECT SUM((P.PrecioCompraUnitario * (1.0 + P.Factor)) * P.Cantidad)
                     FROM Partidas P WHERE P.Folio = I.Folio), 0) AS Suma,
           COALESCE((SELECT MAX(P.Flete) FROM Partidas P WHERE P.Folio = I.Folio), 0) AS Flete
    FROM IndiceMateriales I
    ORDER BY I.Fecha DESC, I.Folio DESC
"""

SQL_COT_INDICE = """
    SELECT TOP 1 I.Folio, I.IdCliente,
           COALESCE((SELECT TOP 1 C.Cliente FROM Clientes C
                     WHERE C.IdCliente = I.IdCliente), '') AS Cliente,
           I.Contacto, I.Fecha, I.Autor, I.Color,
           COALESCE(I.Descripcion, ''), COALESCE(I.Nota, '')
    FROM IndiceMateriales I
    WHERE I.Folio = %s
"""

SQL_COT_TOTAL = """
    SELECT COALESCE(SUM((PrecioCompraUnitario * (1.0 + Factor)) * Cantidad), 0),
           COALESCE(MAX(Flete), 0)
    FROM Partidas
    WHERE Folio = %s
"""

SQL_COT_PARTIDAS = """
    SELECT TOP 10 Partida, Cantidad, Descripcion, PrecioCompraUnitario,
           Factor, PrecioCompraUnitario * (1.0 + Factor) AS PrecioVenta,
           (PrecioCompraUnitario * (1.0 + Factor)) * Cantidad AS Importe,
           COALESCE(Proveedor, ''), COALESCE(TiempoEntregaDias, 0),
           COALESCE(Dolar, 0)
    FROM Partidas
    WHERE Folio = %s
    ORDER BY Partida
"""

SQL_COT_CUENTA = """
    SELECT COUNT(*) FROM Partidas WHERE Folio = %s
"""

ESTATUS_MATERIALES = {0: "PENDIENTE", 1: "ENTREGADA", 2: "PAGADA"}


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
    """
    `HUB_RegistroKilometros.Kilometros` es el **odómetro**, no el recorrido:
    sumarlos daba "395,876 km este mes", que es falso. El recorrido de cada
    vehículo es la diferencia entre la lectura mayor y la menor del mes.
    """
    ahora = now_mx()
    desde = datetime(ahora.year, ahora.month, 1)

    por_auto = _filas(conn, SQL_KM_POR_AUTO, (usuario_id, desde))
    if not por_auto:
        return "No tienes kilómetros registrados este mes."

    recorrido = 0
    registros = 0
    for fila in por_auto:
        desde_km = fila[1] or 0
        hasta_km = fila[2] or 0
        if hasta_km > desde_km:
            recorrido += hasta_km - desde_km
        registros += int(fila[3] or 0)

    ultimo = _una(conn, SQL_KM_ULTIMO, (usuario_id, desde))
    if recorrido:
        texto = (f"Este mes llevas {recorrido:,.0f} km recorridos en {registros} "
                 f"{'registro' if registros == 1 else 'registros'}.")
    else:
        texto = (f"Este mes tienes {registros} "
                 f"{'lectura' if registros == 1 else 'lecturas'} de kilometraje"
                 " (aún sin dos lecturas del mismo vehículo para calcular el "
                 "recorrido).")
    if ultimo:
        texto += (f" Último odómetro: {float(ultimo[0] or 0):,.0f} km "
                  f"el {_fecha(ultimo[1])}.")
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


def _folio_pedido(texto):
    """
    Folio que pide el mensaje: `("num", 1329)`, `("otro", "zzz999")` o
    `(None, None)`.

    Los folios de materiales son numéricos. Si el mensaje trae letras
    (`3K17WZ`) no se recorta a los dígitos: eso contestaría la cotización 317,
    que es otra, y es peor que decir que no existe.
    """
    m = re.search(
        r"\b(?:cotizaciones|cotizacion|cotiza|folio)\b\s*"
        r"(?:no\.?|numero|n[o°])?\s*#?\s*([a-z0-9][a-z0-9\-]{0,9})",
        texto)
    if not m:
        return None, None
    token = m.group(1).strip("-")
    if not token:
        return None, None
    if token.isdigit():
        return "num", int(token)
    return "otro", token.upper()


def _estatus(color):
    try:
        return ESTATUS_MATERIALES.get(int(color or 0), "PENDIENTE")
    except (TypeError, ValueError):
        return "PENDIENTE"


def _partidas(conn, folio):
    """
    Partidas con TODO el detalle interno: el dueño autorizó que los usuarios de
    la lista vean costos, factor, proveedor, entrega y dólar. Se cortan en 10
    para que quepan en un mensaje de WhatsApp.
    """
    lineas = []
    for fila in _filas(conn, SQL_COT_PARTIDAS, (folio,)):
        cantidad = fila[1]
        descripcion = str(fila[2] or "").strip()
        if len(descripcion) > 70:
            descripcion = descripcion[:67].rstrip() + "…"
        cantidad_texto = str(cantidad if cantidad is not None else "")
        costo = float(fila[3] or 0)
        factor = float(fila[4] or 0)
        proveedor = str(fila[7] or "").strip()
        if len(proveedor) > 40 or proveedor.lower().startswith("http"):
            proveedor = ""      # enlaces de eBay/ML: no caben ni aportan
        entrega = int(fila[8] or 0)
        dolar = float(fila[9] or 0)

        detalle = (f"costo {_dinero(costo)} · factor {factor * 100:,.0f}% · "
                   f"venta {_dinero(fila[5])} c/u · importe {_dinero(fila[6])}")
        extra = []
        if proveedor:
            extra.append(f"prov: {proveedor}")
        if entrega:
            extra.append(f"entrega: {entrega} d")
        if dolar:
            extra.append(f"dólar {dolar:,.2f}")
        if extra:
            detalle += " · " + " · ".join(extra)

        lineas.append(f"- {cantidad_texto} × {descripcion}\n  {detalle}")
    return lineas


def _respuesta_cotizacion_folio(conn, folio):
    fila = _una(conn, SQL_COT_INDICE, (folio,))
    if not fila:
        return f"No encontré la cotización {folio}."

    total = _una(conn, SQL_COT_TOTAL, (folio,))
    suma = float(total[0] or 0) if total else 0.0
    flete = float(total[1] or 0) if total else 0.0
    partidas = _partidas(conn, folio)
    cuenta = _una(conn, SQL_COT_CUENTA, (folio,))
    total_partidas = int(cuenta[0] or 0) if cuenta else 0

    cliente = str(fila[2] or "").strip()
    contacto = str(fila[3] or "").strip()
    texto = f"Cotización {int(fila[0])}: {cliente or str(fila[1] or 'sin cliente')}"
    if contacto:
        texto += f" · Contacto: {contacto}"
    texto += f" · {_fecha(fila[4])}"
    elaboro = str(fila[5] or "").strip()
    if elaboro:
        texto += f" · Elaboró: {elaboro}"
    texto += f" · Estatus: {_estatus(fila[6])}."

    descripcion = str(fila[7] or "").strip()
    if descripcion:
        if len(descripcion) > 200:
            descripcion = descripcion[:197].rstrip() + "…"
        texto += f"\n{descripcion}"
    nota = str(fila[8] or "").strip()
    if nota:
        texto += f"\nNota: {nota}"

    texto += (f"\nSubtotal partidas: {_dinero(suma)}")
    if flete:
        texto += f" · Flete: {_dinero(flete)}"
    texto += f" · Total: {_dinero(suma + flete)}."

    if partidas:
        texto += "\nPartidas:\n" + "\n".join(partidas)
        if total_partidas > len(partidas):
            texto += f"\n… y {total_partidas - len(partidas)} partidas más."
    else:
        texto += "\nSin partidas registradas."
    return texto


def _respuesta_cotizaciones_lista(conn):
    filas = _filas(conn, SQL_COT_LISTA, ())
    if not filas:
        return MENSAJE_SIN_COTIZACIONES

    lineas = []
    for fila in filas:
        cliente = str(fila[2] or fila[1] or "sin cliente").strip()
        total = float(fila[7] or 0) + float(fila[8] or 0)
        lineas.append(f"- {int(fila[0])} · {cliente} · {_fecha(fila[4])} · "
                      f"{_dinero(total)} · {_estatus(fila[6])}")
    return (f"Últimas {len(filas)} cotizaciones de materiales:\n"
            + "\n".join(lineas))


def _respuesta_cotizaciones(conn, usuario_id, texto):
    if usuario_id not in _ids_autorizados(conn):
        return MENSAJE_COTIZACIONES_CERRADAS
    tipo, valor = _folio_pedido(texto)
    if tipo == "num":
        return _respuesta_cotizacion_folio(conn, valor)
    if tipo == "otro":
        return f"No encontré la cotización {valor}."
    return _respuesta_cotizaciones_lista(conn)


# ─── Entrada ─────────────────────────────────────────────────────────────────

def responder(norm, usuario_id, conn=None):
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
        return _respuesta_cotizaciones(conn, propio, texto)
    except Exception as exc:
        print(f"[wa_consultas] {tema}: {exc}")
        return MENSAJE_ERROR
