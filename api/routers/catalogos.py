from fastapi import APIRouter, Depends
from auth import require_user
from db import get_connection

router = APIRouter()

@router.get("/vehiculos")
def get_vehiculos(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        user_id = user["id"]
        cur.execute("""
            SELECT a.Id, a.MarcaModelo, a.Placas, a.IdUsuarioAsignado,
                   ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                           WHERE k.IdAutomovil = a.Id
                           ORDER BY k.FechaHora DESC, k.Id DESC), 0) AS KilometrosActuales
            FROM HUB_Automoviles a
            WHERE a.IdUsuarioAsignado = %s OR a.IdUsuarioAsignado IS NULL
            ORDER BY a.MarcaModelo ASC
        """, (user_id,))
        return cur.fetchall()

@router.get("/vehiculos/todos")
def get_all_vehiculos(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT a.Id, a.MarcaModelo, a.Placas, a.IdUsuarioAsignado,
                   ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                           WHERE k.IdAutomovil = a.Id
                           ORDER BY k.FechaHora DESC, k.Id DESC), 0) AS KilometrosActuales
            FROM HUB_Automoviles a
            ORDER BY a.MarcaModelo ASC
        """)
        return cur.fetchall()

@router.get("/vehiculo-asignado")
def get_vehiculo_asignado(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT a.Id, a.MarcaModelo, a.Placas,
                   ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                           WHERE k.IdAutomovil = a.Id
                           ORDER BY k.FechaHora DESC, k.Id DESC), 0) AS KilometrosActuales
            FROM HUB_Automoviles a
            WHERE a.IdUsuarioAsignado = %s
        """, (user["id"],))
        row = cur.fetchone()
        return row if row else None

@router.get("/clientes")
def get_clientes(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT IdCliente, Cliente FROM clientes ORDER BY Cliente")
        return cur.fetchall()

@router.get("/clientes/{id_cliente}/contactos")
def get_contactos_cliente(id_cliente: str, user: dict = Depends(require_user)):
    """Contactos del cliente: catálogo propio + contactos de cotizaciones reales."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT DISTINCT Contacto FROM (
                SELECT Contacto FROM HUB_ContactosClientes
                WHERE IdCliente = %s AND Contacto IS NOT NULL AND LTRIM(RTRIM(Contacto)) <> ''
                UNION
                SELECT Contacto FROM IndiceMateriales
                WHERE IdCliente = %s AND Contacto IS NOT NULL AND LTRIM(RTRIM(Contacto)) <> ''
            ) c
            ORDER BY Contacto ASC
        """, (id_cliente.strip().upper(), id_cliente.strip().upper()))
        return cur.fetchall()

@router.get("/usuarios")
def get_usuarios(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT Id, Nombre FROM HUB_Users WHERE Activo = 1 ORDER BY Nombre")
        return cur.fetchall()

@router.get("/cliente-nombre/{id_cliente}")
def get_cliente_nombre(id_cliente: str, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente.strip().upper(),))
        row = cur.fetchone()
        return {"nombre": row["Cliente"] if row else id_cliente}

@router.get("/estaciones")
def get_estaciones(user: dict = Depends(require_user)):
    """Historial global de estaciones (catálogo compartido)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT Estacion FROM HUB_EstacionesTickets
            WHERE Estacion IS NOT NULL AND LTRIM(RTRIM(Estacion)) <> ''
            ORDER BY Estacion ASC
        """)
        return cur.fetchall()
