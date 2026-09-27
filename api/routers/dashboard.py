"""
Dashboard público — Centro de Operaciones ECCSA
Endpoint sin auth que devuelve todos los KPIs en un solo response.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from db import get_connection
from datetime import datetime, timedelta
import io
import os
import random
import time

router = APIRouter()

# ---------- Cache simple (TTL 15s) ----------
_cache = {}
_cache_ttl = 15

# Cache de thumbnails en disco: evita recomprimir las fotos gigantes (~1MB)
# en cada request del kiosk. Clave = foto + ancho deseado.
_thumb_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache_thumbs")
_thumb_ttl = 86400  # 24h — las fotos de reportes no suelen cambiar


def _cached(key: str, fn, ttl: int = _cache_ttl):
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    data = fn()
    _cache[key] = {"data": data, "ts": now}
    return data


def _b64(blob):
    """Convert BLOB to base64 string."""
    if blob is None:
        return None
    import base64
    return base64.b64encode(blob).decode("utf-8")


def _make_thumbnail(raw: bytes, width: int) -> Optional[bytes]:
    """Genera JPEG redimensionado. Devuelve None si falla (se sirve el original)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if img.width > width:
            ratio = width / float(img.width)
            img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=78, optimize=True, progressive=True)
        return out.getvalue()
    except Exception:
        return None


def _thumb_cache_path(cache_key: str, width: int) -> str:
    os.makedirs(_thumb_dir, exist_ok=True)
    return os.path.join(_thumb_dir, f"{cache_key}_w{width}.jpg")


def _get_thumbnail(raw: bytes, cache_key: str, width: int) -> bytes:
    """Devuelve thumbnail desde disco si es fresco; si no, lo genera y cachea."""
    path = _thumb_cache_path(cache_key, width)
    try:
        if os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < _thumb_ttl:
            with open(path, "rb") as f:
                data = f.read()
            if data:
                return data
    except Exception:
        pass
    thumb = _make_thumbnail(raw, width)
    if thumb is None:
        return raw  # fallback: original
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(thumb)
        os.replace(tmp, path)
    except Exception:
        pass
    return thumb


# ---------- GET /api/dashboard ----------
@router.get("")
def dashboard_data():
    """Devuelve todos los KPIs del dashboard en un solo response."""
    return _cached("dashboard_full", _build_dashboard, ttl=15)


def _build_dashboard():
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            data = {}
            data["timestamp"] = datetime.utcnow().isoformat()

            # ---- Fecha base: semana (domingo) ----
            import datetime as _dt
            hoy_str = datetime.utcnow().strftime("%Y-%m-%d")
            today = _dt.date.today()
            sunday = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
            sunday_str = str(sunday)

            # ---- REPORTES (semana) ----
            cur.execute("SELECT COUNT(*) AS total FROM ReportesServicio WHERE Fecha >= %s", (sunday_str,))
            total_r = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM ReportesServicio WHERE Estatus = 'Firmado' AND Fecha >= %s", (sunday_str,))
            firmados = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM ReportesServicio WHERE CAST(Fecha AS DATE) = %s", (hoy_str,))
            hoy_r = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM ReportesServicio WHERE Fecha >= %s", (sunday_str,))
            semana_r = cur.fetchone()["total"]

            # Por cliente
            cur.execute("""
                SELECT TOP 10 Cliente, COUNT(*) AS total
                FROM ReportesServicio
                GROUP BY Cliente
                ORDER BY total DESC
            """)
            por_cliente = cur.fetchall()

            # Por ingeniero
            cur.execute("""
                SELECT TOP 10 Tecnico AS nombre,
                    SUM(CASE WHEN Estatus = 'Firmado' THEN 1 ELSE 0 END) AS firmados,
                    SUM(CASE WHEN Estatus = 'Borrador' THEN 1 ELSE 0 END) AS pendientes,
                    COUNT(*) AS total
                FROM ReportesServicio
                GROUP BY Tecnico
                ORDER BY total DESC
            """)
            por_ingeniero = cur.fetchall()

            # Últimos 8 reportes
            cur.execute("""
                SELECT TOP 8 Folio, Cliente, Tecnico AS ingeniero,
                    CAST(Fecha AS VARCHAR(10)) AS fecha, Estatus
                FROM ReportesServicio
                ORDER BY IdReporte DESC
            """)
            ultimos_r = cur.fetchall()

            data["reportes"] = {
                "total": total_r,
                "firmados": firmados,
                "pendientes": total_r - firmados,
                "hoy": hoy_r,
                "esta_semana": semana_r,
                "por_cliente": por_cliente,
                "por_ingeniero": por_ingeniero,
                "ultimos": ultimos_r,
            }

            # ---- KILOMETROS: consumo semanal por vehículo ----
            # Totales de la semana / hoy (suma de registros, no odómetro).
            cur.execute("SELECT SUM(Kilometros) AS total FROM HUB_RegistroKilometros WHERE FechaHora >= %s", (sunday_str,))
            total_km_semana = cur.fetchone()["total"] or 0
            cur.execute("""
                SELECT SUM(k.Kilometros) AS total
                FROM HUB_RegistroKilometros k
                WHERE CAST(k.FechaHora AS DATE) = %s
            """, (hoy_str,))
            km_hoy = cur.fetchone()["total"] or 0

            # consumo = último registro ESTA semana − último registro ANTERIOR.
            # Si falta cualquiera de los dos, el vehículo NO se muestra.
            cur.execute("""
                SELECT a.Id, a.MarcaModelo, a.Placas,
                    this_week.km AS km_esta_semana,
                    prev_week.km AS km_semana_pasada,
                    CASE WHEN this_week.km IS NOT NULL THEN 1 ELSE 0 END AS registrado_semana
                FROM HUB_Automoviles a
                OUTER APPLY (
                    SELECT TOP 1 k.Kilometros AS km
                    FROM HUB_RegistroKilometros k
                    WHERE k.IdAutomovil = a.Id
                      AND CAST(k.FechaHora AS DATE) >= %s
                    ORDER BY k.FechaHora DESC
                ) this_week
                OUTER APPLY (
                    SELECT TOP 1 k.Kilometros AS km
                    FROM HUB_RegistroKilometros k
                    WHERE k.IdAutomovil = a.Id
                      AND CAST(k.FechaHora AS DATE) < %s
                    ORDER BY k.FechaHora DESC
                ) prev_week
                WHERE this_week.km IS NOT NULL AND prev_week.km IS NOT NULL
                ORDER BY a.MarcaModelo
            """, (sunday_str, sunday_str))
            vehiculos_km = cur.fetchall()
            for v in vehiculos_km:
                # Consumo real de la semana (puede ser 0 si no se movió)
                v["consumo_semana"] = int((v.get("km_esta_semana") or 0) - (v.get("km_semana_pasada") or 0))

            # Últimos 8 registros de km
            cur.execute("""
                SELECT TOP 8 a.MarcaModelo AS vehiculo, a.Placas,
                    k.Kilometros AS km, u.Nombre AS usuario,
                    CONVERT(VARCHAR(16), k.FechaHora, 120) AS fecha
                FROM HUB_RegistroKilometros k
                JOIN HUB_Automoviles a ON k.IdAutomovil = a.Id
                JOIN HUB_Users u ON k.IdUsuario = u.Id
                ORDER BY k.FechaHora DESC
            """)
            ultimos_km = cur.fetchall()

            data["kilometros"] = {
                "total_semana": total_km_semana,
                "hoy": km_hoy,
                "por_vehiculo": vehiculos_km,
                "sin_registro_semana": [v for v in vehiculos_km if not v["registrado_semana"]],
                "ultimos": ultimos_km,
            }

            # ---- TICKETS (solo esta semana, con indicador de foto) ----
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM HUB_OxxoGasTickets
                WHERE FechaRegistro >= %s
            """, (sunday_str,))
            tickets_semana = cur.fetchone()
            # Últimos 12 tickets DE LA SEMANA (con foto si existe)
            cur.execute("""
                SELECT TOP 12 t.Id AS id_ticket, t.FolioTicket AS folio,
                    a.MarcaModelo AS vehiculo, a.Placas,
                    u.Nombre AS usuario,
                    CONVERT(VARCHAR(16), t.FechaRegistro, 120) AS fecha,
                    ISNULL(c.Cliente, t.IdCliente) AS cliente,
                    ISNULL(t.Descripcion, '') AS descripcion,
                    CASE WHEN t.ImagenTicket IS NOT NULL THEN 1 ELSE 0 END AS tiene_foto
                FROM HUB_OxxoGasTickets t
                LEFT JOIN HUB_Automoviles a ON t.IdVehiculo = a.Id
                JOIN HUB_Users u ON t.IdUsuario = u.Id
                LEFT JOIN clientes c ON t.IdCliente = c.IdCliente
                WHERE t.FechaRegistro >= %s
                ORDER BY t.Id DESC
            """, (sunday_str,))
            ultimos_t = cur.fetchall()

            data["tickets"] = {
                "total_semana": tickets_semana["total"] if tickets_semana else 0,
                "ultimos": ultimos_t,
            }

            # ---- VALES: fuera del kiosk (pantalla eliminada). El módulo vive en /vales con auth.

            # ---- LEGENDS (solo usuarios con passkey) ----
            cur.execute("""
                SELECT TOP 10
                    u.Nombre AS nombre,
                    ISNULL(u.Nickname, u.Nombre) AS nickname,
                    ISNULL(s.PuntuacionSemanal, 0) AS puntos,
                    ISNULL(s.Nivel, 'Bronce') AS nivel,
                    a.AvatarBase64 AS avatar
                FROM HUB_Users u
                LEFT JOIN HUB_UserScores s ON s.IdUsuario = u.Id
                LEFT JOIN HUB_UserAvatars a ON a.IdUsuario = u.Id
                WHERE u.Activo = 1
                  AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)
                ORDER BY ISNULL(s.PuntuacionSemanal, 0) DESC
            """)
            ranking = cur.fetchall()
            # Convert avatar BLOB to string if needed
            for r in ranking:
                if r.get("avatar") and isinstance(r["avatar"], bytes):
                    import base64
                    r["avatar"] = base64.b64encode(r["avatar"]).decode("utf-8")
            # Ganador de la semana
            cur.execute("""
                SELECT TOP 1 u.Nombre AS nombre, w.PuntuacionSemana AS puntos
                FROM HUB_WeeklyWinners w
                JOIN HUB_Users u ON w.IdUsuario = u.Id
                ORDER BY w.FechaInicio DESC
            """)
            ganador = cur.fetchone()
            cur.execute("SELECT ISNULL(SUM(PuntuacionSemanal), 0) AS total FROM HUB_UserScores")
            total_pts = cur.fetchone()["total"]

            data["legends"] = {
                "ranking": ranking,
                "ganador_semana": ganador,
                "total_puntos_otorgados": total_pts,
            }

            # ---- FOTOS (solo IDs para el carrusel) ----
            cur.execute("""
                SELECT TOP 30 f.IdReporte, r.Folio, r.Cliente, r.Tecnico AS ingeniero, f.Orden
                FROM ReportesServicioFotos f
                JOIN ReportesServicio r ON f.IdReporte = r.IdReporte
                ORDER BY NEWID()
            """)
            fotos = cur.fetchall()
            data["fotos"] = {"ids": fotos}

            # ---- NOTAS ----
            cur.execute("""
                SELECT Id, Titulo, Contenido, Color, Autor, Fija,
                    CONVERT(VARCHAR(16), FechaCreacion, 120) AS fecha_creacion,
                    CONVERT(VARCHAR(16), FechaModificado, 120) AS fecha_modificado
                FROM HUB_DashboardNotas
                ORDER BY Fija DESC, FechaModificado DESC
            """)
            data["notas"] = cur.fetchall()

            # ---- CUMPLEANOS Y ANIVERSARIOS DEL MES ----
            # CURP: LLLL YYMM DDSS ... pos 5-6=anio, 7-8=mes, 9-10=dia
            import datetime as _dt
            now = _dt.datetime.now()
            mes_actual = now.month

            cur.execute("""
                SELECT u.Nombre,
                    CASE
                        WHEN u.FechaNacimiento IS NOT NULL THEN DAY(u.FechaNacimiento)
                        WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                            CAST(SUBSTRING(u.CurpRfc, 9, 2) AS INT)
                        ELSE NULL
                    END AS dia,
                    CASE
                        WHEN u.FechaNacimiento IS NOT NULL THEN MONTH(u.FechaNacimiento)
                        WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                            CAST(SUBSTRING(u.CurpRfc, 7, 2) AS INT)
                        ELSE NULL
                    END AS mes,
                    CASE
                        WHEN u.FechaNacimiento IS NOT NULL THEN
                            YEAR(GETDATE()) - YEAR(u.FechaNacimiento)
                        WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                            CASE
                                WHEN CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT) > 30
                                    THEN YEAR(GETDATE()) - (1900 + CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT))
                                ELSE YEAR(GETDATE()) - (2000 + CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT))
                            END
                        ELSE NULL
                    END AS edad
                FROM HUB_Users u
                WHERE u.Activo = 1
                    AND (
                        (u.FechaNacimiento IS NOT NULL AND MONTH(u.FechaNacimiento) = %s)
                        OR (u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10
                            AND CAST(SUBSTRING(u.CurpRfc, 7, 2) AS INT) = %s)
                    )
                ORDER BY dia
            """, (mes_actual, mes_actual))
            cumpleanos = cur.fetchall()

            # Aniversarios de ingreso del mes
            cur.execute("""
                SELECT u.Nombre,
                    DAY(u.FechaIngreso) AS dia,
                    MONTH(u.FechaIngreso) AS mes,
                    YEAR(GETDATE()) - YEAR(u.FechaIngreso) AS anos
                FROM HUB_Users u
                WHERE u.Activo = 1
                    AND u.FechaIngreso IS NOT NULL
                    AND MONTH(u.FechaIngreso) = %s
                ORDER BY dia
            """, (mes_actual,))
            aniversarios = cur.fetchall()

            data["cumpleanos"] = cumpleanos
            data["aniversarios"] = aniversarios

            # ---- AVATARS para cumpleaños/aniversarios ----
            cur.execute("""
                SELECT u.Nombre, a.AvatarBase64 AS avatar
                FROM HUB_Users u
                LEFT JOIN HUB_UserAvatars a ON a.IdUsuario = u.Id
                WHERE u.Activo = 1
            """)
            avatar_map = {}
            for row in cur.fetchall():
                avatar_map[row["Nombre"]] = row.get("avatar")
            # Inject avatars into cumpleanos and aniversarios
            for c in cumpleanos:
                av = avatar_map.get(c["Nombre"])
                c["avatar"] = _b64(av) if av and isinstance(av, bytes) else av
            for a in aniversarios:
                av = avatar_map.get(a["Nombre"])
                a["avatar"] = _b64(av) if av and isinstance(av, bytes) else av

            # ---- NOTIFICACIONES: fuera del dashboard (pantalla eliminada del kiosk).
            # El historial vive en HUB_Notificaciones (HUB admin); los push PWA no dependen de aquí.

            # ---- TOP CLIENTE (semana) ----
            cur.execute("""
                SELECT TOP 1 Cliente, COUNT(*) AS total
                FROM ReportesServicio
                WHERE Fecha >= %s
                GROUP BY Cliente
                ORDER BY total DESC
            """, (sunday_str,))
            top_cliente = cur.fetchone()
            data["top_cliente"] = top_cliente

            # ---- INGENIERO con más horas en reportes (semana) ----
            cur.execute("""
                SELECT TOP 1 Tecnico AS nombre, COUNT(*) AS reportes,
                    SUM(CASE
                        WHEN FechaHoraInicio IS NOT NULL AND FechaHoraFin IS NOT NULL
                        THEN DATEDIFF(MINUTE, FechaHoraInicio, FechaHoraFin)
                        ELSE 0
                    END) AS minutos_totales
                FROM ReportesServicio
                WHERE FechaHoraInicio IS NOT NULL AND FechaHoraFin IS NOT NULL
                    AND Fecha >= %s
                GROUP BY Tecnico
                ORDER BY minutos_totales DESC
            """, (sunday_str,))
            top_ingeniero = cur.fetchone()
            if top_ingeniero and top_ingeniero.get("minutos_totales"):
                top_ingeniero["horas_totales"] = round(top_ingeniero["minutos_totales"] / 60, 1)
            data["top_ingeniero"] = top_ingeniero

            # ---- TOP VEHÍCULOS por tickets OxxoGas (semana) ----
            cur.execute("""
                SELECT TOP 5 a.MarcaModelo AS vehiculo, COUNT(*) AS total_tickets
                FROM HUB_OxxoGasTickets t
                JOIN HUB_Automoviles a ON t.IdVehiculo = a.Id
                WHERE t.FechaRegistro >= %s
                GROUP BY a.MarcaModelo
                ORDER BY total_tickets DESC
            """, (sunday_str,))
            data["tickets_por_vehiculo"] = cur.fetchall()

            return data
    finally:
        conn.close()


# ---------- CRUD Notas ----------

class NotaCreate(BaseModel):
    titulo: str
    contenido: str = ""
    color: str = "#F59E0B"
    autor: str = "Sistema"
    fija: bool = False


class NotaUpdate(BaseModel):
    titulo: Optional[str] = None
    contenido: Optional[str] = None
    color: Optional[str] = None
    fija: Optional[bool] = None


@router.get("/notas")
def listar_notas():
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT Id, Titulo, Contenido, Color, Autor, Fija,
                    CONVERT(VARCHAR(16), FechaCreacion, 120) AS fecha_creacion,
                    CONVERT(VARCHAR(16), FechaModificado, 120) AS fecha_modificado
                FROM HUB_DashboardNotas
                ORDER BY Fija DESC, FechaModificado DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


@router.post("/notas")
def crear_nota(body: NotaCreate):
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                INSERT INTO HUB_DashboardNotas (Titulo, Contenido, Color, Autor, Fija)
                OUTPUT INSERTED.Id
                VALUES (%s, %s, %s, %s, %s)
            """, (body.titulo, body.contenido, body.color, body.autor, 1 if body.fija else 0))
            row = cur.fetchone()
            conn.commit()
            # Invalidate cache
            _cache.pop("dashboard_full", None)
            return {"id": row["Id"], "mensaje": "Nota creada"}
    finally:
        conn.close()


@router.put("/notas/{nota_id}")
def editar_nota(nota_id: int, body: NotaUpdate):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            updates = []
            params = []
            if body.titulo is not None:
                updates.append("Titulo = %s")
                params.append(body.titulo)
            if body.contenido is not None:
                updates.append("Contenido = %s")
                params.append(body.contenido)
            if body.color is not None:
                updates.append("Color = %s")
                params.append(body.color)
            if body.fija is not None:
                updates.append("Fija = %s")
                params.append(1 if body.fija else 0)
            if not updates:
                return {"mensaje": "Sin cambios"}
            updates.append("FechaModificado = GETDATE()")
            params.append(nota_id)
            cur.execute(f"UPDATE HUB_DashboardNotas SET {', '.join(updates)} WHERE Id = %s", params)
            conn.commit()
            _cache.pop("dashboard_full", None)
            return {"mensaje": "Nota actualizada"}
    finally:
        conn.close()


@router.delete("/notas/{nota_id}")
def eliminar_nota(nota_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM HUB_DashboardNotas WHERE Id = %s", (nota_id,))
            conn.commit()
            _cache.pop("dashboard_full", None)
            return {"mensaje": "Nota eliminada"}
    finally:
        conn.close()


# ---------- Public photo endpoint (no auth, con thumbnail cacheado) ----------
@router.get("/foto/{id_reporte}/{orden}")
def dashboard_foto(
    id_reporte: int,
    orden: int,
    w: int = Query(default=0, ge=0, le=2048, description="Ancho del thumbnail (0 = original)"),
):
    """Sirve fotos de reportes sin autenticación (para el dashboard público).

    Con ?w=480 devuelve un JPEG redimensionado cacheado en disco — mucho más
    rápido que servir el original (~1.1MB promedio) en cada rotación del kiosk.
    """
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT FotoComprimida FROM ReportesServicioFotos WHERE IdReporte = %s AND Orden = %s",
                (id_reporte, orden),
            )
            row = cur.fetchone()
            if not row or not row.get("FotoComprimida"):
                raise HTTPException(status_code=404, detail="Foto no encontrada")
            raw = row["FotoComprimida"]
            if w and w > 0:
                payload = _get_thumbnail(raw, f"rep_{id_reporte}_{orden}", w)
                max_age = 86400
            else:
                payload = raw
                max_age = 300
            return StreamingResponse(
                io.BytesIO(payload),
                media_type="image/jpeg",
                headers={"Cache-Control": f"public, max-age={max_age}"},
            )
    finally:
        conn.close()


# ---------- Public ticket photo endpoint (no auth, con thumbnail) ----------
@router.get("/ticket-foto/{id_ticket}")
def dashboard_ticket_foto(
    id_ticket: int,
    w: int = Query(default=480, ge=0, le=2048, description="Ancho del thumbnail (0 = original)"),
):
    """Sirve la foto del ticket OxxoGas sin auth (kiosk). Cacheada/thumbnail."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT ImagenTicket FROM HUB_OxxoGasTickets WHERE Id = %s",
                (id_ticket,),
            )
            row = cur.fetchone()
            if not row or not row.get("ImagenTicket"):
                raise HTTPException(status_code=404, detail="Sin imagen")
            raw = row["ImagenTicket"]
            if w and w > 0:
                payload = _get_thumbnail(raw, f"tk_{id_ticket}", w)
                max_age = 86400
            else:
                payload = raw
                max_age = 300
            return StreamingResponse(
                io.BytesIO(payload),
                media_type="image/jpeg",
                headers={"Cache-Control": f"public, max-age={max_age}"},
            )
    finally:
        conn.close()


# ---------- Background aleatorio desde BD (estilo HUB Bing wallpapers) ----------
_DEFAULT_BGS = [
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=1920&q=80",
    "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?auto=format&fit=crop&w=1920&q=80",
    "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1920&q=80",
]


@router.get("/background")
def dashboard_background():
    """URL de fondo aleatorio desde HUB_BingWallpapers (misma BD que el HUB)."""
    def _pick():
        try:
            conn = get_connection()
            try:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute("SELECT TOP 5 Url FROM HUB_BingWallpapers ORDER BY Id DESC")
                    rows = cur.fetchall()
                    if rows:
                        return {"url": random.choice([r["Url"] for r in rows]), "source": "bd"}
            finally:
                conn.close()
        except Exception:
            pass
        return {"url": random.choice(_DEFAULT_BGS), "source": "default"}

    # TTL corto: el kiosk rota de pantalla cada ~20s y queremos fondo distinto
    # sin golpear la BD en cada frame. 8s = un random distinto por rotación.
    return _cached("bg_random", _pick, ttl=8)
