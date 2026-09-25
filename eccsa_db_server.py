import pymssql
import datetime
import secrets
import uuid
from config_db import load_db_config

DB_CONFIG = load_db_config()

def get_connection():
    return pymssql.connect(**DB_CONFIG)

def get_authorized_users():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM MAC ORDER BY Nombre ASC")
                rows = cur.fetchall()
                users = []
                for r in rows:
                    users.append({
                        'mac': r[0].strip() if r[0] else "",
                        'nombre': r[1].strip() if r[1] else "",
                        'hostname': r[2].strip() if r[2] else "",
                        'admin': bool(r[3]),
                        'supervisor': bool(r[4]),
                        'telefono': r[5].strip() if r[5] else "",
                        'cotizaciones': bool(r[6]),
                        'virtuales': bool(r[7]),
                        'autos': bool(r[8]),
                        'horas': bool(r[9]),
                        'inventario': bool(r[10]),
                        'reportes': bool(r[11])
                    })
                return users
    except Exception as e:
        print(f"Error fetching authorized users: {e}")
        return []

def get_user_by_mac(mac):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # LTRIM/RTRIM handles database CHAR padding
                cur.execute("SELECT * FROM MAC WHERE LTRIM(RTRIM(MAC)) = %s", (mac.strip(),))
                r = cur.fetchone()
                if r:
                    return {
                        'mac': r[0].strip() if r[0] else "",
                        'nombre': r[1].strip() if r[1] else "",
                        'hostname': r[2].strip() if r[2] else "",
                        'admin': bool(r[3]),
                        'supervisor': bool(r[4]),
                        'telefono': r[5].strip() if r[5] else "",
                        'cotizaciones': bool(r[6]),
                        'virtuales': bool(r[7]),
                        'autos': bool(r[8]),
                        'horas': bool(r[9]),
                        'inventario': bool(r[10]),
                        'reportes': bool(r[11])
                    }
                return None
    except Exception as e:
        print(f"Error fetching user by MAC: {e}")
        return None

def get_clients():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdCliente, Cliente FROM clientes ORDER BY Cliente ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching clients: {e}")
        return []

def get_client_by_id(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente,))
                row = cur.fetchone()
                return row[0] if row else ""
    except Exception as e:
        print(f"Error fetching client by ID: {e}")
        return ""

def get_contacts_for_client(id_cliente):
    """Contactos del cliente: catálogo propio (HUB_ContactosClientes) + cotizaciones reales.
    Ya NO se escribe en IndiceMateriales desde Field (evitaba CM fantasma)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT Contacto FROM (
                        SELECT Contacto FROM HUB_ContactosClientes
                        WHERE IdCliente = %s AND Contacto IS NOT NULL AND Contacto <> ''
                        UNION
                        SELECT Contacto FROM IndiceMateriales
                        WHERE IdCliente = %s AND Contacto IS NOT NULL AND Contacto <> ''
                    ) c
                    ORDER BY Contacto ASC
                """, (id_cliente, id_cliente))
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error fetching contacts: {e}")
        return []

def get_quotations_index():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT TOP 999 Folio, IdCliente, Contacto, Fecha, Descripcion, Autor, Color FROM IndiceMateriales ORDER BY Folio DESC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching quotations: {e}")
        return []

def get_quotation_by_folio(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Folio, IdCliente, Contacto, Fecha, Descripcion, Autor, Color FROM IndiceMateriales WHERE Folio = %s", (folio,))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching quotation {folio}: {e}")
        return None

def insert_quotation(id_cliente, contacto, fecha, descripcion, autor, color=0):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                    
                cur.execute(
                    "INSERT INTO IndiceMateriales (IdCliente, Contacto, Fecha, Descripcion, Autor, Color) VALUES (%s, %s, %s, %s, %s, %s); SELECT SCOPE_IDENTITY()",
                    (id_cliente, contacto, fecha_str, descripcion, autor, color)
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                return int(new_id)
    except Exception as e:
        print(f"Error inserting quotation: {e}")
        return None

def update_quotation(folio, id_cliente, contacto, fecha, descripcion, autor):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                    
                cur.execute(
                    "UPDATE IndiceMateriales SET IdCliente = %s, Contacto = %s, Fecha = %s, Descripcion = %s, Autor = %s WHERE Folio = %s",
                    (id_cliente, contacto, fecha_str, descripcion, autor, folio)
                )
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating quotation {folio}: {e}")
        return False

def delete_quotation(folio):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM partidas WHERE Folio = %s", (folio,))
                cur.execute("DELETE FROM IndiceMateriales WHERE Folio = %s", (folio,))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error deleting quotation {folio}: {e}")
        return False

def get_quotation_items(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Folio, Partida, Cantidad, Descripcion, PrecioCompraUnitario, Factor, Proveedor, TiempoEntregaDias, Flete FROM partidas WHERE Folio = %s ORDER BY Partida ASC", (folio,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching items for folio {folio}: {e}")
        return []

def insert_item(folio, partida, cantidad, descripcion, precio_compra, factor, proveedor, tiempo_entrega, flete):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO partidas (Folio, Partida, Cantidad, Descripcion, PrecioCompraUnitario, Factor, Proveedor, TiempoEntregaDias, Flete) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (folio, partida, cantidad, descripcion, precio_compra, factor, proveedor, tiempo_entrega, flete)
                )
                conn.commit()
                return True
    except Exception as e:
        print(f"Error inserting item for folio {folio}: {e}")
        return False

def delete_item(folio, partida):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM partidas WHERE Folio = %s AND Partida = %s", (folio, partida))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error deleting item {partida} for folio {folio}: {e}")
        return False

def authenticate_hub_user(email, password):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Email, Nombre, Password, Activo, 
                           AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso,
                           AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras, AccesoOxxoGas, AccesoValesOxxoGas, AccesoRegistroTicketOxxoGas, AccesoNominas
                    FROM HUB_Users 
                    WHERE LTRIM(RTRIM(Email)) = %s
                """, (email.strip().lower(),))
                r = cur.fetchone()
                if r:
                    if r['Activo'] and r['Password'] == password:
                        return {
                            'id': r['Id'],
                            'email': r['Email'],
                            'nombre': r['Nombre'],
                            'activo': r['Activo'],
                            'acceso_cotizaciones': bool(r['AccesoCotizaciones']),
                            'acceso_vm': bool(r['AccesoVM']),
                            'acceso_configuracion': bool(r['AccesoConfiguracion']),
                            'acceso_usuarios': bool(r['AccesoUsuarios']),
                            'acceso_reportes': bool(r['AccesoReportes']),
                            'acceso_registro_reportes': bool(r['AccesoRegistroReportes']),
                            'acceso_cotizaciones_reportes': bool(r.get('AccesoCotizacionesReportes', False)),
                            'acceso_clientes': bool(r.get('AccesoClientes', True)),
                            'acceso_registro_kilometros': bool(r.get('AccesoRegistroKilometros', True)),
                            'acceso_automoviles': bool(r.get('AccesoAutomoviles', True)),
                            'acceso_configurar_correo': bool(r.get('AccesoConfigurarCorreo', True)),
                            'acceso_config_ai': bool(r.get('AccesoConfigAI', True)),
                            'notificaciones': bool(r.get('Notificaciones', False)),
                            'acceso_vacaciones': bool(r.get('AccesoVacaciones', False)),
                            'acceso_mis_vacaciones': bool(r.get('AccesoMisVacaciones', False)),
                            'acceso_horas_extras': bool(r.get('AccesoHorasExtras', False)),
                            'acceso_mis_horas_extras': bool(r.get('AccesoMisHorasExtras', False)),
                            'acceso_oxxogas': bool(r.get('AccesoOxxoGas', False)),
                            'acceso_vales_oxxogas': bool(r.get('AccesoValesOxxoGas', False)),
                            'acceso_registro_ticket_oxxogas': bool(r.get('AccesoRegistroTicketOxxoGas', False)),
                            'acceso_nominas': bool(r.get('AccesoNominas', False)),
                            'fecha_ingreso': r.get('FechaIngreso')
                        }
                return None
    except Exception as e:
        print(f"Error authenticating HUB user: {e}")
        return None

def get_resumen_cotizaciones():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 999 
                        Folio, IdCliente, Cliente, Contacto, Fecha, DescripcionGeneral, 
                        Autor, Estatus, SumaPartidas, FleteCotizacion, Subtotal, IVA, TotalFinal 
                    FROM vw_ResumenCotizaciones 
                    ORDER BY Folio DESC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching vw_ResumenCotizaciones: {e}")
        return []

def get_user_phone(username):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT TOP 1 Telefono FROM MAC WHERE Nombre = %s ORDER BY Telefono DESC", (username,))
                r = cur.fetchone()
                if r and r['Telefono']:
                    return r['Telefono'].strip()
                return "8183589075" # Corporate fallback phone
    except Exception:
        return "8183589075"

def get_quotation_full_details(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IM.Folio, IM.IdCliente, IM.Contacto, IM.Fecha, IM.Descripcion, IM.Nota, IM.Autor, IM.Color,
                           C.Cliente as ClienteNombre, C.CondicionesPagoDias
                    FROM IndiceMateriales IM
                    LEFT JOIN clientes C ON IM.IdCliente = C.IdCliente
                    WHERE IM.Folio = %s
                """, (folio,))
                details = cur.fetchone()
                if details:
                    details['Telefono'] = get_user_phone(details['Autor'])
                    return details
                return None
    except Exception as e:
        print(f"Error fetching full details for folio {folio}: {e}")
        return None

def get_all_virtual_machines_software():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT id, Nombre, Version, MaqVirtual, Descripcion FROM MaqVirtualSoft ORDER BY MaqVirtual ASC, Nombre ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching MaqVirtualSoft: {e}")
        return []

def delete_quotation(folio):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Delete items in Partidas first
                cur.execute("DELETE FROM Partidas WHERE Folio = %s", (folio,))
                # Delete header in IndiceMateriales
                cur.execute("DELETE FROM IndiceMateriales WHERE Folio = %s", (folio,))
                conn.commit()
                log_user_activity("Cotización Materiales", f"Eliminó cotización de materiales con folio {folio}")
                return True
    except Exception as e:
        print(f"Error deleting quotation {folio}: {e}")
        return False

def get_client_name(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente.strip().upper(),))
                row = cur.fetchone()
                return row['Cliente'] if row else None
    except Exception as e:
        print(f"Error looking up client {id_cliente}: {e}")
        return None

def get_contacts_by_client(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT DISTINCT Contacto 
                    FROM IndiceMateriales 
                    WHERE IdCliente = %s AND Contacto IS NOT NULL AND TRIM(Contacto) <> ''
                    ORDER BY Contacto ASC
                """, (id_cliente.strip().upper(),))
                rows = cur.fetchall()
                return [r['Contacto'] for r in rows]
    except Exception as e:
        print(f"Error looking up contacts for client {id_cliente}: {e}")
        return []

def create_quotation(id_cliente, contacto, descripcion, autor):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO IndiceMateriales (IdCliente, Contacto, Fecha, Descripcion, Autor, Color)
                    VALUES (%s, %s, GETDATE(), %s, %s, 0)
                """, (id_cliente.strip().upper(), contacto.strip(), descripcion.strip(), autor))
                conn.commit()
                
                # Retrieve the newly generated Folio
                cur.execute("SELECT @@IDENTITY")
                row = cur.fetchone()
                new_folio = int(row[0]) if row and row[0] else None
                if new_folio:
                    log_user_activity("Cotización Materiales", f"Creó cotización de materiales con folio CM{new_folio:05d}")
                return new_folio if new_folio else True
    except Exception as e:
        print(f"Error creating quotation: {e}")
        return False

def get_partidas_by_folio(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Partida, Cantidad, Descripcion, PrecioCompraUnitario, Factor, Flete, TiempoEntregaDias, Dolar, Proveedor
                    FROM Partidas
                    WHERE Folio = %s
                    ORDER BY Partida ASC
                """, (folio,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching partidas for folio {folio}: {e}")
        return []

def add_partida(folio, cantidad, descripcion, precio_compra, factor, proveedor, tiempo_entrega_dias, dolar, flete):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Calculate next partida number automatically
                cur.execute("SELECT ISNULL(MAX(Partida), 0) + 1 FROM Partidas WHERE Folio = %s", (folio,))
                row = cur.fetchone()
                next_partida = int(row[0]) if row else 1
                
                # 2. Insert new item row
                cur.execute("""
                    INSERT INTO Partidas (Folio, Partida, Cantidad, Descripcion, PrecioCompraUnitario, Factor, Proveedor, TiempoEntregaDias, Dolar, Flete)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (folio, next_partida, cantidad, descripcion.strip(), precio_compra, factor, proveedor.strip(), tiempo_entrega_dias, dolar, flete))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error adding new partida for folio {folio}: {e}")
        return False

def get_quotation_by_folio(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Folio, IdCliente, Contacto, Fecha, Descripcion, Color, Autor, Nota
                    FROM IndiceMateriales
                    WHERE Folio = %s
                """, (folio,))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching quotation {folio}: {e}")
        return None

def update_quotation(folio, id_cliente, contacto, descripcion, status_color):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE IndiceMateriales
                    SET IdCliente = %s,
                        Contacto = %s,
                        Descripcion = %s,
                        Color = %s
                    WHERE Folio = %s
                """, (id_cliente.strip().upper(), contacto.strip(), descripcion.strip(), status_color, folio))
                conn.commit()
                log_user_activity("Cotización Materiales", f"Modificó cotización de materiales con folio {folio}")
                return True
    except Exception as e:
        print(f"Error updating quotation {folio}: {e}")
        return False

def update_quotation_note(folio, nota):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE IndiceMateriales
                    SET Nota = %s
                    WHERE Folio = %s
                """, (nota.strip(), folio))
                conn.commit()
                log_user_activity("Cotización Materiales", f"Actualizó notas de cotización de materiales con folio {folio}")
                return True
    except Exception as e:
        print(f"Error updating note for folio {folio}: {e}")
        return False

def get_all_clients():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT IdCliente, Cliente, CondicionesPagoDias FROM clientes ORDER BY IdCliente ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching all clients: {e}")
        return []

def add_client(id_cliente, cliente, condiciones_pago):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO clientes (IdCliente, Cliente, CondicionesPagoDias)
                    VALUES (%s, %s, %s)
                """, (id_cliente.strip().upper(), cliente.strip(), condiciones_pago))
                conn.commit()
                log_user_activity("Clientes", f"Registró nuevo cliente: {cliente.strip()} ({id_cliente.strip().upper()})")
                return True
    except Exception as e:
        print(f"Error adding client: {e}")
        return False

def update_client(id_cliente, cliente, condiciones_pago):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clientes
                    SET Cliente = %s,
                        CondicionesPagoDias = %s
                    WHERE IdCliente = %s
                """, (cliente.strip(), condiciones_pago, id_cliente.strip().upper()))
                conn.commit()
                log_user_activity("Clientes", f"Modificó detalles de cliente: {cliente.strip()} ({id_cliente.strip().upper()})")
                return True
    except Exception as e:
        print(f"Error updating client: {e}")
        return False

def delete_client(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente.strip().upper(),))
                row = cur.fetchone()
                c_name = row[0] if row else id_cliente
                cur.execute("DELETE FROM clientes WHERE IdCliente = %s", (id_cliente.strip().upper(),))
                conn.commit()
                log_user_activity("Clientes", f"Eliminó cliente: {c_name.strip()} ({id_cliente.strip().upper()})")
                return True
    except Exception as e:
        print(f"Error deleting client: {e}")
        return False

def get_user_email_by_name(name):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Email FROM HUB_Users WHERE Nombre = %s", (name,))
                r = cur.fetchone()
                if r:
                    return r['Email'].strip()
                return None
    except Exception as e:
        print(f"Error getting email by name {name}: {e}")
        return None

def update_user_password(email, new_password):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Users
                    SET Password = %s
                    WHERE Email = %s
                """, (new_password, email.strip().lower()))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating password for {email}: {e}")
        return False

def get_hub_user_foto(email):
    """Devuelve la foto de perfil (base64 data-URI) del usuario por su correo, o None si no tiene."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Foto FROM HUB_Users WHERE Email = %s", (email.strip().lower(),))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        print(f"get_hub_user_foto error: {e}")
        return None


def update_hub_user_foto(email, foto):
    """Guarda/cambia la foto de perfil del usuario (base64 data-URI). Pasar foto=None para eliminarla."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Users
                    SET Foto = %s
                    WHERE Email = %s
                """, (foto, email.strip().lower()))
                conn.commit()
                log_user_activity("Administración de Usuarios", f"Actualizó la foto de perfil de {email}")
                return True
    except Exception as e:
        print(f"update_hub_user_foto error: {e}")
        return False


def get_all_hub_users():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Id, Email, Nombre, Password, Activo, AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso, AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras, AccesoOxxoGas, AccesoValesOxxoGas, AccesoRegistroTicketOxxoGas, AccesoEdicionBD, AccesoNominas, AccesoInventario, AccesoCalculo, AccesoProveedores, AccesoOC, AccesoTelegram, AccesoAppConfig, AccesoSolicitarVales, AccesoAdminVales, AccesoConfigOxxogas, AccesoDeteccionRed, AccesoPdfConfig FROM HUB_Users ORDER BY Nombre ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching all HUB users: {e}")
        return []

def add_hub_user(email, nombre, password, activo, acceso_cotizaciones, acceso_vm, acceso_configuracion, acceso_usuarios, acceso_reportes, acceso_registro_reportes, acceso_cotizaciones_reportes, acceso_clientes, acceso_registro_kilometros, acceso_automoviles, acceso_vacaciones, fecha_ingreso, acceso_configurar_correo=False, acceso_config_ai=False, notificaciones=False, acceso_mis_vacaciones=False, acceso_horas_extras=False, acceso_mis_horas_extras=False, acceso_oxxogas=False, acceso_vales_oxxogas=False, acceso_registro_ticket_oxxogas=False, acceso_edicion_bd=False, acceso_nominas=False, acceso_inventario=False, acceso_calculos=False, acceso_proveedores=False, acceso_oc=False, acceso_telegram=False, acceso_app_config=False, acceso_solicitar_vales=False, acceso_admin_vales=False, acceso_config_oxxogas=False, acceso_deteccion_red=False, acceso_pdf_config=False, curp_rfc=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Users (Email, Nombre, Password, Activo, AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso, AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras, AccesoOxxoGas, AccesoValesOxxoGas, AccesoRegistroTicketOxxoGas, AccesoEdicionBD, AccesoNominas, AccesoInventario, AccesoCalculo, AccesoProveedores, AccesoOC, AccesoTelegram, AccesoAppConfig, AccesoSolicitarVales, AccesoAdminVales, AccesoConfigOxxogas, AccesoDeteccionRed, AccesoPdfConfig, CurpRfc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (email.strip().lower(), nombre.strip(), password.strip(), int(activo), int(acceso_cotizaciones), int(acceso_vm), int(acceso_configuracion), int(acceso_usuarios), int(acceso_reportes), int(acceso_registro_reportes), int(acceso_cotizaciones_reportes), int(acceso_clientes), int(acceso_registro_kilometros), int(acceso_automoviles), int(acceso_vacaciones), fecha_ingreso, int(acceso_configurar_correo), int(acceso_config_ai), int(notificaciones), int(acceso_mis_vacaciones), int(acceso_horas_extras), int(acceso_mis_horas_extras), int(acceso_oxxogas), int(acceso_vales_oxxogas), int(acceso_registro_ticket_oxxogas), int(acceso_edicion_bd), int(acceso_nominas), int(acceso_inventario), int(acceso_calculos), int(acceso_proveedores), int(acceso_oc), int(acceso_telegram), int(acceso_app_config), int(acceso_solicitar_vales), int(acceso_admin_vales), int(acceso_config_oxxogas), int(acceso_deteccion_red), int(acceso_pdf_config), (curp_rfc or '').strip() or None))
                conn.commit()
                log_user_activity("Administración de Usuarios", f"Registró nuevo usuario: {nombre} ({email})")
                return True
    except Exception as e:
        print(f"Error adding HUB user: {e}")
        return False

def update_hub_user(user_id, email, nombre, password, activo, acceso_cotizaciones, acceso_vm, acceso_configuracion, acceso_usuarios, acceso_reportes, acceso_registro_reportes, acceso_cotizaciones_reportes, acceso_clientes, acceso_registro_kilometros, acceso_automoviles, acceso_vacaciones, fecha_ingreso, acceso_configurar_correo=False, acceso_config_ai=False, notificaciones=False, acceso_mis_vacaciones=False, acceso_horas_extras=False, acceso_mis_horas_extras=False, acceso_oxxogas=False, acceso_vales_oxxogas=False, acceso_registro_ticket_oxxogas=False, acceso_edicion_bd=False, acceso_nominas=False, acceso_inventario=False, acceso_calculos=False, acceso_proveedores=False, acceso_oc=False, acceso_telegram=False, acceso_app_config=False, acceso_solicitar_vales=False, acceso_admin_vales=False, acceso_config_oxxogas=False, acceso_deteccion_red=False, acceso_pdf_config=False, curp_rfc=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Users
                    SET Email = %s,
                        Nombre = %s,
                        Password = %s,
                        Activo = %s,
                        AccesoCotizaciones = %s,
                        AccesoVM = %s,
                        AccesoConfiguracion = %s,
                        AccesoUsuarios = %s,
                        AccesoReportes = %s,
                        AccesoRegistroReportes = %s,
                        AccesoCotizacionesReportes = %s,
                        AccesoClientes = %s,
                        AccesoRegistroKilometros = %s,
                        AccesoAutomoviles = %s,
                        AccesoVacaciones = %s,
                        FechaIngreso = %s,
                        AccesoConfigurarCorreo = %s,
                        AccesoConfigAI = %s,
                        Notificaciones = %s,
                        AccesoMisVacaciones = %s,
                        AccesoHorasExtras = %s,
                        AccesoMisHorasExtras = %s,
                        AccesoOxxoGas = %s,
                        AccesoValesOxxoGas = %s,
                        AccesoRegistroTicketOxxoGas = %s,
                        AccesoEdicionBD = %s,
                        AccesoNominas = %s,
                        AccesoInventario = %s,
                        AccesoCalculo = %s,
                        AccesoProveedores = %s,
                        AccesoOC = %s,
                        AccesoTelegram = %s,
                        AccesoAppConfig = %s,
                        AccesoSolicitarVales = %s,
                        AccesoAdminVales = %s,
                        AccesoConfigOxxogas = %s,
                        AccesoDeteccionRed = %s,
                        AccesoPdfConfig = %s,
                        CurpRfc = %s
                    WHERE Id = %s
                """, (email.strip().lower(), nombre.strip(), password.strip(), int(activo), int(acceso_cotizaciones), int(acceso_vm), int(acceso_configuracion), int(acceso_usuarios), int(acceso_reportes), int(acceso_registro_reportes), int(acceso_cotizaciones_reportes), int(acceso_clientes), int(acceso_registro_kilometros), int(acceso_automoviles), int(acceso_vacaciones), fecha_ingreso, int(acceso_configurar_correo), int(acceso_config_ai), int(notificaciones), int(acceso_mis_vacaciones), int(acceso_horas_extras), int(acceso_mis_horas_extras), int(acceso_oxxogas), int(acceso_vales_oxxogas), int(acceso_registro_ticket_oxxogas), int(acceso_edicion_bd), int(acceso_nominas), int(acceso_inventario), int(acceso_calculos), int(acceso_proveedores), int(acceso_oc), int(acceso_telegram), int(acceso_app_config), int(acceso_solicitar_vales), int(acceso_admin_vales), int(acceso_config_oxxogas), int(acceso_deteccion_red), int(acceso_pdf_config), (curp_rfc or '').strip() or None, int(user_id)))
                conn.commit()
                log_user_activity("Administración de Usuarios", f"Modificó permisos/detalles del usuario: {nombre} ({email})")
                return True
    except Exception as e:
        print(f"Error updating HUB user: {e}")
        return False

def delete_hub_user(user_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(user_id),))
                row = cur.fetchone()
                user_name = row[0] if row else str(user_id)
                cur.execute("DELETE FROM HUB_Users WHERE Id = %s", (int(user_id),))
                conn.commit()
                log_user_activity("Administración de Usuarios", f"Eliminó usuario: {user_name}")
                return True
    except Exception as e:
        print(f"Error deleting HUB user: {e}")
        return False

def update_partida(folio, partida, cantidad, descripcion, precio_compra, factor, proveedor, tiempo_entrega_dias, dolar, flete):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Partidas
                    SET Cantidad = %s,
                        Descripcion = %s,
                        PrecioCompraUnitario = %s,
                        Factor = %s,
                        Proveedor = %s,
                        TiempoEntregaDias = %s,
                        Dolar = %s,
                        Flete = %s
                    WHERE Folio = %s AND Partida = %s
                """, (cantidad, descripcion.strip(), precio_compra, factor, proveedor.strip(), tiempo_entrega_dias, dolar, flete, folio, partida))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating partida {partida} for folio {folio}: {e}")
        return False

def delete_partida(folio, partida):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM Partidas WHERE Folio = %s AND Partida = %s", (folio, partida))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error deleting partida {partida} for folio {folio}: {e}")
        return False

# --- REMISIONES (desde Cotizaciones de Materiales) ---
# Documento inmutable: snapshot del encabezado de la CM + partidas elegidas.
# Solo se crea y se borra; no se edita. Espejo de eccsa_db.py.

def create_remision(folio_cm, partidas_ajustadas, creado_por):
    """Crea una remisión desde una CM. Retorna FolioRemision (str) o False."""
    try:
        quote = get_quotation_by_folio(folio_cm)
        if not quote:
            print(f"Error creating remision: cotización {folio_cm} not found.")
            return False
        if not partidas_ajustadas:
            print(f"Error creating remision: sin partidas para CM{folio_cm}.")
            return False
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ISNULL(COUNT(*), 0) FROM IndiceRemisiones WHERE FolioCotizacion = %s", (folio_cm,))
                row = cur.fetchone()
                seq = int(row[0]) + 1 if row else 1
                folio_rm = f"RM-CM{int(folio_cm):05d}-{seq:02d}"
                cur.execute("""
                    INSERT INTO IndiceRemisiones
                        (FolioRemision, FolioCotizacion, IdCliente, Contacto, Descripcion, Autor, CreadoPor, FechaCreacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, GETDATE())
                """, (
                    folio_rm, int(folio_cm),
                    (quote.get('IdCliente') or '').strip().upper(),
                    (quote.get('Contacto') or '').strip(),
                    (quote.get('Descripcion') or '').strip(),
                    (quote.get('Autor') or '').strip(),
                    (creado_por or '').strip(),
                ))
                cur.execute("SELECT @@IDENTITY")
                id_row = cur.fetchone()
                id_remision = int(id_row[0]) if id_row and id_row[0] else None
                if not id_remision:
                    conn.rollback()
                    print(f"Error creating remision for CM{folio_cm}: no IdRemision.")
                    return False
                for p in partidas_ajustadas:
                    cant = int(p.get('Cantidad', 0) or 0)
                    if cant <= 0:
                        continue
                    desc = str(p.get('Descripcion') or '').strip()
                    if not desc:
                        continue
                    cur.execute("""
                        INSERT INTO RemisionPartidas (IdRemision, Partida, Cantidad, Descripcion)
                        VALUES (%s, %s, %s, %s)
                    """, (id_remision, int(p.get('Partida', 0) or 0), cant, desc))
                conn.commit()
                log_user_activity(
                    "Cotización Materiales",
                    f"Creó remisión {folio_rm} desde cotización CM{int(folio_cm):05d}"
                )
                return folio_rm
    except Exception as e:
        print(f"Error creating remision for CM{folio_cm}: {e}")
        return False

def get_remisiones_by_folio_cm(folio_cm):
    """Lista las remisiones generadas desde una cotización de materiales (asignado + si ya está firmada)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT R.IdRemision, R.FolioRemision, R.FolioCotizacion, R.IdCliente, R.Contacto,
                           R.Descripcion, R.Autor, R.CreadoPor, R.FechaCreacion,
                           R.IdUsuarioAsignado, U.Nombre AS UsuarioAsignadoNombre,
                           CASE WHEN LTRIM(RTRIM(ISNULL(R.FirmaConformidad, ''))) = '' THEN 0 ELSE 1 END AS EstaFirmada
                    FROM IndiceRemisiones R
                    LEFT JOIN HUB_Users U ON R.IdUsuarioAsignado = U.Id
                    WHERE R.FolioCotizacion = %s
                    ORDER BY R.IdRemision DESC
                """, (folio_cm,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching remisiones for CM{folio_cm}: {e}")
        return []

def get_remision_full_details(id_remision):
    """Encabezado de la remisión + cliente, asignado y firma (para el PDF)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT R.IdRemision, R.FolioRemision, R.FolioCotizacion, R.IdCliente, R.Contacto,
                           R.Descripcion, R.Autor, R.CreadoPor, R.FechaCreacion,
                           R.IdUsuarioAsignado, U.Nombre AS UsuarioAsignadoNombre,
                           R.FirmaConformidad, R.FechaFirma,
                           C.Cliente as ClienteNombre
                    FROM IndiceRemisiones R
                    LEFT JOIN clientes C ON R.IdCliente = C.IdCliente
                    LEFT JOIN HUB_Users U ON R.IdUsuarioAsignado = U.Id
                    WHERE R.IdRemision = %s
                """, (id_remision,))
                details = cur.fetchone()
                if details:
                    details['Telefono'] = get_user_phone(details['Autor'])
                    return details
                return None
    except Exception as e:
        print(f"Error fetching remision details {id_remision}: {e}")
        return None

def save_remision_signature(id_remision, signature_base64):
    """Guarda la firma (data URL base64) en IndiceRemisiones.FirmaConformidad — patrón ReportesServicio."""
    try:
        sig = (signature_base64 or '').strip()
        if not sig:
            print(f"Error signing remision {id_remision}: firma vacía.")
            return False
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE IndiceRemisiones SET FirmaConformidad = %s, FechaFirma = GETDATE() WHERE IdRemision = %s",
                    (sig, int(id_remision)),
                )
                conn.commit()
                cur.execute("SELECT FolioRemision FROM IndiceRemisiones WHERE IdRemision = %s", (int(id_remision),))
                row = cur.fetchone()
                folio_rm = row[0] if row else str(id_remision)
                log_user_activity("Cotización Materiales", f"Firmó remisión {folio_rm}")
        try:
            import pdf_generator
            pdf_generator.invalidate_remision_pdf_cache(int(id_remision))
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error saving remision signature {id_remision}: {e}")
        return False

def assign_remision_usuario(id_remision, id_usuario):
    """Asigna (o con id_usuario=None desasigna) un usuario HUB a la remisión para firma en Field."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE IndiceRemisiones SET IdUsuarioAsignado = %s WHERE IdRemision = %s",
                    (id_usuario if id_usuario else None, int(id_remision)),
                )
                conn.commit()
                cur.execute("SELECT FolioRemision FROM IndiceRemisiones WHERE IdRemision = %s", (id_remision,))
                row = cur.fetchone()
                folio_rm = row[0] if row else str(id_remision)
                if id_usuario:
                    cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                    urow = cur.fetchone()
                    uname = urow[0] if urow else str(id_usuario)
                    log_user_activity(
                        "Cotización Materiales",
                        f"Asignó remisión {folio_rm} al usuario {uname} para firma en Field",
                    )
                else:
                    log_user_activity(
                        "Cotización Materiales",
                        f"Quitó asignación de usuario de la remisión {folio_rm}",
                    )
                return True
    except Exception as e:
        print(f"Error assigning remision {id_remision} to user {id_usuario}: {e}")
        return False

def get_remision_partidas(id_remision):
    """Partidas de una remisión (solo cantidad y descripción)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Partida, Cantidad, Descripcion
                    FROM RemisionPartidas
                    WHERE IdRemision = %s
                    ORDER BY Partida ASC
                """, (id_remision,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching remision partidas {id_remision}: {e}")
        return []

def delete_remision(id_remision):
    """Elimina una remisión (índice + partidas). Solo borrar, no editar."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT FolioRemision FROM IndiceRemisiones WHERE IdRemision = %s", (id_remision,))
                row = cur.fetchone()
                folio_rm = row[0] if row else str(id_remision)
                cur.execute("DELETE FROM IndiceRemisiones WHERE IdRemision = %s", (id_remision,))
                conn.commit()
                log_user_activity("Cotización Materiales", f"Eliminó remisión {folio_rm}")
                return True
    except Exception as e:
        print(f"Error deleting remision {id_remision}: {e}")
        return False

def clone_quotation(folio, new_autor):
    try:
        # 1. Fetch original quotation with all metadata including Nota
        original = None
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT IdCliente, Contacto, Descripcion, Color, Nota FROM IndiceMateriales WHERE Folio = %s", (folio,))
                original = cur.fetchone()
        
        if not original:
            print(f"Error cloning: original folio {folio} not found.")
            return False
            
        # 2. Fetch original items
        partidas = get_partidas_by_folio(folio)
        
        # 3. Create the copy in transaction
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Insert main quotation record
                cur.execute("""
                    INSERT INTO IndiceMateriales (IdCliente, Contacto, Fecha, Descripcion, Autor, Color, Nota)
                    VALUES (%s, %s, GETDATE(), %s, %s, %s, %s)
                """, (
                    original['IdCliente'],
                    original['Contacto'],
                    original['Descripcion'],
                    new_autor,
                    original['Color'],
                    original['Nota']
                ))
                
                # Retrieve new Folio
                cur.execute("SELECT @@IDENTITY")
                row = cur.fetchone()
                if not row or not row[0]:
                    raise Exception("Failed to retrieve new folio identity.")
                new_folio = int(row[0])
                
                # Insert items
                for p in partidas:
                    cur.execute("""
                        INSERT INTO Partidas (Folio, Partida, Cantidad, Descripcion, PrecioCompraUnitario, Factor, Proveedor, TiempoEntregaDias, Dolar, Flete)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        new_folio,
                        p['Partida'],
                        p['Cantidad'],
                        p['Descripcion'],
                        p['PrecioCompraUnitario'],
                        p['Factor'],
                        p['Proveedor'],
                        p['TiempoEntregaDias'],
                        p['Dolar'],
                        p['Flete']
                    ))
                
                conn.commit()
                return new_folio
    except Exception as e:
        print(f"Error in clone_quotation for folio {folio}: {e}")
        return False

def get_all_service_reports(tecnico=None):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if tecnico:
                    # Also get reports where user is an additional technician
                    cur.execute("""
                        SELECT IdReporte, Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico, 
                               DescripcionServicio, Estatus, Notas, MaquinaLinea, FechaHoraInicio, FechaHoraFin, 
                               TiempoTraslado, TiempoComida, FirmaConformidad, Cotizacion 
                        FROM ReportesServicio 
                        WHERE Tecnico = %s 
                           OR IdReporte IN (SELECT IdReporte FROM ReportesServicioTecnicos 
                                            INNER JOIN HUB_Users u ON u.Id = ReportesServicioTecnicos.IdUsuario 
                                            WHERE u.Nombre = %s)
                        ORDER BY Fecha DESC, IdReporte DESC
                    """, (tecnico.strip(), tecnico.strip()))
                else:
                    cur.execute("""
                        SELECT TOP 500 IdReporte, Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico, 
                               DescripcionServicio, Estatus, Notas, MaquinaLinea, FechaHoraInicio, FechaHoraFin, 
                               TiempoTraslado, TiempoComida, FirmaConformidad, Cotizacion 
                        FROM ReportesServicio 
                        ORDER BY Fecha DESC, IdReporte DESC
                    """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching all service reports: {e}")
        return []

def get_service_report_by_folio(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdReporte, Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico, 
                           DescripcionServicio, Estatus, Notas, MaquinaLinea, FechaHoraInicio, FechaHoraFin, 
                           TiempoTraslado, TiempoComida, FirmaConformidad, Cotizacion 
                    FROM ReportesServicio 
                    WHERE Folio = %s
                """, (folio.strip(),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching service report by folio: {e}")
        return None

def create_service_report(cliente, contacto, correo_contacto, fecha, tecnico, descripcion, notas, fecha_inicio, fecha_fin, tiempo_traslado, tiempo_comida, maquina_linea=None, tecnicos_adicionales=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Automatically calculate next Folio (e.g. RS-00001)
                cur.execute("SELECT ISNULL(MAX(IdReporte), 0) + 1 FROM ReportesServicio")
                row = cur.fetchone()
                next_id = int(row[0]) if row else 1
                folio = f"RS-{str(next_id).zfill(5)}"
                
                # 2. Convert date if needed
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                    
                cur.execute("""
                    INSERT INTO ReportesServicio (
                        Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico, 
                        DescripcionServicio, Estatus, Notas, MaquinaLinea,
                        FechaHoraInicio, FechaHoraFin, TiempoTraslado, TiempoComida
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'Borrador', %s, %s, %s, %s, %s, %s)
                """, (
                    folio, cliente.strip(), contacto.strip() if contacto else None, correo_contacto.strip() if correo_contacto else None,
                    fecha_str, tecnico.strip(), descripcion.strip() if descripcion else None, notas.strip() if notas else None,
                    maquina_linea.strip() if maquina_linea else None,
                    fecha_inicio, fecha_fin, tiempo_traslado, 1 if tiempo_comida else 0
                ))
                conn.commit()
                cur.execute("SELECT SCOPE_IDENTITY()")
                id_reporte = cur.fetchone()
                id_reporte = int(id_reporte[0]) if id_reporte and id_reporte[0] else None
                # Save additional technicians
                if id_reporte and tecnicos_adicionales:
                    save_report_tecnicos(id_reporte, tecnicos_adicionales)
                log_user_activity("Reportes de Servicio", f"Creó reporte de servicio con folio {folio}")
                return folio
    except Exception as e:
        print(f"Error creating service report: {e}")
        return False

def update_service_report(id_reporte, cliente, contacto, correo_contacto, fecha, tecnico, descripcion, estatus, notas, fecha_inicio, fecha_fin, tiempo_traslado, tiempo_comida, maquina_linea=None, tecnicos_adicionales=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                    
                cur.execute("""
                    UPDATE ReportesServicio
                    SET Cliente = %s,
                        Contacto = %s,
                        CorreoContacto = %s,
                        Fecha = %s,
                        Tecnico = %s,
                        DescripcionServicio = %s,
                        Estatus = %s,
                        Notas = %s,
                        MaquinaLinea = %s,
                        FechaHoraInicio = %s,
                        FechaHoraFin = %s,
                        TiempoTraslado = %s,
                        TiempoComida = %s
                    WHERE IdReporte = %s
                """, (
                    cliente.strip(), contacto.strip() if contacto else None, correo_contacto.strip() if correo_contacto else None,
                    fecha_str, tecnico.strip(), descripcion.strip() if descripcion else None, estatus.strip(), notas.strip() if notas else None,
                    maquina_linea.strip() if maquina_linea else None,
                    fecha_inicio, fecha_fin, tiempo_traslado, 1 if tiempo_comida else 0, int(id_reporte)
                ))
                conn.commit()
                # Get folio
                cur.execute("SELECT Folio FROM ReportesServicio WHERE IdReporte = %s", (int(id_reporte),))
                r = cur.fetchone()
                f_str = r[0] if r else str(id_reporte)
                # Save additional technicians
                if tecnicos_adicionales is not None:
                    save_report_tecnicos(int(id_reporte), tecnicos_adicionales)
                log_user_activity("Reportes de Servicio", f"Modificó reporte de servicio con folio {f_str}")
                return True
    except Exception as e:
        print(f"Error updating service report {id_reporte}: {e}")
        return False

def save_service_report_signature(id_reporte, signature_base64):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ReportesServicio SET FirmaConformidad = %s, Estatus = 'Firmado' WHERE IdReporte = %s", (signature_base64, int(id_reporte)))
                conn.commit()
                # Get folio
                cur.execute("SELECT Folio FROM ReportesServicio WHERE IdReporte = %s", (int(id_reporte),))
                r = cur.fetchone()
                f_str = r[0] if r else str(id_reporte)
                log_user_activity("Reportes de Servicio", f"Firmó reporte de servicio con folio {f_str}")
                return True
    except Exception as e:
        print(f"Error saving signature for report {id_reporte}: {e}")
        return False

def delete_service_report(id_reporte):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Folio FROM ReportesServicio WHERE IdReporte = %s", (int(id_reporte),))
                r = cur.fetchone()
                f_str = r[0] if r else str(id_reporte)
                cur.execute("DELETE FROM ReportesServicio WHERE IdReporte = %s", (int(id_reporte),))
                conn.commit()
                log_user_activity("Registro Reportes", f"Eliminó reporte de servicio con folio {f_str}")
                return True
    except Exception as e:
        print(f"Error deleting service report {id_reporte}: {e}")
        return False


# ---------------------------------------------------------------------------
# FOTOS DE REPORTES DE SERVICIO
# ---------------------------------------------------------------------------

def save_report_fotos(id_reporte, fotos_bytes_list):
    """Guarda una lista de fotos (bytes JPEG comprimido) asociadas a un reporte.
    Cada elemento de fotos_bytes_list es un tuple (foto_bytes, orden).
    Primero elimina las fotos existentes del reporte y luego inserta las nuevas."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ReportesServicioFotos WHERE IdReporte = %s", (int(id_reporte),))
                for foto_bytes, orden in fotos_bytes_list:
                    cur.execute("""
                        INSERT INTO ReportesServicioFotos (IdReporte, FotoComprimida, Orden)
                        VALUES (%s, %s, %s)
                    """, (int(id_reporte), foto_bytes, int(orden)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error saving report fotos for {id_reporte}: {e}")
        return False


def get_report_fotos(id_reporte):
    """Retorna las fotos de un reporte ordenadas por Orden."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdFoto, IdReporte, FotoComprimida, Orden, FechaSubida
                    FROM ReportesServicioFotos
                    WHERE IdReporte = %s
                    ORDER BY Orden
                """, (int(id_reporte),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching report fotos for {id_reporte}: {e}")
        return []


def delete_report_foto(id_foto):
    """Elimina una foto individual por su ID."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ReportesServicioFotos WHERE IdFoto = %s", (int(id_foto),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error deleting report foto {id_foto}: {e}")
        return False


def delete_report_fotos(id_reporte):
    """Elimina todas las fotos de un reporte."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ReportesServicioFotos WHERE IdReporte = %s", (int(id_reporte),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error deleting report fotos for {id_reporte}: {e}")
        return False


# ---------------------------------------------------------------------------
# TECNICOS ADICIONALES POR REPORTE
# ---------------------------------------------------------------------------

def get_report_tecnicos(id_reporte):
    """Retorna los nombres de los técnicos adicionales de un reporte."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT u.Nombre
                    FROM ReportesServicioTecnicos t
                    INNER JOIN HUB_Users u ON t.IdUsuario = u.Id
                    WHERE t.IdReporte = %s
                    ORDER BY u.Nombre
                """, (int(id_reporte),))
                return [r['Nombre'] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error fetching report tecnicos for {id_reporte}: {e}")
        return []


def save_report_tecnicos(id_reporte, tecnicos_nombres):
    """Guarda la lista de técnicos adicionales (nombres) para un reporte."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ReportesServicioTecnicos WHERE IdReporte = %s", (int(id_reporte),))
                for nombre in tecnicos_nombres:
                    cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (nombre.strip(),))
                    row = cur.fetchone()
                    if row:
                        cur.execute("""
                            INSERT INTO ReportesServicioTecnicos (IdReporte, IdUsuario)
                            VALUES (%s, %s)
                        """, (int(id_reporte), int(row[0])))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error saving report tecnicos for {id_reporte}: {e}")
        return False


def get_tecnicos_texto(id_reporte):
    """Retorna string concatenado de todos los técnicos (principal + adicionales)."""
    try:
        reporte = get_service_report_by_id(id_reporte)
        if not reporte:
            return ''
        principal = reporte.get('Tecnico', '')
        adicionales = get_report_tecnicos(id_reporte)
        if adicionales:
            return f"{principal}, {', '.join(adicionales)}"
        return principal
    except Exception:
        return ''


def get_all_active_users():
    """Retorna lista de usuarios activos (Nombre) para multiselect."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Nombre FROM HUB_Users WHERE Activo = 1 ORDER BY Nombre")
                return [r['Nombre'] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error fetching active users: {e}")
        return []


def get_client_payment_days(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT CondicionesPagoDias FROM clientes WHERE IdCliente = %s", (id_cliente.strip().upper(),))
                row = cur.fetchone()
                return row['CondicionesPagoDias'] if row and row['CondicionesPagoDias'] is not None else 0
    except Exception as e:
        print(f"Error fetching client payment days: {e}")
        return 0

def log_user_activity(modulo, accion):
    try:
        import streamlit as st
        usuario = st.session_state.get('name') or st.session_state.get('username') or 'Sistema'
    except Exception:
        usuario = 'Sistema'
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_ActivityLog (Usuario, Modulo, Accion)
                    VALUES (%s, %s, %s)
                """, (usuario.strip(), modulo.strip(), accion.strip()))
                # Cleanup older than 1 month
                cur.execute("""
                    DELETE FROM HUB_ActivityLog 
                    WHERE FechaHora < DATEADD(month, -1, GETDATE())
                """)
                conn.commit()
                return True
    except Exception as e:
        print(f"Error logging activity: {e}")
        return False

def get_recent_activities(limit=50):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP %s FechaHora, Usuario, Modulo, Accion 
                    FROM HUB_ActivityLog 
                    ORDER BY FechaHora DESC
                """, (limit,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching recent activities: {e}")
        return []

# --- VEHICLES MODULE FUNCTIONS ---

def get_automoviles():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT a.Id, a.MarcaModelo, a.Placas, a.PolizaSeguro, a.IdUsuarioAsignado, a.UltimoServicioKms,
                           u.Nombre AS ConductorAsignado,
                           ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k WHERE k.IdAutomovil = a.Id ORDER BY k.FechaHora DESC, k.Id DESC), 0) AS KilometrosActuales
                    FROM HUB_Automoviles a
                    LEFT JOIN HUB_Users u ON a.IdUsuarioAsignado = u.Id
                    ORDER BY a.MarcaModelo ASC
                """)
                rows = cur.fetchall()
                for row in rows:
                    kms_diff = row['KilometrosActuales'] - row['UltimoServicioKms']
                    row['KmsDesdeServicio'] = kms_diff
                    row['RequiereServicio'] = int(kms_diff >= 9500)
                return rows
    except Exception as e:
        print(f"Error fetching automobiles: {e}")
        return []

def add_automovil(marca_modelo, placas, poliza_seguro, id_usuario_asignado):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Automoviles (MarcaModelo, Placas, PolizaSeguro, IdUsuarioAsignado, UltimoServicioKms)
                    VALUES (%s, %s, %s, %s, 0)
                """, (marca_modelo.strip(), placas.strip().upper(), poliza_seguro.strip() if poliza_seguro else None, id_usuario_asignado if id_usuario_asignado else None))
                conn.commit()
                log_user_activity("Automóviles", f"Registró vehículo: {marca_modelo.strip()} ({placas.strip().upper()})")
                return True
    except Exception as e:
        print(f"Error adding automobile: {e}")
        return False

def update_automovil(id_auto, marca_modelo, placas, poliza_seguro, id_usuario_asignado, ultimo_servicio_kms):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Automoviles
                    SET MarcaModelo = %s,
                        Placas = %s,
                        PolizaSeguro = %s,
                        IdUsuarioAsignado = %s,
                        UltimoServicioKms = %s
                    WHERE Id = %s
                """, (marca_modelo.strip(), placas.strip().upper(), poliza_seguro.strip() if poliza_seguro else None, id_usuario_asignado if id_usuario_asignado else None, int(ultimo_servicio_kms), int(id_auto)))
                conn.commit()
                log_user_activity("Automóviles", f"Actualizó datos de vehículo ID {id_auto}: {marca_modelo.strip()} ({placas.strip().upper()})")
                return True
    except Exception as e:
        print(f"Error updating automobile: {e}")
        return False

def delete_automovil(id_auto):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (id_auto,))
                row = cur.fetchone()
                car_info = f"{row[0]} ({row[1]})" if row else f"ID {id_auto}"
                
                cur.execute("DELETE FROM HUB_Automoviles WHERE Id = %s", (id_auto,))
                conn.commit()
                log_user_activity("Automóviles", f"Eliminó vehículo: {car_info}")
                return True
    except Exception as e:
        print(f"Error deleting automobile: {e}")
        return False

def register_kilometros(id_automovil, kilometros, fecha_hora, id_usuario):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Evitar duplicados: solo un registro por automóvil por día.
                cur.execute("""
                    SELECT COUNT(*) FROM HUB_RegistroKilometros
                    WHERE IdAutomovil = %s AND CAST(FechaHora AS DATE) = CAST(%s AS DATE)
                """, (int(id_automovil), fecha_hora))
                if cur.fetchone()[0] > 0:
                    return "duplicado"

                cur.execute("""
                    INSERT INTO HUB_RegistroKilometros (IdAutomovil, Kilometros, FechaHora, IdUsuario)
                    VALUES (%s, %s, %s, %s)
                """, (int(id_automovil), int(kilometros), fecha_hora, int(id_usuario)))
                conn.commit()
                
                cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (id_automovil,))
                row = cur.fetchone()
                car_info = f"{row[0]} ({row[1]})" if row else f"ID {id_automovil}"
                log_user_activity("Registro Kilómetros", f"Registró {kilometros} Km para {car_info}")
                return True
    except Exception as e:
        print(f"Error registering kilometers: {e}")
        return False

def get_kilometros_history(id_automovil):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT k.Id, k.Kilometros, k.FechaHora, u.Nombre AS Usuario
                    FROM HUB_RegistroKilometros k
                    LEFT JOIN HUB_Users u ON k.IdUsuario = u.Id
                    WHERE k.IdAutomovil = %s
                    ORDER BY k.FechaHora DESC, k.Id DESC
                """, (id_automovil,))
                rows = cur.fetchall()
                for i in range(len(rows)):
                    if i < len(rows) - 1:
                        prev = rows[i+1]
                        curr = rows[i]
                        diff_kms = curr['Kilometros'] - prev['Kilometros']
                        diff_time = curr['FechaHora'] - prev['FechaHora']
                        diff_days = diff_time.total_seconds() / 86400.0
                        
                        curr['DiferenciaKms'] = diff_kms
                        curr['DiferenciaDias'] = round(diff_days, 2)
                        curr['TasaConsumo'] = round(diff_kms / diff_days, 2) if diff_days > 0 else 0.0
                    else:
                        curr = rows[i]
                        curr['DiferenciaKms'] = 0
                        curr['DiferenciaDias'] = 0.0
                        curr['TasaConsumo'] = 0.0
                return rows
    except Exception as e:
        print(f"Error fetching kilometer history: {e}")
        return []

def get_servicios(id_automovil):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Fecha, Kilometros, Notas
                    FROM HUB_ServiciosAutomovil
                    WHERE IdAutomovil = %s
                    ORDER BY Fecha DESC, Kilometros DESC
                """, (id_automovil,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching services: {e}")
        return []

def add_servicio(id_automovil, fecha, kilometros, notas):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_ServiciosAutomovil (IdAutomovil, Fecha, Kilometros, Notas)
                    VALUES (%s, %s, %s, %s)
                """, (int(id_automovil), fecha, int(kilometros), notas.strip() if notas else None))
                
                cur.execute("""
                    UPDATE HUB_Automoviles
                    SET UltimoServicioKms = %s
                    WHERE Id = %s AND UltimoServicioKms < %s
                """, (int(kilometros), int(id_automovil), int(kilometros)))
                
                conn.commit()
                
                cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (id_automovil,))
                row = cur.fetchone()
                car_info = f"{row[0]} ({row[1]})" if row else f"ID {id_automovil}"
                log_user_activity("Servicios Automóvil", f"Registró servicio de mantenimiento a los {kilometros} Km para {car_info}")
                return True
    except Exception as e:
        print(f"Error adding service: {e}")
        return False

def delete_servicio(id_servicio):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdAutomovil, Kilometros FROM HUB_ServiciosAutomovil WHERE Id = %s", (id_servicio,))
                row = cur.fetchone()
                if row:
                    id_auto, kms = row[0], row[1]
                    cur.execute("DELETE FROM HUB_ServiciosAutomovil WHERE Id = %s", (id_servicio,))
                    
                    cur.execute("""
                        SELECT MAX(Kilometros) FROM HUB_ServiciosAutomovil WHERE IdAutomovil = %s
                    """, (id_auto,))
                    max_kms_row = cur.fetchone()
                    max_kms = max_kms_row[0] if max_kms_row and max_kms_row[0] is not None else 0
                    
                    cur.execute("""
                        UPDATE HUB_Automoviles
                        SET UltimoServicioKms = %s
                        WHERE Id = %s
                    """, (max_kms, id_auto))
                    
                    conn.commit()
                    log_user_activity("Servicios Automóvil", f"Eliminó registro de servicio (ID {id_servicio}) de vehículo ID {id_auto}")
                    return True
                return False
    except Exception as e:
        print(f"Error deleting service: {e}")
        return False

def get_reparaciones(id_automovil):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Fecha, Costo, Taller, Descripcion
                    FROM HUB_ReparacionesAutomovil
                    WHERE IdAutomovil = %s
                    ORDER BY Fecha DESC, Id DESC
                """, (id_automovil,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching repairs: {e}")
        return []

def add_reparacion(id_automovil, fecha, costo, taller, descripcion):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_ReparacionesAutomovil (IdAutomovil, Fecha, Costo, Taller, Descripcion)
                    VALUES (%s, %s, %s, %s, %s)
                """, (int(id_automovil), fecha, float(costo), taller.strip(), descripcion.strip()))
                conn.commit()
                
                cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (id_automovil,))
                row = cur.fetchone()
                car_info = f"{row[0]} ({row[1]})" if row else f"ID {id_automovil}"
                log_user_activity("Reparaciones Automóvil", f"Registró reparación de ${costo:.2f} en taller {taller.strip()} para {car_info}")
                return True
    except Exception as e:
        print(f"Error adding repair: {e}")
        return False

def delete_reparacion(id_reparacion):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdAutomovil, Costo FROM HUB_ReparacionesAutomovil WHERE Id = %s", (id_reparacion,))
                row = cur.fetchone()
                if row:
                    id_auto, costo = row[0], row[1]
                    cur.execute("DELETE FROM HUB_ReparacionesAutomovil WHERE Id = %s", (id_reparacion,))
                    conn.commit()
                    log_user_activity("Reparaciones Automóvil", f"Eliminó registro de reparación ID {id_reparacion} (costo: ${costo:.2f}) de vehículo ID {id_auto}")
                    return True
                return False
    except Exception as e:
        print(f"Error deleting repair: {e}")
        return False

def get_autos_sin_registro_semanal(start_dt, end_dt):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT a.Id, a.MarcaModelo, a.Placas, u.Nombre AS ConductorAsignado
                    FROM HUB_Automoviles a
                    LEFT JOIN HUB_Users u ON a.IdUsuarioAsignado = u.Id
                    WHERE a.Id NOT IN (
                        SELECT k.IdAutomovil 
                        FROM HUB_RegistroKilometros k
                        WHERE k.FechaHora >= %s AND k.FechaHora <= %s
                    )
                    ORDER BY a.MarcaModelo ASC
                """, (start_dt, end_dt))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching automobiles without weekly registration: {e}")
        return []

def get_email_config():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT TOP 1 SmtpServer, Port, Username, Password, UseSSL, UseTLS, RequireAuth FROM HUB_EmailConfig ORDER BY Id ASC")
                row = cur.fetchone()
                if row:
                    return {
                        'smtp_server': row['SmtpServer'].strip(),
                        'port': int(row['Port']),
                        'username': row['Username'].strip(),
                        'password': row['Password'].strip(),
                        'use_ssl': bool(row['UseSSL']),
                        'use_tls': bool(row['UseTLS']),
                        'require_auth': bool(row['RequireAuth'])
                    }
    except Exception as e:
        print(f"Error fetching email config: {e}")
    fallback = get_email_config_fallback()
    return fallback

def get_email_config_fallback():
    from config_db import load_email_fallback
    return load_email_fallback()

def update_email_config(smtp_server, port, username, password, use_ssl, use_tls, require_auth):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_EmailConfig
                    SET SmtpServer = %s,
                        Port = %s,
                        Username = %s,
                        Password = %s,
                        UseSSL = %s,
                        UseTLS = %s,
                        RequireAuth = %s
                """, (smtp_server.strip(), int(port), username.strip(), password.strip(), int(use_ssl), int(use_tls), int(require_auth)))
                conn.commit()
                log_user_activity("Configuración Correo", "Actualizó los parámetros del servidor de correo saliente (SMTP)")
                return True
    except Exception as e:
        print(f"Error updating email config: {e}")
        return False

def send_email_with_multiple_pdfs(to_email, subject, body, attachments, sender_name, sender_email, cc_email=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    
    # 1. Load email configuration
    config = get_email_config()
    
    # 2. Build message
    msg = MIMEMultipart()
    msg['From'] = f"{sender_name} <{config['username']}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    if sender_email:
        msg['Reply-To'] = sender_email
    
    cc_list = []
    if cc_email:
        msg['Cc'] = cc_email
        cc_list.append(cc_email)
        
    msg.attach(MIMEText(body, 'plain'))
    
    # Attach all files
    for file_bytes, filename in attachments:
        attachment = MIMEBase('application', 'octet-stream')
        attachment.set_payload(file_bytes)
        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(attachment)
    
    # 3. Connect and send
    recipients = [to_email] + cc_list
    try:
        if config['use_ssl']:
            server = smtplib.SMTP_SSL(config['smtp_server'], config['port'], timeout=15)
        else:
            server = smtplib.SMTP(config['smtp_server'], config['port'], timeout=15)
            if config['use_tls']:
                server.starttls()
                
        if config['require_auth']:
            server.login(config['username'], config['password'])
            
        server.sendmail(config['username'], recipients, msg.as_string())
        server.quit()
        return True, "Correo enviado exitosamente."
    except Exception as e:
        return False, f"Error al enviar correo SMTP: {str(e)}"

def send_email_with_pdf(to_email, subject, body, pdf_bytes, pdf_filename, sender_name, sender_email, cc_email=None):
    return send_email_with_multiple_pdfs(
        to_email=to_email,
        subject=subject,
        body=body,
        attachments=[(pdf_bytes, pdf_filename)],
        sender_name=sender_name,
        sender_email=sender_email,
        cc_email=cc_email
    )


# ---------------------------------------------------------------------------
# Session Token Management (persistent login via URL query param ?s=TOKEN)
# ---------------------------------------------------------------------------

SESSION_DAYS = 30  # token validity in days

def create_session_token(user_email: str) -> str:
    """Generate a secure random token, persist it in HUB_Sessions, and return it."""
    token = secrets.token_urlsafe(32)
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Clean up expired tokens for this user first
                cur.execute(
                    "DELETE FROM HUB_Sessions WHERE UserEmail = %s OR ExpiresAt < GETDATE()",
                    (user_email,)
                )
                cur.execute(
                    "INSERT INTO HUB_Sessions (Token, UserEmail, CreatedAt, ExpiresAt) "
                    "VALUES (%s, %s, GETDATE(), %s)",
                    (token, user_email, expires)
                )
            conn.commit()
        return token
    except Exception as e:
        print(f"create_session_token error: {e}")
        return ""


def validate_session_token(token: str):
    """
    Validate a session token.
    Returns the full user row dict if valid, None otherwise.
    """
    if not token or len(token) > 100:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT s.UserEmail FROM HUB_Sessions s "
                    "WHERE s.Token = %s AND s.ExpiresAt > GETDATE()",
                    (token,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                email = row['UserEmail']
                cur.execute(
                    "SELECT Id, Email, Nombre, Activo, "
                    "AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, "
                    "AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, "
                    "AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso, "
                    "AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras "
                    "FROM HUB_Users WHERE Email = %s AND Activo = 1",
                    (email,)
                )
                user = cur.fetchone()
                return user if user else None
    except Exception as e:
        print(f"validate_session_token error: {e}")
        return None


def delete_session_token(token: str):
    """Invalidate a session token (logout)."""
    if not token:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_Sessions WHERE Token = %s", (token,))
            conn.commit()
    except Exception as e:
        print(f"delete_session_token error: {e}")


# ---------------------------------------------------------------------------
# AI Config (Cambio #1 — HUB_AIConfig)
# ---------------------------------------------------------------------------

def get_ai_config() -> dict:
    """Return the stored AI provider configuration (provider, api_key, model)."""
    defaults = {'provider': 'google_gemini', 'api_key': '', 'model': 'gemini-2.0-flash'}
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT TOP 1 Provider, ApiKey, Model FROM HUB_AIConfig ORDER BY Id ASC")
                row = cur.fetchone()
                if row:
                    return {
                        'provider': (row['Provider'] or 'google_gemini').strip(),
                        'api_key':  (row['ApiKey']  or '').strip(),
                        'model':    (row['Model']   or 'gemini-2.0-flash').strip(),
                    }
    except Exception as e:
        print(f"get_ai_config error: {e}")
    return defaults


def update_ai_config(provider: str, api_key: str, model: str = 'gemini-2.0-flash') -> bool:
    """Upsert the AI configuration row (Id=1)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    IF EXISTS (SELECT 1 FROM HUB_AIConfig WHERE Id = 1)
                        UPDATE HUB_AIConfig
                           SET Provider = %s, ApiKey = %s, Model = %s, UpdatedAt = GETDATE()
                         WHERE Id = 1
                    ELSE
                        INSERT INTO HUB_AIConfig (Id, Provider, ApiKey, Model, UpdatedAt)
                        VALUES (1, %s, %s, %s, GETDATE())
                """, (provider, api_key, model, provider, api_key, model))
                conn.commit()
                log_user_activity("Configuración IA", f"Actualizó configuración AI — proveedor: {provider}, modelo: {model}")
                return True
    except Exception as e:
        print(f"update_ai_config error: {e}")
        return False


# ---------------------------------------------------------------------------
# Signature Tokens (Cambio #3 — HUB_SignatureTokens)
# ---------------------------------------------------------------------------

SIGNATURE_TOKEN_HOURS = 24  # token validity in hours


def create_signature_token(id_reporte: int) -> str:
    """Generate a unique 64-char hex token valid for 24 h and store it."""
    token = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex string
    expires = datetime.datetime.utcnow() + datetime.timedelta(hours=SIGNATURE_TOKEN_HOURS)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Invalidate any existing unused tokens for same report
                cur.execute(
                    "DELETE FROM HUB_SignatureTokens WHERE IdReporte = %s AND UsedAt IS NULL",
                    (int(id_reporte),)
                )
                cur.execute(
                    "INSERT INTO HUB_SignatureTokens (Token, IdReporte, CreatedAt, ExpiresAt) "
                    "VALUES (%s, %s, GETDATE(), %s)",
                    (token, int(id_reporte), expires)
                )
            conn.commit()
        log_user_activity("Firma Remota", f"Generó token de firma remota para reporte ID {id_reporte}")
        return token
    except Exception as e:
        print(f"create_signature_token error: {e}")
        return ""


def validate_signature_token(token: str):
    """
    Validate a signature token.
    Returns (True, id_reporte) if valid and unused, else (False, None).
    """
    if not token or len(token) != 64:
        return False, None
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT IdReporte, ExpiresAt, UsedAt "
                    "FROM HUB_SignatureTokens "
                    "WHERE Token = %s",
                    (token,)
                )
                row = cur.fetchone()
                if not row:
                    return False, None
                if row['UsedAt'] is not None:
                    return False, None  # already used
                if datetime.datetime.utcnow() > row['ExpiresAt']:
                    return False, None  # expired
                return True, int(row['IdReporte'])
    except Exception as e:
        print(f"validate_signature_token error: {e}")
        return False, None


def consume_signature_token(token: str) -> bool:
    """Mark a signature token as used (one-time use)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE HUB_SignatureTokens SET UsedAt = GETDATE() WHERE Token = %s",
                    (token,)
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"consume_signature_token error: {e}")
        return False


def get_service_report_by_id(id_reporte: int):
    """Fetch a service report by its numeric ID (used by public signature page)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdReporte, Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico,
                           DescripcionServicio, Estatus, Notas, MaquinaLinea, FechaHoraInicio, FechaHoraFin,
                           TiempoTraslado, TiempoComida, FirmaConformidad, Cotizacion
                    FROM ReportesServicio
                    WHERE IdReporte = %s
                """, (int(id_reporte),))
                return cur.fetchone()
    except Exception as e:
        print(f"get_service_report_by_id error: {e}")
        return None


# ---------------------------------------------------------------------------
# MODULO VACACIONES: HISTORIAL Y REGISTROS
# ---------------------------------------------------------------------------

def get_vacaciones_saldo_manual(id_usuario: int) -> dict:
    """Obtiene el saldo manual de días acumulados del periodo anterior para un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT DiasAcumuladosAnteriores, PeriodoAcumulado FROM HUB_VacacionesSaldosManuales WHERE IdUsuario = %s", (int(id_usuario),))
                row = cur.fetchone()
                if row:
                    return {
                        'dias_acumulados': int(row['DiasAcumuladosAnteriores']),
                        'periodo_acumulado': (row['PeriodoAcumulado'] or '').strip()
                    }
    except Exception as e:
        print(f"Error get_vacaciones_saldo_manual: {e}")
    return {'dias_acumulados': 0, 'periodo_acumulado': ''}

def save_vacaciones_saldo_manual(id_usuario: int, dias: int, periodo: str) -> bool:
    """Inserta o actualiza el saldo de vacaciones acumuladas del periodo anterior para un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    IF EXISTS (SELECT 1 FROM HUB_VacacionesSaldosManuales WHERE IdUsuario = %s)
                        UPDATE HUB_VacacionesSaldosManuales
                           SET DiasAcumuladosAnteriores = %s, PeriodoAcumulado = %s
                         WHERE IdUsuario = %s
                    ELSE
                        INSERT INTO HUB_VacacionesSaldosManuales (IdUsuario, DiasAcumuladosAnteriores, PeriodoAcumulado)
                        VALUES (%s, %s, %s)
                """, (int(id_usuario), int(dias), periodo.strip() if periodo else None, int(id_usuario),
                       int(id_usuario), int(dias), periodo.strip() if periodo else None))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error save_vacaciones_saldo_manual: {e}")
        return False

def get_vacaciones_registros(id_usuario: int = None) -> list:
    """Obtiene todos los días agendados de vacaciones (opcional por usuario)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if id_usuario is not None:
                    cur.execute("""
                        SELECT r.Id, r.IdUsuario, r.Fecha, r.Tipo, r.Notas, r.FechaRegistro, u.Nombre AS UsuarioNombre
                        FROM HUB_VacacionesRegistros r
                        JOIN HUB_Users u ON r.IdUsuario = u.Id
                        WHERE r.IdUsuario = %s
                        ORDER BY r.Fecha ASC
                    """, (int(id_usuario),))
                else:
                    cur.execute("""
                        SELECT r.Id, r.IdUsuario, r.Fecha, r.Tipo, r.Notas, r.FechaRegistro, u.Nombre AS UsuarioNombre
                        FROM HUB_VacacionesRegistros r
                        JOIN HUB_Users u ON r.IdUsuario = u.Id
                        ORDER BY r.Fecha ASC
                    """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_vacaciones_registros: {e}")
        return []

def add_vacaciones_registro(id_usuario: int, fecha: str, tipo: str, notas: str = None) -> bool:
    """Registra un día de vacaciones tomado o por tomar."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM HUB_VacacionesRegistros WHERE IdUsuario = %s AND Fecha = %s", (int(id_usuario), fecha))
                if cur.fetchone():
                    return False
                cur.execute("""
                    INSERT INTO HUB_VacacionesRegistros (IdUsuario, Fecha, Tipo, Notas)
                    VALUES (%s, %s, %s, %s)
                """, (int(id_usuario), fecha, tipo.strip(), notas.strip() if notas else None))
                conn.commit()
                cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                u_row = cur.fetchone()
                u_name = u_row[0] if u_row else f"ID {id_usuario}"
                log_user_activity("Vacaciones", f"Agendó día {fecha} ({tipo}) para el usuario {u_name}")
                return True
    except Exception as e:
        print(f"Error add_vacaciones_registro: {e}")
        return False

def delete_vacaciones_registro(registro_id: int) -> bool:
    """Elimina un día agendado de vacaciones."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT r.Fecha, r.Tipo, u.Nombre 
                    FROM HUB_VacacionesRegistros r
                    JOIN HUB_Users u ON r.IdUsuario = u.Id
                    WHERE r.Id = %s
                """, (int(registro_id),))
                row = cur.fetchone()
                if row:
                    fecha, tipo, nombre = row[0], row[1], row[2]
                    cur.execute("DELETE FROM HUB_VacacionesRegistros WHERE Id = %s", (int(registro_id),))
                    conn.commit()
                    log_user_activity("Vacaciones", f"Canceló día agendado {fecha} ({tipo}) del usuario {nombre}")
                    return True
                return False
    except Exception as e:
        print(f"Error delete_vacaciones_registro: {e}")
        return False


# ---------------------------------------------------------------------------
# MODULO HORAS EXTRAS: CRUD Y REGISTROS
# ---------------------------------------------------------------------------

def get_horas_extras_resumen_usuarios() -> list:
    """Obtiene el listado de usuarios con su saldo total acumulado de horas extras pendientes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT u.Id, u.Nombre, u.Email, u.AccesoHorasExtras,
                           ISNULL(SUM(r.HorasExtrasCalculadas), 0) AS TotalHorasAcumuladas
                    FROM HUB_Users u
                    LEFT JOIN HUB_HorasExtrasRegistros r ON u.Id = r.IdUsuario AND r.Estatus = 'Pendiente'
                    WHERE u.Activo = 1
                    GROUP BY u.Id, u.Nombre, u.Email, u.AccesoHorasExtras
                    ORDER BY u.Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_horas_extras_resumen_usuarios: {e}")
        return []

def get_horas_extras_registros_por_usuario(id_usuario: int) -> list:
    """Obtiene todos los registros de horas extras de un colaborador."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, Fecha, HoraEntrada, HoraSalida, HorasComida,
                           HorasTraslado, HorasExtrasCalculadas, Descripcion, Cliente, Calificacion, Estatus, FechaRegistro
                    FROM HUB_HorasExtrasRegistros
                    WHERE IdUsuario = %s
                    ORDER BY Fecha DESC, Id DESC
                """, (int(id_usuario),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_horas_extras_registros_por_usuario: {e}")
        return []

def add_horas_extras_registro(id_usuario: int, fecha: str, hora_entrada: str, hora_salida: str,
                              horas_comida: float, horas_traslado: float, horas_extras: float,
                              descripcion: str, cliente: str = None, calificacion: int = None, estatus: str = 'Pendiente') -> bool:
    """Registra un nuevo periodo de horas extras o ajuste."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                h_entrada = hora_entrada if (hora_entrada and hora_entrada.strip()) else None
                h_salida = hora_salida if (hora_salida and hora_salida.strip()) else None
                cur.execute("""
                    INSERT INTO HUB_HorasExtrasRegistros (IdUsuario, Fecha, HoraEntrada, HoraSalida, HorasComida, HorasTraslado, HorasExtrasCalculadas, Descripcion, Cliente, Calificacion, Estatus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (int(id_usuario), fecha, h_entrada, h_salida,
                       float(horas_comida) if horas_comida is not None else None,
                       float(horas_traslado) if horas_traslado is not None else None,
                       float(horas_extras), descripcion.strip(),
                       cliente.strip() if cliente else None,
                       int(calificacion) if calificacion is not None else None,
                       estatus))
                conn.commit()
                
                cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                u_row = cur.fetchone()
                u_name = u_row[0] if u_row else f"ID {id_usuario}"
                log_user_activity("Horas Extras", f"Registró {horas_extras} horas extras en fecha {fecha} para el usuario {u_name}")
                return True
    except Exception as e:
        print(f"Error add_horas_extras_registro: {e}")
        return False

def update_horas_extras_registro(id_registro: int, fecha: str, hora_entrada: str, hora_salida: str,
                                 horas_comida: float, horas_traslado: float, horas_extras: float,
                                 descripcion: str, cliente: str = None, calificacion: int = None, estatus: str = 'Pendiente') -> bool:
    """Modifica un registro existente de horas extras."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                h_entrada = hora_entrada if (hora_entrada and hora_entrada.strip()) else None
                h_salida = hora_salida if (hora_salida and hora_salida.strip()) else None
                cur.execute("""
                    UPDATE HUB_HorasExtrasRegistros
                    SET Fecha = %s,
                        HoraEntrada = %s,
                        HoraSalida = %s,
                        HorasComida = %s,
                        HorasTraslado = %s,
                        HorasExtrasCalculadas = %s,
                        Descripcion = %s,
                        Cliente = %s,
                        Calificacion = %s,
                        Estatus = %s
                    WHERE Id = %s
                """, (fecha, h_entrada, h_salida,
                       float(horas_comida) if horas_comida is not None else None,
                       float(horas_traslado) if horas_traslado is not None else None,
                       float(horas_extras), descripcion.strip(),
                       cliente.strip() if cliente else None,
                       int(calificacion) if calificacion is not None else None,
                       estatus, int(id_registro)))
                conn.commit()
                log_user_activity("Horas Extras", f"Modificó registro de horas extras ID {id_registro}")
                return True
    except Exception as e:
        print(f"Error update_horas_extras_registro: {e}")
        return False

def delete_horas_extras_registro(id_registro: int) -> bool:
    """Elimina un registro de horas extras de la base de datos."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_HorasExtrasRegistros WHERE Id = %s", (int(id_registro),))
                conn.commit()
                log_user_activity("Horas Extras", f"Eliminó registro de horas extras ID {id_registro}")
                return True
    except Exception as e:
        print(f"Error delete_horas_extras_registro: {e}")
        return False

def mark_horas_extras_como_pagadas(registro_ids: list) -> bool:
    """Cambia el estatus de varios registros a 'Pagado' de forma masiva."""
    if not registro_ids:
        return True
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Build parameter format string for bulk update
                format_strings = ','.join(['%s'] * len(registro_ids))
                cur.execute(f"""
                    UPDATE HUB_HorasExtrasRegistros
                    SET Estatus = 'Pagado'
                    WHERE Id IN ({format_strings})
                """, tuple(int(x) for x in registro_ids))
                conn.commit()
                log_user_activity("Horas Extras", f"Marcó como pagados los registros ID: {registro_ids}")
                return True
    except Exception as e:
        print(f"Error mark_horas_extras_como_pagadas: {e}")
        return False


# ---------------------------------------------------------------------------
# ASISTENTE EDWIN JARVIS: HISTORIAL Y CONSULTAS SEGURAS
# ---------------------------------------------------------------------------

def get_jarvis_conversaciones(id_usuario: int) -> list:
    """Obtiene las conversaciones de chat de Jarvis para un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Titulo, FechaCreacion 
                    FROM HUB_JarvisConversaciones 
                    WHERE IdUsuario = %s 
                    ORDER BY FechaCreacion DESC
                """, (int(id_usuario),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_jarvis_conversaciones: {e}")
        return []

def add_jarvis_conversacion(id_usuario: int, titulo: str) -> int:
    """Crea una conversación de Jarvis y limpia el exceso si supera 10 conversaciones (FIFO)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Insert new conversation
                cur.execute("""
                    INSERT INTO HUB_JarvisConversaciones (IdUsuario, Titulo)
                    VALUES (%s, %s);
                    SELECT SCOPE_IDENTITY();
                """, (int(id_usuario), titulo.strip()))
                new_id = int(cur.fetchone()[0])
                
                # Check total conversations and keep only latest 10 (FIFO)
                cur.execute("""
                    SELECT Id FROM HUB_JarvisConversaciones 
                    WHERE IdUsuario = %s 
                    ORDER BY FechaCreacion DESC
                """, (int(id_usuario),))
                rows = cur.fetchall()
                if len(rows) > 10:
                    expired_ids = [r[0] for r in rows[10:]]
                    format_strings = ','.join(['%s'] * len(expired_ids))
                    cur.execute(f"""
                        DELETE FROM HUB_JarvisConversaciones 
                        WHERE Id IN ({format_strings})
                    """, tuple(expired_ids))
                
                conn.commit()
                return new_id
    except Exception as e:
        print(f"Error add_jarvis_conversacion: {e}")
        return None

def delete_jarvis_conversacion(id_conversacion: int) -> bool:
    """Elimina una conversación específica de Jarvis."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_JarvisConversaciones WHERE Id = %s", (int(id_conversacion),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error delete_jarvis_conversacion: {e}")
        return False

def get_jarvis_mensajes(id_conversacion: int) -> list:
    """Obtiene el historial de mensajes de una conversación de Jarvis."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Role, Contenido, FechaRegistro 
                    FROM HUB_JarvisMensajes 
                    WHERE IdConversacion = %s 
                    ORDER BY FechaRegistro ASC, Id ASC
                """, (int(id_conversacion),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_jarvis_mensajes: {e}")
        return []

def add_jarvis_mensaje(id_conversacion: int, role: str, contenido: str) -> bool:
    """Guarda un mensaje del usuario o de ECCSA IA en la conversación."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_JarvisMensajes (IdConversacion, Role, Contenido)
                    VALUES (%s, %s, %s)
                """, (int(id_conversacion), role.strip(), contenido.strip()))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error add_jarvis_mensaje: {e}")
        return False

def execute_readonly_sql(query: str) -> list:
    """Ejecuta una consulta SQL de solo lectura (SELECT) y retorna la lista de diccionarios.
       Previene comandos que alteren la base de datos (INSERT, UPDATE, DELETE, etc.).
    """
    clean_q = query.strip().upper()
    
    # Simple validation rules
    forbidden = ["INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE ", "EXEC ", "EXECUTE "]
    for f in forbidden:
        if f in clean_q:
            return [{"Error": f"Instrucción SQL no permitida por seguridad: Contiene {f}"}]
            
    if not clean_q.startswith("SELECT") and not clean_q.startswith("WITH"):
        return [{"Error": "La consulta SQL debe iniciar con SELECT o WITH por motivos de seguridad."}]
        
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(query)
                return cur.fetchall()
    except Exception as e:
        return [{"Error": f"Excepción en ejecución SQL: {str(e)}"}]


def get_user_sueldo(user_id) -> float:
    """Obtiene el sueldo semanal de un usuario desde HUB_Nomina (retorna 0.0 si no tiene o falla)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Sueldo FROM HUB_Nomina WHERE IdUsuario = %s", (int(user_id),))
                row = cur.fetchone()
                if row and row[0] is not None:
                    return float(row[0])
                return 0.0
    except Exception as e:
        print(f"Error fetching sueldo for user_id {user_id}: {e}")
        return 0.0


# ── Go Vale QR ────────────────────────────────────────────────────────────────

def get_govale_credentials():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'govale_user'")
                r = cur.fetchone()
                user = (r['Valor'] or '').strip() if r else ''
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'govale_password'")
                r = cur.fetchone()
                pwd = (r['Valor'] or '').strip() if r else ''
                return {'user': user, 'password': pwd}
    except Exception as e:
        print(f"Error reading govale config (server): {e}")
        return {'user': '', 'password': ''}


def crear_solicitud_vale(id_solicitante, placa, descripcion, cantidad=1, monto_unit=500.00, notas=''):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_SolicitudVales (IdSolicitante, Placa, Descripcion, Cantidad, MontoUnit, Notas)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    SELECT SCOPE_IDENTITY();
                """, (id_solicitante, placa.strip(), descripcion.strip(), int(cantidad), float(monto_unit), notas.strip()))
                sol_id = cur.fetchone()[0]
                conn.commit()
                return int(sol_id)
    except Exception as e:
        print(f"Error creando solicitud vale (server): {e}")
        return None


def get_solicitudes_vales(estatus=None, id_solicitante=None, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                where = []
                params = []
                if estatus:
                    where.append("s.Estatus = %s")
                    params.append(estatus)
                if id_solicitante:
                    where.append("s.IdSolicitante = %s")
                    params.append(int(id_solicitante))
                where_sql = (" WHERE " + " AND ".join(where)) if where else ""
                cur.execute(f"""
                    SELECT TOP {int(limit)}
                        s.Id, s.FechaSolicitud, s.Placa, s.Descripcion, s.Cantidad, s.MontoUnit,
                        s.Estatus, s.IdValeGoVale, s.CodigoQR, s.UrlQR, s.Notas,
                        s.FechaAprobado, s.IdAprobador,
                        u.Nombre AS Solicitante, ap.Nombre AS Aprobador
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Users u  ON s.IdSolicitante = u.Id
                    LEFT JOIN HUB_Users ap ON s.IdAprobador   = ap.Id
                    {where_sql}
                    ORDER BY s.FechaSolicitud DESC
                """, params)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching solicitudes vales (server): {e}")
        return []


def get_solicitud_vale_by_id(solicitud_id):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.*, u.Nombre AS Solicitante, ap.Nombre AS Aprobador
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Users u  ON s.IdSolicitante = u.Id
                    LEFT JOIN HUB_Users ap ON s.IdAprobador   = ap.Id
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching solicitud vale (server): {e}")
        return None


def aprobar_solicitud_vale(solicitud_id, id_aprobador):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'APROBADO', IdAprobador = %s, FechaAprobado = GETDATE()
                    WHERE Id = %s AND Estatus = 'PENDIENTE'
                """, (int(id_aprobador), int(solicitud_id)))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error aprobando solicitud vale (server): {e}")
        return False


def rechazar_solicitud_vale(solicitud_id, id_aprobador, motivo=''):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'RECHAZADO', IdAprobador = %s, FechaAprobado = GETDATE(),
                        Notas = CASE WHEN NULLIF(%s, '') IS NOT NULL THEN Notas + CHAR(13) + CHAR(10) + %s ELSE Notas END
                    WHERE Id = %s AND Estatus = 'PENDIENTE'
                """, (int(id_aprobador), motivo.strip(), motivo.strip(), int(solicitud_id)))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error rechazando solicitud vale (server): {e}")
        return False


def marcar_vale_generado(solicitud_id, id_vale_govale, codigo_qr='', url_qr=''):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'GENERADO', IdValeGoVale = %s, CodigoQR = %s, UrlQR = %s
                    WHERE Id = %s AND Estatus = 'APROBADO'
                """, (str(id_vale_govale), str(codigo_qr)[:2000], str(url_qr)[:500], int(solicitud_id)))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error marcando vale generado (server): {e}")
        return False

