from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from auth import require_user
from db import get_connection

router = APIRouter()


def _row_to_dict(row) -> dict:
    """Convierte fila as_dict a dict con fechas como str (patrón reportes)."""
    d = dict(row)
    for k in ("FechaCreacion", "FechaFirma"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    # Cliente + estatus derivado de la firma (no hay columna Estatus en BD)
    d.setdefault("Cliente", d.get("Cliente") or "")
    firma = (d.get("FirmaConformidad") or "").strip()
    d["Estatus"] = "firmada" if firma else "pendiente"
    # No exponer la firma completa en listas/detalle de lectura (peso); solo marcar si existe
    d["TieneFirma"] = bool(firma)
    return d


# ── LIST (pendientes o firmadas del usuario) ──
@router.get("")
def get_remisiones(user: dict = Depends(require_user)):
    """Devuelve las remisiones asignadas al usuario, separadas en pendientes y firmadas."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT R.IdRemision, R.FolioRemision, R.FolioCotizacion, R.IdCliente,
                   C.Cliente AS Cliente, R.Contacto, R.Descripcion, R.Autor, R.CreadoPor,
                   R.IdUsuarioAsignado, R.FirmaConformidad, R.FechaFirma, R.FechaCreacion
            FROM IndiceRemisiones R
            LEFT JOIN clientes C ON R.IdCliente = C.IdCliente
            WHERE R.IdUsuarioAsignado = %s
            ORDER BY R.FechaCreacion DESC, R.IdRemision DESC
        """, (user["id"],))
        rows = [_row_to_dict(r) for r in cur.fetchall()]

    pendientes = [r for r in rows if not (r.get("FirmaConformidad") or "").strip()]
    firmadas = [r for r in rows if (r.get("FirmaConformidad") or "").strip()]
    # No arrastrar el blob de firma a la lista (solo conteo/detalle de pestañas)
    for r in rows:
        r.pop("FirmaConformidad", None)
    return {"pendientes": pendientes, "firmadas": firmadas}


# ── COUNT SIN FIRMAR (badge home) ──
@router.get("/sin-firmar/count")
def count_sin_firmar(user: dict = Depends(require_user)):
    """Cuenta remisiones asignadas al usuario sin firma (para badge en home)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT FolioRemision FROM IndiceRemisiones
            WHERE IdUsuarioAsignado = %s
              AND (FirmaConformidad IS NULL OR LTRIM(RTRIM(FirmaConformidad)) = '')
            ORDER BY FechaCreacion DESC, IdRemision DESC
        """, (user["id"],))
        rows = cur.fetchall()
    folios = [r["FolioRemision"] for r in rows if r.get("FolioRemision")]
    return {"count": len(folios), "folios": folios[:5]}


# ── GET BY ID (header + partidas) ──
@router.get("/{id_remision}")
def get_remision(id_remision: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT R.IdRemision, R.FolioRemision, R.FolioCotizacion, R.IdCliente,
                   C.Cliente AS Cliente, R.Contacto, R.Descripcion, R.Autor, R.CreadoPor,
                   R.IdUsuarioAsignado, R.FirmaConformidad, R.FechaFirma, R.FechaCreacion
            FROM IndiceRemisiones R
            LEFT JOIN clientes C ON R.IdCliente = C.IdCliente
            WHERE R.IdRemision = %s AND R.IdUsuarioAsignado = %s
        """, (id_remision, user["id"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Remisión no encontrada o sin acceso")

        remision = _row_to_dict(row)
        remision.pop("FirmaConformidad", None)

        # Partidas (solo cantidad + descripción, sin precios)
        cur.execute("""
            SELECT Partida, Cantidad, Descripcion
            FROM RemisionPartidas
            WHERE IdRemision = %s
            ORDER BY Partida
        """, (id_remision,))
        remision["partidas"] = [
            {"Partida": r["Partida"], "Cantidad": r["Cantidad"], "Descripcion": r["Descripcion"]}
            for r in cur.fetchall()
        ]
        return remision


# ── SIGN ──
class FirmaBody(BaseModel):
    firma_base64: str = ""


@router.put("/{id_remision}/firma")
def guardar_firma(id_remision: int, body: FirmaBody, user: dict = Depends(require_user)):
    """Guarda la firma de conformidad de la remisión (solo el usuario asignado)."""
    firma = (body.firma_base64 or "").strip()
    if len(firma) < 1000:
        raise HTTPException(status_code=400, detail="Firma no válida")

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE IndiceRemisiones
            SET FirmaConformidad = %s, FechaFirma = GETDATE()
            WHERE IdRemision = %s AND IdUsuarioAsignado = %s
        """, (firma, id_remision, user["id"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=403, detail="No tienes permiso para firmar esta remisión")
        conn.commit()

    # Notificación best-effort (no bloquea la firma)
    try:
        from routers.push import send_push_notification
        send_push_notification(user["id"], "✍️ Remisión firmada", f"Remisión {id_remision} firmada", "/remisiones")
    except Exception:
        pass
    return {"success": True}
