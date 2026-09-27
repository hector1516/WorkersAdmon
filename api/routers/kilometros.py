from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from auth import require_user
from db import get_connection
from datetime import datetime

router = APIRouter()

class RegistroKm(BaseModel):
    id_auto: int
    kilometros: int

@router.get("/historial/{id_auto}")
def get_historial(id_auto: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT k.Id, k.Kilometros, k.FechaHora, u.Nombre as Usuario
            FROM HUB_RegistroKilometros k
            LEFT JOIN HUB_Users u ON k.IdUsuario = u.Id
            WHERE k.IdAutomovil = %s
            ORDER BY k.FechaHora DESC
        """, (id_auto,))
        return cur.fetchall()

@router.get("/semana/{id_auto}")
def get_semana(id_auto: int, user: dict = Depends(require_user)):
    """Indica si el vehículo ya tiene registro desde el domingo 00:00 (hora México)."""
    import datetime as _dt
    # Domingo 00:00 México (UTC-6 fijo) expresado en UTC (FechaHora se guarda en UTC)
    ahora_mx = _dt.datetime.utcnow() - _dt.timedelta(hours=6)
    domingo_mx = (ahora_mx - _dt.timedelta(days=(ahora_mx.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    domingo_utc = (domingo_mx + _dt.timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT TOP 1 Kilometros, FechaHora FROM HUB_RegistroKilometros
            WHERE IdAutomovil = %s AND FechaHora >= %s
            ORDER BY FechaHora DESC
        """, (id_auto, domingo_utc))
        row = cur.fetchone()
        return {"registrado": row is not None, "domingo_mx": domingo_mx.strftime('%Y-%m-%d %H:%M:%S')}

@router.post("/registrar")
def registrar(req: RegistroKm, user: dict = Depends(require_user)):
    conn = get_connection()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with conn.cursor() as cur:
        # Duplicate check: max 1 per vehicle per day
        cur.execute("""
            SELECT COUNT(*) FROM HUB_RegistroKilometros
            WHERE IdAutomovil = %s AND CAST(FechaHora AS DATE) = CAST(%s AS DATE)
        """, (req.id_auto, now))
        if cur.fetchone()[0] > 0:
            raise HTTPException(status_code=400, detail="Este automóvil ya tiene un registro de kilometraje registrado el día de hoy. Solo se permite un registro por día.")

        # Get last km (from RegistroKilometros OR last vale)
        cur.execute("""
            SELECT ISNULL(MAX(Kilometros), 0) FROM HUB_RegistroKilometros
            WHERE IdAutomovil = %s
        """, (req.id_auto,))
        ultimo_km_registro = cur.fetchone()[0]
        cur.execute("""
            SELECT ISNULL((SELECT TOP 1 Kilometros FROM HUB_SolicitudVales
                           WHERE IdAutomovil = %s AND Kilometros IS NOT NULL AND Kilometros > 0
                           ORDER BY FechaSolicitud DESC, Id DESC), 0)
        """, (req.id_auto,))
        ultimo_km_vale = cur.fetchone()[0]
        ultimo_km = max(ultimo_km_registro, ultimo_km_vale)
        if req.kilometros < ultimo_km:
            raise HTTPException(status_code=400, detail=f"Los kilometros reportados no pueden ser menores al kilometraje actual registrado ({ultimo_km} km).")

        # Insert
        cur.execute("""
            INSERT INTO HUB_RegistroKilometros (IdAutomovil, Kilometros, FechaHora, IdUsuario)
            VALUES (%s, %s, %s, %s)
        """, (req.id_auto, req.kilometros, now, user["id"]))

        # Activity log
        cur.execute("""
            SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s
        """, (req.id_auto,))
        auto = cur.fetchone()
        if auto:
            car_info = f"{auto[0]} ({auto[1]})"
            cur.execute("""
                INSERT INTO HUB_ActivityLog (Usuario, Modulo, Accion)
                SELECT Nombre, 'Registro Kilometros', 'Registró ' + CAST(%s AS VARCHAR) + ' Km para ' + CAST(%s AS VARCHAR)
                FROM HUB_Users WHERE Id = %s
            """, (req.kilometros, car_info, user["id"]))

        conn.commit()
        try:
            from routers.legends import _registrar_metrica
            _registrar_metrica(user["id"], "kilometro", referencia_id=req.id_auto)
        except Exception:
            pass
        try:
            from routers.push import send_push_notification
            send_push_notification(user["id"], "🚗 Kilómetros registrados", f"{req.kilometros} km registrados — +5 pts", "/kilometros")
        except Exception:
            pass
        return {"success": True}
