"""Alertas Telegram desde Field (mismo formato que el HUB).

Encola en HUB_TelegramQueue; el worker del HUB (telegram_worker) despacha.
Resuelve destinatarios por evento y aplica la plantilla configurable.
"""
import datetime

from db import get_connection


def _now_mx():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-6)))
    return now.strftime("%d/%m/%Y"), now.strftime("%H:%M")


def _get_event_template(id_evento):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute(
            "SELECT PlantillaMensaje, AdjuntarArchivo, Activo FROM HUB_TelegramEventos WHERE IdEvento = %s",
            (id_evento,),
        )
        row = cur.fetchone()
    if not row:
        return None, False, False
    return row["PlantillaMensaje"], bool(row["AdjuntarArchivo"]), bool(row["Activo"])


def _resolve_chat_ids(id_evento):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute(
            "SELECT d.IdUsuario, u.ChatId FROM HUB_TelegramDestinatarios d "
            "INNER JOIN HUB_TelegramUsuarios u ON u.IdUsuario = d.IdUsuario "
            "WHERE d.IdEvento = %s AND u.Activo = 1 AND u.ChatId IS NOT NULL",
            (id_evento,),
        )
        rows = cur.fetchall()
    return [int(r["ChatId"]) for r in rows if r.get("ChatId")]


def _apply_template(plantilla, datos):
    """Sustituye {placeholder} de la plantilla con los datos del payload.

    Esta es la copia que usa _queue, o sea la que realmente arma el texto que
    se guarda YA RENDERIZADO en HUB_TelegramQueue. Por eso resuelve alias:
    los productores mandan los nombres internos de cada campo
    (NombreRegistro, Empresa, ProyectoServicio, FolioTicket, Monto) y la
    plantilla de HUB_TelegramEventos usa los nombres planos (Nombre,
    Cliente, Descripcion, Folio, Cantidad). Sin el alias, el placeholder no
    coincide y la alerta sale a Telegram con las llaves puestas, y desde el
    worker ya no se puede corregir.
    """
    import re
    texto = plantilla

    alias = {
        "Nombre": ("Usuario", "NombreRegistro"),
        "Cantidad": ("Monto", "Litros"),
        "Folio": ("FolioTicket",),
        "Auto": ("Automovil", "Vehiculo"),
        "Cliente": ("Empresa", "RazonSocial", "NombreCliente"),
        "Descripcion": ("ProyectoServicio",),
    }
    datos = dict(datos or {})
    for destino, origenes in alias.items():
        if destino in datos:
            continue
        for origen in origenes:
            if datos.get(origen) not in (None, ""):
                datos[destino] = datos[origen]
                break

    # Se recorre la plantilla ORIGINAL linea por linea: si una linea tiene
    # placeholders y todos salen vacios, se descarta entera. Si se mirara
    # despues de sustituir ya no habria llaves que reconocer y la linea
    # "Cantidad:" se quedaria colgando sola.
    lineas = []
    for original in plantilla.split("\n"):
        if not original.strip():
            continue
        claves = re.findall(r"\{(\w+)\}", original)
        if claves and all(not str(datos.get(k, "")).strip() for k in claves):
            continue
        linea = original
        for key, val in datos.items():
            linea = linea.replace("{" + key + "}", str(val if val is not None else ""))
        if not linea.strip() or re.match(r"^[\*\s]+$", linea.strip()):
            continue
        lineas.append(linea)

    texto = "\n".join(lineas)
    sin_resolver = sorted(set(re.findall(r"\{(\w+)\}", texto)))
    if sin_resolver:
        print(f"[telegram] WARN plantilla con placeholders sin resolver: {sin_resolver}",
              flush=True)
    return texto


def _queue(id_evento, datos, adjunto=None, adjunto_nombre=None):
    """Encola alerta en HUB_TelegramQueue. adjunto = bytes (foto/PDF)."""
    try:
        plantilla, adj_config, activo = _get_event_template(id_evento)
        if not activo or not plantilla:
            return 0
        chat_ids = _resolve_chat_ids(id_evento)
        if not chat_ids:
            return 0
        texto = _apply_template(plantilla, datos)
        adj_bytes = adjunto if adj_config and adjunto else None
        adj_name = adjunto_nombre if adj_bytes else None
        conn = get_connection()
        count = 0
        with conn.cursor() as cur:
            for cid in chat_ids:
                cur.execute(
                    "INSERT INTO HUB_TelegramQueue (IdEvento, ChatId, Texto, Adjunto, AdjuntoNombre) VALUES (%s, %s, %s, %s, %s)",
                    (id_evento, cid, texto.strip(), adj_bytes, adj_name),
                )
                count += 1
            conn.commit()
        return count
    except Exception as e:
        print(f"[telegram] queue FAIL {id_evento}: {e}", flush=True)
        return 0


def _auto_name(id_auto):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (id_auto,))
            row = cur.fetchone()
            if row:
                return f"{row[1] or ''} ({row[0] or ''})".strip(" ()")
    except Exception:
        pass
    return f"#{id_auto}"


def _cliente_name(id_cliente):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente,))
            row = cur.fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return str(id_cliente or "")


def alertar_km(id_auto, km, usuario):
    fecha, hora = _now_mx()
    return _queue("KILOMETROS", {
        "Automovil": _auto_name(id_auto), "Kilometros": km,
        "Usuario": usuario, "Fecha": fecha, "Hora": hora,
    })


def alertar_vale(solicitante, id_auto, id_cliente, descripcion, monto="500.00"):
    fecha, hora = _now_mx()
    return _queue("SOLICITUD_VALE_QR", {
        "Solicitante": solicitante, "Vehiculo": _auto_name(id_auto),
        "Empresa": _cliente_name(id_cliente), "Descripcion": descripcion,
        "Monto": f"${monto}", "Fecha": fecha, "Hora": hora,
    })


def alertar_ticket_oxxogas(usuario, folio_server, folio_cliente, id_auto, id_cliente, descripcion,
                           foto_bytes=None, foto_nombre=None, cantidad=None):
    fecha, _ = _now_mx()
    # Incluir folio del ticket del cliente en la descripción
    desc_con_folio = descripcion or ""
    if folio_cliente:
        desc_con_folio += f"\n🎫 Ticket: {folio_cliente}"
    cliente = _cliente_name(id_cliente)
    # Se mandan los dos nombres: los de la plantilla (Nombre, Cliente,
    # Descripcion, Cantidad) y los internos, por si otra version del codigo
    # los sigue esperando. Antes solo iban los internos, y la plantilla pedia
    # los otros: por eso llegaban con las llaves puestas.
    return _queue("OXXOGAS_TICKET", {
        "Fecha": fecha,
        "Nombre": usuario, "Cantidad": cantidad or "",
        "Folio": folio_server, "Auto": _auto_name(id_auto),
        "Cliente": cliente, "Descripcion": desc_con_folio,
        "NombreRegistro": usuario, "FolioTicket": folio_cliente or folio_server,
        "Empresa": cliente, "ProyectoServicio": desc_con_folio,
    }, adjunto=foto_bytes, adjunto_nombre=foto_nombre)


def alertar_reporte_firmado(folio, cliente, usuario_firma):
    fecha, hora = _now_mx()
    return _queue("REPORTE_SERVICIO", {
        "Folio": folio, "Cliente": cliente, "UsuarioFirma": usuario_firma,
        "FechaFirma": fecha, "HoraFirma": hora,
    })


def alertar_reporte_por_id(id_reporte):
    """Resuelve folio/cliente/contacto y encola REPORTE_SERVICIO."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT Folio, Cliente, Contacto FROM ReportesServicio WHERE IdReporte = %s",
                (int(id_reporte),),
            )
            row = cur.fetchone()
        if not row:
            return 0
        return alertar_reporte_firmado(row["Folio"], row["Cliente"], row.get("Contacto") or "Cliente")
    except Exception:
        return 0
