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
    import re
    texto = plantilla
    for key, val in datos.items():
        texto = texto.replace("{" + key + "}", str(val if val is not None else ""))
    lineas = [l for l in texto.split("\n") if l.strip() and not re.match(r"^[\*\s]+$", l.strip())]
    return "\n".join(lineas)


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
                           foto_bytes=None, foto_nombre=None):
    fecha, _ = _now_mx()
    # Incluir folio del ticket del cliente en la descripción
    desc_con_folio = descripcion or ""
    if folio_cliente:
        desc_con_folio += f"\n🎫 Ticket: {folio_cliente}"
    return _queue("OXXOGAS_TICKET", {
        "Fecha": fecha, "NombreRegistro": usuario, "Folio": folio_server,
        "FolioTicket": folio_cliente or folio_server,
        "Auto": _auto_name(id_auto), "Empresa": _cliente_name(id_cliente),
        "ProyectoServicio": desc_con_folio,
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
