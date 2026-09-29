from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from pydantic import BaseModel
from auth import require_user
from db import get_connection
from typing import Optional
import io

router = APIRouter()


def sanitize_pdf(raw_pdf_bytes: bytes) -> bytes:
    """Reescribe el PDF con pypdf para limpiar XREF/estructura de objetos.
    
    ReportLab puede generar estructuras que el parser de WhatsApp iOS
    considera inválidas (XREF cruzada, objetos stream mal formados).
    PdfReader/PdfWriter reconstruye el documento 100% estándar.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(raw_pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return out.getvalue()
    except Exception:
        # Si falla sanitización, devolver original (no bloquear)
        return raw_pdf_bytes

class ReporteCreate(BaseModel):
    id_local: str
    cliente: str
    contacto: Optional[str] = None
    correo_contacto: Optional[str] = None
    fecha: str
    descripcion: Optional[str] = None
    notas: Optional[str] = None
    maquina_linea: Optional[str] = None
    fecha_inicio: Optional[str] = None
    fecha_fin: Optional[str] = None
    tiempo_traslado: Optional[float] = 0
    tiempo_comida: Optional[int] = 0
    tecnicos_adicionales: Optional[list[str]] = None

class ReporteUpdate(BaseModel):
    contacto: Optional[str] = None
    correo_contacto: Optional[str] = None
    fecha_inicio: Optional[str] = None
    fecha_fin: Optional[str] = None
    tiempo_traslado: Optional[float] = 0
    tiempo_comida: Optional[int] = 0
    descripcion: Optional[str] = None
    maquina_linea: Optional[str] = None
    notas: Optional[str] = None
    tecnicos_adicionales: Optional[list[str]] = None

def _get_tecnicos(cur, id_reporte):
    cur.execute("""
        SELECT u.Nombre FROM ReportesServicioTecnicos t
        INNER JOIN HUB_Users u ON t.IdUsuario = u.Id
        WHERE t.IdReporte = %s ORDER BY u.Nombre
    """, (id_reporte,))
    return [r["Nombre"] for r in cur.fetchall()]

def _save_tecnicos(cur, id_reporte, tecnicos_nombres):
    cur.execute("DELETE FROM ReportesServicioTecnicos WHERE IdReporte = %s", (id_reporte,))
    if tecnicos_nombres:
        for nombre in tecnicos_nombres:
            cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (nombre,))
            row = cur.fetchone()
            if row:
                cur.execute("INSERT INTO ReportesServicioTecnicos (IdReporte, IdUsuario) VALUES (%s, %s)",
                           (id_reporte, row[0]))

def _report_to_dict(row, tecnicos=None):
    d = dict(row)
    for k in ["FechaHoraInicio", "FechaHoraFin"]:
        if d.get(k):
            d[k] = str(d[k])
    d["TecnicosAdicionales"] = tecnicos or []
    return d

# ── LIST ──
@router.get("")
def get_reportes(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT r.IdReporte, r.Folio, r.Cliente, r.Contacto, r.CorreoContacto,
                   r.Fecha, r.Tecnico, r.DescripcionServicio, r.Estatus, r.Notas,
                   r.MaquinaLinea, r.FechaHoraInicio, r.FechaHoraFin,
                   r.TiempoTraslado, r.TiempoComida, r.FirmaConformidad
            FROM ReportesServicio r
            WHERE r.Tecnico = %s
               OR r.IdReporte IN (
                   SELECT rt.IdReporte FROM ReportesServicioTecnicos rt
                   INNER JOIN HUB_Users u ON rt.IdUsuario = u.Id
                   WHERE u.Nombre = %s
               )
            ORDER BY r.Fecha DESC, r.IdReporte DESC
        """, (user["nombre"], user["nombre"]))
        return [_report_to_dict(r) for r in cur.fetchall()]

# ── COUNT SIN FIRMAR ──
@router.get("/sin-firmar/count")
def count_sin_firmar(user: dict = Depends(require_user)):
    """Cuenta reportes del usuario sin firma de conformidad (para avisos)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT r.Folio FROM ReportesServicio r
            WHERE (r.Tecnico = %s
               OR r.IdReporte IN (
                   SELECT rt.IdReporte FROM ReportesServicioTecnicos rt
                   INNER JOIN HUB_Users u ON rt.IdUsuario = u.Id
                   WHERE u.Nombre = %s
               ))
              AND (r.FirmaConformidad IS NULL OR LTRIM(RTRIM(r.FirmaConformidad)) = '')
            ORDER BY r.Fecha DESC, r.IdReporte DESC
        """, (user["nombre"], user["nombre"]))
        rows = cur.fetchall()
        folios = [r["Folio"] for r in rows if r.get("Folio")]
        return {"count": len(folios), "folios": folios[:5]}

# ── GET BY ID ──
@router.get("/{id_reporte}")
def get_reporte(id_reporte: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT * FROM ReportesServicio
            WHERE IdReporte = %s
            AND (Tecnico = %s OR IdReporte IN (
                SELECT rt.IdReporte FROM ReportesServicioTecnicos rt
                INNER JOIN HUB_Users u ON rt.IdUsuario = u.Id
                WHERE u.Nombre = %s
            ))
        """, (id_reporte, user["nombre"], user["nombre"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reporte no encontrado o sin acceso")
        tecnicos = _get_tecnicos(cur, id_reporte)
        return _report_to_dict(row, tecnicos)

# ── CREATE ──
@router.post("")
def crear_reporte(req: ReporteCreate, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT ISNULL(MAX(IdReporte), 0) + 1 FROM ReportesServicio")
        new_id = cur.fetchone()[0]
        folio = f"RS-{new_id:05d}"
        cur.execute("""
            INSERT INTO ReportesServicio
            (Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico,
             DescripcionServicio, Notas, MaquinaLinea, FechaHoraInicio,
             FechaHoraFin, TiempoTraslado, TiempoComida, Estatus)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Borrador');
            SELECT SCOPE_IDENTITY();
        """, (folio, req.cliente, req.contacto, req.correo_contacto,
              req.fecha, user["nombre"], req.descripcion, req.notas,
              req.maquina_linea, req.fecha_inicio, req.fecha_fin,
              req.tiempo_traslado, req.tiempo_comida))
        id_server = int(cur.fetchone()[0])
        if req.tecnicos_adicionales:
            _save_tecnicos(cur, id_server, req.tecnicos_adicionales)
        conn.commit()
        return {"id_server": id_server, "folio": folio, "id_local": req.id_local}

# ── UPDATE ──
@router.put("/{id_reporte}")
def editar_reporte(id_reporte: int, req: ReporteUpdate, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        # Check if signed
        cur.execute("SELECT FirmaConformidad FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        if row[0]:
            raise HTTPException(status_code=403, detail="No se puede editar un reporte firmado")

        cur.execute("""
            UPDATE ReportesServicio SET
                Contacto = ISNULL(%s, Contacto),
                CorreoContacto = ISNULL(%s, CorreoContacto),
                FechaHoraInicio = ISNULL(%s, FechaHoraInicio),
                FechaHoraFin = ISNULL(%s, FechaHoraFin),
                TiempoTraslado = ISNULL(%s, TiempoTraslado),
                TiempoComida = ISNULL(%s, TiempoComida),
                DescripcionServicio = ISNULL(%s, DescripcionServicio),
                MaquinaLinea = ISNULL(%s, MaquinaLinea),
                Notas = ISNULL(%s, Notas)
            WHERE IdReporte = %s
        """, (req.contacto, req.correo_contacto, req.fecha_inicio,
              req.fecha_fin, req.tiempo_traslado, req.tiempo_comida,
              req.descripcion, req.maquina_linea, req.notas, id_reporte))
        if req.tecnicos_adicionales is not None:
            _save_tecnicos(cur, id_reporte, req.tecnicos_adicionales)
        conn.commit()
        return {"success": True}

# ── DELETE ──
@router.delete("/{id_reporte}")
def eliminar_reporte(id_reporte: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT FirmaConformidad FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        if row[0]:
            raise HTTPException(status_code=403, detail="No se puede eliminar un reporte firmado")
        cur.execute("DELETE FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
        conn.commit()
        return {"success": True}

# ── SIGN ──
@router.post("/{id_reporte}/firma")
def guardar_firma(id_reporte: int, body: dict, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE ReportesServicio SET FirmaConformidad = %s, Estatus = 'Firmado'
            WHERE IdReporte = %s AND Tecnico = %s
        """, (body.get("firma_base64", ""), id_reporte, user["nombre"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=403, detail="No tienes permiso para firmar este reporte")
        conn.commit()
        try:
            from routers.push import send_push_notification
            send_push_notification(user["id"], "✍️ Reporte firmado", f"Reporte {id_reporte} firmado — +10 pts", "/reportes")
        except Exception:
            pass
        try:
            import telegram_hub
            telegram_hub.alertar_reporte_por_id(id_reporte)
        except Exception:
            pass
        try:
            # WhatsApp con el PDF del reporte. Aqui si sabemos quien firmo
            # (`user["nombre"]`), asi que se lo pasamos para que el mensaje diga
            # quien lo firmo en vez de omitir la linea.
            from openwa_alerts import alertar_reporte_firmado
            alertar_reporte_firmado(id_reporte, usuario_firma=user.get("nombre", ""))
        except Exception:
            pass
        try:
            from routers.legends import _registrar_metrica
            _registrar_metrica(user["id"], "reporte_firmado", referencia_id=id_reporte)
        except Exception:
            pass
        try:
            from routers.legends import registrar_puntos_servicio
            registrar_puntos_servicio(id_reporte)
        except Exception:
            pass
        return {"success": True}

# ── PHOTOS ──
def _compress_photo(data: bytes, max_dim: int = 960, quality: int = 45) -> bytes:
    """Comprime JPEG agresivamente antes de guardar (ahorra disco en SQL Server).
    Si Pillow falla o el original ya es más pequeño, devuelve el original."""
    if not data:
        return data
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        compressed = out.getvalue()
        # Solo usar si de verdad reduce (o si el original era enorme)
        if len(compressed) < len(data) or len(data) > 400_000:
            return compressed
        return data
    except Exception:
        return data


@router.get("/{id_reporte}/fotos")
def get_fotos(id_reporte: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT IdFoto, Orden, FechaSubida
            FROM ReportesServicioFotos
            WHERE IdReporte = %s ORDER BY Orden
        """, (id_reporte,))
        return cur.fetchall()

@router.post("/{id_reporte}/fotos")
async def upload_foto(id_reporte: int, foto: UploadFile = File(...), user: dict = Depends(require_user)):
    foto_bytes = await foto.read()
    if not foto_bytes:
        raise HTTPException(status_code=400, detail="Foto vacía")
    foto_bytes = _compress_photo(foto_bytes)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # INSERT-SELECT con ISNULL(MAX) devuelve 0 filas si el reporte no tiene fotos
            # previas y WHERE no matchea aggregates… usar variable para garantizar 1 fila.
            cur.execute("""
                DECLARE @orden INT;
                SELECT @orden = ISNULL(MAX(Orden), 0) + 1 FROM ReportesServicioFotos WITH (UPDLOCK) WHERE IdReporte = %s;
                IF @orden IS NULL SET @orden = 1;
                INSERT INTO ReportesServicioFotos (IdReporte, FotoComprimida, Orden)
                VALUES (%s, %s, @orden);
            """, (id_reporte, id_reporte, foto_bytes))
            conn.commit()
        return {"success": True, "bytes": len(foto_bytes)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar foto: {e}")

@router.delete("/{id_reporte}/fotos/{id_foto}")
def delete_foto(id_reporte: int, id_foto: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ReportesServicioFotos WHERE IdFoto = %s AND IdReporte = %s", (id_foto, id_reporte))
        conn.commit()
        return {"success": True}

# ── ADDITIONAL TECHNICIANS ──
@router.get("/{id_reporte}/tecnicos")
def get_tecnicos(id_reporte: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        return _get_tecnicos(cur, id_reporte)

@router.post("/{id_reporte}/tecnicos")
def save_tecnicos(id_reporte: int, body: dict, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        _save_tecnicos(cur, id_reporte, body.get("tecnicos", []))
        conn.commit()
        return {"success": True}

# ── AI TEXT IMPROVEMENT ──
@router.post("/ai-mejorar")
def ai_mejorar(body: dict, user: dict = Depends(require_user)):
    texto = (body.get("texto") or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Escribe algo en la descripción antes de mejorarla con IA.")
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT TOP 1 ApiKey, Model FROM HUB_AIConfig ORDER BY Id ASC")
        cfg = cur.fetchone()
    if not cfg or not cfg.get("ApiKey"):
        raise HTTPException(status_code=400, detail="No hay API Key configurada. Ve a Configuración IA en el panel principal.")
    try:
        import google.generativeai as genai
        genai.configure(api_key=cfg["ApiKey"])
        model_name = cfg.get("Model") or "gemini-2.0-flash"
        modelo = genai.GenerativeModel(model_name)
        prompt = (
            "Mejora el siguiente texto de un reporte de servicio de ingeniería industrial. "
            "Usa un tono formal, claro y profesional. No agregues informacion que no este en el texto original. "
            "Devuelve unicamente el texto mejorado, sin introduccion ni comentarios.\n\n"
            f"Texto original:\n{texto}"
        )
        response = modelo.generate_content(prompt)
        return {"mejorado": response.text.strip(), "original": texto}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al llamar al API de IA: {e}")

# ── REMOTE SIGNATURE ──
def _build_firma_mensaje(cliente: str, folio: str, tecnico: str, firma_url: str) -> str:
    return (
        f"Estimado(a) cliente {cliente}:\n\n"
        f"Le compartimos el enlace para firmar de forma remota el reporte de servicio "
        f"{folio}, atendido por el ingeniero {tecnico}.\n\n"
        "Instrucciones de firma:\n"
        "1. Abra el enlace que aparece al final de este mensaje (desde su telefono celular funciona perfecto).\n"
        "2. Revise la informacion del servicio y, si esta conforme, firme dentro del recuadro con su dedo o mouse.\n"
        "3. Presione <<Guardar firma>> y listo: su conformidad quedara registrada en nuestro sistema.\n\n"
        "Datos importantes:\n"
        "- El enlace es personal y de UN SOLO USO.\n"
        "- Tiene vigencia de 24 horas (despues de ese tiempo expira, como el pan de ayer).\n"
        "- Si algo no cuadra, avisenos y le generamos uno nuevo sin problema.\n\n"
        f"Enlace de firma:\n{firma_url}"
    )

def _active_firma_token(cur, id_reporte: int):
    import datetime
    cur.execute("""
        SELECT Token, ExpiresAt FROM HUB_SignatureTokens
        WHERE IdReporte = %s AND UsedAt IS NULL AND ExpiresAt > GETDATE()
        ORDER BY ExpiresAt DESC
    """, (id_reporte,))
    row = cur.fetchone()
    if not row:
        return None
    exp = row["ExpiresAt"]
    if isinstance(exp, datetime.datetime):
        # ISO con T y milisegundos: new Date() en Safari falla con "2026-09-25 16:52:37.870000"
        exp = exp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    return {"token": row["Token"], "expiresAt": str(exp)}

@router.get("/{id_reporte}/firma-remota")
def estado_firma_remota(id_reporte: int, user: dict = Depends(require_user)):
    """Devuelve el link activo (si hay) con su vigencia. Sin crear nada."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT IdReporte, Folio, Cliente FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
        report = cur.fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        active = _active_firma_token(cur, id_reporte)
    if not active:
        return {"active": False}
    firma_url = f"https://field.ecc-sa.com.mx/?firma={active['token']}"
    return {
        "active": True, "url": firma_url, "token": active["token"],
        "expiresAt": active["expiresAt"],
        "mensaje": _build_firma_mensaje(report["Cliente"] or "estimado cliente", report["Folio"], user["nombre"], firma_url),
    }

@router.post("/{id_reporte}/firma-remota")
def generar_firma_remota(id_reporte: int, user: dict = Depends(require_user)):
    import uuid
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        # Validate report exists and belongs to user
        cur.execute("""
            SELECT IdReporte, Folio, Cliente, Tecnico FROM ReportesServicio
            WHERE IdReporte = %s AND (Tecnico = %s OR IdReporte IN (
                SELECT rt.IdReporte FROM ReportesServicioTecnicos rt
                INNER JOIN HUB_Users u ON rt.IdUsuario = u.Id WHERE u.Nombre = %s
            ))
        """, (id_reporte, user["nombre"], user["nombre"]))
        report = cur.fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")

        # Si ya hay un link vigente, devolverlo (no generar otro)
        active = _active_firma_token(cur, id_reporte)
        if active:
            firma_url = f"https://field.ecc-sa.com.mx/?firma={active['token']}"
            return {
                "url": firma_url, "token": active["token"], "nuevo": False,
                "expiresAt": active["expiresAt"],
                "mensaje": _build_firma_mensaje(
                    report["Cliente"] or "estimado cliente", report["Folio"], user["nombre"], firma_url),
            }

        # Delete existing unused tokens
        cur.execute("DELETE FROM HUB_SignatureTokens WHERE IdReporte = %s AND UsedAt IS NULL", (id_reporte,))

        # Create new token (64 hex chars)
        token = uuid.uuid4().hex + uuid.uuid4().hex
        cur.execute("""
            INSERT INTO HUB_SignatureTokens (Token, IdReporte, CreatedAt, ExpiresAt)
            VALUES (%s, %s, GETDATE(), DATEADD(HOUR, 24, GETDATE()))
        """, (token, id_reporte))
        conn.commit()

        # Get full report for message
        cur.execute("SELECT Cliente FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
        r = cur.fetchone()
        cliente = r["Cliente"] if r else "estimado cliente"
        tecnico = user["nombre"]

        # Build message
        firma_url = f"https://field.ecc-sa.com.mx/?firma={token}"
        mensaje = _build_firma_mensaje(cliente, report["Folio"], tecnico, firma_url)
        cur.execute("SELECT ExpiresAt FROM HUB_SignatureTokens WHERE Token = %s", (token,))
        exp_row = cur.fetchone()
        _exp = exp_row["ExpiresAt"]
        _exp_iso = _exp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] if hasattr(_exp, "strftime") else str(_exp)
        return {"url": firma_url, "token": token, "mensaje": mensaje, "nuevo": True,
                "expiresAt": _exp_iso}

# ── REMOTE SIGNATURE (PUBLIC: clientes sin cuenta) ──
def _get_signature_token(cur, token: str):
    # DbNow: hora del servidor SQL — los tokens se crean con GETDATE() (reloj
    # local del servidor, UTC-6) y el contenedor Field corre en UTC, por lo que
    # validar con datetime.now() desfasaba la vigencia 6 horas HACIA ARRIBA
    # (los links "expiraban" antes de tiempo). Comparar siempre contra GETDATE().
    cur.execute("""
        SELECT t.Token, t.IdReporte, t.ExpiresAt, t.UsedAt,
               r.Folio, r.Cliente, r.Tecnico, r.DescripcionServicio,
               GETDATE() AS DbNow
        FROM HUB_SignatureTokens t
        INNER JOIN ReportesServicio r ON r.IdReporte = t.IdReporte
        WHERE t.Token = %s
    """, (token,))
    return cur.fetchone()

@router.get("/firma-remota/{token}")
def firma_remota_info(token: str):
    """Info publica del reporte para firmar (sin login)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        row = _get_signature_token(cur, token)
    if not row:
        raise HTTPException(status_code=404, detail="Enlace no válido")
    if row["UsedAt"] is not None:
        raise HTTPException(status_code=410, detail="Este enlace ya fue utilizado")
    if row["ExpiresAt"] < row["DbNow"]:
        raise HTTPException(status_code=410, detail="Este enlace expiró (24 horas)")
    return {
        "folio": row["Folio"], "cliente": row["Cliente"], "tecnico": row["Tecnico"],
        "descripcion": row["DescripcionServicio"] or "",
    }

class FirmaRemotaSign(BaseModel):
    firma_base64: str = ""
    nombre: str = ""

@router.post("/firma-remota/{token}")
def firma_remota_sign(token: str, body: FirmaRemotaSign):
    """Guarda la firma del cliente (sin login). Un solo uso."""
    firma = (body.firma_base64 or "").strip()
    if len(firma) < 1000:
        raise HTTPException(status_code=400, detail="Firma no válida")
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        row = _get_signature_token(cur, token)
        if not row:
            raise HTTPException(status_code=404, detail="Enlace no válido")
        if row["UsedAt"] is not None:
            raise HTTPException(status_code=410, detail="Este enlace ya fue utilizado")
        if row["ExpiresAt"] < row["DbNow"]:
            raise HTTPException(status_code=410, detail="Este enlace expiró (24 horas)")
        cur.execute("""
            UPDATE ReportesServicio SET FirmaConformidad = %s, Estatus = 'Firmado'
            WHERE IdReporte = %s
        """, (firma, row["IdReporte"]))
        cur.execute("UPDATE HUB_SignatureTokens SET UsedAt = GETDATE() WHERE Token = %s", (token,))
        conn.commit()
    print(f"[firma-remota] firmada IdReporte={row['IdReporte']} folio={row['Folio']}", flush=True)
    try:
        from routers.push import notify_all_users
        notify_all_users("✍️ Reporte firmado (remoto)", f"Cliente firmó el reporte {row['Folio']}")
    except Exception:
        pass
    try:
        import telegram_hub
        telegram_hub.alertar_reporte_por_id(row["IdReporte"])
    except Exception:
        pass
    return {"success": True, "folio": row["Folio"]}

# ── PDF GENERATION ──
@router.get("/{id_reporte}/pdf")
def generar_pdf(id_reporte: int, token: str = None, request: Request = None):
    # Accept token via query param (for iframe) or Authorization header
    user = None
    if token:
        from auth import decode_token
        payload = decode_token(token)
        if payload:
            user = {"id": payload.get("sub"), "nombre": payload.get("nombre")}
    if not user:
        # Fallback: try Authorization header
        try:
            from auth import require_user
            # Extract token from Authorization header manually
            if request:
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    from auth import decode_token
                    payload = decode_token(auth_header[7:])
                    if payload:
                        user = {"id": payload.get("sub"), "nombre": payload.get("nombre")}
        except Exception:
            pass
    if not user:
        raise HTTPException(status_code=401, detail="Token requerido")

    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT r.*, c.Cliente AS ClienteNombre
            FROM ReportesServicio r
            LEFT JOIN clientes c ON r.Cliente = c.IdCliente
            WHERE r.IdReporte = %s
        """, (id_reporte,))
        report = cur.fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        tecnicos = _get_tecnicos(cur, id_reporte)
        report["TecnicosAdicionales"] = tecnicos

    try:
        from pdf_generator import generate_service_report_pdf
        pdf_bytes = generate_service_report_pdf(dict(report))
        if not pdf_bytes:
            raise HTTPException(status_code=500, detail="Error generando PDF")
        # Sanitizar PDF: reconstruye XREF/estructura para compatibilidad iOS/WhatsApp
        pdf_bytes = sanitize_pdf(pdf_bytes)
        from fastapi.responses import Response
        return Response(content=pdf_bytes, media_type="application/pdf",
                       headers={"Content-Disposition": f"inline; filename={report['Folio']}.pdf"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {e}")

# ── SAVE CUSTOM CONTACT ──
# Guarda contactos en HUB_ContactosClientes (catálogo propio).
# ANTES insertaba en IndiceMateriales con Descripcion='' y creaba
# cotizaciones de materiales fantasma en el HUB al crear un reporte.
@router.post("/guardar-contacto")
def guardar_contacto(body: dict, user: dict = Depends(require_user)):
    id_cliente = (body.get("id_cliente") or "").strip().upper()
    contacto = (body.get("contacto") or "").strip()
    if not id_cliente or not contacto:
        raise HTTPException(status_code=400, detail="Faltan datos")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM HUB_ContactosClientes
            WHERE IdCliente = %s AND LTRIM(RTRIM(Contacto)) = %s
        """, (id_cliente, contacto))
        if cur.fetchone()[0] == 0:
            # UNIQUE(IdCliente, Contacto): si ya existe en cotizaciones reales, no fallar
            cur.execute("""
                SELECT COUNT(*) FROM IndiceMateriales
                WHERE IdCliente = %s AND LTRIM(RTRIM(Contacto)) = %s
            """, (id_cliente, contacto))
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO HUB_ContactosClientes (IdCliente, Contacto, CreadoPor, FechaRegistro)
                    VALUES (%s, %s, %s, GETDATE())
                """, (id_cliente, contacto, user.get('nombre', '')))
                conn.commit()
        return {"success": True}
