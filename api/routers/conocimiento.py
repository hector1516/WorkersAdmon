"""Base de Conocimientos - Búsqueda rápida de información extraída de reportes de servicio."""
from fastapi import APIRouter, Depends, Query
from auth import require_user
from db import get_connection
from typing import Optional

router = APIRouter(tags=["conocimiento"])


@router.get("/search")
def search_conocimiento(
    q: str = Query(..., min_length=2),
    cliente: Optional[str] = Query(None),
    maquina: Optional[str] = Query(None),
    desde: Optional[str] = Query(None),
    hasta: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(require_user),
):
    """Busca en descripciones y notas de reportes de servicio firmados."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        search = f"%{q}%"
        conditions = [
            "(r.DescripcionServicio LIKE %s OR r.Notas LIKE %s OR r.Folio LIKE %s OR r.Contacto LIKE %s)",
            "r.Estatus = 'Firmado'",
        ]
        params = [search, search, search, search]

        if cliente:
            conditions.append("r.Cliente = %s")
            params.append(cliente)
        if maquina:
            conditions.append("r.MaquinaLinea LIKE %s")
            params.append(f"%{maquina}%")
        if desde:
            conditions.append("r.Fecha >= %s")
            params.append(desde)
        if hasta:
            conditions.append("r.Fecha <= %s")
            params.append(hasta)

        where = " AND ".join(conditions)
        params_tuple = tuple(params)

        # Count total
        count_sql = f"SELECT COUNT(*) AS Total FROM ReportesServicio r WHERE {where}"
        cur.execute(count_sql, params_tuple)
        total = cur.fetchone()["Total"]
        pages = max(1, -(-total // limit))

        # Query with pagination
        offset = (page - 1) * limit
        query_sql = f"""
            SELECT r.IdReporte, r.Folio, r.Fecha, r.Cliente, r.Contacto,
                   r.MaquinaLinea, r.Tecnico, r.DescripcionServicio, r.Notas,
                   c.Cliente AS ClienteNombre,
                   STUFF((SELECT ', ' + u.Nombre
                    FROM ReportesServicioTecnicos rt
                    JOIN HUB_Users u ON rt.IdUsuario = u.Id
                    WHERE rt.IdReporte = r.IdReporte
                    FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), 1, 2, '') AS TecnicosExtra
            FROM ReportesServicio r
            LEFT JOIN Clientes c ON r.Cliente = c.IdCliente
            WHERE {where}
            ORDER BY r.Fecha DESC
            OFFSET %s ROWS FETCH NEXT %s ROWS ONLY
        """
        cur.execute(query_sql, tuple(params + [offset, limit]))
        rows = cur.fetchall()

        # Convert date to string for JSON
        for row in rows:
            if row.get("Fecha"):
                row["Fecha"] = row["Fecha"].isoformat()

        return {
            "results": rows,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
        }


@router.get("/filters")
def get_filters(user: dict = Depends(require_user)):
    """Retorna listas de clientes y máquinas únicos para los filtros."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT DISTINCT r.Cliente, c.Cliente AS ClienteNombre
            FROM ReportesServicio r
            LEFT JOIN Clientes c ON r.Cliente = c.IdCliente
            WHERE r.Estatus = 'Firmado'
            ORDER BY r.Cliente
        """)
        clientes = [{"id": r["Cliente"], "nombre": r["ClienteNombre"] or r["Cliente"]} for r in cur.fetchall()]

        cur.execute("""
            SELECT DISTINCT MaquinaLinea
            FROM ReportesServicio
            WHERE Estatus = 'Firmado' AND MaquinaLinea IS NOT NULL AND MaquinaLinea != ''
            ORDER BY MaquinaLinea
        """)
        maquinas = [r["MaquinaLinea"] for r in cur.fetchall()]

        return {"clientes": clientes, "maquinas": maquinas}
