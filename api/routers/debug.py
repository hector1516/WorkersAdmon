"""api/routers/debug.py — Sistema de debugging con códigos de error únicos.

Mismo contrato que Admon, escribiendo en la tabla compartida `HUB_DebugLog`
(ECCSA_Admon). Los códigos de Field siguen el formato `FLD-MODULO-COMPONENTE-NNN`
(definidos en `src/lib/audit.js` -> `ERROR_CODES`).
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json

from auth import require_user
from db import get_connection

router = APIRouter()


class DebugLogRequest(BaseModel):
    codigo: str
    app: str = "WRK"
    modulo: str = ""
    mensaje: str
    contexto: Optional[Dict[str, Any]] = None
    url: Optional[str] = None


@router.post("/log")
def log_debug(body: DebugLogRequest, user: dict = Depends(require_user)):
    """Registra un error de debug en la bitácora."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUB_DebugLog (Codigo, App, Modulo, Mensaje, Contexto, Url, Usuario, Fecha) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, GETDATE())",
            (body.codigo[:30], body.app[:10], body.modulo[:10], body.mensaje[:500],
             json.dumps(body.contexto, ensure_ascii=False) if body.contexto else None,
             (body.url or "")[:500], user.get("email") or ""),
        )
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


@router.get("/log")
def get_debug_log(
    codigo: Optional[str] = None,
    app: Optional[str] = None,
    modulo: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(require_user),
):
    """Consulta el log de debug (solo admin)."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Sin acceso")
    conn = get_connection()
    cur = conn.cursor(as_dict=True)
    conditions = ["1=1"]
    params = []
    if codigo:
        conditions.append("Codigo LIKE %s")
        params.append(f"%{codigo}%")
    if app:
        conditions.append("App = %s")
        params.append(app)
    if modulo:
        conditions.append("Modulo = %s")
        params.append(modulo)
    where = " AND ".join(conditions)
    cur.execute(
        f"SELECT TOP ({int(limit)}) Id, Codigo, App, Modulo, Mensaje, Contexto, Url, Usuario, Fecha "
        f"FROM HUB_DebugLog WHERE {where} ORDER BY Fecha DESC",
        tuple(params),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r["Id"],
            "codigo": r["Codigo"],
            "app": r["App"],
            "modulo": r["Modulo"],
            "mensaje": r["Mensaje"],
            "contexto": r["Contexto"],
            "url": r["Url"],
            "usuario": r["Usuario"],
            "fecha": r["Fecha"].strftime("%Y-%m-%d %H:%M:%S") if r.get("Fecha") else "",
        }
        for r in rows
    ]
