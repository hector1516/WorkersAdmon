"""Router del Repositorio Clientes — búsqueda y descarga de archivos indexados."""
import os
import io
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from auth import require_user
from db import get_connection
from typing import Optional, List

router = APIRouter()


@router.get("/tree")
def get_tree(
    path: Optional[str] = Query(None),
    servidor: Optional[str] = Query(None),
    user: dict = Depends(require_user),
):
    """Retorna hijos de una carpeta (carpetas primero, luego archivos)."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            if path:
                cur.execute("""
                    SELECT Id, Nombre, Extension, EsCarpeta, Tamanio, FechaModificado, Servidor, RutaCompleta
                    FROM HUB_FileIndex
                    WHERE RutaPadre = %s
                    ORDER BY EsCarpeta DESC, Nombre ASC
                """, (path,))
            else:
                # Raíz: mostrar las carpetas top-level (Shared y Aplicaciones)
                cur.execute("""
                    SELECT Id, Nombre, Extension, EsCarpeta, Tamanio, FechaModificado, Servidor, RutaCompleta
                    FROM HUB_FileIndex
                    WHERE EsCarpeta = 1 AND RutaPadre IN ('/Shared', '/Aplicaciones')
                    ORDER BY Nombre ASC
                """)

            rows = cur.fetchall()
            # Convertir datetime a string
            for r in rows:
                if r.get("FechaModificado"):
                    r["FechaModificado"] = r["FechaModificado"].isoformat()
            return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.get("/search")
def search_files(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_user),
):
    """Búsqueda por nombre/extensión con paginación."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            offset = (page - 1) * limit
            search = f"%{q}%"

            # Contar total
            cur.execute("""
                SELECT COUNT(*) AS Total FROM HUB_FileIndex
                WHERE Nombre LIKE %s OR Extension LIKE %s
            """, (search, search))
            total = (cur.fetchone() or {}).get("Total", 0)

            # Obtener página
            cur.execute("""
                SELECT Id, Nombre, Extension, EsCarpeta, Tamanio, FechaModificado,
                       Servidor, RutaCompleta, RutaPadre
                FROM HUB_FileIndex
                WHERE Nombre LIKE %s OR Extension LIKE %s
                ORDER BY EsCarpeta DESC, Nombre ASC
                OFFSET %s ROWS FETCH NEXT %s ROWS ONLY
            """, (search, search, offset, limit))

            rows = cur.fetchall()
            for r in rows:
                if r.get("FechaModificado"):
                    r["FechaModificado"] = r["FechaModificado"].isoformat()

            return {
                "results": rows,
                "total": total,
                "page": page,
                "pages": (total + limit - 1) // limit if total > 0 else 0,
            }
    finally:
        try:
            conn.close()
        except Exception:
            pass


class DownloadRequest(BaseModel):
    ids: List[int]


@router.post("/download")
def download_files(req: DownloadRequest, user: dict = Depends(require_user)):
    """Descarga un solo archivo directo desde el SMB (sin ZIP)."""
    if not req.ids:
        raise HTTPException(status_code=400, detail="No hay archivo seleccionado")
    if len(req.ids) > 1:
        raise HTTPException(status_code=400, detail="Selecciona solo un archivo para compartir")

    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT Id, Nombre, RutaCompleta, EsCarpeta, Extension, Tamanio
                FROM HUB_FileIndex
                WHERE Id = %s AND EsCarpeta = 0
            """, (req.ids[0],))
            f = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not f:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    # Conectar SMB y descargar archivo directo
    try:
        from smb.SMBConnection import SMBConnection
    except ImportError:
        raise HTTPException(status_code=500, detail="pysmb no instalado")

    smb_conn = SMBConnection("eccsa", "eyccazo", "field_api", "FILESERVER",
                             domain="", use_ntlm_v2=True, is_direct_tcp=True)
    if not smb_conn.connect("10.188.141.15", 445, timeout=30):
        raise HTTPException(status_code=503, detail="No se pudo conectar al FileServer")

    try:
        ruta = f["RutaCompleta"]
        parts = ruta.split("/", 2)
        if len(parts) < 3:
            raise HTTPException(status_code=400, detail="Ruta inválida")
        smb_path = f"/{parts[1]}/{parts[2]}"
        share_name = "Docs"

        file_obj = io.BytesIO()
        smb_conn.retrieveFile(share_name, smb_path, file_obj)
        file_obj.seek(0)

        # MIME type por extensión
        ext = (f["Extension"] or "").lower()
        mime_map = {
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.gif': 'image/gif', '.bmp': 'image/bmp',
            '.doc': 'application/msword', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.ppt': 'application/vnd.ms-powerpoint', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.zip': 'application/zip', '.rar': 'application/x-rar-compressed',
            '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
            '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
            '.txt': 'text/plain', '.csv': 'text/csv',
        }
        content_type = mime_map.get(ext, 'application/octet-stream')

        return StreamingResponse(
            file_obj,
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{f["Nombre"]}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error descargando: {e}")
    finally:
        try:
            smb_conn.close()
        except Exception:
            pass


@router.get("/stats")
def get_stats(user: dict = Depends(require_user)):
    """Retorna metadata de indexación: última fecha y total de archivos."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT MAX(Indexado) AS UltimaIndexacion, COUNT(*) AS TotalArchivos FROM HUB_FileIndex")
            row = cur.fetchone() or {}
            return {
                "ultima_indexacion": str(row.get("UltimaIndexacion") or ""),
                "total_archivos": row.get("TotalArchivos") or 0,
            }
    finally:
        conn.close()
