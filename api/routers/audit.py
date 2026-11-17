"""api/routers/audit.py — Bitácora de actividad (audit trail) para Field.

Mismo contrato que Admon (`api/routers/audit.py`), pero autenticando con el
`require_user` de Field (`Authorization: Bearer <jwt>`) y escribiendo en las
tablas compartidas `HUB_ActivityLog` (ECCSA_Admon).

El cliente `src/lib/audit.js` del shell hace POST a `/api/audit/log`. Sin este
router esas llamadas daban 404 y la bitácora de Field quedaba vacía.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json

from auth import require_user
from db import get_connection

router = APIRouter()


class AuditLogRequest(BaseModel):
    app: str = "WRK"
    modulo: str = ""
    tipo_accion: str = "click"  # click, navigate, insert, update, delete, error, login, logout, view
    accion: str
    detalle: Optional[str] = None
    datos: Optional[Dict[str, Any]] = None
    duracion_ms: Optional[int] = None


def registrar_accion(user: dict, modulo: str, tipo_accion: str, accion: str,
                     detalle: Optional[str] = None, datos: Optional[Dict[str, Any]] = None,
                     duracion_ms: Optional[int] = None) -> bool:
    """Inserta una fila en HUB_ActivityLog desde el servidor.

    Pensado para llamarse DESPUÉS del commit de la operación real: usa su propia
    conexión y se traga cualquier error para que un fallo de bitácora nunca
    tumbe la operación del usuario. Devuelve True si se registró.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUB_ActivityLog (FechaHora, Usuario, Modulo, Accion, App, UsuarioId, "
            "TipoAccion, Detalle, Datos, Dispositivo, IpAddress, DuracionMs) "
            "VALUES (GETDATE(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ((user or {}).get("email") or "", (modulo or "")[:10], (accion or "")[:500], "WRK",
             (user or {}).get("id"), (tipo_accion or "")[:20], (detalle or "")[:500],
             json.dumps(datos, ensure_ascii=False) if datos else None,
             "", "", duracion_ms),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


@router.post("/log")
def log_action(body: AuditLogRequest, user: dict = Depends(require_user)):
    """Registra una acción del usuario en la bitácora."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUB_ActivityLog (FechaHora, Usuario, Modulo, Accion, App, UsuarioId, "
            "TipoAccion, Detalle, Datos, Dispositivo, IpAddress, DuracionMs) "
            "VALUES (GETDATE(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user.get("email") or "", body.modulo[:10], body.accion[:500], body.app[:10],
             user.get("id"), body.tipo_accion[:20], (body.detalle or "")[:500],
             json.dumps(body.datos, ensure_ascii=False) if body.datos else None,
             "", "", body.duracion_ms),
        )
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


@router.get("/log")
def get_audit_log(
    usuario: Optional[str] = None,
    modulo: Optional[str] = None,
    tipo: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(require_user),
):
    """Consulta la bitácora (solo admin)."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Sin acceso")
    conn = get_connection()
    cur = conn.cursor(as_dict=True)
    conditions = ["1=1"]
    params = []
    if usuario:
        conditions.append("Usuario LIKE %s")
        params.append(f"%{usuario}%")
    if modulo:
        conditions.append("Modulo = %s")
        params.append(modulo)
    if tipo:
        conditions.append("TipoAccion = %s")
        params.append(tipo)
    if desde:
        conditions.append("FechaHora >= %s")
        params.append(desde)
    if hasta:
        conditions.append("FechaHora <= %s")
        params.append(hasta)
    where = " AND ".join(conditions)
    cur.execute(
        f"SELECT TOP ({int(limit)}) Id, FechaHora, Usuario, Modulo, Accion, App, TipoAccion, "
        f"Detalle, Datos, DuracionMs FROM HUB_ActivityLog WHERE {where} ORDER BY FechaHora DESC",
        tuple(params),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r["Id"],
            "fecha": r["FechaHora"].strftime("%Y-%m-%d %H:%M:%S") if r.get("FechaHora") else "",
            "usuario": r["Usuario"],
            "modulo": r["Modulo"],
            "accion": r["Accion"],
            "app": r["App"],
            "tipo": r["TipoAccion"],
            "detalle": r["Detalle"],
            "datos": r["Datos"],
            "duracion_ms": r["DuracionMs"],
        }
        for r in rows
    ]


@router.delete("/log/clean")
def clean_audit_log(dias: int = 30, user: dict = Depends(require_user)):
    """Limpia registros antiguos (solo admin). Política: 30 días."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Sin acceso")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM HUB_ActivityLog WHERE FechaHora < DATEADD(day, -%s, GETDATE())", (int(dias),))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True, "deleted": deleted}
