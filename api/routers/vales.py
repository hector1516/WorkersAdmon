from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from auth import require_user
from db import get_connection
from typing import Optional
from datetime import datetime, timedelta

router = APIRouter()


def _evaluar_criterios(conn, id_usuario: int, id_automovil: int):
    """Evalúa criterios de auto-aprobación (mismos que HUB).
    Retorna (aprobado: bool, motivos: list[str])
    """
    motivos = []
    with conn.cursor(as_dict=True) as cur:
        # Cooldown usuario 2 días
        cur.execute("""
            SELECT TOP 1 FechaAprobado FROM HUB_SolicitudVales
            WHERE IdSolicitante = %s AND Estatus = 'GENERADO'
            ORDER BY FechaAprobado DESC
        """, (id_usuario,))
        row = cur.fetchone()
        if row and row.get("FechaAprobado"):
            dias = (datetime.now() - row["FechaAprobado"]).days
            if dias < 2:
                motivos.append(f"Cooldown usuario: último vale hace {dias} día(s) (requiere 2)")

        # Cooldown vehículo 2 días
        cur.execute("""
            SELECT TOP 1 FechaAprobado FROM HUB_SolicitudVales
            WHERE IdAutomovil = %s AND Estatus = 'GENERADO'
            ORDER BY FechaAprobado DESC
        """, (id_automovil,))
        row = cur.fetchone()
        if row and row.get("FechaAprobado"):
            dias = (datetime.now() - row["FechaAprobado"]).days
            if dias < 2:
                motivos.append(f"Cooldown vehículo: último vale hace {dias} día(s) (requiere 2)")

        # Máx 4 vales/semana por usuario
        cur.execute("""
            SELECT COUNT(*) AS Total FROM HUB_SolicitudVales
            WHERE IdSolicitante = %s AND Estatus IN ('APROBADO', 'GENERADO')
              AND FechaSolicitud > DATEADD(DAY, -7, GETDATE())
        """, (id_usuario,))
        total_u = (cur.fetchone() or {}).get("Total", 0)
        if total_u >= 4:
            motivos.append(f"Límite semanal usuario: {total_u}/4 vales esta semana")

        # Máx 4 vales/semana por vehículo
        cur.execute("""
            SELECT COUNT(*) AS Total FROM HUB_SolicitudVales
            WHERE IdAutomovil = %s AND Estatus IN ('APROBADO', 'GENERADO')
              AND FechaSolicitud > DATEADD(DAY, -7, GETDATE())
        """, (id_automovil,))
        total_a = (cur.fetchone() or {}).get("Total", 0)
        if total_a >= 4:
            motivos.append(f"Límite semanal vehículo: {total_a}/4 vales esta semana")

    return (len(motivos) == 0, motivos)

class ValeCreate(BaseModel):
    id_local: str
    id_automovil: int
    id_cliente: Optional[str] = None
    descripcion: str
    notas: Optional[str] = ""
    kilometros: Optional[int] = None

@router.get("")
def get_vales(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT TOP 100 s.Id, s.IdAutomovil, s.FechaSolicitud, s.Estatus, s.Descripcion,
                   a.MarcaModelo AS Vehiculo, c.Cliente AS Empresa,
                   s.Kilometros, s.MontoUnit, s.Cantidad,
                   (ISNULL(s.Cantidad, 1) * ISNULL(s.MontoUnit, 500)) AS MontoTotal,
                   s.Placa, s.Notas, s.CodigoQR, s.UrlQR,
                   u.Nombre AS Solicitante
            FROM HUB_SolicitudVales s
            LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
            LEFT JOIN clientes c ON s.IdCliente = c.IdCliente
            LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
            WHERE s.IdSolicitante = %s
            ORDER BY s.FechaSolicitud DESC
        """, (user["id"],))
        return cur.fetchall()

@router.get("/{id_vale}")
def get_vale(id_vale: int, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT s.Id, s.FechaSolicitud, s.Estatus, s.Descripcion,
                   a.MarcaModelo AS Vehiculo, c.Cliente AS Empresa,
                   s.Kilometros, s.MontoUnit, s.Cantidad, s.Placa, s.Notas,
                   s.CodigoQR, s.UrlQR, s.QrImage,
                   s.MotivoRechazo, s.FechaRechazo, s.RechazadoPor,
                   u.Nombre AS Solicitante, ap.Nombre AS Aprobador,
                   s.FechaAprobado
            FROM HUB_SolicitudVales s
            LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
            LEFT JOIN clientes c ON s.IdCliente = c.IdCliente
            LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
            LEFT JOIN HUB_Users ap ON s.IdAprobador = ap.Id
            WHERE s.Id = %s AND s.IdSolicitante = %s
        """, (id_vale, user["id"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Vale no encontrado")
        # Convert QrImage to base64 if present
        if row.get("QrImage"):
            import base64
            row["QrImageBase64"] = base64.b64encode(row["QrImage"]).decode("utf-8")
            del row["QrImage"]
        return row

@router.post("")
def crear_vale(req: ValeCreate, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        # Validate: max 1 vale per vehicle per day (PENDIENTE, APROBADO, or GENERADO)
        cur.execute("""
            SELECT COUNT(*) FROM HUB_SolicitudVales
            WHERE IdAutomovil = %s
              AND CAST(FechaSolicitud AS DATE) = CAST(GETDATE() AS DATE)
              AND Estatus IN ('PENDIENTE', 'APROBADO', 'GENERADO')
        """, (req.id_automovil,))
        if cur.fetchone()[0] > 0:
            return {"success": False, "status": "duplicate", "message": "Este automóvil ya tiene un vale registrado hoy. Solo se permite un vale por vehículo por día."}

        # Validate: verificar saldo disponible en OxxoGas (sin revelar cantidad)
        cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'govale_saldo'")
        saldo_row = cur.fetchone()
        try:
            saldo = float(saldo_row[0]) if saldo_row and saldo_row[0] else 0.0
        except (ValueError, TypeError):
            saldo = 0.0
        if saldo < 500:
            return {"success": False, "status": "sin_saldo", "message": "No hay saldo disponible en OxxoGas para generar vales. Contacta al administrador."}

        # Validate km >= current (from RegistroKilometros OR last vale)
        if req.kilometros is not None:
            # Check last km from RegistroKilometros
            cur.execute("""
                SELECT ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                               WHERE k.IdAutomovil = %s
                               ORDER BY k.FechaHora DESC, k.Id DESC), 0)
            """, (req.id_automovil,))
            kms_registro = cur.fetchone()[0]
            # Check last km from previous vales
            cur.execute("""
                SELECT ISNULL((SELECT TOP 1 Kilometros FROM HUB_SolicitudVales
                               WHERE IdAutomovil = %s AND Kilometros IS NOT NULL AND Kilometros > 0
                               ORDER BY FechaSolicitud DESC, Id DESC), 0)
            """, (req.id_automovil,))
            kms_vale = cur.fetchone()[0]
            kms_actuales = max(kms_registro, kms_vale)
            if kms_actuales > 0 and req.kilometros < kms_actuales:
                return {"success": False, "status": "km_invalid", "message": f"Los kilómetros no pueden ser menores a los últimos registrados ({kms_actuales} km)."}

        # Get placa
        cur.execute("SELECT Placas FROM HUB_Automoviles WHERE Id = %s", (req.id_automovil,))
        auto_row = cur.fetchone()
        placa = auto_row[0] if auto_row else ''

        # Validate description min 5 chars
        if not req.descripcion or len(req.descripcion.strip()) < 5:
            return {"success": False, "status": "desc_short", "message": "La descripción debe tener al menos 5 caracteres."}

        cur.execute("""
            INSERT INTO HUB_SolicitudVales
            (IdSolicitante, IdAutomovil, IdCliente, Placa, Descripcion, Cantidad, MontoUnit, Notas, Kilometros)
            VALUES (%s, %s, %s, %s, %s, 1, 500.00, %s, %s);
            SELECT SCOPE_IDENTITY();
        """, (user["id"], req.id_automovil, req.id_cliente,
              placa.strip(), req.descripcion.strip(), req.notas or '',
              req.kilometros))
        new_id = int(cur.fetchone()[0])
        conn.commit()

        # Evaluar criterios para auto-aprobación
        aprobado, motivos = _evaluar_criterios(conn, user["id"], req.id_automovil)
        if aprobado:
            cur.execute("""
                UPDATE HUB_SolicitudVales
                SET Estatus = 'APROBADO', IdAprobador = %s, FechaAprobado = GETDATE()
                WHERE Id = %s
            """, (user["id"], new_id))
            conn.commit()

        folio = f"VL-{new_id:05d}"
        estatus = "APROBADO" if aprobado else "PENDIENTE"
        try:
            from routers.legends import _registrar_metrica
            _registrar_metrica(user["id"], "vale_generado", referencia_id=new_id)
        except Exception:
            pass
        try:
            from routers.push import send_push_notification
            send_push_notification(user["id"], "📋 Vale generado", f"Vale {folio} registrado — -2 pts", "/vales")
        except Exception:
            pass
        return {"success": True, "folio": folio, "id_server": new_id, "estatus": estatus, "motivos": motivos}
