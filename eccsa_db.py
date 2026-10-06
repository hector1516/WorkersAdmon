import pymssql
import datetime
import secrets
import uuid
import threading
from config_db import load_db_config

DB_CONFIG = load_db_config()

def now_mexico():
    """Devuelve la fecha y hora actual en zona horaria de México (UTC-6)."""
    try:
        import pytz
        return datetime.datetime.now(pytz.timezone('America/Mexico_City'))
    except ImportError:
        return datetime.datetime.utcnow() - datetime.timedelta(hours=6)

# --- Connection Pool (1 connection per thread, auto-reconnect) ---
_local = threading.local()

def get_connection():
    conn = getattr(_local, 'conn', None)
    try:
        if conn is not None:
            conn.cursor().execute("SELECT 1")
            return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
        conn = None
    if conn is None:
        conn = pymssql.connect(**DB_CONFIG, login_timeout=10, timeout=30)
        _local.conn = conn
    return conn

# --- Cache wrapper (works in Streamlit and standalone workers) ---
def _cache_data(ttl=300):
    """Decorator que usa st.cache_data si está disponible, si no usa caché en memoria."""
    try:
        import streamlit as st
        return st.cache_data(ttl=ttl, show_spinner=False)
    except Exception:
        def decorator(func):
            _mem = {}
            def wrapper(*args, **kwargs):
                import time
                key = (args, tuple(sorted(kwargs.items())))
                now = time.time()
                if key in _mem and now - _mem[key][1] < ttl:
                    return _mem[key][0]
                result = func(*args, **kwargs)
                _mem[key] = (result, now)
                return result
            wrapper.clear = lambda: None
            return wrapper
        return decorator

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
def get_clients():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdCliente, Cliente FROM clientes ORDER BY Cliente ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching clients: {e}")
        return []

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
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

def get_hub_user_credentials_by_email(email):
    """Devuelve Nombre y Password de un usuario activo dado su correo, o None si no existe.
    Se usa para recuperar credenciales por correo. NO valida la contraseña."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Nombre, Password, Activo
                    FROM HUB_Users
                    WHERE LTRIM(RTRIM(Email)) = %s
                """, (email.strip().lower(),))
                r = cur.fetchone()
                if r and r['Activo']:
                    return {'nombre': r['Nombre'], 'password': r['Password']}
                return None
    except Exception as e:
        print(f"Error retrieving HUB user credentials by email: {e}")
        return None


def authenticate_hub_user(email, password):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Email, Nombre, Password, Activo, Nickname,
                           AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso,
                           AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras, AccesoOxxoGas, AccesoValesOxxoGas, AccesoRegistroTicketOxxoGas, AccesoEdicionBD, AccesoNominas, AccesoInventario, AccesoCalculo, AccesoProveedores, AccesoOC, AccesoTelegram, AccesoDeteccionRed
                    FROM HUB_Users 
                    WHERE LTRIM(RTRIM(Email)) = %s
                """, (email.strip().lower(),))
                r = cur.fetchone()
                if r:
                    pw_match = r['Password'] == password
                    print(f"[AUTH DEBUG] email={repr(email)} found=True activo={r['Activo']} pw_match={pw_match} stored_pw_hex={r['Password'].encode('utf-8').hex()} input_pw_len={len(password)}")
                    if r['Activo'] and pw_match:
                        return {
                            'id': r['Id'],
                            'email': r['Email'],
                            'nombre': r['Nombre'],
                            'activo': r['Activo'],
                            # Nickname de Legends (solo informativo; se inicializa solo si estaba vacío)
                            'nickname': (r.get('Nickname') or '').strip(),
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
                            'acceso_edicion_bd': bool(r.get('AccesoEdicionBD', False)),
                            'acceso_nominas': bool(r.get('AccesoNominas', False)),
                            'acceso_inventario': bool(r.get('AccesoInventario', False)),
                            'acceso_calculos': bool(r.get('AccesoCalculo', False)),
                            'acceso_proveedores': bool(r.get('AccesoProveedores', False)),
                            'acceso_oc': bool(r.get('AccesoOC', False)),
                            'acceso_telegram': bool(r.get('AccesoTelegram', False)),
                            'acceso_deteccion_red': bool(r.get('AccesoDeteccionRed', False)),
                            'fecha_ingreso': r.get('FechaIngreso')
                        }
                else:
                    print(f"[AUTH DEBUG] email={repr(email)} found=False - user not in DB")
                return None
    except Exception as e:
        print(f"Error authenticating HUB user: {e}")
        return {"_db_error": str(e)}

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
def get_contacts_by_client(id_cliente):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT DISTINCT Contacto 
                    FROM IndiceMateriales 
                    WHERE IdCliente = %s AND Contacto IS NOT NULL AND LTRIM(RTRIM(Contacto)) <> ''
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

def buscar_clave_sat_por_descripcion(descripcion: str) -> str | None:
    """Busca código SAT interno por keywords en la descripción.
    Reglas mapeadas: si la descripción contiene ciertos términos, devolvemos la clave SAT típica.
    Retorna None si no hay match."""
    if not descripcion:
        return None
    d = descripcion.upper()
    # Mapeo de keywords → claves SAT CFDI 4.0 comunes
    # Estructura: (lista_de_keywords, clave_sat)
    # Orden import: más específicos primero
    mapping = [
        (["PLC", "control lógico programable"], "44101701"),  # PLCs
        (["MOTOR", "motor eléctrico"], "84039000"),          # Motores eléctricos
        (["BOMBA", "bomba"], "84131000"),                    # Bombas
        (["VÁLVULA", "válvula"], "84818000"),                # Válvulas
        (["SENSOR", "sensor"], "90278000"),                  # Sensores
        (["TRANSFORMADOR", "transformador"], "85044090"),    # Transformadores
        (["CONTROL", "controlador"], "84799090"),            # Controladores
        (["INVERSOR", "inversor"], "85044090"),              # Inversores
        (["PANEL", "panel"], "85389000"),                    # Paneles
        (["MEDIDOR", "medidor"], "90278000"),                # Medidores
        (["MANOMETRO", "manómetro"], "90268000"),            # Manómetros
        (["MANGUERA", "manguera"], "40092200"),              # Mangueras
        (["ACOPLAMIENTO", "acoplamiento"], "84129000"),      # Acoplamientos
        (["JUNTA", "junta"], "40169100"),                    # Juntas
        (["BALANZA", "balanza"], "84233000"),                # Balanzas
        (["BASURA", "residuo"], "38249900"),                 # Residuos
    ]
    for keywords, clave in mapping:
        for kw in keywords:
            if kw in d:
                return clave
    return None


def sugerir_clave_sat_ia(descripcion: str, api_key: str | None = None, modelo: str = 'gemini-1.5-flash') -> str | None:
    """Consulta a Gemini IA para sugerir el código SAT más conveniente para una descripción dada.
    Usa function calling para obtener una clave estructurada y rationale."""
    import google.generativeai as genai
    import google.ai.generativelanguage as glm
    
    # Configurar API key
    if api_key:
        genai.configure(api_key=api_key)
    
    # Usar la API key de config si no se proporciona la directa
    if not api_key:
        from eccsa_db_server import get_ai_config as _get_ai_cfg
        try:
            cfg = _get_ai_cfg()
            if cfg and cfg.get('api_key'):
                api_key = cfg['api_key']
        except Exception:
            pass
    
    if not api_key:
        return None
    
    try:
        model = genai.GenerativeModel(
            model_name=modelo,
            system_instruction=(
                "Eres un experto en claves SAT CFDI 4.0 para México. "
                "Tu trabajo es asignar la clave SAT más específica y correcta a cada producto o servicio. "
                "NUNCA uses '99839200' (Otros) a menos que sea absolutamente imposible determinar la clave. "
                "Elije siempre la clave más específica posible. "
                "Responde SOLO con JSON: {\"clave_sat\": \"...\", \"razon\": \"...\"}."
            )
        )
        
        prompt = f"""
Asigna el código SAT CFDI 4.0 más específico y correcto para esta partida de cotización:

DESCRIPCIÓN: {descripcion}

Catálogo de claves SAT comunes para equipo industrial/eléctrico:
- 84039000: Motores eléctricos
- 84131000: Bombas para líquidos
- 84136000: Bombas de engranes
- 84139100: Partes de bombas
- 84812000: Válvulas de control de presión
- 84813000: Válvulas de retención
- 84814000: Válvulas de seguridad/relieve
- 84818099: Otras válvulas
- 85044090: Convertidores/estáticos eléctricos
- 85369099: Otros aparatos de conexión
- 85371091: Paneles de control eléctricos
- 85389019: Partes de tableros/paneles
- 85414095: Otros diodos
- 85444209: Cables con aislamiento
- 85444909: Otros conductores eléctricos
- 90268000: Otros instrumentos de medida
- 90278000: Instrumentos de medida física/química
- 90318098: Otros instrumentos de medida
- 40169300: Juntas tóricas
- 40169999: Otras piezas de caucho
- 39173299: Otras tubos de plástico
- 73079999: Otras conexiones de hierro/acero
- 73269099: Otras artículos de hierro/acero
- 82073000: Herramientas para trabajar metales
- 84799099: Otras máquinas con función propia
- 44101701: Máquinas de control numérico
- 44111200: Software/informática
- 43231500: Software para redes
- 99839200: Otros (SOLO si no hay clave específica)

Responde EXACTAMENTE con este JSON sin texto adicional:
{{"clave_sat": "clave_de_8_digitos", "razon": "explicación breve"}}
"""
        
        response = model.generate_content(prompt)
        
        # Try to parse JSON from response
        import json
        text = response.text.strip()
        # Extract JSON from possible surrounding text
        start = text.index('{')
        end = text.rindex('}') + 1
        data = json.loads(text[start:end])
        
        clave = str(data.get('clave_sat', '')).strip()
        razon = data.get('razon', '')
        
        # Validar que sea una clave SAT válida (8 dígitos)
        import re
        if not re.match(r'^\d{8}$', clave):
            clave = "99839200"
            razon = razon or "No se pudo determinar clave específica"
        
        return f"{clave} - {razon}"
        
    except Exception as e:
        print(f"Error suggesting SAT code via IA: {e}")
        return None

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

@_cache_data(ttl=300)
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


@_cache_data(ttl=300)
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
# Documento inmutable: se crea con un snapshot del encabezado de la CM y las
# partidas elegidas (cantidad editable al crear, sin precios). Solo se borra.

def create_remision(folio_cm, partidas_ajustadas, creado_por):
    """
    Crea una remisión a partir de una cotización de materiales.
    folio_cm: Folio numérico de IndiceMateriales.
    partidas_ajustadas: lista de dicts con Partida, Cantidad, Descripcion.
    creado_por: usuario de sesión que genera la remisión.
    Retorna el FolioRemision (str) o False.
    """
    try:
        # Snapshot del encabezado de la cotización origen
        quote = get_quotation_by_folio(folio_cm)
        if not quote:
            print(f"Error creating remision: cotización {folio_cm} not found.")
            return False
        if not partidas_ajustadas:
            print(f"Error creating remision: sin partidas para CM{folio_cm}.")
            return False

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Contador de remisiones de esta CM → folio RM-CM#####-NN
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
    """
    Guarda la firma capturada (Field o HUB) en IndiceRemisiones.FirmaConformidad.
    signature_base64: data URL (data:image/png;base64,...) o solo base64 — mismo patrón
    que ReportesServicio.FirmaConformidad. Invalida el cache del PDF de la remisión.
    """
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
        # Invalida cache local del PDF para que al regenerar salga con la firma
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
    """
    Asigna (o con id_usuario=None desasigna) un usuario del HUB a la remisión
    para que pueda firmarla desde Field. No altera partidas ni encabezado de negocio.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE IndiceRemisiones SET IdUsuarioAsignado = %s WHERE IdRemision = %s",
                    (id_usuario if id_usuario else None, int(id_remision)),
                )
                conn.commit()
                # Bitácora con folio + usuario destino
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
    """Elimina una remisión (índice + partidas vía ON DELETE CASCADE). Solo borrar, no editar."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Folio para la bitácora antes de borrar
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

@_cache_data(ttl=300)
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
                # Sync overtime for ALL technicians (primary + additional)
                todos_tecnicos = [tecnico] + (tecnicos_adicionales or [])
                for t in todos_tecnicos:
                    id_usuario = get_hub_user_id_by_name(t)
                    if id_reporte and id_usuario:
                        sync_horas_extras_desde_reporte(id_reporte, id_usuario, folio, cliente, fecha_str, fecha_inicio, fecha_fin, tiempo_traslado, tiempo_comida)
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
                # Sync overtime for ALL technicians (primary + additional)
                todos_tecnicos = [tecnico] + (tecnicos_adicionales or [])
                for t in todos_tecnicos:
                    id_usuario = get_hub_user_id_by_name(t)
                    if id_usuario:
                        sync_horas_extras_desde_reporte(int(id_reporte), id_usuario, f_str, cliente, fecha_str, fecha_inicio, fecha_fin, tiempo_traslado, tiempo_comida)
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

                # Alertas Telegram y WhatsApp con el PDF (disparo silencioso)
                try:
                    from telegram_alerts import alertar_reporte_firmado
                    alertar_reporte_firmado(id_reporte)
                except Exception:
                    pass
                try:
                    from openwa_alerts import alertar_reporte_firmado as alerta_firma_wa
                    # Aqui si sabemos quien firmo: se lo pasamos para que el
                    # mensaje no salga con el nombre vacio.
                    nombre_firma = ''
                    try:
                        nombre_firma = (cur.description and '') or ''
                        cur.execute("SELECT TOP 1 Nombre FROM HUB_Users WHERE Id = %s", (int(usuario_id),))
                        f = cur.fetchone()
                        nombre_firma = (f.get('Nombre') if isinstance(f, dict) else (f[0] if f else '')) or ''
                    except Exception:
                        pass
                    alerta_firma_wa(id_reporte, usuario_firma=nombre_firma)
                except Exception:
                    pass

                # Puntos ECCSA Legends (disparo silencioso)
                try:
                    registrar_puntos_servicio(id_reporte)
                except Exception:
                    pass

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
# PUNTOS ECCSA LEGENDS - HORAS DE SERVICIO
# ---------------------------------------------------------------------------

def registrar_puntos_servicio(id_reporte):
    """Calcula y registra puntos ECCSA Legends por horas de servicio firmado.
    
    Formula:
      HorasTotales  = (FechaHoraFin - FechaHoraInicio)
      HorasNetas    = HorasTotales - TiempoTraslado - (TiempoComida ? 1 : 0)
      PuntosPersona = max(floor(HorasNetas / NumIngenieros), 1)
    
    Tambien otorga +10 por reporte_firmado si no se ha registrado.
    Si TiempoComida=True, aplica -1 punto de penalizacion.
    
    Se registra una metrica 'servicio' por cada ingeniero del reporte.
    Solo aplica a reportes firmados.
    """
    import math
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                # 1. Leer datos del reporte
                cur.execute("""
                    SELECT IdReporte, Tecnico, FechaHoraInicio, FechaHoraFin,
                           TiempoTraslado, TiempoComida, Estatus
                    FROM ReportesServicio WHERE IdReporte = %s
                """, (int(id_reporte),))
                reporte = cur.fetchone()
                if not reporte:
                    return 0
                if reporte['Estatus'] != 'Firmado':
                    return 0
                if not reporte['FechaHoraInicio'] or not reporte['FechaHoraFin']:
                    return 0

                # 2. Otorgar +10 por reporte_firmado si no existe
                cur.execute(
                    "SELECT 1 FROM HUB_ScoreLog WHERE ReferenciaId = %s AND Metrica = 'reporte_firmado'",
                    (int(id_reporte),)
                )
                if not cur.fetchone():
                    cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (reporte['Tecnico'],))
                    u = cur.fetchone()
                    if u:
                        uid = u['Id']
                        cur.execute(
                            "INSERT INTO HUB_ScoreLog (IdUsuario, Metrica, Puntos, ReferenciaId) VALUES (%s, 'reporte_firmado', 10, %s)",
                            (uid, int(id_reporte))
                        )
                        cur.execute("SELECT PuntuacionSemanal, PuntuacionTotal FROM HUB_UserScores WHERE IdUsuario = %s", (uid,))
                        row = cur.fetchone()
                        if row:
                            cur.execute("""
                                UPDATE HUB_UserScores
                                SET PuntuacionSemanal = PuntuacionSemanal + 10, PuntuacionTotal = PuntuacionTotal + 10,
                                    Nivel = CASE WHEN PuntuacionTotal + 10 >= 3500 THEN 'Diamante' WHEN PuntuacionTotal + 10 >= 1500 THEN 'Oro' WHEN PuntuacionTotal + 10 >= 500 THEN 'Plata' ELSE 'Bronce' END,
                                    UltimoActivo = GETDATE(), FechaCalculo = GETDATE()
                                WHERE IdUsuario = %s
                            """, (uid,))
                        else:
                            cur.execute("""
                                INSERT INTO HUB_UserScores (IdUsuario, PuntuacionSemanal, PuntuacionTotal, Nivel, UltimoActivo)
                                VALUES (%s, 10, 10, 'Bronce', GETDATE())
                            """, (uid,))

                # 3. Calcular horas totales
                inicio = reporte['FechaHoraInicio']
                fin = reporte['FechaHoraFin']
                horas_totales = (fin - inicio).total_seconds() / 3600.0
                if horas_totales <= 0:
                    return 0

                # 4. Restar traslado y comida
                traslado = float(reporte['TiempoTraslado'] or 0)
                tiene_comida = bool(reporte['TiempoComida'])
                comida = 1.0 if tiene_comida else 0.0
                horas_netas = horas_totales - traslado - comida
                if horas_netas <= 0:
                    return 0

                # 5. Contar participantes (principal + adicionales)
                tecnico_principal = reporte['Tecnico']
                cur.execute("""
                    SELECT u.Nombre
                    FROM ReportesServicioTecnicos t
                    INNER JOIN HUB_Users u ON t.IdUsuario = u.Id
                    WHERE t.IdReporte = %s
                """, (int(id_reporte),))
                adicionales = [r['Nombre'] for r in cur.fetchall()]
                participantes = [tecnico_principal] + adicionales
                num_participantes = len(participantes)

                # 6. Calcular puntos por persona
                puntos_por_persona = max(math.floor(horas_netas / num_participantes), 1)

                # 7. Buscar IDs de usuarios y registrar metricas
                puntos_registrados = 0
                for nombre in participantes:
                    cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (nombre,))
                    u = cur.fetchone()
                    if not u:
                        continue
                    uid = u['Id']
                    # Verificar elegibilidad: tiene passkey + Nickname (HUB_Users)
                    cur.execute(
                        "SELECT TOP 1 1 FROM HUB_Users u WHERE u.Id = %s "
                        "AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> '' "
                        "AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)",
                        (uid,)
                    )
                    if not cur.fetchone():
                        continue
                    # Verificar si ya tiene score de servicio para este reporte
                    cur.execute(
                        "SELECT 1 FROM HUB_ScoreLog WHERE IdUsuario=%s AND Metrica='servicio' AND ReferenciaId=%s",
                        (uid, int(id_reporte))
                    )
                    if cur.fetchone():
                        continue  # Ya registrado, no duplicar
                    # Insertar servicio
                    cur.execute(
                        "INSERT INTO HUB_ScoreLog (IdUsuario, Metrica, Puntos, ReferenciaId) VALUES (%s, 'servicio', %s, %s)",
                        (uid, puntos_por_persona, int(id_reporte))
                    )
                    # Penalizacion por comida: -1 punto
                    if tiene_comida:
                        cur.execute(
                            "INSERT INTO HUB_ScoreLog (IdUsuario, Metrica, Puntos, ReferenciaId) VALUES (%s, 'comida_reporte', -1, %s)",
                            (uid, int(id_reporte))
                        )
                    # Actualizar score
                    cur.execute("SELECT PuntuacionSemanal, PuntuacionTotal FROM HUB_UserScores WHERE IdUsuario = %s", (uid,))
                    row = cur.fetchone()
                    pts_extra = -1 if tiene_comida else 0
                    total_pts = puntos_por_persona + pts_extra
                    if row:
                        nueva_semanal = row['PuntuacionSemanal'] + total_pts
                        nueva_total = row['PuntuacionTotal'] + total_pts
                        cur.execute("""
                            UPDATE HUB_UserScores
                            SET PuntuacionSemanal = %s, PuntuacionTotal = %s,
                                Nivel = CASE
                                    WHEN %s >= 3500 THEN 'Diamante'
                                    WHEN %s >= 1500 THEN 'Oro'
                                    WHEN %s >= 500 THEN 'Plata'
                                    ELSE 'Bronce'
                                END,
                                UltimoActivo = GETDATE(), FechaCalculo = GETDATE()
                            WHERE IdUsuario = %s
                        """, (nueva_semanal, nueva_total, nueva_total, nueva_total, nueva_total, uid))
                    else:
                        cur.execute("""
                            INSERT INTO HUB_UserScores (IdUsuario, PuntuacionSemanal, PuntuacionTotal, Nivel, UltimoActivo)
                            VALUES (%s, %s, %s, CASE
                                WHEN %s >= 3500 THEN 'Diamante'
                                WHEN %s >= 1500 THEN 'Oro'
                                WHEN %s >= 500 THEN 'Plata'
                                ELSE 'Bronce'
                            END, GETDATE())
                        """, (uid, max(total_pts,0), max(total_pts,0), total_pts, total_pts, total_pts))
                    puntos_registrados += 1

                conn.commit()
                print(f"[legends] Reporte {id_reporte}: {puntos_por_persona} pts × {puntos_registrados} ingenieros ({horas_netas:.1f}h netas, {num_participantes} participantes)")
                return puntos_por_persona * puntos_registrados
    except Exception as e:
        print(f"Error registrando puntos servicio para reporte {id_reporte}: {e}")
        return 0


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
    """Guarda la lista de técnicos adicionales (nombres) para un reporte.
    Primero elimina los existentes y luego inserta los nuevos."""
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


def get_siguiente_folio_cotizacion_servproy():
    """Genera el folio contiguo CSP-AAAA-NNNN para Cotizaciones Servicios y Proyectos."""
    anio = datetime.date.today().year
    prefijo = f"CSP-{anio}-"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM Indice_CotizacionesServProy WHERE Folio LIKE %s", (prefijo + "%",))
                count = int(cur.fetchone()[0] or 0)
                return f"{prefijo}{count + 1:04d}"
    except Exception as e:
        print(f"get_siguiente_folio_cotizacion_servproy error: {e}")
        return f"{prefijo}0001"


def log_historial_cotizacion_servproy(folio, usuario, accion):
    """Registra una entrada breve en el historial de modificaciones de una cotización."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO Historial_CotizacionesServProy (Folio, Usuario, Accion)
                    VALUES (%s, %s, %s)
                """, (folio, usuario or "Sistema", accion))
                conn.commit()
                return True
    except Exception as e:
        print(f"log_historial_cotizacion_servproy error: {e}")
        return False


def get_historial_cotizacion_servproy(folio):
    """Historial breve de cambios de una cotización (más reciente primero)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Folio, Usuario, Accion, Fecha
                    FROM Historial_CotizacionesServProy
                    WHERE Folio = %s
                    ORDER BY Fecha DESC, Id DESC
                """, (folio.strip(),))
                return cur.fetchall()
    except Exception as e:
        print(f"get_historial_cotizacion_servproy error: {e}")
        return []


def create_cotizacion_servproy(id_cliente, contacto, descripcion, elaboro, realizado,
                               orden_compra, factura, id_calculo=None, notas=None):
    """Crea el encabezado de una cotización de Servicios y Proyectos. Devuelve el folio."""
    folio = get_siguiente_folio_cotizacion_servproy()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO Indice_CotizacionesServProy
                        (Folio, IdCliente, Contacto, Descripcion, Elaboro, Realizado,
                         OrdenCompraCliente, Factura, IdCalculoFuente, Color, ActualizadoPor, Notas)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
                """, (folio, (id_cliente or "").strip().upper(), (contacto or "").strip(),
                      (descripcion or "").strip(), elaboro or "", realizado or "",
                      (orden_compra or "").strip(), (factura or "").strip(),
                      id_calculo if id_calculo else None, elaboro, (notas or "").strip()))
                conn.commit()
                log_user_activity("Cotizaciones Serv y Proy",
                                  f"Creó cotización {folio} para cliente {id_cliente}")
                return folio
    except Exception as e:
        print(f"Error creating cotizacion servproy: {e}")
        return None


@_cache_data(ttl=300)
def get_indice_cotizaciones_servproy():
    """Índice con todos los campos de las cotizaciones + nombre de cliente + folio del cálculo fuente."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT c.Id, c.Folio, c.IdCliente, COALESCE(c.Contacto,'') AS Contacto,
                           c.FechaCreacion, c.FechaActualizado, COALESCE(c.Descripcion,'') AS Descripcion,
                           COALESCE(c.Elaboro,'') AS Elaboro, COALESCE(c.Realizado,'') AS Realizado,
                           COALESCE(c.OrdenCompraCliente,'') AS OrdenCompraCliente,
                           COALESCE(c.Factura,'') AS Factura, COALESCE(c.IdCalculoFuente,'') AS IdCalculoFuente,
                           COALESCE(cl.Cliente, c.IdCliente) AS ClienteNombre,
                           COALESCE(calc.FolioCalculo,'') AS FolioCalculo,
                           c.Color, c.Subtotal, c.IVA, c.Total, COALESCE(c.ActualizadoPor,'') AS ActualizadoPor,
                           COALESCE(c.Notas,'') AS Notas
                    FROM Indice_CotizacionesServProy c
                    LEFT JOIN clientes cl ON cl.IdCliente = c.IdCliente
                    LEFT JOIN Indice_calculocotizacion calc ON calc.Id = c.IdCalculoFuente
                    ORDER BY c.Id DESC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_indice_cotizaciones_servproy error: {e}")
        return []


def get_cotizacion_servproy_by_folio(folio):
    """Devuelve el encabezado de una cotización por folio."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, Folio, IdCliente, COALESCE(Contacto,'') AS Contacto, FechaCreacion,
                           FechaActualizado, COALESCE(Descripcion,'') AS Descripcion,
                           COALESCE(Elaboro,'') AS Elaboro, COALESCE(Realizado,'') AS Realizado,
                           COALESCE(OrdenCompraCliente,'') AS OrdenCompraCliente,
                           COALESCE(Factura,'') AS Factura, COALESCE(IdCalculoFuente,'') AS IdCalculoFuente,
                           Color, Subtotal, IVA, Total, COALESCE(ActualizadoPor,'') AS ActualizadoPor,
                           COALESCE(Notas,'') AS Notas
                    FROM Indice_CotizacionesServProy
                    WHERE Folio = %s
                """, (folio.strip(),))
                return cur.fetchone()
    except Exception as e:
        print(f"get_cotizacion_servproy_by_folio error: {e}")
        return None


def update_cotizacion_servproy(folio, contacto, descripcion, realizado, orden_compra,
                               factura, color, usuario, notas=None):
    """Actualiza los campos editables y el Color (semáforo de estatus). Registra historial."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_CotizacionesServProy
                    SET Contacto = %s, Descripcion = %s, Realizado = %s, OrdenCompraCliente = %s,
                        Factura = %s, Color = %s, ActualizadoPor = %s, FechaActualizado = GETDATE(),
                        Notas = %s
                    WHERE Folio = %s
                """, ((contacto or "").strip(), (descripcion or "").strip(), (realizado or "").strip(),
                      (orden_compra or "").strip(), (factura or "").strip(), int(color or 0),
                      usuario, (notas or "").strip(), folio.strip()))
                conn.commit()
                log_historial_cotizacion_servproy(folio, usuario, "Modificó cotización")
                return True
    except Exception as e:
        print(f"Error updating cotizacion servproy: {e}")
        return False


def update_cotizacion_realizado(folio, realizado, usuario):
    """Actualiza únicamente el campo Realizado (usuarios que realizaron el servicio)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_CotizacionesServProy
                    SET Realizado = %s, ActualizadoPor = %s, FechaActualizado = GETDATE()
                    WHERE Folio = %s
                """, ((realizado or "").strip(), usuario, folio.strip()))
                conn.commit()
                log_historial_cotizacion_servproy(folio, usuario, "Actualizó realizado del servicio")
                return True
    except Exception as e:
        print(f"Error updating realizado cotizacion servproy: {e}")
        return False


def update_cotizacion_oc_factura(folio, orden_compra, factura, usuario):
    """Actualiza únicamente la Orden de Compra del cliente y la Factura ECCSA de una cotización."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_CotizacionesServProy
                    SET OrdenCompraCliente = %s, Factura = %s, ActualizadoPor = %s, FechaActualizado = GETDATE()
                    WHERE Folio = %s
                """, ((orden_compra or "").strip(), (factura or "").strip(), usuario, folio.strip()))
                conn.commit()
                log_historial_cotizacion_servproy(folio, usuario, "Actualizó OC/factura del cliente")
                return True
    except Exception as e:
        print(f"Error updating oc/factura cotizacion servproy: {e}")
        return False


def delete_cotizacion_servproy(folio):
    """Elimina una cotización (las partidas caen por FK con CASCADE)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM Indice_CotizacionesServProy WHERE Folio = %s", (folio.strip(),))
                conn.commit()
                log_user_activity("Cotizaciones Servicios y Proyectos",
                                  f"Eliminó cotización {folio}")
                return True
    except Exception as e:
        print(f"Error deleting cotizacion servproy: {e}")
        return False


def get_partidas_cotizacion_servproy(folio):
    """Partidas de una cotización ordenadas por número de partida."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT p.Id, p.Partida, p.Tipo, p.Cantidad, COALESCE(p.Descripcion,'') AS Descripcion,
                           COALESCE(p.Modelo,'') AS Modelo, COALESCE(p.Proveedor,'') AS Proveedor,
                           COALESCE(p.TiempoEntrega,'') AS TiempoEntrega,
                           p.PrecioVentaUnit, p.PrecioVentaTotal
                    FROM CotizacionServProyPartidas p
                    INNER JOIN Indice_CotizacionesServProy c ON c.Id = p.IdCotizacion
                    WHERE c.Folio = %s
                    ORDER BY p.Partida ASC, p.Id ASC
                """, (folio.strip(),))
                return cur.fetchall()
    except Exception as e:
        print(f"get_partidas_cotizacion_servproy error: {e}")
        return []


def add_partida_cotizacion_servproy(folio, cantidad, descripcion, modelo, proveedor,
                                    tiempo_entrega, precio_venta_unit, tipo="MATERIAL"):
    """Agrega una partida manual y recalcula los totales."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM Indice_CotizacionesServProy WHERE Folio = %s", (folio.strip(),))
                row = cur.fetchone()
                if not row:
                    return False
                id_cot = row[0]
                cur.execute("SELECT ISNULL(MAX(Partida), 0) + 1 FROM CotizacionServProyPartidas WHERE IdCotizacion = %s",
                            (id_cot,))
                partida = int(cur.fetchone()[0] or 1)
                cantidad = float(cantidad or 0)
                precio_total = round(float(precio_venta_unit or 0) * cantidad, 2)
                cur.execute("""
                    INSERT INTO CotizacionServProyPartidas
                        (IdCotizacion, Partida, Tipo, Cantidad, Descripcion, Modelo,
                         Proveedor, TiempoEntrega, PrecioVentaUnit, PrecioVentaTotal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (id_cot, partida, tipo, cantidad, (descripcion or "").strip(),
                      (modelo or "").strip(), (proveedor or "").strip(),
                      (tiempo_entrega or "").strip(), float(precio_venta_unit or 0), precio_total))
                conn.commit()
                recalcular_totales_cotizacion_servproy(folio)
                return True
    except Exception as e:
        print(f"Error agregando partida servproy: {e}")
        return False


def update_partida_cotizacion_servproy(folio, partida, tipo, cantidad, descripcion, modelo,
                                       proveedor, tiempo_entrega, precio_venta_unit):
    """Actualiza una partida existente y recalcula los totales."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM Indice_CotizacionesServProy WHERE Folio = %s", (folio.strip(),))
                row = cur.fetchone()
                if not row:
                    return False
                id_cot = row[0]
                cantidad = float(cantidad or 0)
                precio_total = float(precio_venta_unit or 0) * cantidad
                cur.execute("""
                    UPDATE CotizacionServProyPartidas
                    SET Tipo = %s, Cantidad = %s, Descripcion = %s, Modelo = %s, Proveedor = %s,
                        TiempoEntrega = %s, PrecioVentaUnit = %s, PrecioVentaTotal = %s
                    WHERE IdCotizacion = %s AND Partida = %s
                """, (tipo, cantidad, (descripcion or "").strip(), (modelo or "").strip(),
                      (proveedor or "").strip(), (tiempo_entrega or "").strip(),
                      float(precio_venta_unit or 0), precio_total, id_cot, int(partida)))
                conn.commit()
                recalcular_totales_cotizacion_servproy(folio)
                return True
    except Exception as e:
        print(f"Error actualizando partida servproy: {e}")
        return False


def delete_partida_cotizacion_servproy(folio, partida):
    """Elimina una partida por número y recalcula los totales."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM Indice_CotizacionesServProy WHERE Folio = %s", (folio.strip(),))
                row = cur.fetchone()
                if not row:
                    return False
                id_cot = row[0]
                cur.execute("DELETE FROM CotizacionServProyPartidas WHERE IdCotizacion = %s AND Partida = %s",
                            (id_cot, int(partida)))
                conn.commit()
                recalcular_totales_cotizacion_servproy(folio)
                return True
    except Exception as e:
        print(f"Error borrando partida servproy: {e}")
        return False


def recalcular_totales_cotizacion_servproy(folio):
    """Suma PrecioVentaTotal de las partidas → Subtotal, IVA 16% y Total del encabezado."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_CotizacionesServProy
                    SET Subtotal = ISNULL((
                            SELECT SUM(ISNULL(PrecioVentaTotal, 0))
                            FROM CotizacionServProyPartidas p
                            WHERE p.IdCotizacion = Indice_CotizacionesServProy.Id), 0),
                        IVA = ROUND(ISNULL((
                            SELECT SUM(ISNULL(PrecioVentaTotal, 0))
                            FROM CotizacionServProyPartidas p
                            WHERE p.IdCotizacion = Indice_CotizacionesServProy.Id), 0) * 0.16, 2),
                        Total = ISNULL((
                            SELECT SUM(ISNULL(PrecioVentaTotal, 0))
                            FROM CotizacionServProyPartidas p
                            WHERE p.IdCotizacion = Indice_CotizacionesServProy.Id), 0) * 1.16,
                        FechaActualizado = GETDATE()
                    WHERE Folio = %s
                """, (folio.strip(),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error recalculando totales servproy: {e}")
        return False

def get_calculos_sin_cotizacion(id_cliente=None):
    """Cálculos aún NO convertidos a cotización (IdCotizacionFuente vacío).
    Si id_cliente se indica, filtra sólo cálculos de ese cliente."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if id_cliente:
                    cur.execute("""
                        SELECT Id, FolioCalculo, IdCliente, COALESCE(Contacto,'') AS Contacto,
                               COALESCE(Descripcion,'') AS Descripcion, COALESCE(Elaboro,'') AS Elaboro,
                               Estatus, TasaDolar, Subtotal, IVA, Total
                        FROM Indice_calculocotizacion
                        WHERE LTRIM(RTRIM(ISNULL(IdCotizacionFuente,''))) = ''
                          AND LTRIM(RTRIM(ISNULL(IdCliente,''))) = %s
                        ORDER BY FechaActualizado DESC
                    """, (id_cliente.strip().upper(),))
                else:
                    cur.execute("""
                        SELECT Id, FolioCalculo, IdCliente, COALESCE(Contacto,'') AS Contacto,
                               COALESCE(Descripcion,'') AS Descripcion, COALESCE(Elaboro,'') AS Elaboro,
                               Estatus, TasaDolar, Subtotal, IVA, Total
                        FROM Indice_calculocotizacion
                        WHERE LTRIM(RTRIM(ISNULL(IdCotizacionFuente,''))) = ''
                        ORDER BY FechaActualizado DESC
                    """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_calculos_sin_cotizacion error: {e}")
        return []


def copiar_items_calculo_a_cotizacion(folio, calc_id, usuario=None):
    """Copia los items del cálculo (cantidades de venta) como partidas de la cotización."""
    try:
        items = get_items_calculo(calc_id)
        utilizados = 0
        for it in items:
            if float(it.get('Cantidad') or 0) <= 0:
                continue
            tipo = it.get('Tipo') or 'MATERIAL'
            if tipo not in ('SERVICIO', 'MATERIAL'):
                tipo = 'MATERIAL'
            precio_total = float(it.get('PrecioVentaTotal') or 0)
            cantidad = float(it.get('Cantidad') or 0)
            precio_unit = round(precio_total / cantidad, 2) if cantidad else float(it.get('PrecioVentaUnit') or 0)
            add_partida_cotizacion_servproy(
                folio, cantidad, it.get('Descripcion'), it.get('Modelo'),
                it.get('Proveedor'), it.get('TiempoEntrega'), precio_unit, tipo=tipo)
            utilizados += 1
        recalcular_totales_cotizacion_servproy(folio)
        log_historial_cotizacion_servproy(folio, usuario or "Sistema",
            f"Copió {utilizados} partidas del cálculo {calc_id}")
        return True
    except Exception as e:
        print(f"copiar_items_calculo_a_cotizacion error: {e}")
        return False


def copiar_items_calculo_seleccion_a_cotizacion(folio, calc_id, ids, usuario=None):
    """Copia del cálculo únicamente los ítems cuyos Id están en la lista `ids` (Id es único por ítem)."""
    try:
        items = get_items_calculo(calc_id)
        conjunto = {int(i) for i in ids}
        utilizados = set()
        for it in items:
            if int(it.get('Id') or 0) not in conjunto:
                continue
            if float(it.get('Cantidad') or 0) <= 0:
                continue
            tipo = it.get('Tipo') or 'MATERIAL'
            if tipo not in ('SERVICIO', 'MATERIAL'):
                tipo = 'MATERIAL'
            precio_total = float(it.get('PrecioVentaTotal') or 0)
            cantidad = float(it.get('Cantidad') or 0)
            precio_unit = round(precio_total / cantidad, 2) if cantidad else float(it.get('PrecioVentaUnit') or 0)
            add_partida_cotizacion_servproy(
                folio, cantidad, it.get('Descripcion'), it.get('Modelo'),
                it.get('Proveedor'), it.get('TiempoEntrega'), precio_unit, tipo=tipo)
            utilizados.add(int(it.get('Id') or 0))
        recalcular_totales_cotizacion_servproy(folio)
        if utilizados:
            log_historial_cotizacion_servproy(folio, usuario or "Sistema",
                f"Copió {len(utilizados)} partidas seleccionadas del cálculo {calc_id}")
        return bool(utilizados)
    except Exception as e:
        print(f"copiar_items_calculo_seleccion_a_cotizacion error: {e}")
        return False


def copiar_item_calculo_a_cotizacion(folio, calc_id, id_item, descripcion_override=None, usuario=None):
    """Copia UN ítem del cálculo (por Id) como partida, opcionalmente con descripción propia (p.ej. generada por IA)."""
    try:
        items = get_items_calculo(calc_id)
        it = next((x for x in items if int(x.get('Id') or 0) == int(id_item)), None)
        if not it:
            return False
        if float(it.get('Cantidad') or 0) <= 0:
            return False
        tipo = it.get('Tipo') or 'MATERIAL'
        if tipo not in ('SERVICIO', 'MATERIAL'):
            tipo = 'MATERIAL'
        precio_total = float(it.get('PrecioVentaTotal') or 0)
        cantidad = float(it.get('Cantidad') or 0)
        precio_unit = round(precio_total / cantidad, 2) if cantidad else float(it.get('PrecioVentaUnit') or 0)
        desc = (descripcion_override or '').strip() or it.get('Descripcion')
        add_partida_cotizacion_servproy(
            folio, cantidad, desc, it.get('Modelo'),
            it.get('Proveedor'), it.get('TiempoEntrega'), precio_unit, tipo=tipo)
        log_historial_cotizacion_servproy(folio, usuario or "Sistema",
            f"Copió la partida {id_item} del cálculo {calc_id}")
        return True
    except Exception as e:
        print(f"copiar_item_calculo_a_cotizacion error: {e}")
        return False


def marcar_calculo_cotizado(calc_id, folio_cotizacion):
    """Marca el cálculo fuente como ya cotizado (IdCotizacionFuente = folio de la cotización)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_calculocotizacion
                    SET IdCotizacionFuente = %s, FechaActualizado = GETDATE()
                    WHERE Id = %s
                """, (folio_cotizacion, int(calc_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"marcar_calculo_calculado error: {e}")
        return False


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

def clone_cotizacion_servproy(folio, usuario=None):
    """Duplica una cotización de Servicios y Proyectos con sus partidas,
    generando un folio nuevo (Color 0 = pendiente)."""
    try:
        original = get_cotizacion_servproy_by_folio(folio)
        if not original:
            return None
        nuevo_folio = create_cotizacion_servproy(
            original.get('IdCliente'), original.get('Contacto'), original.get('Descripcion'),
            usuario or original.get('Elaboro'), original.get('Realizado'),
            original.get('OrdenCompraCliente'), "",
            original.get('IdCalculoFuente') if original.get('IdCalculoFuente') else None
        )
        if not nuevo_folio:
            return None
        for p in get_partidas_cotizacion_servproy(folio):
            add_partida_cotizacion_servproy(
                nuevo_folio, p['Cantidad'], p['Descripcion'], p['Modelo'], p['Proveedor'],
                p['TiempoEntrega'], p['PrecioVentaUnit'], tipo=p['Tipo'])
        log_historial_cotizacion_servproy(nuevo_folio, usuario or "Sistema",
            f"Clonó cotización desde {folio}")
        return nuevo_folio
    except Exception as e:
        print(f"clone_cotizacion_servproy error: {e}")
        return None

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
                conn.commit()
                return True
    except Exception as e:
        print(f"Error logging activity: {e}")
        return False

def cleanup_old_activity_log():
    """Elimina registros del activity log mayores a 3 meses. Llamar periódicamente."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM HUB_ActivityLog 
                    WHERE FechaHora < DATEADD(month, -3, GETDATE())
                """)
                conn.commit()
    except Exception:
        pass

@_cache_data(ttl=300)
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

@_cache_data(ttl=300)
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
                # Se compara por FechaHora redondeada a día, así cualquier camino
                # (diálogo, página, worker) no puede registrar dos veces el mismo día.
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

                # Alertas Telegram y WhatsApp (disparo silencioso: que falle
                # un aviso no puede hacer fallar el registro de kilometros)
                try:
                    from telegram_alerts import alertar_kilometros
                    alertar_kilometros(id_automovil, kilometros, id_usuario)
                except Exception:
                    pass
                try:
                    from openwa_alerts import alertar_kilometros as alertar_km_whatsapp
                    alertar_km_whatsapp(id_automovil, kilometros, id_usuario)
                except Exception:
                    pass

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

def send_email_with_multiple_pdfs(to_email, subject, body, attachments, sender_name, sender_email, cc_email=None, html_body=None, inline_images=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    from email.mime.base import MIMEBase
    from email import encoders
    
    # 1. Load email configuration
    config = get_email_config()
    
    # 2. Build message
    is_html = bool(html_body)
    html_msg = html_body if is_html else None

    # Siempre usamos un contenedor raíz correcto:
    #  - Con adjuntos  -> multipart/mixed (los archivos van al nivel raíz, no como
    #    "alternativas" del cuerpo; muchos clientes estrictos ignoran adjuntos dentro
    #    de multipart/alternative).
    #  - Sin adjuntos   -> alternative (o related si hay HTML con imágenes inline).
    has_attachments = bool(attachments)
    has_inline = bool(inline_images)

    if has_attachments:
        msg = MIMEMultipart('mixed')
    elif is_html:
        msg = MIMEMultipart('related')
    else:
        msg = MIMEMultipart('alternative')

    msg['From'] = f"{sender_name} <{config['username']}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    if sender_email:
        msg['Reply-To'] = sender_email

    cc_list = []
    if cc_email:
        msg['Cc'] = cc_email
        cc_list.append(cc_email)

    if has_attachments:
        # Sub-contenedor con el cuerpo (texto plano y, si aplica, HTML con imágenes)
        if is_html:
            body_container = MIMEMultipart('related')
        else:
            body_container = MIMEMultipart('alternative')
        body_container.attach(MIMEText(body, 'plain', 'utf-8'))
        if is_html:
            body_container.attach(MIMEText(html_msg, 'html', 'utf-8'))
# Imágenes inline dentro del mismo contenedor related que el HTML
        inline_images = inline_images or []
        for idx, (img_bytes, img_name) in enumerate(inline_images):
            inline = MIMEImage(img_bytes, _subtype='png')
            inline.add_header('Content-ID', f'<image_{idx}>')
            inline.add_header('Content-Disposition', 'inline')
            body_container.attach(inline)
        msg.attach(body_container)
    else:
        # Sin adjuntos: el cuerpo va directo al contenedor raíz
        body_type = 'html' if is_html else 'plain'
        msg.attach(MIMEText(html_msg if is_html else body, body_type, 'utf-8'))
        # Imágenes inline (HTML sin adjuntos)
        inline_images = inline_images or []
        for idx, (img_bytes, img_name) in enumerate(inline_images):
            inline = MIMEImage(img_bytes, _subtype='png')
            img_cid = f"image_{idx}"
            inline.add_header('Content-ID', f'<{img_cid}>')
            inline.add_header('Content-Disposition', 'inline')
            msg.attach(inline)

    # Attach all regular files (nivel raíz del mixed)
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
                    "SELECT Id, Email, Nombre, Activo, Nickname, "
                    "AccesoCotizaciones, AccesoVM, AccesoConfiguracion, AccesoUsuarios, "
                    "AccesoReportes, AccesoRegistroReportes, AccesoCotizacionesReportes, "
                    "AccesoClientes, AccesoRegistroKilometros, AccesoAutomoviles, AccesoVacaciones, FechaIngreso, "
                    "AccesoConfigurarCorreo, AccesoConfigAI, Notificaciones, AccesoMisVacaciones, AccesoHorasExtras, AccesoMisHorasExtras, AccesoOxxoGas, AccesoValesOxxoGas, AccesoRegistroTicketOxxoGas, AccesoEdicionBD, AccesoNominas, AccesoInventario, AccesoCalculo, AccesoProveedores, AccesoOC, AccesoTelegram "
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

@_cache_data(ttl=300)
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
                        SELECT TOP 1000 r.Id, r.IdUsuario, r.Fecha, r.Tipo, r.Notas, r.FechaRegistro, u.Nombre AS UsuarioNombre
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

def is_mexican_holiday(d):
    # Fixed holidays in Mexico (Federal Labor Law Art. 74)
    fixed = [
        (1, 1),   # Año Nuevo
        (5, 1),   # Día del Trabajo
        (9, 16),  # Día de la Independencia
        (12, 25), # Navidad
    ]
    if (d.month, d.day) in fixed:
        return True

    # Floating holidays:
    # 1. Constitución: First Monday of February (day <= 7)
    if d.month == 2 and d.weekday() == 0:
        if d.day <= 7:
            return True
    # 2. Benito Juárez: Third Monday of March (15 <= day <= 21)
    if d.month == 3 and d.weekday() == 0:
        if 15 <= d.day <= 21:
            return True
    # 3. Revolución: Third Monday of November (15 <= day <= 21)
    if d.month == 11 and d.weekday() == 0:
        if 15 <= d.day <= 21:
            return True

    # 4. Transmisión del Poder Ejecutivo Federal: October 1 every 6 years (e.g. 2024, 2030)
    if d.month == 10 and d.day == 1:
        if d.year in [2024, 2030, 2036, 2042, 2048]:
            return True

    return False


def partition_hours(start_dt, end_dt, travel_hours, food_deduct):
    step = 0.25  # 15-minute steps
    current_dt = start_dt

    normal_int = 0.0
    double_int = 0.0
    triple_int = 0.0
    quad_int = 0.0

    while current_dt < end_dt:
        rem = (end_dt - current_dt).total_seconds() / 3600.0
        current_step = min(step, rem)

        d = current_dt.date()
        h = current_dt.hour

        # Check Sunday or Mexican holiday
        is_sun_or_hol = (current_dt.weekday() == 6) or is_mexican_holiday(d)

        if 0 <= h < 8:
            # 1. Between 12am and 8am -> Quadruple (4x)
            quad_int += current_step
        elif is_sun_or_hol:
            # 2. Sunday or Holiday -> Triple (3x)
            triple_int += current_step
        elif h >= 18:
            # 3. Weekday overtime after 6pm -> Double (2x)
            double_int += current_step
        else:
            # 4. Weekday normal 8am to 6pm -> Normal (1x)
            normal_int += current_step

        current_dt += datetime.timedelta(hours=current_step)

    # Apply food deduction if requested
    if food_deduct:
        deduct_left = 1.0  # 1 hour

        # Deduct from Normal (1x) first
        deduct_normal = min(normal_int, deduct_left)
        normal_int -= deduct_normal
        deduct_left -= deduct_normal

        # Deduct from Double (2x)
        if deduct_left > 0:
            deduct_double = min(double_int, deduct_left)
            double_int -= deduct_double
            deduct_left -= deduct_double

        # Deduct from Triple (3x)
        if deduct_left > 0:
            deduct_triple = min(triple_int, deduct_left)
            triple_int -= deduct_triple
            deduct_left -= deduct_triple

        # Deduct from Quadruple (4x)
        if deduct_left > 0:
            deduct_quad = min(quad_int, deduct_left)
            quad_int -= deduct_quad
            deduct_left -= deduct_quad

    return {
        "normal": round(normal_int, 2),
        "double": round(double_int, 2),
        "triple": round(triple_int, 2),
        "quad": round(quad_int, 2),
        "travel": round(travel_hours, 2)
    }


def _hora_decimal(dt):
    """Convierte un datetime a hora decimal (ej. 9:30 -> 9.5)."""
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


def partition_horas_extras(start_dt, end_dt, travel_hours):
    """Calcula las HORAS EXTRA respetando la jornada base de ECCSA:
    - Lunes a viernes: 9:00 a 18:30 (incluye 1 hora de comida implícita en la jornada).
    - Sábados: 9:30 a 13:30 (sin comida).
    - Domingo o festivo: todo el día se considera extra (triple).

    Solo las horas FUERA de la jornada base se contabilizan como horas extra:
    - 0:00 a 8:00 -> Cuádruple (4x)
    - Domingo o festivo -> Triple (3x)
    - Antes de la entrada o después de la salida -> Doble (2x)

    La comida ya está contenida dentro de la jornada (no se descuenta de las extras).
    El traslado se divide en 2 viajes (ida al inicio y regreso al final) que ocurren
    DENTRO de la ventana reportada, por lo que ya fueron contabilizados por las horas de la
    ventana. Solo se adiciona como extra (a 1x) el EXCEDENTE de traslado que caiga FUERA
    de la ventana y fuera de la jornada base, evitando el doble conteo.
    """
    step = 0.25  # pasos de 15 minutos
    current_dt = start_dt

    normal_int = 0.0
    double_int = 0.0
    triple_int = 0.0
    quad_int = 0.0

    def _jornada(dt):
        """Devuelve (hora_entrada, hora_salida) en decimal según el día. Domingo -> (None, None)."""
        wd = dt.weekday()
        if wd <= 4:      # Lunes a viernes
            return 9.0, 18.5
        elif wd == 5:    # Sábado
            return 9.5, 13.5
        return None, None

    def _suma_extra_fuera_jornada(ini, fin):
        """Suma (a 1x) las horas de [ini, fin] que caigan FUERA de la jornada base. No duplica
        horas que ya estén dentro de la ventana del reporte."""
        acc = 0.0
        cur = ini
        while cur < fin:
            rem = (fin - cur).total_seconds() / 3600.0
            cs = min(step, rem)
            d = cur.date()
            h_dec = _hora_decimal(cur)
            is_sun_or_hol = (cur.weekday() == 6) or is_mexican_holiday(d)
            if is_sun_or_hol:
                acc += cs
            else:
                entrada, salida = _jornada(cur)
                dentro = entrada is not None and entrada <= h_dec < salida
                if not dentro:
                    acc += cs
            cur += datetime.timedelta(hours=cs)
        return acc

    while current_dt < end_dt:
        rem = (end_dt - current_dt).total_seconds() / 3600.0
        current_step = min(step, rem)

        d = current_dt.date()
        h_dec = _hora_decimal(current_dt)
        is_sun_or_hol = (current_dt.weekday() == 6) or is_mexican_holiday(d)

        # Domingo o festivo: todo el día es extra a triple
        if is_sun_or_hol:
            triple_int += current_step
            current_dt += datetime.timedelta(hours=current_step)
            continue

        entrada, salida = _jornada(current_dt)

        # Dentro de la jornada base -> NO es hora extra
        if entrada is not None and entrada <= h_dec < salida:
            current_dt += datetime.timedelta(hours=current_step)
            continue

        # Fuera de jornada -> sí es hora extra
        if 0 <= h_dec < 8:
            quad_int += current_step      # madrugada (4x)
        else:
            double_int += current_step    # antes de la entrada o después de la salida (2x)

        current_dt += datetime.timedelta(hours=current_step)

    # Traslado: ya incluido en la ventana. Solo suma el excedente fuera de la ventana.
    ventana_horas = (end_dt - start_dt).total_seconds() / 3600.0
    excedente = max(0.0, travel_hours - ventana_horas)
    travel_extra = 0.0
    if excedente > 0:
        mitad = excedente / 2.0
        # Mitad del excedente antes del inicio y mitad después del fin de la ventana.
        travel_extra += _suma_extra_fuera_jornada(start_dt - datetime.timedelta(hours=mitad), start_dt)
        travel_extra += _suma_extra_fuera_jornada(end_dt, end_dt + datetime.timedelta(hours=mitad))

    return {
        "normal": round(normal_int, 2),
        "double": round(double_int, 2),
        "triple": round(triple_int, 2),
        "quad": round(quad_int, 2),
        "travel": round(travel_extra, 2)
    }


def get_hub_user_id_by_name(nombre):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (str(nombre).strip(),))
                r = cur.fetchone()
                return int(r[0]) if r else None
    except Exception as e:
        print(f"Error get_hub_user_id_by_name: {e}")
        return None


def sync_horas_extras_desde_reporte(id_reporte, id_usuario, folio, cliente, fecha, fecha_inicio, fecha_fin, tiempo_traslado, tiempo_comida):
    try:
        if not id_usuario or not fecha_inicio or not fecha_fin:
            return False
        res = partition_horas_extras(fecha_inicio, fecha_fin, float(tiempo_traslado or 0.0))
        horas_eq = round(res["normal"] + (2 * res["double"]) + (3 * res["triple"]) + (4 * res["quad"]) + res["travel"], 2)
        if isinstance(fecha_inicio, (datetime.date, datetime.datetime)):
            fecha_str = fecha_inicio.strftime("%Y-%m-%d")
        else:
            fecha_str = str(fecha_inicio)[:10]
        desc = f"Generado desde reporte {folio}" if folio else "Generado desde reporte de servicio"
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM HUB_HorasExtrasRegistros WHERE IdReporte = %s", (int(id_reporte),))
                existing = cur.fetchone()
                if existing:
                    cur.execute("""
                        UPDATE HUB_HorasExtrasRegistros
                        SET Fecha = %s, HorasExtrasCalculadas = %s, Descripcion = %s, Cliente = %s,
                            HoraEntrada = %s, HoraSalida = %s, HorasComida = %s, HorasTraslado = %s, Estatus = 'Pendiente'
                        WHERE Id = %s
                    """, (fecha_str, horas_eq, desc, cliente.strip() if cliente else None,
                          fecha_inicio.strftime("%H:%M:%S"), fecha_fin.strftime("%H:%M:%S"),
                          round(float(tiempo_comida or 0), 2), round(float(tiempo_traslado or 0.0), 2),
                          int(existing[0])))
                else:
                    cur.execute("""
                        INSERT INTO HUB_HorasExtrasRegistros (IdUsuario, Fecha, HoraEntrada, HoraSalida, HorasComida, HorasTraslado, HorasExtrasCalculadas, Descripcion, Cliente, Calificacion, Estatus, FechaRegistro, IdReporte)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, 'Pendiente', %s, %s)
                    """, (int(id_usuario), fecha_str,
                          fecha_inicio.strftime("%H:%M:%S"), fecha_fin.strftime("%H:%M:%S"),
                          round(float(tiempo_comida or 0), 2), round(float(tiempo_traslado or 0.0), 2),
                          horas_eq, desc, cliente.strip() if cliente else None,
                          now_mexico(), int(id_reporte)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error sync_horas_extras_desde_reporte: {e}")
        return False


def backfill_horas_extras_desde_reportes(tecnico=None) -> dict:
    """Genera los registros de horas extras para reportes ya existentes que no tienen registro vinculado.
    Si tecnico es None, procesa todos los reportes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if tecnico:
                    cur.execute("""
                        SELECT IdReporte, Folio, Cliente, Fecha, FechaHoraInicio, FechaHoraFin, TiempoTraslado, TiempoComida
                        FROM ReportesServicio
                        WHERE Tecnico = %s
                        ORDER BY Fecha ASC, IdReporte ASC
                    """, (str(tecnico).strip(),))
                else:
                    cur.execute("""
                        SELECT IdReporte, Folio, Cliente, Fecha, Tecnico, FechaHoraInicio, FechaHoraFin, TiempoTraslado, TiempoComida
                        FROM ReportesServicio
                        ORDER BY Fecha ASC, IdReporte ASC
                    """)
                reports = cur.fetchall()
        created = 0
        updated = 0
        skipped = 0
        for rep in reports:
            if not rep.get('FechaHoraInicio') or not rep.get('FechaHoraFin'):
                skipped += 1
                continue
            id_usuario = None
            if tecnico:
                id_usuario = get_hub_user_id_by_name(tecnico)
            else:
                cur2 = None
                try:
                    with get_connection() as conn2:
                        with conn2.cursor() as cur2:
                            cur2.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (str(rep['Tecnico']).strip(),))
                            row = cur2.fetchone()
                            id_usuario = int(row[0]) if row else None
                except Exception as e:
                    print(f"Error backfill lookup user: {e}")
            if not id_usuario:
                skipped += 1
                continue
            if isinstance(rep['Fecha'], (datetime.date, datetime.datetime)):
                fecha_str = rep['Fecha'].strftime("%Y-%m-%d")
            else:
                fecha_str = str(rep['Fecha'])[:10]
            ok = sync_horas_extras_desde_reporte(
                int(rep['IdReporte']), id_usuario, rep['Folio'], rep['Cliente'], fecha_str,
                rep['FechaHoraInicio'], rep['FechaHoraFin'], rep['TiempoTraslado'], rep['TiempoComida']
            )
            if ok:
                created += 1
            else:
                skipped += 1
        return {"created": created, "skipped": skipped}
    except Exception as e:
        print(f"Error backfill_horas_extras_desde_reportes: {e}")
        return {"created": 0, "skipped": 0}


@_cache_data(ttl=300)
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
                           HorasTraslado, HorasExtrasCalculadas, Descripcion, Cliente, Calificacion, Estatus, FechaRegistro, IdReporte
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

# ---------------------------------------------------------------------------
# PUSH NOTIFICATIONS
# ---------------------------------------------------------------------------

def _normalizar_vapid_privada(priv: str) -> str:
    """Deja la clave privada en el formato que `pywebpush` sí entiende.

    `pywebpush` (y `py-vapid`) quieren la privada como base64url de los 32 bytes
    CRUDOS de la clave. Si en la tabla queda una clave PEM o un DER en base64, el
    envío falla SIEMPRE y el error que sale no dice nada útil: parece que el push
    no llegó a ningún dispositivo.

    Ya se encontró una así en la base de pruebas (39 bytes de DER en lugar de 32).
    El de producción sí está bien, pero normalizar aquí evita que un `UPDATE` a
    mano en HUB_PushConfig rompa todos los avisos sin que nadie lo note.

    Si la clave no se puede interpretar, se devuelve tal cual: mejor un error de
    `pywebpush` que un "" silencioso.
    """
    import base64
    original = (priv or "").strip()
    if not original:
        return original

    def _b64d(texto):
        pad = "=" * ((4 - len(texto) % 4) % 4)
        return base64.urlsafe_b64decode((texto + pad).replace("-", "+").replace("_", "/"))

    # Caso normal: ya son 32 bytes crudos. Se devuelve sin tocar.
    try:
        if len(_b64d(original)) == 32:
            return original
    except Exception:
        pass

    try:
        from cryptography.hazmat.primitives import serialization
        # Dos entradas posibles: PEM en texto plano, o base64 de un DER.
        if "BEGIN" in original:
            clave = serialization.load_pem_private_key(original.encode(), password=None)
        else:
            clave = serialization.load_der_private_key(_b64d(original), password=None)
        crudo = clave.private_numbers().private_value.to_bytes(32, "big")
        return base64.urlsafe_b64encode(crudo).rstrip(b"=").decode()
    except Exception as e:
        print(f"vapid: no se pudo normalizar la clave privada ({e}); se usa tal cual")
        return original


@_cache_data(ttl=300)
def get_vapid_keys():
    """Return the VAPID keys for Web Push from config."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT VapidPublicKey, VapidPrivateKey FROM HUB_PushConfig WHERE Id = 1")
                row = cur.fetchone()
                if row:
                    return (row['VapidPublicKey'].strip(),
                            _normalizar_vapid_privada(row['VapidPrivateKey']))
    except Exception as e:
        print(f"get_vapid_keys error: {e}")
    return None, None

def save_push_subscription(user_email, endpoint, p256dh_key, auth_key):
    """Store a push subscription for a user."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Remove old subscription for same endpoint
                cur.execute("DELETE FROM HUB_PushSubscriptions WHERE Endpoint = %s", (endpoint,))
                cur.execute("""
                    INSERT INTO HUB_PushSubscriptions (UserEmail, Endpoint, P256dhKey, AuthKey, CreatedAt)
                    VALUES (%s, %s, %s, %s, GETDATE())
                """, (user_email.strip().lower(), endpoint, p256dh_key, auth_key))
                conn.commit()
                return True
    except Exception as e:
        print(f"save_push_subscription error: {e}")
        return False

def remove_push_subscription(endpoint):
    """Remove a push subscription by endpoint."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_PushSubscriptions WHERE Endpoint = %s", (endpoint,))
                conn.commit()
                return True
    except Exception as e:
        print(f"remove_push_subscription error: {e}")
        return False

def get_users_with_subscriptions_and_cars():
    """Return users who have push subscriptions, with their assigned car info."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT DISTINCT u.Email, u.Nombre, u.Notificaciones,
                           a.MarcaModelo AS AutoAsignado, a.Placas, a.Id AS IdAutomovil
                    FROM HUB_Users u
                    LEFT JOIN HUB_Automoviles a ON a.IdUsuarioAsignado = u.Id
                    WHERE u.Activo = 1
                    ORDER BY u.Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_users_with_subscriptions_and_cars error: {e}")
        return []

def get_push_subscriptions_by_emails(emails):
    """Return push subscriptions for specific user emails."""
    if not emails:
        return []
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                placeholders = ",".join("%s" for _ in emails)
                cur.execute(f"""
                    SELECT UserEmail, Endpoint, P256dhKey, AuthKey
                    FROM HUB_PushSubscriptions
                    WHERE UserEmail IN ({placeholders})
                """, list(emails))
                return cur.fetchall()
    except Exception as e:
        print(f"get_push_subscriptions_by_emails error: {e}")
        return []

def get_all_push_subscriptions():
    """Return all push subscriptions as list of dicts."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Id, UserEmail, Endpoint, P256dhKey, AuthKey FROM HUB_PushSubscriptions")
                return cur.fetchall()
    except Exception as e:
        print(f"get_all_push_subscriptions error: {e}")
        return []

def log_notification(titulo, mensaje, autor, enviado_a, recibido_por=0):
    """Log a sent notification in HUB_Notificaciones."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Notificaciones (Titulo, Message, Autor, EnviadoA, RecibidoPor)
                    VALUES (%s, %s, %s, %s, %s)
                """, (titulo, mensaje, autor, enviado_a, recibido_por))
                conn.commit()
    except Exception as e:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO HUB_Notificaciones (Titulo, Mensaje, Autor, EnviadoA, RecibidoPor)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (titulo, mensaje, autor, enviado_a, recibido_por))
                    conn.commit()
        except Exception as e2:
            print(f"log_notification error: {e2}")

def get_notification_history(limit=50):
    """Return last N sent notifications."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP %d Id, Titulo, Mensaje, Autor, FechaEnvio, EnviadoA, RecibidoPor
                    FROM HUB_Notificaciones ORDER BY FechaEnvio DESC
                """ % limit)
                return cur.fetchall()
    except Exception as e:
        try:
            with get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute("""
                        SELECT TOP %d Id, Titulo, Message AS Mensaje, Autor, FechaEnvio, EnviadoA, RecibidoPor
                        FROM HUB_Notificaciones ORDER BY FechaEnvio DESC
                    """ % limit)
                    return cur.fetchall()
        except Exception as e2:
            print(f"get_notification_history error: {e2}")
            return []

def send_push_notification(titulo, mensaje, extra=None):
    """Send a push notification to all subscribed users using pywebpush.

    `extra` son campos adicionales del payload (url, tag, badge_count, silent…).
    El service worker los decide por plataforma: `badge` es un NÚMERO en iOS y
    la URL de una IMAGEN en Android, así que no se manda `badge` sino el conteo
    y el SW lo traduce. Sin esto el aviso abre siempre la raíz y en Android el
    ícono se ve mal.
    """
    import json, base64
    from io import BytesIO
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("pywebpush not installed")
        return 0, 0

    pub_key, priv_key = get_vapid_keys()
    if not pub_key or not priv_key:
        print("VAPID keys not configured")
        return 0, 0

    subs = get_all_push_subscriptions()
    if not subs:
        print("No push subscriptions")
        return 0, 0

    vapid_claims = {
        "sub": "mailto:robot@ecc-sa.com.mx"
    }

    payload = json.dumps({"title": titulo, "body": mensaje, **(extra or {})})
    sent = 0
    failed = 0

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub['Endpoint'],
                    "keys": {
                        "p256dh": sub['P256dhKey'],
                        "auth": sub['AuthKey']
                    }
                },
                data=payload,
                vapid_private_key=priv_key,
                vapid_claims=vapid_claims
            )
            sent += 1
        except WebPushException as e:
            if e.response and e.response.status_code in (410, 404):
                remove_push_subscription(sub['Endpoint'])
            failed += 1
        except Exception as e:
            print(f"send push error for {sub.get('UserEmail','?')}: {e}")
            failed += 1

    return sent, failed

def send_push_notification_to_users(titulo, mensaje, user_emails, extra=None):
    """Send a push notification to specific users by email list.

    `extra` son campos adicionales del payload; ver `send_push_notification`.
    """
    import json
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("pywebpush not installed")
        return 0, 0

    pub_key, priv_key = get_vapid_keys()
    if not pub_key or not priv_key:
        print("VAPID keys not configured")
        return 0, 0

    subs = get_push_subscriptions_by_emails(user_emails)
    if not subs:
        return 0, 0

    vapid_claims = {
        "sub": "mailto:robot@ecc-sa.com.mx"
    }

    payload = json.dumps({"title": titulo, "body": mensaje, **(extra or {})})
    sent = 0
    failed = 0

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub['Endpoint'],
                    "keys": {
                        "p256dh": sub['P256dhKey'],
                        "auth": sub['AuthKey']
                    }
                },
                data=payload,
                vapid_private_key=priv_key,
                vapid_claims=vapid_claims
            )
            sent += 1
        except WebPushException as e:
            if e.response and e.response.status_code in (410, 404):
                remove_push_subscription(sub['Endpoint'])
            failed += 1
        except Exception as e:
            print(f"send push error for {sub.get('UserEmail','?')}: {e}")
            failed += 1

    return sent, failed


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


def get_db_tables():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_TYPE = 'BASE TABLE'
                    ORDER BY TABLE_NAME ASC
                """)
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error fetching DB tables: {e}")
        return []


def get_db_server_info():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT @@SERVERNAME AS ServerName,
                           @@VERSION AS Version,
                           DB_NAME() AS DatabaseName,
                           @@ROWCOUNT AS Dummy
                """)
                row = cur.fetchone()
                if row:
                    version = (row.get('Version') or '').split('\n')[0]
                    return {
                        'server': row.get('ServerName'),
                        'version': version,
                        'database': row.get('DatabaseName'),
                        'user': DB_CONFIG.get('user'),
                        'host': DB_CONFIG.get('server')
                    }
                return {}
    except Exception as e:
        print(f"Error fetching DB server info: {e}")
        return {}


def get_table_columns(table_name):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE,
                           COALESCE(CHARACTER_MAXIMUM_LENGTH, 0) AS MaxLen
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                """, (table_name,))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching columns for {table_name}: {e}")
        return []


def get_table_data(table_name, limit=500):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(f"SELECT TOP {int(limit)} * FROM [{table_name}]")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching data from {table_name}: {e}")
        return []


def get_table_primary_keys(table_name):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                    WHERE TABLE_NAME = %s AND OBJECTPROPERTY(OBJECT_ID(CONSTRAINT_SCHEMA + '.' + QUOTENAME(CONSTRAINT_NAME)), 'IsPrimaryKey') = 1
                    ORDER BY ORDINAL_POSITION
                """, (table_name,))
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error fetching primary keys for {table_name}: {e}")
        return []


def update_table_cell(table_name, pk_cols, pk_vals, column, value):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if not isinstance(pk_cols, list):
                    pk_cols = [pk_cols]
                if not isinstance(pk_vals, list):
                    pk_vals = [pk_vals]
                where_clause = " AND ".join(["[" + str(c) + "] = %s" for c in pk_cols])
                sql = f"UPDATE [{table_name}] SET [{column}] = %s WHERE {where_clause}"
                params = [value] + [v for v in pk_vals]
                cur.execute(sql, tuple(params))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating cell in {table_name}: {e}")
        return False


# --- NOMINAS MODULE FUNCTIONS (confidencial) ---

@_cache_data(ttl=300)
def get_nomina_datos():
    """Devuelve la lista simple de nómina: usuario, sueldo, utilidades y aguinaldo.

    Utilidades = 14 días de sueldo semanal, Aguinaldo = 15 días de sueldo semanal.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT u.Id, u.Nombre, ISNULL(n.Sueldo, 0) AS Sueldo
                    FROM HUB_Users u
                    LEFT JOIN HUB_Nomina n ON n.IdUsuario = u.Id
                    WHERE u.AccesoNominas = 1 AND u.Activo = 1
                    ORDER BY u.Nombre ASC
                """)
                rows = cur.fetchall()
        resultado = []
        for r in rows:
            sueldo = float(r['Sueldo'] or 0)
            resultado.append({
                'Id': r['Id'],
                'Nombre': r['Nombre'],
                'Sueldo': sueldo,
                'Utilidades': round((sueldo / 7) * 14, 2),
                'Aguinaldo': round((sueldo / 7) * 15, 2),
            })
        return resultado
    except Exception as e:
        print(f"Error fetching nomina data: {e}")
        return []


def update_nomina_sueldo(user_id, sueldo):
    """Actualiza (o inserta) el sueldo semanal de un usuario en HUB_Nomina."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    IF EXISTS (SELECT 1 FROM HUB_Nomina WHERE IdUsuario = %s)
                        UPDATE HUB_Nomina SET Sueldo = %s, FechaActualizado = GETDATE() WHERE IdUsuario = %s
                    ELSE
                        INSERT INTO HUB_Nomina (IdUsuario, Sueldo, FechaActualizado) VALUES (%s, %s, GETDATE())
                """, (int(user_id), float(sueldo), int(user_id), int(user_id), float(sueldo)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating nomina sueldo: {e}")
        return False


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


# ─── WebAuthn / Passkeys (esquema Field: CredentialId string + RpId) ────────
def register_passkey(user_id, credential_id, public_key, sign_count=0, label="Mi equipo", rp_id="ecc-sa.com.mx", transports=""):
    """Guarda una credencial WebAuthn (passkey) nueva para un usuario.
    credential_id y public_key son strings base64url (esquema Field)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_Passkeys WHERE CredentialId = %s", (credential_id,))
                cur.execute("""
                    INSERT INTO HUB_Passkeys (IdUsuario, CredentialId, PublicKey, SignCount, Transports, Etiqueta, RpId)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (int(user_id), credential_id, public_key, int(sign_count), transports, label[:100], rp_id))
                # Inicializar Nickname si está vacío (no sobrescribir existente)
                cur.execute("SELECT Nickname FROM HUB_Users WHERE Id = %s", (int(user_id),))
                u = cur.fetchone()
                nick = (u[0] or "").strip() if u else ""
                if not nick:
                    cur.execute("UPDATE HUB_Users SET Nickname = %s WHERE Id = %s", (label[:100], int(user_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"register_passkey error: {e}")
        return False


def get_passkeys_by_user(user_id):
    """Lista las credenciales WebAuthn registradas de un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, CredentialId, Etiqueta, FechaCreacion, UltimoUso, RpId, Transports
                    FROM HUB_Passkeys WHERE IdUsuario = %s ORDER BY FechaCreacion DESC
                """, (int(user_id),))
                return cur.fetchall()
    except Exception as e:
        print(f"get_passkeys_by_user error: {e}")
        return []


def find_user_by_passkey(credential_id):
    """
    Busca el usuario al que pertenece una credencial WebAuthn.
    credential_id es string base64url (esquema Field).
    Devuelve el dict del usuario (con permisos) o None.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT u.Id, u.Email, u.Nombre, u.Activo, u.Nickname,
                           u.AccesoCotizaciones, u.AccesoVM, u.AccesoConfiguracion, u.AccesoUsuarios,
                           u.AccesoReportes, u.AccesoRegistroReportes, u.AccesoCotizacionesReportes,
                           u.AccesoClientes, u.AccesoRegistroKilometros, u.AccesoAutomoviles,
                           u.AccesoVacaciones, u.AccesoMisVacaciones, u.AccesoHorasExtras,
                           u.AccesoMisHorasExtras, u.AccesoOxxoGas, u.AccesoValesOxxoGas,
                           u.AccesoRegistroTicketOxxoGas, u.AccesoEdicionBD, u.AccesoNominas,
                           u.FechaIngreso
                    FROM HUB_Passkeys p
                    INNER JOIN HUB_Users u ON u.Id = p.IdUsuario
                    WHERE p.CredentialId = %s AND u.Activo = 1
                """, (credential_id,))
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    'id': r['Id'],
                    'email': r['Email'],
                    'nombre': r['Nombre'],
                    'activo': bool(r['Activo']),
                    'nickname': (r.get('Nickname') or '').strip(),
                    'acceso_cotizaciones': bool(r['AccesoCotizaciones']),
                    'acceso_vm': bool(r['AccesoVM']),
                    'acceso_configuracion': bool(r['AccesoConfiguracion']),
                    'acceso_usuarios': bool(r['AccesoUsuarios']),
                    'acceso_reportes': bool(r['AccesoReportes']),
                    'acceso_registro_reportes': bool(r.get('AccesoRegistroReportes', False)),
                    'acceso_cotizaciones_reportes': bool(r.get('AccesoCotizacionesReportes', False)),
                    'acceso_clientes': bool(r.get('AccesoClientes', True)),
                    'acceso_registro_kilometros': bool(r.get('AccesoRegistroKilometros', True)),
                    'acceso_automoviles': bool(r.get('AccesoAutomoviles', True)),
                    'acceso_vacaciones': bool(r.get('AccesoVacaciones', False)),
                    'acceso_mis_vacaciones': bool(r.get('AccesoMisVacaciones', False)),
                    'acceso_horas_extras': bool(r.get('AccesoHorasExtras', False)),
                    'acceso_mis_horas_extras': bool(r.get('AccesoMisHorasExtras', False)),
                    'acceso_oxxogas': bool(r.get('AccesoOxxoGas', False)),
                    'acceso_vales_oxxogas': bool(r.get('AccesoValesOxxoGas', False)),
                    'acceso_registro_ticket_oxxogas': bool(r.get('AccesoRegistroTicketOxxoGas', False)),
                    'acceso_edicion_bd': bool(r.get('AccesoEdicionBD', False)),
                    'acceso_nominas': bool(r.get('AccesoNominas', False)),
                    'fecha_ingreso': r.get('FechaIngreso')
                }
    except Exception as e:
        print(f"find_user_by_passkey error: {e}")
        return None


def get_passkey_public_key(credential_id):
    """Obtiene la clave pública COSE de una credencial (string base64url)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PublicKey FROM HUB_Passkeys WHERE CredentialId = %s", (credential_id,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        print(f"get_passkey_public_key error: {e}")
        return None


def update_passkey_sign_count(credential_id, sign_count):
    """Actualiza el contador de uso de una credencial (protección anti-replay)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Passkeys
                    SET SignCount = %s, UltimoUso = GETDATE()
                    WHERE CredentialId = %s
                """, (int(sign_count), credential_id))
                conn.commit()
                return True
    except Exception as e:
        print(f"update_passkey_sign_count error: {e}")
        return False


def delete_passkey(user_id, credential_id):
    """Elimina una credencial WebAuthn de un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_Passkeys WHERE IdUsuario = %s AND CredentialId = %s",
                            (int(user_id), credential_id))
                conn.commit()
                return True
    except Exception as e:
        print(f"delete_passkey error: {e}")
        return False


def get_passkey_by_id(passkey_id, user_id=None):
    """Obtiene una passkey por Id (y opcionalmente verifica que pertenezca al usuario)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if user_id is not None:
                    cur.execute("SELECT * FROM HUB_Passkeys WHERE Id = %s AND IdUsuario = %s",
                                (int(passkey_id), int(user_id)))
                else:
                    cur.execute("SELECT * FROM HUB_Passkeys WHERE Id = %s", (int(passkey_id),))
                r = cur.fetchone()
                return dict(r) if r else None
    except Exception as e:
        print(f"get_passkey_by_id error: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# MÓDULO INVENTARIO
# ──────────────────────────────────────────────────────────────────────────────

@_cache_data(ttl=300)
def get_categorias_inventario():
    """Devuelve las categorías del catálogo de inventario (ordenadas alfabéticamente)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT IdCategoria, Categoria, FechaAlta FROM HUB_InventarioCategorias ORDER BY Categoria ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"get_categorias_inventario error: {e}")
        return []


def add_categoria_inventario(categoria):
    """Agrega una nueva categoría al catálogo de inventario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO HUB_InventarioCategorias (Categoria) VALUES (%s)", (categoria.strip(),))
                conn.commit()
                log_user_activity("Inventario", f"Agregó categoría: {categoria.strip()}")
                return True
    except Exception as e:
        print(f"add_categoria_inventario error: {e}")
        return False


def update_categoria_inventario(id_categoria, categoria):
    """Actualiza el nombre de una categoría de inventario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_InventarioCategorias SET Categoria = %s WHERE IdCategoria = %s",
                            (categoria.strip(), int(id_categoria)))
                conn.commit()
                log_user_activity("Inventario", f"Renombró categoría {id_categoria} a: {categoria.strip()}")
                return True
    except Exception as e:
        print(f"update_categoria_inventario error: {e}")
        return False


def delete_categoria_inventario(id_categoria):
    """Elimina una categoría de inventario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                ## Si la categoría tiene ítems asociados, se desvinculan (IdCategoria a NULL).
                cur.execute("UPDATE HUB_Inventario SET IdCategoria = NULL WHERE IdCategoria = %s", (int(id_categoria),))
                cur.execute("DELETE FROM HUB_InventarioCategorias WHERE IdCategoria = %s", (int(id_categoria),))
                conn.commit()
                log_user_activity("Inventario", f"Eliminó categoría {id_categoria}")
                return True
    except Exception as e:
        print(f"delete_categoria_inventario error: {e}")
        return False


@_cache_data(ttl=300)
def get_inventario():
    """Devuelve todos los ítems de inventario con su categoría (para visualización)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT i.IdInventario, i.IdCategoria, COALESCE(c.Categoria, '') AS Categoria,
                           i.Marca, i.Modelo, i.Descripcion, i.Ubicacion, i.Proveedor,
                           i.Cantidad, i.StockMinimo, i.Precio, i.FechaActualizado
                    FROM HUB_Inventario i
                    LEFT JOIN HUB_InventarioCategorias c ON i.IdCategoria = c.IdCategoria
                    ORDER BY i.Cantidad ASC, i.Modelo ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_inventario error: {e}")
        return []


def buscar_inventario(termino):
    """Busca ítems de inventario cuyo término coincida en cualquiera de las columnas de texto."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT i.IdInventario, i.IdCategoria, COALESCE(c.Categoria, '') AS Categoria,
                           i.Marca, i.Modelo, i.Descripcion, i.Ubicacion, i.Proveedor,
                           i.Cantidad, i.StockMinimo, i.Precio, i.FechaActualizado
                    FROM HUB_Inventario i
                    LEFT JOIN HUB_InventarioCategorias c ON i.IdCategoria = c.IdCategoria
                    WHERE i.Marca LIKE %s OR i.Modelo LIKE %s OR COALESCE(i.Descripcion, '') LIKE %s
                       OR COALESCE(i.Ubicacion, '') LIKE %s OR COALESCE(i.Proveedor, '') LIKE %s
                       OR COALESCE(c.Categoria, '') LIKE %s
                    ORDER BY i.Cantidad ASC, i.Modelo ASC
                """, ("%" + termino + "%", "%" + termino + "%", "%" + termino + "%",
                      "%" + termino + "%", "%" + termino + "%", "%" + termino + "%"))
                return cur.fetchall()
    except Exception as e:
        print(f"buscar_inventario error: {e}")
        return []


def get_inventario_foto(id_inventario):
    """Devuelve la foto (base64 data-URI) de un ítem, o None si no tiene."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Foto FROM HUB_Inventario WHERE IdInventario = %s", (int(id_inventario),))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        print(f"get_inventario_foto error: {e}")
        return None


def update_inventario_foto(id_inventario, foto):
    """Actualiza solo la foto de un ítem (agrega/cambia, base64 data-URI)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Inventario
                    SET Foto = %s, FechaActualizado = GETDATE()
                    WHERE IdInventario = %s
                """, (foto, int(id_inventario)))
                conn.commit()
                log_user_activity("Inventario", f"Actualizó la foto del ítem {id_inventario}")
                return True
    except Exception as e:
        print(f"update_inventario_foto error: {e}")
        return False


def add_inventario(id_categoria, marca, modelo, descripcion, cantidad, stock_minimo, ubicacion, proveedor, precio, foto=None):
    """Agrega un ítem nuevo de inventario (foto opcional, base64 data-URI)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Inventario (IdCategoria, Marca, Modelo, Descripcion, Cantidad, StockMinimo, Ubicacion, Proveedor, Precio, Foto)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (int(id_categoria) if id_categoria else None, marca.strip() if marca else None,
                      modelo.strip() if modelo else None, descripcion.strip() if descripcion else None,
                      int(cantidad or 0), int(stock_minimo or 0),
                      ubicacion.strip() if ubicacion else None,
                      proveedor.strip() if proveedor else None,
                      float(precio) if precio else None, foto))
                conn.commit()
                log_user_activity("Inventario", f"Agregó ítem: {modelo.strip() if modelo else 'sin modelo'}")
                return True
    except Exception as e:
        print(f"add_inventario error: {e}")
        return False


def update_inventario(id_inventario, id_categoria, marca, modelo, descripcion, cantidad, stock_minimo, ubicacion, proveedor, precio, foto=None):
    """Actualiza un ítem de inventario existente (foto opcional)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Inventario
                    SET IdCategoria = %s, Marca = %s, Modelo = %s, Descripcion = %s,
                        Cantidad = %s, StockMinimo = %s, Ubicacion = %s, Proveedor = %s,
                        Precio = %s, Foto = COALESCE(%s, Foto), FechaActualizado = GETDATE()
                    WHERE IdInventario = %s
                """, (int(id_categoria) if id_categoria else None, marca.strip() if marca else None,
                      modelo.strip() if modelo else None, descripcion.strip() if descripcion else None,
                      int(cantidad or 0), int(stock_minimo or 0),
                      ubicacion.strip() if ubicacion else None,
                      proveedor.strip() if proveedor else None,
                      float(precio) if precio else None, foto, int(id_inventario)))
                conn.commit()
                log_user_activity("Inventario", f"Actualizó ítem {id_inventario}: {modelo.strip() if modelo else ''}")
                return True
    except Exception as e:
        print(f"update_inventario error: {e}")
        return False


def delete_inventario(id_inventario):
    """Elimina un ítem de inventario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_Inventario WHERE IdInventario = %s", (int(id_inventario),))
                conn.commit()
                log_user_activity("Inventario", f"Eliminó ítem {id_inventario}")
                return True
    except Exception as e:
        print(f"delete_inventario error: {e}")
        return False


# =============================================================================
# MÓDULO NUEVO: Cálculos de Cotizaciones (Servicios y Materiales)
# =============================================================================

@_cache_data(ttl=300)
def get_tipo_cambio_default() -> float:
    """Devuelve el tipo de cambio USD/MXN guardado en configuración (0 si no existe)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'tipo_cambio_usd'")
                row = cur.fetchone()
                if row and row[0]:
                    return float(str(row[0]).strip())
    except Exception as e:
        print(f"get_tipo_cambio_default error: {e}")
    return 0.0


def set_tipo_cambio_default(valor):
    """Guarda (upsert) el tipo de cambio default de la app."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = 'tipo_cambio_usd'
                """, (str(valor).strip(),))
                if cur.rowcount == 0:
                    cur.execute("""
                        INSERT INTO HUB_Config (Clave, Valor) VALUES ('tipo_cambio_usd', %s)
                    """, (str(valor).strip(),))
                conn.commit()
                return True
    except Exception as e:
        print(f"set_tipo_cambio_default error: {e}")
        return False


def get_tipo_cambio_ultima_actualizacion():
    """Devuelve la fecha/hora en que se actualizó por última vez el tipo de cambio default."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Actualizado FROM HUB_Config WHERE Clave = 'tipo_cambio_usd'")
                row = cur.fetchone()
                if row:
                    return row[0]
    except Exception as e:
        print(f"get_tipo_cambio_ultima_actualizacion error: {e}")
    return None


def actualizar_tipo_cambio_auto(panel=0.0, oferta_pond=0.0):
    """Obtiene el tipo de cambio (Banxico con fallback open.er-api) y, si el guardado es
    más viejo que un día (∼23 h) o nunca se actualizó, lo guarda como default de la app.
    Devuelve (valor_resultante, fuente, actualizado)."""
    valor, fuente = fetch_tipo_cambio_oficial()
    actualizado = None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Actualizado FROM HUB_Config WHERE Clave = 'tipo_cambio_usd'")
                r = cur.fetchone()
                actualizado = r[0] if r else None
    except Exception:
        actualizado = None
    hace = None
    if actualizado:
        ref = actualizado if isinstance(actualizado, datetime.datetime) else datetime.datetime(1990, 1, 1)
        hace = now_mexico() - ref
    debe_actualizar = valor is not None and (hace is None or hace.days >= 1 or hace.total_seconds() > 82800)
    if debe_actualizar:
        guardado = valor + float(panel or 0.0) + float(oferta_pond or 0.0)
        set_tipo_cambio_default(round(guardado, 4))
    return valor, fuente, actualizado


def fetch_tipo_cambio_oficial():
    """Obtiene el tipo de cambio USD/MXN diario.

    Intenta primero Banxico (dólar FIX, serie SF51158 — cambio oficial publicado por Banxico),
    y si falla (red/token/404) hace fallback a la API gratuita open.er-api.com.
    Devuelve (valor, fuente) con valor numérico y 'Banxico'/'open.er-api' o (None, None)."""
    import urllib.request
    import json as _json
    hoy = datetime.date.today().isoformat()

    # 1) Intento Banxico (oficial)
    serie = "SF51158"  # Serie dólar FIX (tipo de cambio de referencia, diario)
    token = ""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'banxico_token'")
                r = cur.fetchone()
                if r:
                    token = (r[0] or "").strip()
    except Exception:
        pass
    if token:
        try:
            url = f"https://www.banxico.org.mx/SieAPIRest/service/sie/{serie}/datos/{hoy}/{hoy}?token={token}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            serie_datos = data["bmx"]["series"][0]["datos"]
            if serie_datos:
                dato = serie_datos[0].get("dato")
                if dato is not None and str(dato).strip():
                    val = float(str(dato).replace(",", ""))
                    if val > 0:
                        return val, "Banxico"
        except Exception as e:
            print(f"fetch_tipo_cambio_oficial.Banxico error: {e}")

    # 2) Fallback: open.er-api.com (gratuita, sin token, actualizada diario)
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/USD",
                                     headers={"User-Agent": "HUB-ECCSA/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        mxn = data["rates"].get("MXN")
        if mxn:
            return float(mxn), "open.er-api.com"
    except Exception as e:
        print(f"fetch_tipo_cambio_oficial.fallback error: {e}")

    return None, None


def _next_folio_calculo():
    """Genera el siguiente FolioCalculo: 'CALC-AAAA-NNNN' (numérico contiguo libre)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                anio = datetime.date.today().year
                prefix = f"CALC-{anio}-"
                i = 1
                while True:
                    cand = f"{prefix}{i:04d}"
                    cur.execute("SELECT COUNT(*) FROM Indice_calculocotizacion WHERE FolioCalculo = %s", (cand,))
                    if cur.fetchone()[0] == 0:
                        return cand
                    i += 1
    except Exception as e:
        print(f"_next_folio_calculo error: {e}")
        # Fallback: folio llamado por timestamp para no colisionar.
        return f"CALC-{datetime.date.today().year}-" + str(int(now_mexico().timestamp()))


def add_calculo_cotizacion(id_cliente, contacto, descripcion, departamento, elaboro, tasa_dolar=0.0):
    """Crea un cálculo nuevo (índice/encabezado). Devuelve el dict del cálculo o None."""
    try:
        folio = _next_folio_calculo()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO Indice_calculocotizacion
                        (FolioCalculo, IdCliente, Contacto, Descripcion, Departamento, Elaboro, TasaDolar)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (folio,
                      (id_cliente or "").strip().upper() or None,
                      (contacto or "").strip() or None,
                      (descripcion or "").strip() or None,
                      (departamento or "").strip() or None,
                      (elaboro or "").strip() or None,
                      float(tasa_dolar or 0.0)))
                conn.commit()
                cur.execute("SELECT Id FROM Indice_calculocotizacion WHERE FolioCalculo = %s", (folio,))
                r = cur.fetchone()
                if r:
                    log_user_activity("Calculos Cotizaciones", f"Creó cálculo {folio}")
                    return get_calculo_cotizacion(int(r[0]))
    except Exception as e:
        print(f"add_calculo_cotizacion error: {e}")
    return None


@_cache_data(ttl=300)
def get_calculos_cotizacion():
    """Lista todos los cálculos (para el índice)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, FolioCalculo, IdCliente, COALESCE(Contacto,'') AS Contacto,
                           COALESCE(Descripcion,'') AS Descripcion, COALESCE(Elaboro,'') AS Elaboro,
                           FechaCreacion, FechaActualizado, COALESCE(IdCotizacionFuente,'') AS IdCotizacionFuente,
                           Estatus, TasaDolar, SubtotalServicios, SubtotalMateriales, Subtotal, IVA, Total
                    FROM Indice_calculocotizacion
                    ORDER BY FechaActualizado DESC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_calculos_cotizacion error: {e}")
        return []


@_cache_data(ttl=300)
def get_calculos_cotizaciones():
    """Alias plural de get_calculos_cotizacion (usado por la vista)."""
    return get_calculos_cotizacion()


def get_calculo_cotizacion(identificador):
    """Devuelve un cálculo por Id o por FolioCalculo."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cond = "FolioCalculo = %s" if not str(identificador).isdigit() and not isinstance(identificador, int) else "Id = %s"
                cur.execute(f"""
                    SELECT Id, FolioCalculo, IdCliente, COALESCE(Contacto,'') AS Contacto,
                           COALESCE(Descripcion,'') AS Descripcion, COALESCE(Departamento,'') AS Departamento,
                           COALESCE(Elaboro,'') AS Elaboro, FechaCreacion, FechaActualizado,
                           COALESCE(IdCotizacionFuente,'') AS IdCotizacionFuente, Estatus, TasaDolar,
                           SubtotalServicios, SubtotalMateriales, Subtotal, IVA, Total
                    FROM Indice_calculocotizacion WHERE {cond}
                """.replace("{cond}", cond), (identificador,))
                return cur.fetchone()
    except Exception as e:
        print(f"get_calculo_cotizacion error: {e}")
    return None


def update_calculo_encabezado(calc_id, id_cliente, contacto, descripcion, departamento, tasa_dolar=None):
    """Actualiza el encabezado (índice) de un cálculo y marca la última modificación."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_calculocotizacion
                    SET IdCliente = %s, Contacto = %s, Descripcion = %s, Departamento = %s,
                        TasaDolar = COALESCE(%s, TasaDolar), FechaActualizado = GETDATE()
                    WHERE Id = %s
                """, ((id_cliente or "").strip().upper() or None,
                      (contacto or "").strip() or None, (descripcion or "").strip() or None,
                      (departamento or "").strip() or None,
                      float(tasa_dolar) if tasa_dolar is not None else None,
                      int(calc_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"update_calculo_encabezado error: {e}")
        return False


def set_calculo_estatus(calc_id, estatus):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Indice_calculocotizacion SET Estatus = %s, FechaActualizado = GETDATE() WHERE Id = %s
                """, (str(estatus).strip().upper(), int(calc_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"set_calculo_estatus error: {e}")
        return False


def delete_calculo_cotizacion(calc_id):
    """"Elimina un cálculo (los items se borran por cascada)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM Indice_calculocotizacion WHERE Id = %s", (int(calc_id),))
                conn.commit()
                log_user_activity("Calculos Cotizaciones", f"Eliminó cálculo id {calc_id}")
                return True
    except Exception as e:
        print(f"delete_calculo_cotizacion error: {e}")
        return False


def get_items_calculo(calc_id):
    """Devuelve los ítems (Servicios y Materiales) de un cálculo."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdCalculo, Numero, Tipo, COALESCE(Fuente,'') AS Fuente, IdReporteOrigen,
                           Cantidad, COALESCE(Descripcion,'') AS Descripcion, COALESCE(Modelo,'') AS Modelo,
                           COALESCE(Proveedor,'') AS Proveedor, COALESCE(TiempoEntrega,'') AS TiempoEntrega,
                           Tarifa, Factor, PrecioVentaUnit, PrecioVentaTotal
                    FROM CalculoCotizacionItem
                    WHERE IdCalculo = %s
                    ORDER BY CASE WHEN Tipo = 'SERVICIO' THEN 0 ELSE 1 END, Numero, Id
                """, (int(calc_id),))
                return cur.fetchall()
    except Exception as e:
        print(f"get_items_calculo error: {e}")
        return []


def _recalc_item(venta_total=None, cantidad=None, tarifa=None, factor=None):
    """Devuelve (PrecioVentaUnit, PrecioVentaTotal) con la fórmula de negocio."""
    cant = float(cantidad or 0.0)
    tar = float(tarifa or 0.0)
    fac = float(factor or 0.0)
    unit = round(tar * (1.0 + fac), 2)
    total = round(unit * cant, 2)
    return unit, total


def add_calculo_item(calc_id, tipo, cantidad, descripcion, modelo, proveedor=None, tiempo_entrega=None,
                     tarifa=0.0, factor=0.0, fuente="manual", id_reporte=None):
    """Agrega un item al cálculo y recalcula los totales."""
    try:
        unit, total = _recalc_item(cantidad=cantidad, tarifa=tarifa, factor=factor)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO CalculoCotizacionItem
                        (IdCalculo, Numero, Tipo, Fuente, IdReporteOrigen, Cantidad, Descripcion, Modelo,
                         Proveedor, TiempoEntrega, Tarifa, Factor, PrecioVentaUnit, PrecioVentaTotal)
                    VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (int(calc_id), tipo, fuente, id_reporte, float(cantidad), descripcion,
                      modelo, proveedor, tiempo_entrega, float(tarifa), float(factor), unit, total))
                conn.commit()
                recalcular_totales_calculo(calc_id)
                log_user_activity("Calculos Cotizaciones", f"Agregó línea {tipo} a cálculo {calc_id}")
                return True
    except Exception as e:
        print(f"add_calculo_item error: {e}")
        return False


def update_calculo_item(item_id, cantidad, descripcion, modelo, proveedor, tiempo_entrega, tarifa, factor):
    """Actualiza un item y recalcula los totales del cálculo."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdCalculo FROM CalculoCotizacionItem WHERE Id = %s", (int(item_id),))
                r = cur.fetchone()
                if not r:
                    return False
                calc_id = r[0]
                unit, total = _recalc_item(cantidad=cantidad, tarifa=tarifa, factor=factor)
                cur.execute("""
                    UPDATE CalculoCotizacionItem
                    SET Cantidad = %s, Descripcion = %s, Modelo = %s, Proveedor = %s, TiempoEntrega = %s,
                        Tarifa = %s, Factor = %s, PrecioVentaUnit = %s, PrecioVentaTotal = %s
                    WHERE Id = %s
                """, (float(cantidad), descripcion, modelo, proveedor, tiempo_entrega,
                      float(tarifa), float(factor), (unit, total)[0], (unit, total)[1], int(item_id)))
                conn.commit()
                recalcular_totales_calculo(calc_id)
                return True
    except Exception as e:
        print(f"update_calculo_item error: {e}")
        return False


def delete_calculo_item(item_id):
    """Elimina un item y recalcula los totales de su cálculo."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdCalculo FROM CalculoCotizacionItem WHERE Id = %s", (int(item_id),))
                r = cur.fetchone()
                calc_id = r[0] if r else None
                cur.execute("DELETE FROM CalculoCotizacionItem WHERE Id = %s", (int(item_id),))
                conn.commit()
                if calc_id:
                    recalcular_totales_calculo(calc_id)
                return True
    except Exception as e:
        print(f"delete_calculo_item error: {e}")
        return False


def recalcular_totales_calculo(calc_id):
    """Recalcula y persiste subtotales (Serv/Materiales), Subtotal, IVA (16%) y Total.
    También toca FechaActualizado para reflejar la última modificación."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COALESCE(SUM(CASE WHEN Tipo='SERVICIO' THEN PrecioVentaTotal ELSE 0 END),0),
                        COALESCE(SUM(CASE WHEN Tipo='MATERIAL' THEN PrecioVentaTotal ELSE 0 END),0)
                    FROM CalculoCotizacionItem WHERE IdCalculo = %s
                """, (int(calc_id),))
                r = cur.fetchone()
                serv = float(r[0] or 0.0)
                mat = float(r[1] or 0.0)
                subtotal = serv + mat
                iva = round(subtotal * 0.16, 2)
                total = round(subtotal + iva, 2)
                cur.execute("""
                    UPDATE Indice_calculocotizacion
                    SET SubtotalServicios = %s, SubtotalMateriales = %s, Subtotal = %s,
                        IVA = %s, Total = %s, FechaActualizado = GETDATE()
                    WHERE Id = %s
                """, (serv, mat, subtotal, iva, total, int(calc_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"recalcular_totales_calculo error: {e}")
        return False


def get_reportes_para_calculo(id_cliente):
    """Reportes de servicio de un cliente (para cargar sus horas factuables).
    Trae los campos de tiempo para después aplicar la fórmula de horas equivalentes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdReporte, Folio, Cliente, COALESCE(Contacto,'') AS Contacto, Fecha,
                           FechaHoraInicio, FechaHoraFin, TiempoTraslado, TiempoComida, Tecnico,
                           COALESCE(DescripcionServicio,'') AS DescripcionServicio, MaquinaLinea
                    FROM ReportesServicio
                    WHERE Cliente = %s
                    ORDER BY Fecha DESC, Folio DESC
                """, (str(id_cliente).strip().upper(),))
                return cur.fetchall()
    except Exception as e:
        print(f"get_reportes_para_calculo error: {e}")
        return []


# =====================================================================
# MÓDULO PROVEEDORES
# =====================================================================

@_cache_data(ttl=300)
def get_all_proveedores():
    """Lista completa de proveedores (catálogo) para la vista de administración."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdProveedor, Nombre, COALESCE(Contacto,'') AS Contacto,
                           COALESCE(Telefono,'') AS Telefono, COALESCE(Email,'') AS Email,
                           Activo
                    FROM HUB_Proveedores
                    ORDER BY Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_all_proveedores error: {e}")
        return []

@_cache_data(ttl=300)
def get_proveedores_activos():
    """Solo proveedores activos (para el selector de proveedor de una OC)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdProveedor, Nombre FROM HUB_Proveedores
                    WHERE Activo = 1 ORDER BY Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_proveedores_activos error: {e}")
        return []

def get_proximo_id_proveedor():
    """Calcula el siguiente IdProveedor secuencial (PROV###)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT IdProveedor FROM HUB_Proveedores")
                ids = cur.fetchall()
                nums = []
                for row in ids:
                    s = (row[0] or "").replace("PROV", "")
                    if s.isdigit():
                        nums.append(int(s))
                prox = (max(nums) + 1) if nums else 1
                return f"PROV{prox:03d}"
    except Exception as e:
        print(f"get_proximo_id_proveedor error: {e}")
        return "PROV001"

def add_proveedor(nombre, contacto, telefono, email, activo=True):
    """Registra un proveedor nuevo. Devuelve el IdProveedor generado o None."""
    try:
        nuevo_id = get_proximo_id_proveedor()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Proveedores (IdProveedor, Nombre, Contacto, Telefono, Email, Activo)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (nuevo_id, nombre.strip(), (contacto or '').strip(), (telefono or '').strip(),
                      (email or '').strip(), int(activo)))
                conn.commit()
                log_user_activity("Proveedores", f"Registró nuevo proveedor: {nombre.strip()} ({nuevo_id})")
                return nuevo_id
    except Exception as e:
        print(f"add_proveedor error: {e}")
        return None

def update_proveedor(id_proveedor, nombre, contacto, telefono, email, activo):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_Proveedores
                    SET Nombre = %s, Contacto = %s, Telefono = %s, Email = %s, Activo = %s
                    WHERE IdProveedor = %s
                """, (nombre.strip(), (contacto or '').strip(), (telefono or '').strip(),
                      (email or '').strip(), int(activo), id_proveedor))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Proveedores", f"Modificó proveedor: {nombre.strip()} ({id_proveedor})")
                return True
    except Exception as e:
        print(f"update_proveedor error: {e}")
        return False

def delete_proveedor(id_proveedor):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Nombre FROM HUB_Proveedores WHERE IdProveedor = %s", (id_proveedor,))
                row = cur.fetchone()
                p_nombre = row[0] if row else id_proveedor
                cur.execute("DELETE FROM HUB_Proveedores WHERE IdProveedor = %s", (id_proveedor,))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Proveedores", f"Eliminó proveedor: {p_nombre.strip()} ({id_proveedor})")
                return True
    except Exception as e:
        print(f"delete_proveedor error: {e}")
        return False


# =====================================================================
# MÓDULO ÓRDENES DE COMPRA (OC)
# =====================================================================

@_cache_data(ttl=300)
def get_oc_index():
    """Lista de todas las OC (encabezado) para el índice."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT io.FolioOC, io.IdProveedor, COALESCE(p.Nombre,'') AS ProveedorNombre,
                           io.Fecha, COALESCE(io.Condicion,'') AS Condicion, io.Moneda, io.TasaDolar,
                           io.Subtotal, io.TotalMXN, COALESCE(io.Notas,'') AS Notas,
                           io.Autor, io.Estatus, io.FechaCreacion
                    FROM HUB_IndiceOC io
                    LEFT JOIN HUB_Proveedores p ON io.IdProveedor = p.IdProveedor
                    ORDER BY io.FechaCreacion DESC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_oc_index error: {e}")
        return []

def get_oc_by_folio(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT io.FolioOC, io.IdProveedor, COALESCE(p.Nombre,'') AS ProveedorNombre,
                           io.Fecha, COALESCE(io.Condicion,'') AS Condicion, io.Moneda, io.TasaDolar,
                           io.Subtotal, io.IVA, io.TotalMXN, COALESCE(io.Notas,'') AS Notas,
                           io.Autor, io.Estatus, io.FechaCreacion, io.PdfNombre, io.PdfAdjunto
                    FROM HUB_IndiceOC io
                    LEFT JOIN HUB_Proveedores p ON io.IdProveedor = p.IdProveedor
                    WHERE io.FolioOC = %s
                """, (folio,))
                return cur.fetchone()
    except Exception as e:
        print(f"get_oc_by_folio error: {e}")
        return None

def get_siguiente_folio_oc():
    """Genera un folio contiguo OC-AAAA-NNNN basado en el año actual."""
    anio = datetime.date.today().year
    prefijo = f"OC-{anio}-"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM HUB_IndiceOC WHERE FolioOC LIKE %s", (prefijo + "%",))
                count = int(cur.fetchone()[0] or 0)
                return f"{prefijo}{count + 1:04d}"
    except Exception as e:
        print(f"get_siguiente_folio_oc error: {e}")
        return f"{prefijo}0001"

def create_oc(id_proveedor, fecha, condicion, moneda, tasa_dolar, notas, autor, estatus="PENDIENTE",
              pdf_bytes=None, pdf_nombre=None):
    """Crea una OC (encabezado). Devuelve el folio generado o None."""
    folio = get_siguiente_folio_oc()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                cur.execute("""
                    INSERT INTO HUB_IndiceOC (FolioOC, IdProveedor, Fecha, Condicion, Moneda, TasaDolar,
                                              Notas, Autor, Estatus, PdfNombre, PdfAdjunto)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (folio, id_proveedor, fecha_str, condicion, moneda, float(tasa_dolar or 0),
                      notas, autor, estatus, pdf_nombre,
                      pymssql.Binary(pdf_bytes) if pdf_bytes else None))
                conn.commit()
                log_user_activity("OrdenesCompra", f"Creó Orden de Compra: {folio}")
                return folio
    except Exception as e:
        print(f"create_oc error: {e}")
        return None

def update_oc(folio, id_proveedor, fecha, condicion, moneda, tasa_dolar, notas, estatus):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if isinstance(fecha, (datetime.date, datetime.datetime)):
                    fecha_str = fecha.strftime("%Y-%m-%d")
                else:
                    fecha_str = str(fecha)
                cur.execute("""
                    UPDATE HUB_IndiceOC
                    SET IdProveedor = %s, Fecha = %s, Condicion = %s, Moneda = %s, TasaDolar = %s,
                        Notas = %s, Estatus = %s
                    WHERE FolioOC = %s
                """, (id_proveedor, fecha_str, condicion, moneda, float(tasa_dolar or 0),
                      notas, estatus, folio))
                conn.commit()
                return True
    except Exception as e:
        print(f"update_oc error: {e}")
        return False

def delete_oc(folio):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_OCPartidas WHERE FolioOC = %s", (folio,))
                cur.execute("DELETE FROM HUB_IndiceOC WHERE FolioOC = %s", (folio,))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("OrdenesCompra", f"Eliminó Orden de Compra: {folio}")
                return True
    except Exception as e:
        print(f"delete_oc error: {e}")
        return False

def get_oc_partidas(folio):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT FolioOC, Partida, Cantidad, COALESCE(Descripcion,'') AS Descripcion,
                           COALESCE(Modelo,'') AS Modelo, PrecioUnitario, PrecioTotal,
                           TiempoEntregaDias, FechaCompra, FechaRecibido
                    FROM HUB_OCPartidas WHERE FolioOC = %s ORDER BY Partida ASC
                """, (folio,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_oc_partidas error: {e}")
        return []

def add_oc_partida(folio, cantidad, descripcion, modelo, precio_unitario, tiempo_entrega_dias, fecha_compra):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ISNULL(MAX(Partida), 0) + 1 FROM HUB_OCPartidas WHERE FolioOC = %s", (folio,))
                next_part = int(cur.fetchone()[0])
                precio_total = round(float(precio_unitario or 0) * float(cantidad or 0), 2)
                cur.execute("""
                    INSERT INTO HUB_OCPartidas (FolioOC, Partida, Cantidad, Descripcion, Modelo,
                                                PrecioUnitario, PrecioTotal, TiempoEntregaDias, FechaCompra)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (folio, next_part, cantidad, descripcion, modelo, precio_unitario, precio_total,
                      int(tiempo_entrega_dias or 0), fecha_compra))
                conn.commit()
                recalc_oc_totales(folio)
                return True
    except Exception as e:
        print(f"add_oc_partida error: {e}")
        return False

def update_oc_partida(folio, partida, cantidad, descripcion, modelo, precio_unitario, tiempo_entrega_dias,
                      fecha_compra, fecha_recibido):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                precio_total = round(float(precio_unitario or 0) * float(cantidad or 0), 2)
                cur.execute("""
                    UPDATE HUB_OCPartidas
                    SET Cantidad = %s, Descripcion = %s, Modelo = %s, PrecioUnitario = %s, PrecioTotal = %s,
                        TiempoEntregaDias = %s, FechaCompra = %s, FechaRecibido = %s
                    WHERE FolioOC = %s AND Partida = %s
                """, (cantidad, descripcion, modelo, precio_unitario, precio_total,
                      int(tiempo_entrega_dias or 0), fecha_compra, fecha_recibido, folio, partida))
                conn.commit()
                recalc_oc_totales(folio)
                return True
    except Exception as e:
        print(f"update_oc_partida error: {e}")
        return False

def delete_oc_partida(folio, partida):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_OCPartidas WHERE FolioOC = %s AND Partida = %s", (folio, partida))
                conn.commit()
                recalc_oc_totales(folio)
                return True
    except Exception as e:
        print(f"delete_oc_partida error: {e}")
        return False

def marcar_partida_recibida(folio, partida):
    """Marca/desmarca una partida como recibida (fecha de hoy)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT FechaRecibido FROM HUB_OCPartidas WHERE FolioOC = %s AND Partida = %s",
                            (folio, partida))
                row = cur.fetchone()
                if row and row[0]:
                    cur.execute("UPDATE HUB_OCPartidas SET FechaRecibido = NULL WHERE FolioOC = %s AND Partida = %s",
                                (folio, partida))
                else:
                    cur.execute("UPDATE HUB_OCPartidas SET FechaRecibido = GETDATE() WHERE FolioOC = %s AND Partida = %s",
                                (folio, partida))
                conn.commit()
                actualizar_estatus_oc(folio)
                return True
    except Exception as e:
        print(f"marcar_partida_recibida error: {e}")
        return False

def recalc_oc_totales(folio):
    """Recalcula Subtotal, IVA y TotalMXN del encabezado OC según moneda."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PrecioTotal FROM HUB_OCPartidas WHERE FolioOC = %s", (folio,))
                rows = cur.fetchall()
                cur.execute("SELECT Moneda, TasaDolar FROM HUB_IndiceOC WHERE FolioOC = %s", (folio,))
                head = cur.fetchone()
                moneda = head[0] if head else "MXN"
                tasa = float(head[1] or 0) if head else 0
                subtotal = round(sum(float(r[0] or 0) for r in rows), 2)
                iva = round(subtotal * 0.16, 2)
                total_mxn = round(subtotal + iva, 2)
                if moneda.upper() == "USD" and tasa > 0:
                    total_mxn = round((subtotal + iva) * tasa, 2)
                cur.execute("""
                    UPDATE HUB_IndiceOC SET Subtotal = %s, IVA = %s, TotalMXN = %s WHERE FolioOC = %s
                """, (subtotal, iva, total_mxn, folio))
                conn.commit()
                actualizar_estatus_oc(folio)
                return True
    except Exception as e:
        print(f"recalc_oc_totales error: {e}")
        return False

def actualizar_estatus_oc(folio):
    """Recalcula el estatus del encabezado según partidas recibidas."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*), SUM(CASE WHEN FechaRecibido IS NOT NULL THEN 1 ELSE 0 END)
                    FROM HUB_OCPartidas WHERE FolioOC = %s
                """, (folio,))
                r = cur.fetchone()
                total = int(r[0] or 0)
                recibidas = int(r[1] or 0)
                if total == 0 or recibidas == 0:
                    nuevo = "PENDIENTE"
                elif recibidas < total:
                    nuevo = "PARCIAL"
                else:
                    nuevo = "COMPLETADA"
                cur.execute("UPDATE HUB_IndiceOC SET Estatus = %s WHERE FolioOC = %s", (nuevo, folio))
                conn.commit()
                return True
    except Exception as e:
        print(f"actualizar_estatus_oc error: {e}")
        return False

def guardar_pdf_oc(folio, pdf_bytes, pdf_nombre):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_IndiceOC SET PdfAdjunto = %s, PdfNombre = %s WHERE FolioOC = %s
                """, (pymssql.Binary(pdf_bytes), pdf_nombre, folio))
                conn.commit()
                return True
    except Exception as e:
        print(f"guardar_pdf_oc error: {e}")
        return False

def get_pdf_oc(folio):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PdfAdjunto, PdfNombre FROM HUB_IndiceOC WHERE FolioOC = %s", (folio,))
                row = cur.fetchone()
                if row and row[0]:
                    return bytes(row[0]), (row[1] or f"OC_{folio}.pdf")
                return None, None
    except Exception as e:
        print(f"get_pdf_oc error: {e}")
        return None, None

def calcular_horas_equivalentes(reporte):
    """Aplica la fórmula de horas equivalentes facturables a un dict de ReportesServicio.
    Devuelve dict con normal/double/triple/quad/travel y eq, o None si faltan fechas."""
    try:
        start_dt = reporte.get("FechaHoraInicio")
        end_dt = reporte.get("FechaHoraFin")
        if not start_dt or not end_dt:
            return None
        travel = float(reporte.get("TiempoTraslado") or 0.0)
        food = bool(reporte.get("TiempoComida"))
        res = partition_hours(start_dt, end_dt, travel, food)
        normal = res["normal"]; dbl = res["double"]; tpl = res["triple"]; quad = res["quad"]; tr = res["travel"]
        eq = round(normal + 2 * dbl + 3 * tpl + 4 * quad + tr, 2)
        return {"normal": normal, "double": dbl, "triple": tpl, "quad": quad,
                "travel": tr, "eq": eq}
    except Exception as e:
        print(f"calcular_horas_equivalentes error: {e}")
        return None


# ---------------------------------------------------------------------------
# Dashboard: resumen agregado de KPIs por módulo (una sola consulta)
# ---------------------------------------------------------------------------

@_cache_data(ttl=300)
def get_resumen_dashboard():
    """Devuelve un dict con conteos agregados de KPIs para el dashboard.
    Usa subconsultas escalares para no traer todas las filas a Python.
    Conserva el mismo criterio de 'pendiente' que usa cada módulo."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM IndiceMateriales) AS cot_total,
                        (SELECT COUNT(*) FROM IndiceMateriales WHERE ISNULL(Color, 0) = 1) AS cot_entregadas,
                        (SELECT COUNT(*) FROM ReportesServicio) AS rep_total,
                        (SELECT COUNT(*) FROM ReportesServicio WHERE LTRIM(RTRIM(ISNULL(FirmaConformidad,''))) = '') AS rep_sin_firma,
                        (SELECT COUNT(*) FROM HUB_OxxoGasTickets) AS tkt_total,
                        (SELECT COUNT(*) FROM HUB_OxxoGasTickets T
                          WHERE NOT EXISTS (SELECT 1 FROM HUB_OxxoGasVales V
                                            WHERE V.XmlFolio = T.FolioTicket
                                               OR V.XmlContent LIKE '%' + T.FolioTicket + '%')) AS tkt_sin_match,
                        (SELECT COUNT(*) FROM HUB_IndiceOC WHERE UPPER(ISNULL(Estatus,'')) IN ('PENDIENTE','PARCIAL')) AS oc_pendientes,
                        (SELECT COUNT(*) FROM HUB_IndiceOC) AS oc_total,
                        (SELECT COUNT(*) FROM HUB_Inventario) AS inv_total,
                        (SELECT COUNT(*) FROM HUB_Inventario WHERE ISNULL(Cantidad,0) <= 0) AS inv_sin_stock,
                        (SELECT COUNT(*) FROM HUB_Inventario WHERE ISNULL(Cantidad,0) > 0 AND ISNULL(Cantidad,0) <= ISNULL(StockMinimo,0)) AS inv_poco_stock,
                        (SELECT SUM(ISNULL(Precio,0) * ISNULL(Cantidad,0)) FROM HUB_Inventario) AS inv_valor,
                        (SELECT COUNT(*) FROM HUB_VacacionesRegistros WHERE Fecha >= CONVERT(date, GETDATE())) AS vac_proximas,
                        (SELECT COUNT(*) FROM clientes) AS cli_total,
                        (SELECT COUNT(*) FROM HUB_Proveedores WHERE ISNULL(Activo,1) = 1) AS prov_activos,
                        (SELECT COUNT(*) FROM HUB_Automoviles) AS autos_total,
                        (SELECT COUNT(*) FROM HUB_Automoviles a
                          WHERE ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                                        WHERE k.IdAutomovil = a.Id ORDER BY k.FechaHora DESC, k.Id DESC), 0)
                                - a.UltimoServicioKms >= 9500) AS autos_requieren,
                        (SELECT COUNT(*) FROM HUB_Users WHERE ISNULL(Activo,0) = 1) AS usuarios_activos,
                        (SELECT COUNT(*) FROM HUB_Users) AS usuarios_total,
                        (SELECT COUNT(*) FROM Indice_calculocotizacion) AS calc_total,
                        (SELECT COUNT(*) FROM Indice_calculocotizacion
                          WHERE LTRIM(RTRIM(ISNULL(IdCotizacionFuente,''))) = '') AS calc_sin_cotizacion,
                        (SELECT ISNULL(SUM(ISNULL(Total,0)),0) FROM Indice_calculocotizacion) AS calc_total_usd,
                        (SELECT COUNT(*) FROM Indice_CotizacionesServProy) AS csp_total,
                        (SELECT COUNT(*) FROM Indice_CotizacionesServProy WHERE ISNULL(Color, 0) = 1) AS csp_entregadas,
                        (SELECT COUNT(*) FROM HUB_ActivityLog) AS act_total
                """)
                r = cur.fetchone()
                if not r:
                    return {}
                return dict(r)
    except Exception as e:
        print(f"get_resumen_dashboard error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Dashboard: series para gráficas por módulo
# ---------------------------------------------------------------------------

@_cache_data(ttl=300)
def get_serie_cotizaciones(ultimos_p=12):
    """Cotizaciones de materiales por mes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), Fecha, 120) AS Mes, COUNT(*) AS Valor
                    FROM IndiceMateriales
                    WHERE Fecha IS NOT NULL
                      AND Fecha >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), Fecha, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_cotizaciones error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_reportes(ultimos_p=12):
    """Reportes de servicio por mes del campo Fecha."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), Fecha, 120) AS Mes, COUNT(*) AS Valor
                    FROM ReportesServicio
                    WHERE Fecha IS NOT NULL
                      AND Fecha >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), Fecha, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_reportes error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_oc(ultimos_p=12):
    """Órdenes de compra: TotalMXN por mes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), Fecha, 120) AS Mes,
                           SUM(ISNULL(TotalMXN, 0)) AS Valor
                    FROM HUB_IndiceOC
                    WHERE Fecha IS NOT NULL
                      AND Fecha >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), Fecha, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_oc error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_horas_extras(ultimos_p=12):
    """Horas extras calculadas (suma) por mes."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), FechaRegistro, 120) AS Mes,
                           SUM(ISNULL(HorasExtrasCalculadas, 0)) AS TotalHoras
                    FROM HUB_HorasExtrasRegistros
                    WHERE FechaRegistro IS NOT NULL
                      AND FechaRegistro >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), FechaRegistro, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_horas_extras error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_calculos(ultimos_p=12):
    """Cálculos de cotización: Total USD y conteo por mes (FechaCreacion)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), FechaCreacion, 120) AS Mes,
                           COUNT(*) AS Conteo,
                           SUM(ISNULL(Total, 0)) AS TotalUSD
                    FROM Indice_calculocotizacion
                    WHERE FechaCreacion IS NOT NULL
                      AND FechaCreacion >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), FechaCreacion, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_calculos error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_oxxogas_monto(ultimos_p=12):
    """Vales OxxoGas: monto y litros por mes (tabla HUB_OxxoGasVales)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT CONVERT(varchar(7), Fecha, 120) AS Mes,
                           COUNT(*) AS Conteo,
                           SUM(ISNULL(Monto, 0)) AS TotalMonto,
                           SUM(ISNULL(XmlLitros, 0)) AS TotalLitros
                    FROM HUB_OxxoGasVales
                    WHERE Fecha IS NOT NULL
                      AND Fecha >= DATEADD(month, -%s, CONVERT(date, GETDATE()))
                    GROUP BY CONVERT(varchar(7), Fecha, 120)
                    ORDER BY Mes ASC
                """, (ultimos_p,))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_oxxogas_monto error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_actividad_por_modulo(ultimos_p=30, top_n=10):
    """Conteo de actividades por módulo en los últimos N días (para top)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP %s Modulo, COUNT(*) AS Valor
                    FROM HUB_ActivityLog
                    WHERE FechaHora >= DATEADD(day, -%s, GETDATE())
                    GROUP BY Modulo
                    ORDER BY Valor DESC
                """, (top_n, ultimos_p))
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_actividad_por_modulo error: {e}")
        return []


@_cache_data(ttl=300)
def get_serie_kms_por_auto():
    """Km actuales por automóvil (última lectura)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT a.MarcaModelo, a.Placas,
                           ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                                   WHERE k.IdAutomovil = a.Id ORDER BY k.FechaHora DESC, k.Id DESC), 0) AS KmActuales,
                           a.UltimoServicioKms AS UltimoServicio
                    FROM HUB_Automoviles a
                    ORDER BY a.MarcaModelo ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"get_serie_kms_por_auto error: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# MÓDULO TELEGRAM — Config, CRUD, Cola de envíos y despacho
# ──────────────────────────────────────────────────────────────────────────────

import requests as _requests

_TELEGRAM_API = "https://api.telegram.org"


@_cache_data(ttl=300)
def get_telegram_config():
    """Devuelve dict con token del bot desde HUB_Config."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'telegram_bot_token'")
                r = cur.fetchone()
                return {'token': (r['Valor'] or '').strip()} if r else {'token': ''}
    except Exception as e:
        print(f"Error reading telegram config: {e}")
        return {'token': ''}


def save_telegram_config(token):
    """Guarda el token del bot en HUB_Config."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = 'telegram_bot_token'", (token.strip(),))
                if cur.rowcount == 0:
                    cur.execute("INSERT INTO HUB_Config (Clave, Valor) VALUES ('telegram_bot_token', %s)", (token.strip(),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error saving telegram config: {e}")
        return False


# ── Eventos ──────────────────────────────────────────────────────────────────

@_cache_data(ttl=300)
def get_telegram_eventos():
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT IdEvento, Nombre, PlantillaMensaje, AdjuntarArchivo, Activo FROM HUB_TelegramEventos ORDER BY Nombre ASC")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching telegram events: {e}")
        return []


def update_telegram_evento(id_evento, plantilla, adjuntar_archivo, activo):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_TelegramEventos
                    SET PlantillaMensaje = %s, AdjuntarArchivo = %s, Activo = %s
                    WHERE IdEvento = %s
                """, (plantilla.strip(), int(adjuntar_archivo), int(activo), id_evento.strip()))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating telegram event: {e}")
        return False


# ── Destinatarios por evento ─────────────────────────────────────────────────

def get_telegram_destinatarios(id_evento):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.IdUsuario, u.Nombre, u.Email
                    FROM HUB_TelegramDestinatarios d
                    JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE d.IdEvento = %s
                    ORDER BY u.Nombre ASC
                """, (id_evento.strip(),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching telegram destinatarios: {e}")
        return []


def set_telegram_destinatarios(id_evento, lista_ids):
    """Reemplaza todos los destinatarios de un evento."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_TelegramDestinatarios WHERE IdEvento = %s", (id_evento.strip(),))
                for uid in lista_ids:
                    cur.execute(
                        "INSERT INTO HUB_TelegramDestinatarios (IdEvento, IdUsuario) VALUES (%s, %s)",
                        (id_evento.strip(), int(uid))
                    )
                conn.commit()
                return True
    except Exception as e:
        print(f"Error setting telegram destinatarios: {e}")
        return False


# ── Vinculación usuario ↔ Telegram ───────────────────────────────────────────

def get_telegram_usuario_by_chat_id(chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT * FROM HUB_TelegramUsuarios WHERE ChatId = %s", (int(chat_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching telegram user by chat_id: {e}")
        return None


def add_telegram_usuario(id_usuario, chat_id, nombre_telegram, telefono_mac):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_TelegramUsuarios AS t
                    USING (SELECT %s AS IdUsuario) AS s ON t.IdUsuario = s.IdUsuario
                    WHEN MATCHED THEN UPDATE SET ChatId = %s, NombreTelegram = %s, TelefonoMAC = %s, Activo = 1, FechaVinculado = GETDATE()
                    WHEN NOT MATCHED THEN INSERT (IdUsuario, ChatId, NombreTelegram, TelefonoMAC) VALUES (%s, %s, %s, %s);
                """, (int(id_usuario), int(chat_id), nombre_telegram, telefono_mac, int(id_usuario), int(chat_id), nombre_telegram, telefono_mac))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error adding telegram user: {e}")
        return False


@_cache_data(ttl=300)
def get_telegram_vinculados():
    """Lista todos los usuarios vinculados a Telegram."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT t.IdUsuario, t.ChatId, t.NombreTelegram, t.TelefonoMAC, t.Activo, t.FechaVinculado,
                           u.Nombre, u.Email
                    FROM HUB_TelegramUsuarios t
                    JOIN HUB_Users u ON t.IdUsuario = u.Id
                    ORDER BY u.Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching linked telegram users: {e}")
        return []


def set_telegram_usuario_activo(id_usuario, activo):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_TelegramUsuarios SET Activo = %s WHERE IdUsuario = %s", (int(activo), int(id_usuario)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error toggling telegram user: {e}")
        return False


def unlink_telegram_usuario(id_usuario):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_TelegramDestinatarios WHERE IdUsuario = %s", (int(id_usuario),))
                cur.execute("DELETE FROM HUB_TelegramUsuarios WHERE IdUsuario = %s", (int(id_usuario),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error unlinking telegram user: {e}")
        return False


# ── Cola de envíos ───────────────────────────────────────────────────────────

def queue_telegram_alerta(id_evento, chat_id, texto, adjunto=None, adjunto_nombre=None):
    """Encola un mensaje para envío por Telegram. adjunto = bytes (PDF/foto)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_TelegramQueue (IdEvento, ChatId, Texto, Adjunto, AdjuntoNombre)
                    VALUES (%s, %s, %s, %s, %s)
                """, (id_evento.strip(), int(chat_id), texto.strip(), adjunto, adjunto_nombre))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error queuing telegram alert: {e}")
        return False


def dequeue_telegram_pendientes(limite=10):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP (%s) Id, IdEvento, ChatId, Texto, Adjunto, AdjuntoNombre, Intentos
                    FROM HUB_TelegramQueue
                    WHERE Estado = 'PENDIENTE'
                    ORDER BY Creado ASC
                """, (int(limite),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error dequeuing telegram messages: {e}")
        return []


def marcar_telegram_enviado(id_queue, respuesta):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_TelegramQueue
                    SET Estado = 'ENVIADO', UltimoIntento = GETDATE(), Respuesta = %s
                    WHERE Id = %s
                """, (respuesta.strip()[:500], int(id_queue)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error marking telegram sent: {e}")
        return False


def marcar_telegram_fallado(id_queue, respuesta, intentos):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                nuevo_estado = 'FALLADO' if intentos >= 3 else 'PENDIENTE'
                cur.execute("""
                    UPDATE HUB_TelegramQueue
                    SET Estado = %s, UltimoIntento = GETDATE(), Respuesta = %s, Intentos = %s
                    WHERE Id = %s
                """, (nuevo_estado, respuesta.strip()[:500], int(intentos), int(id_queue)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error marking telegram failed: {e}")
        return False


def get_telegram_historial(limite=50):
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP (%s) Id, IdEvento, ChatId, LEFT(Texto, 120) AS Texto,
                           AdjuntoNombre, Estado, Intentos, Respuesta, Creado
                    FROM HUB_TelegramQueue
                    ORDER BY Creado DESC
                """, (int(limite),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching telegram history: {e}")
        return []


def limpiar_telegram_historial(dias=30):
    """Elimina registros de la cola con más de N días."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM HUB_TelegramQueue
                    WHERE Estado IN ('ENVIADO', 'FALLADO') AND Creado < DATEADD(day, -%s, GETDATE())
                """, (int(dias),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error cleaning telegram history: {e}")
        return False


# ── OpenWA / WhatsApp ───────────────────────────────────────────────────────
# Espejo de lo de arriba, para los avisos que salen por WhatsApp en vez de por
# Telegram. Conviven: cada canal tiene su catalogo de eventos y su cola, y lo
# que se dice lo arma notif_messages.py (comun a los dos).

def get_openwa_config():
    """Devuelve la conexion a OpenWA: {api_key, base_url, session_id}.

    La API key se pega desde la interfaz (Notificaciones > Conexion > OpenWA).
    Se devuelve tal cual porque la necesita el cliente HTTP, pero ninguna
    pantalla la muestra: ahi va enmascarada.
    """
    vacio = {'api_key': '', 'base_url': '', 'session_id': ''}
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Clave, Valor FROM HUB_Config "
                    "WHERE Clave IN ('openwa_api_key', 'openwa_base_url', 'openwa_session_id')")
                cfg = {r['Clave']: (r['Valor'] or '').strip() for r in cur.fetchall()}
        return {'api_key': cfg.get('openwa_api_key', ''),
                'base_url': cfg.get('openwa_base_url', ''),
                'session_id': cfg.get('openwa_session_id', '')}
    except Exception as e:
        print(f"Error reading openwa config: {e}")
        return dict(vacio)


def save_openwa_config(api_key=None, base_url=None, session_id=None):
    """
    Guarda la conexion. Cada parametro es opcional y solo se tocan los que se
    mandan: la pantalla manda la key sola la mayoria de las veces y no debe
    borrar el resto. Un valor vacio SI se guarda (es como se borra la key).
    """
    campos = {'openwa_api_key': api_key, 'openwa_base_url': base_url,
              'openwa_session_id': session_id}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for clave, valor in campos.items():
                    if valor is None:
                        continue
                    valor = str(valor).strip()
                    cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() "
                                "WHERE Clave = %s", (valor, clave))
                    if cur.rowcount == 0:
                        cur.execute("INSERT INTO HUB_Config (Clave, Valor) VALUES (%s, %s)",
                                    (clave, valor))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error saving openwa config: {e}")
        return False


def get_openwa_eventos():
    """Catalogo de avisos de WhatsApp con su plantilla y sus telefonos."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdEvento, Nombre, Descripcion, PlantillaMensaje,
                           AdjuntarArchivo, Telefonos, Activo, Actualizado
                    FROM HUB_WhatsappEventos
                    ORDER BY Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error reading openwa events: {e}")
        return []


def get_openwa_evento(id_evento):
    """Un evento por su clave, o None si no existe."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdEvento, Nombre, Descripcion, PlantillaMensaje,
                           AdjuntarArchivo, Telefonos, Activo, Actualizado
                    FROM HUB_WhatsappEventos WHERE IdEvento = %s
                """, (str(id_evento).strip(),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error reading openwa event: {e}")
        return None


def save_openwa_evento(id_evento, plantilla=None, telefonos=None, activo=None,
                       adjuntar=None):
    """Guarda un aviso. Igual que la config: solo toca lo que se le manda."""
    campos = {'PlantillaMensaje': plantilla, 'Telefonos': telefonos,
              'Activo': activo, 'AdjuntarArchivo': adjuntar}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                seteo, params = [], []
                for columna, valor in campos.items():
                    if valor is None:
                        continue
                    seteo.append(f"{columna} = %s")
                    params.append(int(valor) if isinstance(valor, bool) else valor)
                if not seteo:
                    return True
                params.append(str(id_evento).strip())
                cur.execute(f"UPDATE HUB_WhatsappEventos SET {', '.join(seteo)}, "
                            f"Actualizado = GETDATE() WHERE IdEvento = %s", tuple(params))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error saving openwa event: {e}")
        return False


def get_ultimo_registro_kilometros():
    """
    El ultimo odometro registrado, con su auto y su usuario.

    Existe para el boton "reenviar el ultimo evento" de los avisos: el aviso no
    se guarda como evento (en la cola solo queda el texto ya rendido, y se
    borra a los 30 dias), asi que para reproducirlo hay que releer el registro
    real de la tabla del modulo.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 k.Id, k.IdAutomovil, k.Kilometros, k.FechaHora, k.IdUsuario
                    FROM HUB_RegistroKilometros k
                    ORDER BY k.FechaHora DESC, k.Id DESC
                """)
                return cur.fetchone()
    except Exception as e:
        print(f"Error reading last kilometro: {e}")
        return None


def get_ultimo_ticket_oxxogas(con_foto=True):
    """
    El ultimo ticket registrado, con la foto si se pide.

    `con_foto=False` para listados: la imagen pesa (~1 MB) y aca solo se usa
    para mandarla de nuevo.
    """
    cols = ("t.FolioTicket, t.IdVehiculo, t.IdCliente, t.Descripcion, t.IdUsuario, "
            "t.FechaRegistro, t.Estacion, t.ImagenNombre"
            + (", t.ImagenTicket" if con_foto else ""))
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(f"""
                    SELECT TOP 1 {cols}
                    FROM HUB_OxxoGasTickets t
                    ORDER BY t.FechaRegistro DESC, t.Id DESC
                """)
                return cur.fetchone()
    except Exception as e:
        print(f"Error reading last ticket: {e}")
        return None


def get_ultimo_reporte_firmado():
    """
    El ultimo reporte de servicio que tiene firma (Estatus = 'Firmado' y firma
    guardada). Sin la firma no hay PDF que mandar, asi que no cuenta.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 IdReporte, Folio, Cliente, Estatus
                    FROM ReportesServicio
                    WHERE Estatus = 'Firmado' AND FirmaConformidad IS NOT NULL
                    ORDER BY IdReporte DESC
                """)
                return cur.fetchone()
    except Exception as e:
        print(f"Error reading last signed report: {e}")
        return None


def queue_openwa_alerta(id_evento, chat_id, texto, adjunto=None, adjunto_nombre=None,
                        adjunto_tipo=None):
    """Encola un mensaje de WhatsApp. El texto entra YA renderizado."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_WhatsappQueue
                        (IdEvento, ChatId, Texto, Adjunto, AdjuntoNombre, AdjuntoTipo)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (str(id_evento).strip(), str(chat_id).strip(),
                      (texto or '').strip() or None, adjunto, adjunto_nombre, adjunto_tipo))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error queuing openwa alert: {e}")
        return False


def dequeue_openwa_pendientes(limite=10):
    """Lo pendiente, mas viejo primero: el orden en que se avisa."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP (%s) Id, IdEvento, ChatId, Texto, Adjunto, AdjuntoNombre,
                           AdjuntoTipo, Intentos
                    FROM HUB_WhatsappQueue
                    WHERE Estado = 'PENDIENTE'
                    ORDER BY Creado ASC
                """, (int(limite),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error dequeuing openwa messages: {e}")
        return []


def marcar_openwa_enviado(id_queue):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_WhatsappQueue
                    SET Estado = 'ENVIADO', Intentos = Intentos + 1,
                        Error = NULL, Enviado = GETDATE()
                    WHERE Id = %s
                """, (int(id_queue),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error marking openwa sent: {e}")
        return False


def marcar_openwa_fallido(id_queue, error, intentos, max_intentos=3):
    """Suma un intento. A los `max_intentos` deja de reintentar y marca FALLADO."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                nuevo_estado = 'FALLADO' if intentos >= max_intentos else 'PENDIENTE'
                cur.execute("""
                    UPDATE HUB_WhatsappQueue
                    SET Estado = %s, Intentos = %s, Error = %s
                    WHERE Id = %s
                """, (nuevo_estado, int(intentos), str(error or '')[:500], int(id_queue)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error marking openwa failed: {e}")
        return False


def get_openwa_historial(limite=100, estado=None):
    """Ultimos mensajes enviados o fallidos, para la sub-pestana Historial."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if estado:
                    cur.execute("""
                        SELECT TOP (%s) Id, IdEvento, ChatId, Estado, Intentos, Error,
                               Creado, Enviado
                        FROM HUB_WhatsappQueue WHERE Estado = %s
                        ORDER BY Creado DESC
                    """, (int(limite), estado))
                else:
                    cur.execute("""
                        SELECT TOP (%s) Id, IdEvento, ChatId, Estado, Intentos, Error,
                               Creado, Enviado
                        FROM HUB_WhatsappQueue
                        ORDER BY Creado DESC
                    """, (int(limite),))
                return cur.fetchall()
    except Exception as e:
        print(f"Error reading openwa history: {e}")
        return []


def openwa_metrics():
    """Resumen para la tarjeta de conexion: cuantos avisos hay y como van."""
    m = {'eventos': 0, 'activos': 0, 'con_telefonos': 0,
         'pendientes': 0, 'enviados': 0, 'fallados': 0}
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN Activo = 1 THEN 1 ELSE 0 END) AS act, "
                            "SUM(CASE WHEN Telefonos IS NOT NULL AND LEN(LTRIM(RTRIM(Telefonos))) > 0 "
                            "THEN 1 ELSE 0 END) AS con_tel FROM HUB_WhatsappEventos")
                r = cur.fetchone() or {}
                m['eventos'] = r.get('n') or 0
                m['activos'] = r.get('act') or 0
                m['con_telefonos'] = r.get('con_tel') or 0
                cur.execute("SELECT Estado, COUNT(*) AS n FROM HUB_WhatsappQueue "
                            "GROUP BY Estado")
                for row in cur.fetchall():
                    clave = (row.get('Estado') or '').lower()
                    if clave in m:
                        m[clave] = row['n']
        return m
    except Exception as e:
        print(f"Error reading openwa metrics: {e}")
        return m


def limpiar_openwa_historial(dias=30):
    """Borra de la cola lo ya cerrado hace mucho (los adjuntos ocupan)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM HUB_WhatsappQueue
                    WHERE Estado IN ('ENVIADO', 'FALLADO') AND Creado < DATEADD(day, -%s, GETDATE())
                """, (int(dias),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error cleaning openwa history: {e}")
        return False


# ── Envío HTTP a la API de Telegram ─────────────────────────────────────────

def _normalize_telegram_image(raw_bytes, max_dim=1600, quality=85):
    """Recodifica bytes a JPEG baseline válido para Telegram.

    Devuelve (bytes, nombre_sugerido) o (None, None) si no es una imagen legible.
    Evita IMAGE_PROCESS_FAILED por formatos corruptos, HEIC/WebP mal nombrados,
    PNG con nombre .jpg, progresivos o archivos demasiado grandes.
    """
    try:
        from PIL import Image as PILImage
        import io as _io
        img = PILImage.open(_io.BytesIO(raw_bytes))
        if img.mode in ('RGBA', 'LA', 'P'):
            background = PILImage.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), PILImage.LANCZOS)
        out = _io.BytesIO()
        img.save(out, format='JPEG', quality=quality, progressive=False, optimize=True)
        return out.getvalue(), 'foto.jpg'
    except Exception:
        return None, None


def _telegram_upload(chat_id, url, field, filename, mime, texto, data_bytes):
    """POST multipart a sendPhoto o sendDocument. Devuelve (ok, message_id_o_error)."""
    import io
    endpoint = 'sendPhoto' if field == 'photo' else 'sendDocument'
    files = {field: (filename, io.BytesIO(data_bytes), mime)}
    resp = _requests.post(
        f"{url}/{endpoint}",
        data={'chat_id': int(chat_id), 'caption': texto, 'parse_mode': 'Markdown'},
        files=files,
        timeout=45
    )
    payload = resp.json()
    if resp.ok and payload.get('ok'):
        return True, f"ok ({payload.get('result', {}).get('message_id', '')})"
    return False, f"API error: {payload.get('description', resp.text[:200])}"


def send_telegram_message(chat_id, texto, adjunto=None, adjunto_nombre=None):
    """Envía un mensaje de texto o con adjunto (PDF/foto) a Telegram vía HTTP.

    Imagenes: se revalidan/recodifican con Pillow y, si sendPhoto falla
    (p. ej. IMAGE_PROCESS_FAILED), se reintenta como sendDocument.
    Si el adjunto sigue fallando, se envía solo el texto para no perder la alerta.
    """
    cfg = get_telegram_config()
    token = cfg.get('token', '')
    if not token:
        return False, 'No hay token de Telegram configurado'

    url = f"{_TELEGRAM_API}/bot{token}"

    try:
        if adjunto:
            import io
            # Detección de MIME: extensión + magic bytes (nombre .jpg no confiable)
            head = bytes(adjunto[:12]) if adjunto else b''
            if head.startswith(b'%PDF'):
                mime, is_image = 'application/pdf', False
            elif head.startswith(b'\xff\xd8\xff'):
                mime, is_image = 'image/jpeg', True
            elif head.startswith(b'\x89PNG\r\n\x1a\n'):
                mime, is_image = 'image/png', True
            elif head[4:8] == b'ftyp':
                mime, is_image = 'image/heic', True  # re-codificar abajo
            else:
                ext = ''
                if adjunto_nombre and '.' in adjunto_nombre:
                    ext = '.' + adjunto_nombre.rsplit('.', 1)[-1].lower()
                mime = {
                    '.pdf': 'application/pdf',
                    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                }.get(ext, 'application/octet-stream')
                is_image = mime.startswith('image/')

            if is_image:
                # Siempre re-codificar a JPEG baseline válido para sendPhoto
                norm, norm_name = _normalize_telegram_image(adjunto)
                if norm:
                    adjunto = norm
                    adjunto_nombre = norm_name or adjunto_nombre or 'foto.jpg'
                    mime = 'image/jpeg'
                else:
                    # No legible como imagen → intentar como documento crudo
                    mime = 'application/octet-stream'
                    is_image = False

            if is_image:
                ok, result = _telegram_upload(
                    chat_id, url, 'photo', adjunto_nombre or 'foto.jpg', mime, texto, adjunto
                )
                if not ok:
                    # Fallback: enviar como archivo adjunto (document)
                    ok2, result2 = _telegram_upload(
                        chat_id, url, 'document', adjunto_nombre or 'archivo.bin',
                        'application/octet-stream', texto, adjunto
                    )
                    if ok2:
                        return True, f"{result2} (doc fallback)"
                    # Si falla el adjunto, al menos entregar el texto
                    ok3, result3 = _send_telegram_text_only(url, chat_id, texto)
                    if ok3:
                        return True, f"{result3} (sin adjunto: {result} / {result2})"
                    return False, f"{result} / {result2}"
                return True, result
            else:
                ok, result = _telegram_upload(
                    chat_id, url, 'document', adjunto_nombre or 'archivo', mime, texto, adjunto
                )
                if not ok:
                    ok3, result3 = _send_telegram_text_only(url, chat_id, texto)
                    if ok3:
                        return True, f"{result3} (sin adjunto: {result})"
                    return False, result
                return True, result

        return _send_telegram_text_only(url, chat_id, texto)

    except _requests.exceptions.ConnectionError:
        return False, "Error de conexion a api.telegram.org"
    except _requests.exceptions.Timeout:
        return False, "Timeout conectando a Telegram"
    except Exception as e:
        return False, f"Excepcion: {str(e)[:200]}"


def _send_telegram_text_only(url, chat_id, texto):
    """Envía solo texto (sin adjunto). Devuelve (ok, message_id_o_error)."""
    resp = _requests.post(
        f"{url}/sendMessage",
        json={'chat_id': int(chat_id), 'text': texto, 'parse_mode': 'Markdown'},
        timeout=15
    )
    payload = resp.json()
    if resp.ok and payload.get('ok'):
        return True, f"ok ({payload.get('result', {}).get('message_id', '')})"
    return False, f"API error: {payload.get('description', resp.text[:200])}"


def test_telegram_bot():
    """Prueba la conexion al bot: llama a getMe. Devuelve (ok, info_dict)."""
    cfg = get_telegram_config()
    token = cfg.get('token', '')
    if not token:
        return False, {'error': 'No hay token configurado'}
    try:
        resp = _requests.get(f"{_TELEGRAM_API}/bot{token}/getMe", timeout=10)
        data = resp.json()
        if resp.ok and data.get('ok'):
            bot = data['result']
            return True, {
                'id': bot.get('id'),
                'name': bot.get('first_name', ''),
                'username': bot.get('username', ''),
                'can_join_groups': bot.get('can_join_groups', False),
                'can_read_all': bot.get('can_read_all_group_messages', False),
            }
        else:
            return False, {'error': data.get('description', 'Token invalido')}
    except Exception as e:
        return False, {'error': str(e)[:200]}


def get_telegram_updates_offset(offset=None):
    """Obtiene updates pendientes del bot (para capturar /start y compartir contacto)."""
    cfg = get_telegram_config()
    token = cfg.get('token', '')
    if not token:
        return []
    try:
        params = {'timeout': 2, 'allowed_updates': '["message"]'}
        if offset is not None:
            params['offset'] = int(offset)
        resp = _requests.get(f"{_TELEGRAM_API}/bot{token}/getUpdates", params=params, timeout=10)
        data = resp.json()
        if resp.ok and data.get('ok'):
            return data.get('result', [])
        return []
    except Exception:
        return []


# ─── AppConfig: Frases de Splash Screen ───────────────────────────────────────

@_cache_data(ttl=300)
def get_app_config_phrases(tipo='SPLASH_MSG'):
    """Obtiene todas las frases de un tipo dado (default: SPLASH_MSG)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Id, Tipo, Texto, Activo, FechaCreacion, FechaActualizado FROM HUB_SplashPhrases WHERE Tipo = %s ORDER BY Id ASC",
                    (tipo,)
                )
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching AppConfig phrases: {e}")
        return []


@_cache_data(ttl=300)
def get_app_config_active_phrases(tipo='SPLASH_MSG'):
    """Obtiene solo las frases activas de un tipo (para el splash screen)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Id, Texto FROM HUB_SplashPhrases WHERE Tipo = %s AND Activo = 1 ORDER BY Id ASC",
                    (tipo,)
                )
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching active AppConfig phrases: {e}")
        return []


def add_app_config_phrase(texto, tipo='SPLASH_MSG'):
    """Agrega una frase nueva."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO HUB_SplashPhrases (Tipo, Texto) VALUES (%s, %s)",
                    (tipo, texto.strip())
                )
                conn.commit()
                log_user_activity("AppConfig", f"Agregó frase de splash: {texto[:60]}...")
                return True
    except Exception as e:
        print(f"Error adding AppConfig phrase: {e}")
        return False


def update_app_config_phrase(frase_id, texto, activo=None):
    """Actualiza el texto y/o estado de una frase."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if activo is not None:
                    cur.execute(
                        "UPDATE HUB_SplashPhrases SET Texto = %s, Activo = %s, FechaActualizado = GETDATE() WHERE Id = %s",
                        (texto.strip(), int(activo), int(frase_id))
                    )
                else:
                    cur.execute(
                        "UPDATE HUB_SplashPhrases SET Texto = %s, FechaActualizado = GETDATE() WHERE Id = %s",
                        (texto.strip(), int(frase_id))
                    )
                conn.commit()
                log_user_activity("AppConfig", f"Actualizó frase #{frase_id}: {texto[:60]}...")
                return True
    except Exception as e:
        print(f"Error updating AppConfig phrase: {e}")
        return False


def delete_app_config_phrase(frase_id):
    """Elimina una frase."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Texto FROM HUB_SplashPhrases WHERE Id = %s", (int(frase_id),))
                row = cur.fetchone()
                texto = row[0] if row else str(frase_id)
                cur.execute("DELETE FROM HUB_SplashPhrases WHERE Id = %s", (int(frase_id),))
                conn.commit()
                log_user_activity("AppConfig", f"Eliminó frase: {texto[:60]}...")
                return True
    except Exception as e:
        print(f"Error deleting AppConfig phrase: {e}")
        return False


# ── Go Vale QR ────────────────────────────────────────────────────────────────
# Solicitudes de vales QR generados desde Go Vale

def get_govale_credentials():
    """Obtiene credenciales Go Vale (usuario/password) desde HUB_Config."""
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
        print(f"Error reading govale config: {e}")
        return {'user': '', 'password': ''}


def save_govale_credentials(user, password):
    """Guarda/actualiza credenciales Go Vale en HUB_Config."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for clave, valor in [('govale_user', user), ('govale_password', password)]:
                    cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = %s", (valor.strip(), clave))
                    if cur.rowcount == 0:
                        cur.execute("INSERT INTO HUB_Config (Clave, Valor) VALUES (%s, %s)", (clave, valor.strip()))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error saving govale credentials: {e}")
        return False


def crear_solicitud_vale(id_solicitante, id_automovil, id_cliente, descripcion, notas='', kilometros=None):
    """Crea una solicitud de vale QR. Auto-aprueba si cumple criterios.
    Retorna (sol_id, auto_aprobado, motivos) o (None, False, [])."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Validar: máximo 1 vale por automóvil por día (solo PENDIENTE o APROBADO)
                cur.execute("""
                    SELECT COUNT(*) FROM HUB_SolicitudVales
                    WHERE IdAutomovil = %s
                      AND CAST(FechaSolicitud AS DATE) = CAST(GETDATE() AS DATE)
                      AND Estatus IN ('PENDIENTE', 'APROBADO', 'GENERADO')
                """, (int(id_automovil),))
                count = cur.fetchone()[0]
                if count > 0:
                    return (None, False, ['Ya tiene vale registrado hoy para este vehículo'])  # Ya tiene vale hoy para este auto

                # Obtener placa del automóvil
                cur.execute("SELECT Placas FROM HUB_Automoviles WHERE Id = %s", (int(id_automovil),))
                auto_row = cur.fetchone()
                placa = auto_row[0] if auto_row else ''

                cur.execute("""
                    INSERT INTO HUB_SolicitudVales (IdSolicitante, IdAutomovil, IdCliente, Placa, Descripcion, Cantidad, MontoUnit, Notas, Kilometros)
                    VALUES (%s, %s, %s, %s, %s, 1, 500.00, %s, %s);
                    SELECT SCOPE_IDENTITY();
                """, (id_solicitante, int(id_automovil), id_cliente.strip() if id_cliente else None,
                      placa.strip(), descripcion.strip(), notas.strip(),
                      int(kilometros) if kilometros is not None else None))
                sol_id = int(cur.fetchone()[0])
                conn.commit()

                # Evaluar criterios para auto-aprobación
                aprobado, motivos = evaluar_criterios_vale(id_solicitante, id_automovil)

                if aprobado:
                    # Auto-aprobar
                    cur.execute("""
                        UPDATE HUB_SolicitudVales
                        SET Estatus = 'APROBADO', IdAprobador = %s, FechaAprobado = GETDATE()
                        WHERE Id = %s
                    """, (int(id_solicitante), int(sol_id)))
                    conn.commit()
                    log_user_activity("Vales QR", f"Auto-aprobó vale #{sol_id}: {placa} - {descripcion[:60]}")
                else:
                    log_user_activity("Vales QR", f"Solicitó vale #{sol_id} (pendiente): {placa} - {descripcion[:60]}. Motivos: {'; '.join(motivos)}")

                return (sol_id, aprobado, motivos)
    except Exception as e:
        print(f"Error creando solicitud vale: {e}")
        return (None, False, [f'Error al crear solicitud: {str(e)[:80]}'])


def get_auto_ya_tiene_vale_hoy(id_automovil):
    """Verifica si un automóvil ya tiene vale APROBADO/GENERADO hoy (rechazados/pendientes no cuentan)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM HUB_SolicitudVales
                    WHERE IdAutomovil = %s
                      AND CAST(FechaSolicitud AS DATE) = CAST(GETDATE() AS DATE)
                      AND Estatus IN ('APROBADO', 'GENERADO')
                """, (int(id_automovil),))
                return cur.fetchone()[0] > 0
    except Exception as e:
        print(f"Error checking vale hoy: {e}")
        return False


def evaluar_criterios_vale(id_solicitante, id_automovil):
    """Evalúa si una solicitud cumple criterios para auto-aprobación.
    Retorna (aprobado: bool, motivos: list[str])
    Criterios:
      1. Cooldown usuario 2 días (desde último vale GENERADO del usuario)
      2. Cooldown vehículo 2 días (desde último vale GENERADO del vehículo)
      3. Máx 4 vales/semana por usuario
      4. 1 vale/día por vehículo (ya validado en crear_solicitud_vale)
    """
    motivos = []
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                # Criterio 1: Cooldown usuario 2 días
                cur.execute("""
                    SELECT TOP 1 FechaAprobado
                    FROM HUB_SolicitudVales
                    WHERE IdSolicitante = %s AND Estatus = 'GENERADO'
                    ORDER BY FechaAprobado DESC
                """, (int(id_solicitante),))
                row = cur.fetchone()
                if row and row.get('FechaAprobado'):
                    from datetime import datetime, timedelta
                    dias_desde = (datetime.now() - row['FechaAprobado']).days
                    if dias_desde < 2:
                        motivos.append(f"Cooldown usuario: último vale hace {dias_desde} días (requiere 2)")

                # Criterio 2: Cooldown vehículo 2 días
                cur.execute("""
                    SELECT TOP 1 FechaAprobado
                    FROM HUB_SolicitudVales
                    WHERE IdAutomovil = %s AND Estatus = 'GENERADO'
                    ORDER BY FechaAprobado DESC
                """, (int(id_automovil),))
                row = cur.fetchone()
                if row and row.get('FechaAprobado'):
                    from datetime import datetime, timedelta
                    dias_desde = (datetime.now() - row['FechaAprobado']).days
                    if dias_desde < 2:
                        motivos.append(f"Cooldown vehículo: último vale hace {dias_desde} días (requiere 2)")

                # Criterio 3: Máx 4 vales/semana por usuario
                cur.execute("""
                    SELECT COUNT(*) AS Total
                    FROM HUB_SolicitudVales
                    WHERE IdSolicitante = %s
                      AND Estatus IN ('APROBADO', 'GENERADO')
                      AND FechaSolicitud > DATEADD(DAY, -7, GETDATE())
                """, (int(id_solicitante),))
                row = cur.fetchone()
                total_semana = row['Total'] if row else 0
                if total_semana >= 4:
                    motivos.append(f"Límite semanal usuario: {total_semana}/4 vales esta semana")

                # Criterio 4: Máx 4 vales/semana por vehículo
                cur.execute("""
                    SELECT COUNT(*) AS Total
                    FROM HUB_SolicitudVales
                    WHERE IdAutomovil = %s
                      AND Estatus IN ('APROBADO', 'GENERADO')
                      AND FechaSolicitud > DATEADD(DAY, -7, GETDATE())
                """, (int(id_automovil),))
                row = cur.fetchone()
                total_semana_auto = row['Total'] if row else 0
                if total_semana_auto >= 4:
                    motivos.append(f"Límite semanal vehículo: {total_semana_auto}/4 vales esta semana")

                return (len(motivos) == 0, motivos)
    except Exception as e:
        print(f"Error evaluando criterios vale: {e}")
        return (False, [f"Error al evaluar criterios: {str(e)[:80]}"])


def get_solicitudes_vales(estatus=None, id_solicitante=None, limit=100):
    """Lista solicitudes de vales con join a usuarios, automóviles y clientes."""
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
                params.append(int(limit))
                cur.execute(f"""
                    SELECT TOP {int(limit)}
                        s.Id, s.FechaSolicitud, s.Placa, s.Descripcion, s.Cantidad, s.MontoUnit,
                        s.Estatus, s.IdValeGoVale, s.CodigoQR, s.UrlQR, s.Notas,
                        s.FechaAprobado, s.IdAprobador, s.IdAutomovil, s.IdCliente, s.Kilometros,
                        a.MarcaModelo AS Vehiculo,
                        cl.Cliente AS Empresa,
                        u.Nombre AS Solicitante, ap.Nombre AS Aprobador
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Users u  ON s.IdSolicitante = u.Id
                    LEFT JOIN HUB_Users ap ON s.IdAprobador   = ap.Id
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN clientes cl ON s.IdCliente = cl.IdCliente
                    {where_sql}
                    ORDER BY s.FechaSolicitud DESC
                """, params)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching solicitudes vales: {e}")
        return []


def get_solicitud_vale_by_id(solicitud_id):
    """Obtiene una solicitud por ID."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.Id, s.FechaSolicitud, s.Placa, s.Descripcion, s.Cantidad, s.MontoUnit,
                           s.Estatus, s.IdValeGoVale, s.CodigoQR, s.UrlQR, s.Notas,
                           s.FechaAprobado, s.IdAprobador, s.IdSolicitante, s.IdAutomovil, s.IdCliente,
                           s.Kilometros,
                           a.MarcaModelo AS Vehiculo, cl.Cliente AS Empresa,
                           u.Nombre AS Solicitante, ap.Nombre AS Aprobador
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Users u  ON s.IdSolicitante = u.Id
                    LEFT JOIN HUB_Users ap ON s.IdAprobador   = ap.Id
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN clientes cl ON s.IdCliente = cl.IdCliente
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching solicitud vale: {e}")
        return None


def aprobar_solicitud_vale(solicitud_id, id_aprobador):
    """Aprueba una solicitud de vale."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'APROBADO', IdAprobador = %s, FechaAprobado = GETDATE()
                    WHERE Id = %s AND Estatus = 'PENDIENTE'
                """, (int(id_aprobador), int(solicitud_id)))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Vales QR", f"Aprobó solicitud vale #{solicitud_id}")
                    return True
                return False
    except Exception as e:
        print(f"Error aprobando solicitud vale: {e}")
        return False


def rechazar_solicitud_vale(solicitud_id, id_aprobador, motivo=''):
    """Rechaza una solicitud de vale con motivo. Cambia estatus a RECHAZADO."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Placa, Descripcion FROM HUB_SolicitudVales WHERE Id = %s", (int(solicitud_id),))
                row = cur.fetchone()
                placa = row[0] if row else '?'
                desc = row[1] if row else '?'

                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'RECHAZADO',
                        MotivoRechazo = %s,
                        FechaRechazo = GETDATE(),
                        RechazadoPor = %s
                    WHERE Id = %s AND Estatus IN ('PENDIENTE', 'APROBADO')
                """, (motivo, int(id_aprobador), int(solicitud_id)))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Vales QR", f"Rechazó solicitud vale #{solicitud_id}: {placa} - {desc[:40]}. Motivo: {motivo[:60]}")
                    return True
                return False
    except Exception as e:
        print(f"Error rechazando solicitud vale: {e}")
        return False


def revertir_solicitud_pendiente(solicitud_id):
    """Revierte una solicitud de APROBADO a PENDIENTE (cuando falla la generación del vale)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'PENDIENTE', IdAprobador = NULL, FechaAprobado = NULL
                    WHERE Id = %s AND Estatus = 'APROBADO'
                """, (int(solicitud_id),))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Vales QR", f"Revertió solicitud vale #{solicitud_id} a PENDIENTE (fallo generación)")
                    return True
                return False
    except Exception as e:
        print(f"Error revirtiendo solicitud vale: {e}")
        return False


def marcar_vale_generado(solicitud_id, id_vale_govale, codigo_qr='', url_qr=''):
    """Marca una solicitud como GENERADO con datos del vale QR."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_SolicitudVales
                    SET Estatus = 'GENERADO', IdValeGoVale = %s, CodigoQR = %s, UrlQR = %s
                    WHERE Id = %s AND Estatus = 'APROBADO'
                """, (str(id_vale_govale), str(codigo_qr)[:2000], str(url_qr)[:500], int(solicitud_id)))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Vales QR", f"Vale generado: solicitud #{solicitud_id} → Go Vale ID {id_vale_govale}")
                    return True
                return False
    except Exception as e:
        print(f"Error marcando vale generado: {e}")
        return False


def get_vales_qr_pendientes_generar():
    """Obtiene solicitudes APROBADAS listas para generar vale QR."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.Id, s.FechaSolicitud, s.Placa, s.Descripcion, s.Cantidad, s.MontoUnit,
                           s.Estatus, s.Notas, u.Nombre AS Solicitante
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
                    WHERE s.Estatus = 'APROBADO'
                    ORDER BY s.FechaSolicitud ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching vales pendientes: {e}")
        return []


def get_resumen_vales_qr():
    """Resumen de solicitudes de vales QR para dashboard."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS Total,
                        SUM(CASE WHEN Estatus = 'PENDIENTE' THEN 1 ELSE 0 END) AS Pendientes,
                        SUM(CASE WHEN Estatus = 'APROBADO' THEN 1 ELSE 0 END) AS Aprobados,
                        SUM(CASE WHEN Estatus = 'GENERADO' THEN 1 ELSE 0 END) AS Generados,
                        SUM(CASE WHEN Estatus = 'RECHAZADO' THEN 1 ELSE 0 END) AS Rechazados,
                        SUM(CASE WHEN Estatus IN ('APROBADO','GENERADO') THEN Cantidad * MontoUnit ELSE 0 END) AS MontoTotal
                    FROM HUB_SolicitudVales
                """)
                return cur.fetchone() or {}
    except Exception as e:
        print(f"Error fetching resumen vales QR: {e}")
        return {}


# =============================================================================
# GO VALE - VOUCHERS CACHE (sync incremental desde Go Vale)
# =============================================================================

def get_govale_config(clave):
    """Obtiene valor de HUB_Config para Go Vale."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = %s", (clave,))
                row = cur.fetchone()
                return row['Valor'] if row else None
    except Exception:
        return None


def set_govale_config(clave, valor):
    """Guarda valor en HUB_Config para Go Vale."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_Config AS target
                    USING (SELECT %s AS Clave) AS source
                    ON target.Clave = source.Clave
                    WHEN MATCHED THEN
                        UPDATE SET Valor = %s
                    WHEN NOT MATCHED THEN
                        INSERT (Clave, Valor) VALUES (%s, %s);
                """, (clave, valor, clave, valor))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error set_govale_config: {e}")
        return False


def vale_exists_in_cache(qr_code):
    """Verifica si un vale ya esta en el cache."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM HUB_OxxoGas_VouchersCache WHERE QrCode = %s", (qr_code,))
                return cur.fetchone() is not None
    except Exception:
        return False


def upsert_vale_cache(qr_code, voucher_id=None, monto=0, saldo=0,
                      estatus='Desconocido', contacto='', empresa='',
                      qr_image=None):
    """Inserta o actualiza un vale en el cache de Go Vale."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_OxxoGas_VouchersCache AS target
                    USING (SELECT %s AS QrCode) AS source
                    ON target.QrCode = source.QrCode
                    WHEN MATCHED THEN
                        UPDATE SET Monto = %s, Saldo = %s, Estatus = %s,
                                   Contacto = %s, Empresa = %s,
                                   QrImage = ISNULL(%s, target.QrImage),
                                   FechaSincronizado = GETDATE()
                    WHEN NOT MATCHED THEN
                        INSERT (QrCode, VoucherId, Monto, Saldo, Estatus,
                                Contacto, Empresa, QrImage, FechaCreacionGoVale, FechaSincronizado)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, GETDATE(), GETDATE());
                """, (qr_code, monto, saldo, estatus, contacto, empresa,
                      qr_image, qr_code, voucher_id, monto, saldo, estatus,
                      contacto, empresa, qr_image))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error upsert_vale_cache: {e}")
        return False


def get_vales_cache(estatus=None, contacto=None, limit=200):
    """Obtiene vales del cache con filtros opcionales."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                query = "SELECT * FROM HUB_OxxoGas_VouchersCache WHERE 1=1"
                params = []
                if estatus and estatus != 'Todos':
                    query += " AND Estatus LIKE %s"
                    params.append(f"%{estatus}%")
                if contacto:
                    query += " AND Contacto LIKE %s"
                    params.append(f"%{contacto}%")
                query += " ORDER BY FechaSincronizado DESC"
                cur.execute(query, params)
                return cur.fetchall()[:limit]
    except Exception as e:
        print(f"Error get_vales_cache: {e}")
        return []


def get_vales_cache_count():
    """Cuenta vales en el cache."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM HUB_OxxoGas_VouchersCache")
                return cur.fetchone()[0]
    except Exception:
        return 0


def link_vales_cache_to_solicitudes():
    """Vincula vales del cache con solicitudes PENDIENTE/APROBADO que no tengan QR."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE s
                    SET s.CodigoQR = vc.QrCode,
                        s.QrImage = vc.QrImage,
                        s.Estatus = 'GENERADO',
                        s.FechaAprobado = GETDATE()
                    FROM HUB_SolicitudVales s
                    INNER JOIN HUB_OxxoGas_Mapeo m ON s.IdSolicitante = m.IdUsuario
                    INNER JOIN HUB_OxxoGas_VouchersCache vc ON vc.Contacto = m.ContactoOxxoGas
                    WHERE s.Estatus IN ('PENDIENTE', 'APROBADO')
                      AND s.CodigoQR IS NULL
                      AND vc.Monto = s.MontoUnit
                """)
                conn.commit()
                linked = cur.rowcount
                if linked > 0:
                    log_user_activity("Vales QR", f"Worker vinculó {linked} vales del cache con solicitudes")
                return linked
    except Exception as e:
        print(f"Error link_vales_cache_to_solicitudes: {e}")
        return 0


def get_oxxogas_mapeo():
    """Obtiene todos los mapeos activos."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, IdAutomovil, Placa, ContactoOxxoGas, Activo
                    FROM HUB_OxxoGas_Mapeo
                    WHERE Activo = 1
                    ORDER BY Placa
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching oxxogas mapeo: {e}")
        return []


def get_oxxogas_mapeo_by_automovil(id_automovil):
    """Obtiene mapeo por IdAutomovil."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdAutomovil, Placa, EmpresaOxxoGas, ContactoOxxoGas
                    FROM HUB_OxxoGas_Mapeo
                    WHERE IdAutomovil = %s AND Activo = 1
                """, (int(id_automovil),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching oxxogas mapeo by automovil: {e}")
        return None


def get_oxxogas_mapeo_by_placa(placa):
    """Obtiene mapeo por placa (compatibilidad)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, IdAutomovil, Placa, EmpresaOxxoGas, ContactoOxxoGas
                    FROM HUB_OxxoGas_Mapeo
                    WHERE Placa = %s AND Activo = 1
                """, (placa.strip(),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching oxxogas mapeo by placa: {e}")
        return None


def get_oxxogas_mapeo_by_usuario_automovil(id_usuario, placa):
    """Obtiene mapeo por usuario y placa (compatibilidad)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, IdAutomovil, Placa, ContactoOxxoGas
                    FROM HUB_OxxoGas_Mapeo
                    WHERE IdUsuario = %s AND (Placa = %s OR Placa = '' OR Placa IS NULL) AND Activo = 1
                """, (int(id_usuario), placa.strip()))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching oxxogas mapeo by usuario+placa: {e}")
        return None


def get_oxxogas_mapeo_by_usuario(id_usuario):
    """Obtiene mapeo por solo usuario (para mapeo Usuario → Contacto)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Id, IdUsuario, IdAutomovil, Placa, ContactoOxxoGas
                    FROM HUB_OxxoGas_Mapeo
                    WHERE IdUsuario = %s AND Activo = 1
                """, (int(id_usuario),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching oxxogas mapeo by usuario: {e}")
        return None


def upsert_oxxogas_mapeo(id_usuario, id_automovil, placa, contacto_oxxogas, user_id):
    """Crea o actualiza un mapeo (solo usuario + contacto; empresa es fija)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Verificar si existe
                cur.execute("SELECT Id FROM HUB_OxxoGas_Mapeo WHERE IdUsuario = %s AND IdAutomovil = %s", (int(id_usuario), int(id_automovil)))
                existing = cur.fetchone()
                if existing:
                    cur.execute("""
                        UPDATE HUB_OxxoGas_Mapeo
                        SET Placa = %s, ContactoOxxoGas = %s, Activo = 1
                        WHERE IdUsuario = %s AND IdAutomovil = %s
                    """, (placa.strip(), contacto_oxxogas.strip(), int(id_usuario), int(id_automovil)))
                else:
                    cur.execute("""
                        INSERT INTO HUB_OxxoGas_Mapeo (IdUsuario, IdAutomovil, Placa, ContactoOxxoGas, Activo)
                        VALUES (%s, %s, %s, %s, 1)
                    """, (int(id_usuario), int(id_automovil), placa.strip(), contacto_oxxogas.strip()))
                conn.commit()
                log_user_activity("Vales QR", f"Mapeo OxxoGas guardado: user={id_usuario}, auto={id_automovil} -> contacto={contacto_oxxogas}")
                return True
    except Exception as e:
        print(f"Error upsert oxxogas mapeo: {e}")
        return False


def upsert_oxxogas_mapeo_usuario(id_usuario, contacto_oxxogas, user_id):
    """Crea o actualiza mapeo solo por usuario (sin vehículo). Usa IdAutomovil=0 y Placa=''."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT Id FROM HUB_OxxoGas_Mapeo WHERE IdUsuario = %s AND IdAutomovil = 0", (int(id_usuario),))
                existing = cur.fetchone()
                if existing:
                    cur.execute("""
                        UPDATE HUB_OxxoGas_Mapeo
                        SET ContactoOxxoGas = %s, Activo = 1
                        WHERE IdUsuario = %s AND IdAutomovil = 0
                    """, (contacto_oxxogas.strip(), int(id_usuario)))
                else:
                    cur.execute("""
                        INSERT INTO HUB_OxxoGas_Mapeo (IdUsuario, IdAutomovil, Placa, ContactoOxxoGas, Activo)
                        VALUES (%s, 0, '', %s, 1)
                    """, (int(id_usuario), contacto_oxxogas.strip()))
                conn.commit()
                log_user_activity("Vales QR", f"Mapeo OxxoGas usuario guardado: user={id_usuario} -> contacto={contacto_oxxogas}")
                return True
    except Exception as e:
        print(f"Error upsert oxxogas mapeo usuario: {e}")
        return False


def delete_oxxogas_mapeo(mapeo_id, user_id):
    """Desactiva un mapeo (soft delete)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_OxxoGas_Mapeo SET Activo = 0 WHERE Id = %s", (int(mapeo_id),))
                conn.commit()
                if cur.rowcount > 0:
                    log_user_activity("Vales QR", f"Mapeo OxxoGas eliminado: Id={mapeo_id}")
                    return True
                return False
    except Exception as e:
        print(f"Error deleting oxxogas mapeo: {e}")
        return False


def get_automoviles_para_mapeo():
    """Obtiene automóviles que NO tienen mapeo para el usuario actual."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT a.Id, a.Placa, a.Marca, a.Modelo, u.Nombre AS Conductor
                    FROM HUB_Automoviles a
                    LEFT JOIN HUB_Users u ON a.IdUsuarioAsignado = u.Id
                    LEFT JOIN HUB_OxxoGas_Mapeo m ON a.Id = m.IdAutomovil AND m.Activo = 1 AND m.IdUsuario = a.IdUsuarioAsignado
                    WHERE a.Activo = 1 AND m.Id IS NULL
                    ORDER BY a.Placa
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching automoviles para mapeo: {e}")
        return []


def get_oxxogas_empresas_contactos(user=None, pwd=None, force_refresh=False):
    """Obtiene contactos desde cache (o UI si force_refresh o cache vacío)."""
    # Primero intenta cache
    cache = get_oxxogas_contactos_cache()
    if cache and not force_refresh:
        return {'empresas': [], 'contactos': cache}
    
    # Fallback a UI automation
    try:
        from oxxogas_vales_automation import get_oxxogas_empresas_contactos as _impl
        data = _impl(user, pwd)
        if data.get('contactos'):
            update_oxxogas_contactos_cache(data['contactos'])
        return data
    except Exception as e:
        print(f"Error get_oxxogas_empresas_contactos: {e}")
        return {'empresas': [], 'contactos': []}


def get_oxxogas_contactos_cache():
    """Obtiene contactos desde cache DB."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT Contacto FROM HUB_OxxoGas_ContactosCache
                    WHERE Activo = 1
                    ORDER BY Contacto
                """)
                return [row['Contacto'] for row in cur.fetchall()]
    except Exception as e:
        print(f"Error get_oxxogas_contactos_cache: {e}")
        return []


def update_oxxogas_contactos_cache(contactos):
    """Actualiza cache con lista de contactos (MERGE)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for c in contactos:
                    c = c.strip()
                    if not c:
                        continue
                    cur.execute("""
                        MERGE HUB_OxxoGas_ContactosCache AS target
                        USING (SELECT %s AS Contacto, 'CONTACTO' AS Tipo) AS source
                        ON target.Contacto = source.Contacto
                        WHEN MATCHED THEN
                            UPDATE SET FechaActualizacion = GETDATE(), Activo = 1
                        WHEN NOT MATCHED THEN
                            INSERT (Contacto, Tipo, FechaActualizacion, Activo)
                            VALUES (source.Contacto, source.Tipo, GETDATE(), 1);
                    """, (c,))
                conn.commit()
        return True
    except Exception as e:
        print(f"Error update_oxxogas_contactos_cache: {e}")
        return False


def get_oxxogas_cache_info():
    """Info del cache: último update, count."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT 
                        MAX(FechaActualizacion) AS UltimaActualizacion,
                        COUNT(*) AS TotalContactos
                    FROM HUB_OxxoGas_ContactosCache
                    WHERE Activo = 1
                """)
                return cur.fetchone() or {}
    except Exception as e:
        print(f"Error get_oxxogas_cache_info: {e}")
        return {}


# =============================================================================
# ZERO TIER DEVICE MANAGEMENT
# =============================================================================

def get_zerotier_config():
    """Lee subnet y enabled desde HUB_Config."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Clave, Valor FROM HUB_Config WHERE Clave IN ('zerotier_subnet', 'zerotier_enabled')")
                return {row['Clave']: row['Valor'] for row in cur.fetchall()}
    except Exception:
        return {'zerotier_subnet': '10.147.', 'zerotier_enabled': '1'}

def is_zerotier_ip(client_ip: str) -> bool:
    """True si la IP está en la subnet ZeroTier configurada."""
    cfg = get_zerotier_config()
    if cfg.get('zerotier_enabled', '1') != '1':
        return False
    subnet = cfg.get('zerotier_subnet', '10.147.')
    return client_ip.startswith(subnet)

def get_user_by_zerotier_ip(zt_ip: str):
    """Devuelve user_email si el dispositivo está registrado y activo."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT UserEmail FROM HUB_ZeroTierDevices
                    WHERE ZeroTierIP = %s AND IsActive = 1
                """, (zt_ip,))
                row = cur.fetchone()
                return row['UserEmail'] if row else None
    except Exception:
        return None

def register_zerotier_device(zt_ip: str, user_email: str, device_name: str = None):
    """Registra o actualiza dispositivo ZeroTier para un usuario (upsert)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_ZeroTierDevices AS target
                    USING (SELECT %s AS ZeroTierIP) AS source
                    ON target.ZeroTierIP = source.ZeroTierIP
                    WHEN MATCHED THEN
                        UPDATE SET UserEmail = %s, DeviceName = ISNULL(%s, target.DeviceName),
                                   LastSeenAt = GETDATE(), IsActive = 1
                    WHEN NOT MATCHED THEN
                        INSERT (ZeroTierIP, UserEmail, DeviceName)
                        VALUES (%s, %s, %s);
                """, (zt_ip, user_email, device_name, zt_ip, user_email, device_name))
                conn.commit()
                return True
    except Exception as e:
        print(f"register_zerotier_device error: {e}")
        return False

def get_user_zerotier_devices(user_email: str):
    """Lista dispositivos ZeroTier de un usuario (para vista de admin)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT ZeroTierIP, DeviceName, RegisteredAt, LastSeenAt, IsActive
                    FROM HUB_ZeroTierDevices WHERE UserEmail = %s ORDER BY LastSeenAt DESC
                """, (user_email,))
                return cur.fetchall()
    except Exception:
        return []


def get_user_zerotier_ip(user_email: str):
    """Obtiene la IP ZeroTier activa del usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT ZeroTierIP FROM HUB_ZeroTierDevices
                    WHERE UserEmail = %s AND IsActive = 1
                    ORDER BY LastSeenAt DESC
                """, (user_email,))
                row = cur.fetchone()
                return row['ZeroTierIP'] if row else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO: DETECCIÓN DE DISPOSITIVOS POR RED
# ═══════════════════════════════════════════════════════════════════════════════

def get_network_config():
    """Lee configuración del módulo desde HUB_Config (claves net_*)."""
    config = {}
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Clave, Valor FROM HUB_Config WHERE Clave LIKE 'net_%'")
                for row in cur.fetchall():
                    config[row['Clave']] = row['Valor']
    except Exception as e:
        print(f"Error leyendo network config: {e}")
    return config


def save_network_config(config_dict):
    """Guarda configuración del módulo en HUB_Config (upsert por clave)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for clave, valor in config_dict.items():
                    cur.execute("""
                        MERGE HUB_Config AS target
                        USING (SELECT %s AS Clave) AS source
                        ON target.Clave = source.Clave
                        WHEN MATCHED THEN
                            UPDATE SET Valor = %s, Actualizado = GETDATE()
                        WHEN NOT MATCHED THEN
                            INSERT (Clave, Valor) VALUES (%s, %s);
                    """, (clave, str(valor), clave, str(valor)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error guardando network config: {e}")
        return False


def get_network_devices():
    """Lista completa de dispositivos conocidos con info de usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, d.Activo, d.FechaRegistro, d.Notas,
                           u.Nombre AS NombreUsuario, u.Email AS EmailUsuario,
                           ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    ORDER BY d.FechaRegistro DESC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching network devices: {e}")
        return []


def get_network_device_by_mac(mac):
    """Busca un dispositivo por MAC address."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, d.Activo, d.FechaRegistro, d.Notas,
                           u.Nombre AS NombreUsuario,
                           ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    WHERE LTRIM(RTRIM(d.MACAddress)) = %s
                """, (mac.strip().upper(),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching device by MAC: {e}")
        return None


def get_network_device_by_id(device_id):
    """Busca un dispositivo por ID."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, d.Activo, d.FechaRegistro, d.Notas,
                           u.Nombre AS NombreUsuario,
                           ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    WHERE d.Id = %s
                """, (int(device_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching device by ID: {e}")
        return None


def add_network_device(mac, nombre, id_usuario=None, tipo='CELULAR', notas=''):
    """Registra un dispositivo conocido."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_NetworkDevices (MACAddress, NombreDispositivo, IdUsuario, Tipo, Notas)
                    VALUES (%s, %s, %s, %s, %s)
                """, (mac.strip().upper(), nombre.strip() if nombre else None,
                      int(id_usuario) if id_usuario else None, tipo.strip(), notas.strip() if notas else None))
                conn.commit()
                log_user_activity("Detección de Red", f"Dispositivo registrado: {mac} ({nombre})")
                return True
    except Exception as e:
        print(f"Error adding network device: {e}")
        return False


def update_network_device(device_id, mac=None, nombre=None, id_usuario=None, tipo=None, activo=None, notas=None):
    """Actualiza un dispositivo conocido (solo campos no None)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sets = []
                params = []
                if mac is not None:
                    sets.append("MACAddress = %s")
                    params.append(mac.strip().upper())
                if nombre is not None:
                    sets.append("NombreDispositivo = %s")
                    params.append(nombre.strip() if nombre else None)
                if id_usuario is not None:
                    sets.append("IdUsuario = %s")
                    params.append(int(id_usuario) if id_usuario else None)
                if tipo is not None:
                    sets.append("Tipo = %s")
                    params.append(tipo.strip())
                if activo is not None:
                    sets.append("Activo = %s")
                    params.append(int(activo))
                if notas is not None:
                    sets.append("Notas = %s")
                    params.append(notas.strip() if notas else None)
                if not sets:
                    return False
                params.append(int(device_id))
                cur.execute(f"UPDATE HUB_NetworkDevices SET {', '.join(sets)} WHERE Id = %s", params)
                conn.commit()
                log_user_activity("Detección de Red", f"Dispositivo #{device_id} actualizado")
                return True
    except Exception as e:
        print(f"Error updating network device: {e}")
        return False


def delete_network_device(device_id):
    """Elimina un dispositivo conocido y su estado."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_NetworkState WHERE IdDispositivo = %s", (int(device_id),))
                cur.execute("DELETE FROM HUB_NetworkPresence WHERE IdDispositivo = %s", (int(device_id),))
                cur.execute("DELETE FROM HUB_NetworkDevices WHERE Id = %s", (int(device_id),))
                conn.commit()
                log_user_activity("Detección de Red", f"Dispositivo #{device_id} eliminado")
                return True
    except Exception as e:
        print(f"Error deleting network device: {e}")
        return False


def link_device_to_user(device_id, user_id):
    """Vincula un dispositivo a un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_NetworkDevices SET IdUsuario = %s WHERE Id = %s",
                           (int(user_id) if user_id else None, int(device_id)))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error linking device to user: {e}")
        return False


def get_all_device_states():
    """Retorna estado actual de todos los dispositivos activos."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk,
                           u.Nombre AS NombreUsuario
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE d.Activo = 1
                    ORDER BY u.Nombre ASC
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching device states: {e}")
        return []


def get_presence_history(fecha_inicio=None, fecha_fin=None, user_id=None, limit=500):
    """Historial de eventos de presencia con filtros."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                query = """
                    SELECT p.Id, p.IdDispositivo, p.FechaHora, p.TipoEvento,
                           p.Confianza, p.Notas,
                           d.MACAddress, d.NombreDispositivo, d.Tipo,
                           u.Nombre AS NombreUsuario
                    FROM HUB_NetworkPresence p
                    JOIN HUB_NetworkDevices d ON p.IdDispositivo = d.Id
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE 1=1
                """
                params = []
                if fecha_inicio:
                    query += " AND p.FechaHora >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    query += " AND p.FechaHora <= %s"
                    params.append(fecha_fin)
                if user_id:
                    query += " AND d.IdUsuario = %s"
                    params.append(int(user_id))
                query += " ORDER BY p.FechaHora DESC"
                cur.execute(query, params)
                rows = cur.fetchall()
                return rows[:limit] if limit else rows
    except Exception as e:
        print(f"Error fetching presence history: {e}")
        return []


def get_latest_scan_batch():
    """Obtiene el lote más reciente de resultados de scan (agrupado por FechaScan)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 FechaScan, COUNT(*) AS DispositivosDetectados
                    FROM HUB_NetworkScanResults
                    WHERE Procesado = 1
                    GROUP BY FechaScan
                    ORDER BY FechaScan DESC
                """)
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching latest scan batch: {e}")
        return None


def get_latest_scan_results():
    """Detalle del último scan procesado."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 FechaScan
                    FROM HUB_NetworkScanResults
                    WHERE Procesado = 1
                    ORDER BY FechaScan DESC
                """)
                row = cur.fetchone()
                if not row:
                    return []
                cur.execute("""
                    SELECT s.MACAddress, s.IP, s.Hostname, s.FechaScan,
                           d.NombreDispositivo, d.Tipo,
                           u.Nombre AS NombreUsuario,
                           CASE WHEN d.Id IS NOT NULL THEN 1 ELSE 0 END AS Conocido
                    FROM HUB_NetworkScanResults s
                    LEFT JOIN HUB_NetworkDevices d ON LTRIM(RTRIM(s.MACAddress)) = LTRIM(RTRIM(d.MACAddress)) AND d.Activo = 1
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE s.FechaScan = %s
                    ORDER BY s.MACAddress
                """, (row['FechaScan'],))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching latest scan results: {e}")
        return []


def register_scan_result(mac, ip, hostname):
    """Guarda un resultado individual de scan (usado por el worker del host)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_NetworkScanResults (MACAddress, IP, Hostname, FechaScan, Procesado)
                    VALUES (%s, %s, %s, GETDATE(), 0)
                """, (mac.strip().upper(), ip, hostname.strip() if hostname else None))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error registering scan result: {e}")
        return False


def get_unprocessed_scans():
    """Obtiene MACs del scan más reciente no procesado."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 FechaScan
                    FROM HUB_NetworkScanResults
                    WHERE Procesado = 0
                    ORDER BY FechaScan DESC
                """)
                row = cur.fetchone()
                if not row:
                    return None, set()
                fecha = row['FechaScan']
                cur.execute("SELECT MACAddress FROM HUB_NetworkScanResults WHERE FechaScan = %s AND Procesado = 0", (fecha,))
                macs = {r['MACAddress'].strip().upper() for r in cur.fetchall()}
                return fecha, macs
    except Exception as e:
        print(f"Error fetching unprocessed scans: {e}")
        return None, set()


def mark_scan_processed(fecha_scan):
    """Marca un lote de scan como procesado."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_NetworkScanResults SET Procesado = 1 WHERE FechaScan = %s AND Procesado = 0", (fecha_scan,))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error marking scan processed: {e}")
        return False


def update_device_state(device_id, estado, momento=None):
    """
    Actualiza el estado de un dispositivo (AQUI/FUERA) con el instante del
    ESCANEO que produjo el cambio.

    `momento` es el `FechaScan`, no la hora de proceso. En la rama FUERA antes no
    se guardaba ningún instante (solo el estado), con lo que se perdía el
    "última vez que se vio" y con eso el techo de la ventana de salida.
    """
    import datetime
    if momento is None:
        momento = now_mexico()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if estado == 'AQUI':
                    cur.execute("""
                        MERGE HUB_NetworkState AS target
                        USING (SELECT %s AS IdDispositivo) AS source
                        ON target.IdDispositivo = source.IdDispositivo
                        WHEN MATCHED THEN
                            UPDATE SET Estado = %s, UltimaVezEnRed = %s, UltimoScanOk = %s
                        WHEN NOT MATCHED THEN
                            INSERT (IdDispositivo, Estado, UltimaVezEnRed, UltimoScanOk)
                            VALUES (%s, %s, %s, %s);
                    """, (device_id, estado, momento, momento,
                          device_id, estado, momento, momento))
                else:
                    # UltimoScanOk NO se toca: sigue siendo la última vez que se
                    # vio el equipo, que es el piso de la salida. Lo que se
                    # guarda es UltimaVezEnRed = cuándo se constató la ausencia.
                    cur.execute("""
                        MERGE HUB_NetworkState AS target
                        USING (SELECT %s AS IdDispositivo) AS source
                        ON target.IdDispositivo = source.IdDispositivo
                        WHEN MATCHED THEN
                            UPDATE SET Estado = %s, UltimaVezEnRed = %s
                        WHEN NOT MATCHED THEN
                            INSERT (IdDispositivo, Estado, UltimaVezEnRed)
                            VALUES (%s, %s, %s);
                    """, (device_id, estado, momento, device_id, estado, momento))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error updating device state: {e}")
        return False


def register_presence_event(device_id, tipo_evento, confianza=95.0, notas='',
                           fecha_deteccion=None, ultima_vez_visto=None,
                           incertidumbre_min=None, origen='RED'):
    """
    Registra un evento de presencia (ENTRADA/SALIDA) con su EVIDENCIA.

    Las dos columnas de fecha tienen papeles distintos y por eso existen las dos:

        FechaHora     GETDATE(): cuándo lo procesó el worker. Sirve para
                      diagnosticar el retraso del propio worker.
        FechaDeteccion  el instante del ESCANEO que prueba el evento. Es lo que
                      usa la asistencia.

    Antes se sellaba todo con GETDATE(), y por eso la entrada salía 1-4 minutos
    después de la real: el desfase no era de la red, era del proceso.

    `ultima_vez_visto` es el otro extremo de la ventana: en una ENTRADA, la
    última vez que se vio el equipo AUSENTE (piso de la llegada); en una
    SALIDA, la última vez que se vio PRESENTE (piso de la salida). Con los dos,
    la asistencia sabe que pasó en un intervalo y no en un minuto inventado.

    OJO con el orden del despliegue: si el código llega antes que la migración
    0042, el INSERT con las columnas nuevas falla y se dejarían de registrar
    eventos, o sea, se dejaría de generar asistencia sin que nada más avise. Por
    eso, si el INSERT completo falla, se reintenta con el INSERT viejo (5
    columnas): la asistencia sigue siendo inexacta, pero no se pierde nada
    mientras se aplica la migración.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_NetworkPresence
                        (IdDispositivo, FechaHora, TipoEvento, Confianza, Notas,
                         FechaDeteccion, UltimaVezVisto, VentanaMin, Origen)
                    VALUES (%s, GETDATE(), %s, %s, %s, %s, %s, %s, %s)
                """, (device_id, tipo_evento, confianza,
                      notas.strip() if notas else None,
                      fecha_deteccion, ultima_vez_visto,
                      incertidumbre_min, origen))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error registrando evento de presencia: {e}")
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO HUB_NetworkPresence
                            (IdDispositivo, FechaHora, TipoEvento, Confianza, Notas)
                        VALUES (%s, GETDATE(), %s, %s, %s)
                    """, (device_id, tipo_evento, confianza,
                          notas.strip() if notas else None))
                    conn.commit()
                    return True
        except Exception as e2:
            print(f"Error registrando evento (tampoco con el INSERT simple): {e2}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO: HORARIOS Y ASISTENCIAS
# ════════════════════════════════════════════════════════════════════════════════

# --- TURNOS ---

def get_turnos(activos_solo=True):
    """Lista todos los turnos."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                q = "SELECT * FROM HUB_Turnos WHERE 1=1"
                if activos_solo:
                    q += " AND Activo = 1"
                q += " ORDER BY Nombre"
                cur.execute(q)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching turnos: {e}")
        return []


def get_turno_by_id(turno_id):
    """Obtiene un turno por ID."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT * FROM HUB_Turnos WHERE Id = %s", (int(turno_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching turno: {e}")
        return None


def add_turno(nombre, descripcion, lv_entrada, lv_salida, sab_entrada=None, sab_salida=None,
              dom_entrada=None, dom_salida=None, tol_llegada=15, tol_salida_antes=5, tol_salida_despues=0, activo=True):
    """Crea un nuevo turno."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_Turnos (Nombre, Descripcion, LV_Entrada, LV_Salida, Sab_Entrada, Sab_Salida,
                        Dom_Entrada, Dom_Salida, Tol_Llegada_Min, Tol_Salida_Antes_Min, Tol_Salida_Despues_Min, Activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (nombre.strip(), descripcion.strip() if descripcion else None,
                      lv_entrada, lv_salida, sab_entrada, sab_salida,
                      dom_entrada, dom_salida, int(tol_llegada), int(tol_salida_antes), int(tol_salida_despues), int(activo)))
                conn.commit()
                log_user_activity("Horarios", f"Turno creado: {nombre}")
                return True
    except Exception as e:
        print(f"Error adding turno: {e}")
        return False


def update_turno(turno_id, **kwargs):
    """Actualiza un turno (solo campos proporcionados)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sets = []
                params = []
                campos_map = {
                    'nombre': 'Nombre',
                    'descripcion': 'Descripcion',
                    'lv_entrada': 'LV_Entrada',
                    'lv_salida': 'LV_Salida',
                    'sab_entrada': 'Sab_Entrada',
                    'sab_salida': 'Sab_Salida',
                    'dom_entrada': 'Dom_Entrada',
                    'dom_salida': 'Dom_Salida',
                    'tol_llegada': 'Tol_Llegada_Min',
                    'tol_salida_antes': 'Tol_Salida_Antes_Min',
                    'tol_salida_despues': 'Tol_Salida_Despues_Min',
                    'activo': 'Activo',
                }
                for k, v in kwargs.items():
                    if k in campos_map and v is not None:
                        sets.append(f"{campos_map[k]} = %s")
                        params.append(v)
                if not sets:
                    return False
                params.append(int(turno_id))
                cur.execute(f"UPDATE HUB_Turnos SET {', '.join(sets)} WHERE Id = %s", params)
                conn.commit()
                log_user_activity("Horarios", f"Turno #{turno_id} actualizado")
                return True
    except Exception as e:
        print(f"Error updating turno: {e}")
        return False


def delete_turno(turno_id):
    """Elimina un turno (si no tiene usuarios asignados)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Verificar si hay usuarios asignados
                cur.execute("SELECT COUNT(*) FROM HUB_UsuarioTurno WHERE IdTurno = %s AND Activo = 1", (int(turno_id),))
                if cur.fetchone()[0] > 0:
                    print("Turno tiene usuarios asignados")
                    return False
                cur.execute("DELETE FROM HUB_Turnos WHERE Id = %s", (int(turno_id),))
                conn.commit()
                log_user_activity("Horarios", f"Turno #{turno_id} eliminado")
                return True
    except Exception as e:
        print(f"Error deleting turno: {e}")
        return False


# --- ASIGNACIÓN USUARIO-TURNO ---

def get_usuario_turno_actual(user_id):
    """Obtiene el turno activo actual de un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT ut.*, t.Nombre AS TurnoNombre, t.LV_Entrada, t.LV_Salida, t.Sab_Entrada, t.Sab_Salida,
                           t.Dom_Entrada, t.Dom_Salida,
                           t.Tol_Llegada_Min, t.Tol_Salida_Antes_Min
                    FROM HUB_UsuarioTurno ut
                    JOIN HUB_Turnos t ON ut.IdTurno = t.Id
                    WHERE ut.IdUsuario = %s AND ut.Activo = 1
                      AND (ut.FechaHasta IS NULL OR ut.FechaHasta >= CAST(GETDATE() AS DATE))
                    ORDER BY ut.FechaDesde DESC
                """, (int(user_id),))
                return cur.fetchone()
    except Exception as e:
        print(f"Error fetching usuario turno: {e}")
        return None


def get_all_usuario_turnos():
    """Lista todas las asignaciones usuario-turno activas."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT ut.Id, ut.IdUsuario, ut.IdTurno, ut.FechaDesde, ut.FechaHasta, ut.Activo,
                           u.Nombre AS UsuarioNombre, u.Email,
                           t.Nombre AS TurnoNombre, t.LV_Entrada, t.LV_Salida, t.Sab_Entrada, t.Sab_Salida
                    FROM HUB_UsuarioTurno ut
                    JOIN HUB_Users u ON ut.IdUsuario = u.Id
                    JOIN HUB_Turnos t ON ut.IdTurno = t.Id
                    WHERE ut.Activo = 1
                    ORDER BY u.Nombre
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching usuario turnos: {e}")
        return []


def asignar_turno_usuario(user_id, turno_id, fecha_desde=None, fecha_hasta=None):
    """Asigna un turno a un usuario (desactiva asignación anterior)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Desactivar asignación anterior
                cur.execute("""
                    UPDATE HUB_UsuarioTurno SET Activo = 0
                    WHERE IdUsuario = %s AND Activo = 1
                """, (int(user_id),))
                # Crear nueva
                if fecha_desde is None:
                    fecha_desde = 'GETDATE()'
                else:
                    fecha_desde = f"'{fecha_desde}'"
                if fecha_hasta:
                    fecha_hasta_sql = f"'{fecha_hasta}'"
                else:
                    fecha_hasta_sql = 'NULL'
                cur.execute(f"""
                    INSERT INTO HUB_UsuarioTurno (IdUsuario, IdTurno, FechaDesde, FechaHasta, Activo)
                    VALUES (%s, %s, {fecha_desde}, {fecha_hasta_sql}, 1)
                """, (int(user_id), int(turno_id)))
                conn.commit()
                log_user_activity("Horarios", f"Turno asignado a usuario #{user_id}")
                return True
    except Exception as e:
        print(f"Error asignando turno: {e}")
        return False


def desactivar_turno_usuario(user_id):
    """Desactiva el turno actual de un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE HUB_UsuarioTurno SET Activo = 0 WHERE IdUsuario = %s AND Activo = 1", (int(user_id),))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error desactivando turno usuario: {e}")
        return False


# --- CÁLCULO DE ASISTENCIAS ---

def _get_horario_dia(turno, fecha):
    """Obtiene hora entrada/salida para una fecha según turno."""
    import datetime
    dia_semana = fecha.weekday()  # 0=Lunes, 5=Sábado, 6=Domingo
    if dia_semana <= 4:  # Lunes-Viernes
        return turno.get('LV_Entrada'), turno.get('LV_Salida')
    elif dia_semana == 5:  # Sábado
        return turno.get('Sab_Entrada'), turno.get('Sab_Salida')
    else:  # Domingo
        return turno.get('Dom_Entrada'), turno.get('Dom_Salida')


# --- Ventana de evidencia ---------------------------------------------------
# La asistencia se infiere de la red, así que el dato nunca es un minuto: es un
# intervalo. Estas dos funciones son las que convierten la línea de tiempo de
# escaneos en ese intervalo.

def _ventana_turno(fecha, entrada_esperada, salida_esperada, margen_min):
    """
    (desde, hasta) en datetimes: el margen alrededor del turno dentro del cual
    se considera que hay evidencia de presencia.

    Sirve para que un equipo encendido de madrugada no cuente como "llegó a las
    00:15". Antes el fallback tomaba el primer escaneo del día y ese equipo
    salía como puntual toda la semana sin que nadie lo notara.
    """
    import datetime
    desde = datetime.datetime.combine(fecha, entrada_esperada) - datetime.timedelta(minutes=margen_min)
    hasta = datetime.datetime.combine(fecha, salida_esperada) + datetime.timedelta(minutes=margen_min)
    return desde, hasta


def _linea_de_escaneos(desde, hasta):
    """
    Los instantes en que se escaneó la red dentro de [desde, hasta], en orden.

    OJO con por qué esto NO es "SELECT COUNT(*)": save_results() no escribe nada
    cuando un escaneo no detecta equipos, así que de madrugada (oficina vacía)
    un escáner sano no deja ninguna fila. Por eso el conteo solo NO prueba que
    el sistema estuviera vivo; los huecos entre escaneos sí.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT DISTINCT FechaScan
                    FROM HUB_NetworkScanResults
                    WHERE FechaScan >= %s AND FechaScan <= %s
                    ORDER BY FechaScan
                """, (desde, hasta))
                return [r['FechaScan'] for r in cur.fetchall()]
    except Exception as e:
        print(f"Error leyendo línea de escaneos: {e}")
        return []


def _hueco_maximo(escaneos, desde, hasta):
    """
    El hueco más largo ENTRE escaneos consecutivos, en minutos, o None si hay
    menos de dos. Un hueco grande significa que el escáner se estuvo caído, y
    por eso ese día no se puede afirmar nada (SIN DATOS, no AUSENTE).

    OJO con lo que NO se hace: NO se mide el hueco desde el último escaneo hasta
    el fin de la ventana, ni del inicio de la ventana al primer escaneo. Se
    podría, y parecería más estricto, pero sería un falso positivo enorme:
    save_results() no escribe NADA cuando un escaneo no detecta equipos, así que
    una oficina vacía (temprano en la mañana, o al final del día) no deja ni una
    fila. Medir esos bordes convertiría "la gente se fue a las 15:00" en "el
    escáner se cayó 5 horas" y marcaría el día SIN DATOS. La cobertura del día
    la mide el conteo (`escaneos_minimos`); el hueco, solo los huecos de
    verdad, los del medio.
    """
    import datetime
    if not escaneos or len(escaneos) < 2:
        return None
    return int(max((b - a).total_seconds() / 60.0
                   for a, b in zip(escaneos, escaneos[1:])))


def _piso_de_llegada(escaneos, techo, respaldo):
    """
    El extremo temprano de la llegada: el escaneo INMEDIATAMENTE anterior al que
    lo vio, porque en ese momento el equipo no estaba. Ese escaneo es la
    última prueba de que aún no había llegado, y es lo que hace que la ventana
    mida lo que mide el muestreo (un intervalo) y no días.
    """
    anteriores = [e for e in escaneos if e < techo]
    if anteriores:
        return max(anteriores)
    return respaldo  # evento sin línea de tiempo: se usa la evidencia del evento


def _techo_de_salida(escaneos, piso, respaldo):
    """
    El extremo tardío de la salida: el escaneo INMEDIATAMENTE posterior al
    último que lo vio. A partir de ahí ya no estaba, así que se fue en el
    medio. Es el mismo intervalo que el de la llegada, por construcción.
    """
    posteriores = [e for e in escaneos if e > piso]
    if posteriores:
        return min(posteriores)
    return respaldo


def _eventos_del_usuario(user_id, desde, hasta):
    """
    Eventos de presencia del usuario en la ventana, con su evidencia.

    `FechaDeteccion` es el instante del ESCANEO que prueba el evento;
    `FechaHora` quedó como el instante en que el worker lo procesó. La
    diferencia entre las dos columnas ES el retraso del worker, medido.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT np.TipoEvento,
                           COALESCE(np.FechaDeteccion, np.FechaHora) AS Deteccion,
                           np.UltimaVezVisto,
                           np.VentanaMin
                    FROM HUB_NetworkPresence np
                    JOIN HUB_NetworkDevices nd ON np.IdDispositivo = nd.Id
                    WHERE nd.IdUsuario = %s
                      AND COALESCE(np.FechaDeteccion, np.FechaHora) >= %s
                      AND COALESCE(np.FechaDeteccion, np.FechaHora) <= %s
                    ORDER BY COALESCE(np.FechaDeteccion, np.FechaHora)
                """, (int(user_id), desde, hasta))
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching eventos: {e}")
        return []


def _config_asistencia():
    """Parámetros del cálculo, con los defaults del código (migración 0042)."""
    import config_db
    valores = {
        'net_asistencia_ventana_min': '120',
        'net_asistencia_escaneos_minimos': '20',
        'net_asistencia_politica': 'PISO',
        'net_scan_interval_seg': '180',
    }
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Clave, Valor FROM HUB_Config WHERE Clave IN (%s)",
                    tuple(valores))
                for row in cur.fetchall():
                    valores[row['Clave']] = row['Valor']
    except Exception as e:
        print(f"Error leyendo config de asistencia: {e}")
    try:
        ventana = int(valores['net_asistencia_ventana_min'])
        minimos = int(valores['net_asistencia_escaneos_minimos'])
        politica = (valores['net_asistencia_politica'] or 'PISO').upper()
        intervalo_min = max(1, int(valores['net_scan_interval_seg']) // 60)
    except (TypeError, ValueError):
        ventana, minimos, politica, intervalo_min = 120, 20, 'PISO', 3
    return {
        'ventana_min': ventana,
        'escaneos_minimos': minimos,
        'politica': politica,
        'intervalo_min': intervalo_min,
        # Tres intervalos seguidos sin escanear ya no es "el escáner se atrasó",
        # es que no hubo datos. Con el intervalo por defecto son 9 minutos.
        'gap_maximo_min': max(15, intervalo_min * 3),
    }


def calcular_asistencia_dia(user_id, fecha=None):
    """
    Calcula la asistencia de un usuario en una fecha a partir de la evidencia
    de red, y devuelve la VENTANA en la que podido entrar/salir, no un minuto.

    La lógica de veredicto vive en `asistencia_core` (funciones puras, con
    pruebas); aquí solo se traen los datos de la base y se le pasan.

    Claves del dict devuelto:
        estado                  CALCULADA | TARDE | SALIDA_TEMPRANA |
                               INDETERMINADO | AUSENTE | SIN_DATOS |
                               NO_APLICA
        entrada_piso/techo      la ventana de la llegada
        salida_piso/techo       la ventana de la salida
        minutos_tarde/antes     según la política configurada
        ventana_min   qué tan ancho es el dato
        escaneos_dia            evidencia de que el sistema estuvo escaneando
        gap_maximo_min          el hueco más largo entre escaneos
    """
    import datetime
    import asistencia_core as core

    if fecha is None:
        fecha = datetime.date.today()

    turno = get_usuario_turno_actual(user_id)
    if not turno:
        return {'error': 'Usuario sin turno asignado', 'estado': core.ESTADO_NO_APLICA,
                'fecha': fecha, 'sin_turno': True}

    entrada_esperada, salida_esperada = _get_horario_dia(turno, fecha)
    entrada_esperada = core.limpiar_hora(entrada_esperada)
    salida_esperada = core.limpiar_hora(salida_esperada)
    hay_turno = bool(entrada_esperada and salida_esperada)

    cfg = _config_asistencia()
    tol_llegada = int(turno.get('Tol_Llegada_Min', 15) or 0)
    tol_salida = int(turno.get('Tol_Salida_Antes_Min', 5) or 0)

    # Sin horario para ese día no se busca evidencia: no hay contra qué medir y
    # se marcaría AUSENTE al que solo tiene un día normal.
    if not hay_turno:
        return core.evaluar_dia(fecha, None, None, tol_llegada, tol_salida,
                                hay_turno=False, escaneos_dia=0,
                                escaneos_minimos=cfg['escaneos_minimos'])

    desde, hasta = _ventana_turno(fecha, entrada_esperada, salida_esperada,
                                  cfg['ventana_min'])
    escaneos = _linea_de_escaneos(desde, hasta)
    escaneos_dia = len(escaneos)
    gap_maximo = _hueco_maximo(escaneos, desde, hasta)

    # Si el escáner no cubrió el día, NO se marca ausente: no hay evidencia.
    escaneos_efectivos = escaneos_dia
    if gap_maximo is not None and gap_maximo > cfg['gap_maximo_min']:
        # Un hueco largo no es "faltaron escaneos": es que el sistema estuvo
        # caído un rato, y lo que hay a ambos lados no cubre la jornada.
        escaneos_efectivos = 0

    eventos = _eventos_del_usuario(user_id, desde, hasta)
    entradas = [e for e in eventos if e['TipoEvento'] == 'ENTRADA']
    salidas = [e for e in eventos if e['TipoEvento'] == 'SALIDA']

    entrada_piso = entrada_techo = None
    if entradas:
        entrada_techo = entradas[0]['Deteccion']
        entrada_piso = _piso_de_llegada(escaneos, entrada_techo,
                                        entradas[0].get('UltimaVezVisto'))

    salida_piso = salida_techo = None
    if salidas:
        # En la SALIDA los dos extremos están al revés que en la entrada: el
        # piso es la última vez que se vio PRESENTE (`UltimaVezVisto`) y el
        # techo es el escaneo que ya no lo vio. Por eso aquí no se usa
        # `Deteccion` como piso: eso ya es el techo.
        ultimo_presente = salidas[-1].get('UltimaVezVisto') or salidas[-1]['Deteccion']
        salida_piso = ultimo_presente
        salida_techo = _techo_de_salida(escaneos, ultimo_presente,
                                        salidas[-1]['Deteccion'])

    resultado = core.evaluar_dia(
        fecha=fecha,
        entrada_esperada=entrada_esperada,
        salida_esperada=salida_esperada,
        tol_llegada_min=tol_llegada,
        tol_salida_antes_min=tol_salida,
        entrada_piso=entrada_piso,
        entrada_techo=entrada_techo,
        salida_piso=salida_piso,
        salida_techo=salida_techo,
        escaneos_dia=escaneos_efectivos,
        escaneos_minimos=cfg['escaneos_minimos'],
        hay_turno=True,
        politica=cfg['politica'],
    )
    resultado.update({
        'turno_nombre': turno.get('Nombre'),
        'turno_id': turno.get('IdTurno') or turno.get('Id'),
        'gap_maximo_min': gap_maximo,
        'entrada_tardia': resultado.get('entrada_tardia', False),
        'salida_temprana': resultado.get('salida_temprana', False),
        'minutos_tarde': resultado.get('minutos_tarde'),
        'minutos_antes': resultado.get('minutos_antes'),
        'fuente': 'RED',
    })
    return resultado


def guardar_asistencia_diaria(user_id, fecha, datos):
    """
    Guarda/actualiza el registro diario de asistencia.

    Además de los minutos "declarados" (HoraEntradaReal/HoraSalidaReal, que se
    mantienen para no romper nada que ya los lea) se guarda la VENTANA completa
    y el estado. Lo declarado pasa a ser el extremo que decide la política
    configurada, y lo demás queda como evidencia para poder auditar y recalcular
    sin volver a escanear la red.
    """
    import asistencia_core as core

    estado = datos.get('estado') or core.ESTADO_CALCULADA
    entrada_piso = datos.get('entrada_piso')
    entrada_techo = datos.get('entrada_techo')
    salida_piso = datos.get('salida_piso')
    salida_techo = datos.get('salida_techo')

    # El minuto "declarado" es el que la política eligió: con PISO, el extremo
    # que favorece; con TECHO, el que castiga. Antes era el techo de la
    # llegada, que es el dato más caro de la ventana.
    def _hora_legible(valor):
        h = core.limpiar_hora(valor)
        return h.strftime('%H:%M:%S') if h else None

    params = (
        int(user_id), fecha,
        datos.get('turno_id'),
        _hora_legible(datos.get('entrada_esperada')),
        _hora_legible(datos.get('salida_esperada')),
        _hora_legible(entrada_techo), _hora_legible(salida_piso),
        int(bool(datos.get('entrada_tardia', False))),
        datos.get('minutos_tarde'),
        int(bool(datos.get('salida_temprana', False))),
        datos.get('minutos_antes'),
        int(bool(datos.get('ausente', False))),
        _hora_legible(entrada_piso), _hora_legible(entrada_techo),
        _hora_legible(salida_piso), _hora_legible(salida_techo),
        datos.get('ventana_min'),
        int(bool(datos.get('indeterminado', False))),
        datos.get('escaneos_dia'),
        datos.get('gap_maximo_min'),
        estado,
        (datos.get('fuente') or 'RED'),
        (datos.get('observaciones') or '')[:500],
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_AsistenciaDiaria AS target
                    USING (SELECT %s AS IdUsuario, %s AS Fecha) AS source
                    ON target.IdUsuario = source.IdUsuario AND target.Fecha = source.Fecha
                    WHEN MATCHED THEN
                        UPDATE SET IdTurno = %s,
                                   HoraEntradaEsperada = %s, HoraSalidaEsperada = %s,
                                   HoraEntradaReal = %s, HoraSalidaReal = %s,
                                   EntradaTardia = %s, MinutosTarde = %s,
                                   SalidaTemprana = %s, MinutosAntes = %s,
                                   Ausente = %s,
                                   EntradaPiso = %s, EntradaTecho = %s,
                                   SalidaPiso = %s, SalidaTecho = %s,
                                   VentanaMin = %s, Indeterminado = %s,
                                   EscaneosDia = %s, GapMaximoMin = %s,
                                   Estado = %s, Fuente = %s, Observaciones = %s,
                                   FechaCalculo = GETDATE()
                    WHEN NOT MATCHED THEN
                        INSERT (IdUsuario, Fecha, IdTurno,
                                HoraEntradaEsperada, HoraSalidaEsperada,
                                HoraEntradaReal, HoraSalidaReal,
                                EntradaTardia, MinutosTarde,
                                SalidaTemprana, MinutosAntes, Ausente,
                                EntradaPiso, EntradaTecho, SalidaPiso, SalidaTecho,
                                VentanaMin, Indeterminado,
                                EscaneosDia, GapMaximoMin, Estado, Fuente,
                                Observaciones)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    # USING
                    params[0], params[1],
                    # UPDATE SET
                    *params[2:],
                    # INSERT
                    params[0], params[1], *params[2:]
                ))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error guardando asistencia: {e}")
        return False


def get_asistencia_usuario(user_id, fecha_inicio=None, fecha_fin=None):
    """Obtiene historial de asistencias de un usuario."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                q = """
                    SELECT ad.*, t.Nombre AS TurnoNombre
                    FROM HUB_AsistenciaDiaria ad
                    LEFT JOIN HUB_Turnos t ON ad.IdTurno = t.Id
                    WHERE ad.IdUsuario = %s
                """
                params = [int(user_id)]
                if fecha_inicio:
                    q += " AND ad.Fecha >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    q += " AND ad.Fecha <= %s"
                    params.append(fecha_fin)
                q += " ORDER BY ad.Fecha DESC"
                cur.execute(q, params)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching asistencias: {e}")
        return []


def get_asistencia_fecha(fecha, user_id=None):
    """Obtiene asistencias de una fecha (todos los usuarios o uno)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                q = """
                    SELECT ad.*, u.Nombre AS UsuarioNombre, u.Email, t.Nombre AS TurnoNombre
                    FROM HUB_AsistenciaDiaria ad
                    JOIN HUB_Users u ON ad.IdUsuario = u.Id
                    LEFT JOIN HUB_Turnos t ON ad.IdTurno = t.Id
                    WHERE ad.Fecha = %s
                """
                params = [fecha]
                if user_id:
                    q += " AND ad.IdUsuario = %s"
                    params.append(int(user_id))
                q += " ORDER BY u.Nombre"
                cur.execute(q, params)
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching asistencia fecha: {e}")
        return []


def calcular_y_guardar_asistencias_fecha(fecha=None):
    """
    Calcula y guarda asistencias de todos los usuarios con turno para una fecha.

    Devuelve el desglose por estado, no solo un número: el número solo no dice
    nada (guardar "40 asistencias" suena a que 40 personas asistieron, cuando
    pueden ser 40 filas de las cuales 12 son SIN DATOS y 3 NO_APLICA). Lo que
    sirve para nómina es saber cuántas son afirmables y cuántas no.
    """
    import datetime
    if fecha is None:
        fecha = datetime.date.today() - datetime.timedelta(days=1)  # día anterior

    usuarios_turno = get_all_usuario_turnos()
    guardados = 0
    por_estado = {}
    sin_turno = 0
    for ut in usuarios_turno:
        user_id = ut['IdUsuario']
        # Verificar si el turno está vigente en la fecha
        if ut['FechaHasta'] and ut['FechaHasta'] < fecha:
            continue
        if ut['FechaDesde'] > fecha:
            continue

        datos = calcular_asistencia_dia(user_id, fecha)
        if datos.get('error'):
            sin_turno += 1
            continue
        datos.setdefault('turno_id', ut['IdTurno'])
        estado = datos.get('estado') or 'CALCULADA'
        por_estado[estado] = por_estado.get(estado, 0) + 1
        if guardar_asistencia_diaria(user_id, fecha, datos):
            guardados += 1

    return {
        'fecha': fecha,
        'guardados': guardados,
        'por_estado': por_estado,
        'sin_turno': sin_turno,
        'afirmables': por_estado.get('CALCULADA', 0)
                      + por_estado.get('TARDE', 0)
                      + por_estado.get('SALIDA_TEMPRANA', 0)
                      + por_estado.get('AUSENTE', 0),
    }


# =============================================================================
# PDF STORAGE - Configuración SMB para guardado automático de PDFs
# =============================================================================

_PDF_STORAGE_KEYS = ['smb_share_path', 'smb_user', 'smb_password', 'smb_domain', 'pdf_output_dir']


def get_pdf_storage_config():
    """Devuelve dict con la configuración de guardado de PDFs desde HUB_Config."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                placeholders = ', '.join(['%s'] * len(_PDF_STORAGE_KEYS))
                cur.execute(f"SELECT Clave, Valor FROM HUB_Config WHERE Clave IN ({placeholders})", _PDF_STORAGE_KEYS)
                rows = cur.fetchall()
                cfg = {row['Clave']: (row['Valor'] or '').strip() for row in rows}
                return {k: cfg.get(k, '') for k in _PDF_STORAGE_KEYS}
    except Exception as e:
        print(f"Error reading pdf storage config: {e}")
        return {k: '' for k in _PDF_STORAGE_KEYS}


def save_pdf_storage_config(smb_share_path, smb_user, smb_password, smb_domain, pdf_output_dir):
    """Guarda la configuración de guardado de PDFs en HUB_Config (upsert)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                values = {
                    'smb_share_path': smb_share_path.strip(),
                    'smb_user': smb_user.strip(),
                    'smb_password': smb_password.strip(),
                    'smb_domain': smb_domain.strip(),
                    'pdf_output_dir': pdf_output_dir.strip(),
                }
                for clave, valor in values.items():
                    cur.execute("""
                        MERGE HUB_Config AS target
                        USING (SELECT %s AS Clave) AS source
                        ON target.Clave = source.Clave
                        WHEN MATCHED THEN
                            UPDATE SET Valor = %s, Actualizado = GETDATE()
                        WHEN NOT MATCHED THEN
                            INSERT (Clave, Valor) VALUES (%s, %s);
                    """, (clave, valor, clave, valor))
                conn.commit()
                return True
    except Exception as e:
        print(f"Error saving pdf storage config: {e}")
        return False


def get_pdf_fecha_cotizacion_materiales(folio):
    """Obtiene la Fecha de una cotización de materiales por su folio numérico."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Fecha FROM IndiceMateriales WHERE Folio = %s", (folio,))
                row = cur.fetchone()
                return row['Fecha'] if row else None
    except Exception:
        return None


def get_pdf_fecha_cotizacion_servproy(folio):
    """Obtiene la FechaCreacion de una cotización de servicios/proyectos por folio."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT FechaCreacion FROM Indice_CotizacionesServProy WHERE Folio = %s", (folio.strip(),))
                row = cur.fetchone()
                return row['FechaCreacion'] if row else None
    except Exception:
        return None


def get_pdf_fecha_orden_compra(folio):
    """Obtiene la Fecha de una orden de compra por su folio."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Fecha FROM HUB_IndiceOC WHERE FolioOC = %s", (folio,))
                row = cur.fetchone()
                return row['Fecha'] if row else None
    except Exception:
        return None


# =============================================================================
# PDF STORAGE - Folios por rango de fechas (para generación masiva y worker)
# =============================================================================

def get_folios_cotizaciones_materiales_by_range(fecha_inicio, fecha_fin):
    """Devuelve folios de cotizaciones de materiales en un rango de fechas."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Folio, Fecha FROM IndiceMateriales WHERE Fecha BETWEEN %s AND %s ORDER BY Fecha",
                    (fecha_inicio, fecha_fin))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_folios_cm_range: {e}")
        return []


def get_folios_cotizaciones_servproy_by_range(fecha_inicio, fecha_fin):
    """Devuelve folios de cotizaciones de servicios/proyectos en un rango de fechas."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Folio, FechaCreacion FROM Indice_CotizacionesServProy WHERE CAST(FechaCreacion AS DATE) BETWEEN %s AND %s ORDER BY FechaCreacion",
                    (fecha_inicio, fecha_fin))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_folios_csp_range: {e}")
        return []


def get_folios_reportes_servicio_by_range(fecha_inicio, fecha_fin):
    """Devuelve folios de reportes de servicio en un rango de fechas."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT IdReporte, Folio, Fecha FROM ReportesServicio WHERE Fecha BETWEEN %s AND %s ORDER BY Fecha",
                    (fecha_inicio, fecha_fin))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_folios_rs_range: {e}")
        return []


def get_folios_ordenes_compra_by_range(fecha_inicio, fecha_fin):
    """Devuelve folios de órdenes de compra en un rango de fechas."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT FolioOC, Fecha FROM HUB_IndiceOC WHERE Fecha BETWEEN %s AND %s ORDER BY Fecha",
                    (fecha_inicio, fecha_fin))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_folios_oc_range: {e}")
        return []


def get_remisiones_by_range(fecha_inicio, fecha_fin):
    """Devuelve remisiones creadas en un rango de fechas (worker de PDFs)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT IdRemision, FolioRemision, FechaCreacion FROM IndiceRemisiones "
                    "WHERE CAST(FechaCreacion AS DATE) BETWEEN %s AND %s ORDER BY FechaCreacion",
                    (fecha_inicio, fecha_fin))
                return cur.fetchall()
    except Exception as e:
        print(f"Error get_remisiones_range: {e}")
        return []


def get_service_report_full_by_folio(folio):
    """Devuelve un reporte de servicio completo por su folio (para el worker)."""
    try:
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT IdReporte, Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico,
                           DescripcionServicio, Estatus, Notas, MaquinaLinea, FechaHoraInicio, FechaHoraFin,
                           TiempoTraslado, TiempoComida, FirmaConformidad, Cotizacion
                    FROM ReportesServicio WHERE Folio = %s
                """, (folio,))
                return cur.fetchone()
    except Exception as e:
        print(f"Error get_service_report_full_by_folio: {e}")
        return None

