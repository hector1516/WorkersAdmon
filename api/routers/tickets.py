from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from auth import require_user
from db import get_connection
from typing import Optional

router = APIRouter()

@router.get("")
def get_tickets(user: dict = Depends(require_user)):
    """Historial de tickets: SIEMPRE filtrado por el usuario actual.

    Antes los admins veían todos los tickets; el historial de Field es personal
    (la vista global vive en el HUB). Incluye CreadoPor para mostrarlo en la tarjeta.
    """
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT T.Id, T.FolioTicket, T.Estacion, T.Descripcion, T.FechaRegistro,
                   A.MarcaModelo AS Vehiculo, C.Cliente AS Cliente,
                   U.Nombre AS CreadoPor,
                   CASE WHEN T.ImagenTicket IS NOT NULL THEN 1 ELSE 0 END AS TieneFoto
            FROM HUB_OxxoGasTickets T
            LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
            LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
            LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
            WHERE T.IdUsuario = %s
            ORDER BY T.FechaRegistro DESC
        """, (user["id"],))
        return cur.fetchall()

@router.get("/{id_ticket}")
def get_ticket(id_ticket: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT T.Id, T.FolioTicket, T.Estacion, T.Descripcion, T.FechaRegistro,
                   A.MarcaModelo AS Vehiculo, C.Cliente AS Cliente,
                   U.Nombre AS CreadoPor,
                   CASE WHEN T.ImagenTicket IS NOT NULL THEN 1 ELSE 0 END AS TieneFoto
            FROM HUB_OxxoGasTickets T
            LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
            LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
            LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
            WHERE T.Id = %s
        """, (id_ticket,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        is_admin = user.get("is_admin", False)
        if not is_admin and row.get("CreadoPor") != user["nombre"]:
            raise HTTPException(status_code=403, detail="Sin acceso")
        return row

@router.get("/{id_ticket}/imagen")
def get_ticket_imagen(id_ticket: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT ImagenTicket, ImagenNombre FROM HUB_OxxoGasTickets WHERE Id = %s", (id_ticket,))
        row = cur.fetchone()
        if not row or not row.get("ImagenTicket"):
            raise HTTPException(status_code=404, detail="Sin imagen")
        import base64
        return {
            "imagen": base64.b64encode(row["ImagenTicket"]).decode("utf-8"),
            "nombre": row.get("ImagenNombre", "ticket.jpg")
        }

@router.post("")
async def crear_ticket(
    id_local: str = Form(...),
    id_vehiculo: int = Form(...),
    id_cliente: str = Form(...),
    folio_ticket: str = Form(""),
    estacion: str = Form(""),
    descripcion: str = Form(""),
    id_solicitud_vale: str = Form(""),
    foto: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user)
):
    conn = get_connection()
    folio_val = folio_ticket.strip() if folio_ticket else ""
    estacion_val = estacion.strip() if estacion else ""
    desc_val = descripcion.strip() if descripcion else ""
    vale_val = int(id_solicitud_vale) if (id_solicitud_vale or "").strip().isdigit() else None

    # Validaciones: todos obligatorios excepto descripcion (notas)
    if not folio_val:
        raise HTTPException(status_code=400, detail="Folio del ticket es obligatorio")
    if not estacion_val:
        raise HTTPException(status_code=400, detail="Estación es obligatoria")
    if not desc_val:
        raise HTTPException(status_code=400, detail="Servicio/Proyecto es obligatorio")
    if not foto:
        raise HTTPException(status_code=400, detail="Foto del ticket es obligatoria")

    # Read image bytes
    image_bytes = await foto.read()
    image_name = foto.filename or "ticket.jpg"

    with conn.cursor(as_dict=True) as cur:
        # El folio identifica la carga. Si ya existe se rechaza en vez de
        # duplicar: el mismo vale llegue a Produce un ticket repetido.
        from vales_tickets import folio_ya_registrado
        previo = folio_ya_registrado(cur, folio_val)
        if previo:
            raise HTTPException(
                status_code=409,
                detail=(f"El folio {folio_val} ya está registrado "
                        f"(ticket #{previo['Id']}, {previo['Estacion'] or 'sin estación'}, "
                        f"{str(previo['FechaRegistro'])[:16]}). No se puede registrar dos veces."))

        cur.execute("""
            INSERT INTO HUB_OxxoGasTickets
            (FolioTicket, Estacion, ImagenTicket, ImagenNombre, IdVehiculo, Descripcion,
             IdCliente, IdUsuario, FechaRegistro, IdSolicitudVale)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, GETDATE(), %s);
            SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id;
        """, (folio_val, estacion_val, image_bytes, image_name,
              id_vehiculo, desc_val, id_cliente, user["id"], vale_val))
        new_id = int(cur.fetchone()["new_id"])

        # El vale nunca es obligatorio, pero si se eligió uno se cierra con él.
        from vales_tickets import cerrar_vale
        cerrar_vale(cur, vale_val, new_id)
    conn.commit()

    try:
        from routers.legends import _registrar_metrica
        _registrar_metrica(user["id"], "ticket_oxxogas", referencia_id=new_id)
    except Exception:
        pass
    folio_display = folio_val if folio_val else f"TK-{new_id:05d}"
    try:
        from routers.push import send_push_notification
        send_push_notification(user["id"], "⛽ Ticket OxxoGas registrado", f"Ticket {folio_display} registrado — +3 pts", "/tickets")
    except Exception:
        pass
    # Aviso por WhatsApp con la foto del ticket. Este registro (el manual, desde
    # el panel) antes no avisaba a nadie: el push es solo para quien lo registro.
    try:
        from openwa_alerts import alertar_ticket_oxxogas
        alertar_ticket_oxxogas(
            folio_val or folio_display, id_vehiculo=id_vehiculo, id_cliente=id_cliente,
            id_usuario=user["id"], descripcion=desc_val, foto_bytes=image_bytes,
            usuario_actual=user.get("nombre", ""))
    except Exception:
        pass
    return {"success": True, "folio": folio_display, "id_server": new_id}

@router.post("/ai-extraer-folio")
async def ai_extraer_folio(
    foto: UploadFile = File(...),
    user: dict = Depends(require_user)
):
    import os
    api_key = None
    model_name = "gemini-2.0-flash"
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT ApiKey, Model FROM HUB_AIConfig WHERE Id = 1")
        row = cur.fetchone()
        if row:
            api_key = row.get("ApiKey")
            if row.get("Model"):
                model_name = row["Model"]
    if not api_key:
        raise HTTPException(status_code=400, detail="No hay clave de IA configurada")

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # Modelo rápido y disponible (1 sola llamada para folio + estación).
        # REGLA (docs/TROUBLESHOOTING.md §3): no usar gemini-2.0/2.5-flash (404);
        # no hacer 2 llamadas secuenciales (timeout 30s); prompt en texto plano.
        model = genai.GenerativeModel("gemini-3.5-flash-lite")
        image_bytes = await foto.read()
        import PIL.Image
        import io
        img = PIL.Image.open(io.BytesIO(image_bytes))

        # Una sola llamada: extrae folio y estación en formato "FOLIO=... ESTACION=..."
        prompt = (
            "Eres un lector de tickets de OxxoGas. "
            "Extrae el folio (número de 10-12 dígitos cerca de código de barras o etiqueta Folio/Ticket/Secuencia) "
            "y la estación (texto después de ESTACION/Estación/EST./SUCURSAL). "
            "Responde EXACTAMENTE en este formato, sin nada más:\n"
            "FOLIO=numero_o_vacio ESTACION=texto_o_vacio"
        )
        response = model.generate_content([prompt, img])
        text = (response.text or "").strip()
        import logging
        logging.warning(f"[IA OCR] Raw response: {text[:500]}")
        import re
        # Parsear "FOLIO=123456 ESTACION=CENTRO"
        folio_m = re.search(r"FOLIO\s*=\s*(\S+)", text, re.IGNORECASE)
        est_m = re.search(r"ESTACION\s*=\s*(.+)$", text, re.IGNORECASE)
        folio = folio_m.group(1) if folio_m else ""
        estacion = est_m.group(1).strip() if est_m else ""
        # Fallback: si no hay FOLIO=, buscar cualquier número de 10-12 dígitos en la respuesta
        if not folio:
            num_m = re.search(r"\b(\d{10,12})\b", text)
            if num_m:
                folio = num_m.group(1)
                logging.warning(f"[IA OCR] Fallback regex found folio: {folio}")
        # Limpiar tokens no numéricos del folio
        if folio and not re.match(r"^\d{6,15}$", folio):
            folio_num = re.search(r"\d{6,15}", folio)
            folio = folio_num.group(0) if folio_num else ""
        logging.warning(f"[IA OCR] Result: folio={folio!r} estacion={estacion!r}")
        return {"folio": folio, "estacion": estacion}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error con IA: {str(e)}")

@router.post("/guardar-estacion")
async def guardar_estacion(
    estacion: str = Form(...),
    user: dict = Depends(require_user)
):
    """Guarda una nueva estación en el catálogo global (idempotente por UNIQUE)."""
    conn = get_connection()
    est_val = estacion.strip() if estacion else ""
    if not est_val:
        raise HTTPException(status_code=400, detail="Estación vacía")
    with conn.cursor(as_dict=True) as cur:
        # Verificar si ya existe
        cur.execute("SELECT Estacion FROM HUB_EstacionesTickets WHERE Estacion = %s", (est_val,))
        if cur.fetchone():
            return {"success": True, "exists": True, "estacion": est_val}
        # Insertar nueva
        cur.execute("INSERT INTO HUB_EstacionesTickets (Estacion, CreadoPor) VALUES (%s, %s)", (est_val, user["nombre"]))
        conn.commit()
        return {"success": True, "exists": False, "estacion": est_val}
