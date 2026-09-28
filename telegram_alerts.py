"""
Dispatcher de alertas Telegram del HUB.

Punto de entrada para encolar alertas. Los triggers (vistas, workers)
llaman a las funciones de esta capa. El worker `cron_sync_telegram.py`
consume la cola y despacha a la API de Telegram.

Uso desde cualquier punto del HUB:
    from telegram_alerts import alertar_kilometros, alertar_ticket_oxxogas, alertar_reporte_firmado
"""
import datetime
import traceback
import eccsa_db as db


def _normalize_foto_para_cola(foto_bytes, max_dim=1600, quality=85):
    """Recodifica una foto a JPEG baseline antes de encolarla a Telegram.

    Evita IMAGE_PROCESS_FAILED por formatos corruptos/mal nombrados.
    Si no es legible, devuelve None (se encola solo el texto).
    """
    if not foto_bytes:
        return None
    try:
        from PIL import Image as PILImage
        import io
        img = PILImage.open(io.BytesIO(foto_bytes))
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
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=quality, progressive=False, optimize=True)
        return out.getvalue()
    except Exception as e:
        print(f"telegram_alerts: foto no normalizable ({e}); se omite adjunto")
        return None


def _get_current_user():
    """Obtiene el nombre del usuario actual desde session_state."""
    try:
        import streamlit as st
        return st.session_state.get('username') or st.session_state.get('name') or 'Sistema'
    except Exception:
        return 'Sistema'


def _now_str():
    """Fecha y hora actual formateada (hora de México, UTC-6)."""
    try:
        import pytz
        now = datetime.datetime.now(pytz.timezone('America/Mexico_City'))
    except ImportError:
        now = datetime.datetime.utcnow() - datetime.timedelta(hours=6)
    return now.strftime("%d/%m/%Y"), now.strftime("%H:%M")


def _resolve_chat_ids(id_evento):
    """Devuelve lista de chat_ids activos vinculados a un evento."""
    destinatarios = db.get_telegram_destinatarios(id_evento)
    chat_ids = []
    for d in destinatarios:
        uid = d['IdUsuario']
        vinculado = None
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute(
                        "SELECT ChatId, Activo FROM HUB_TelegramUsuarios WHERE IdUsuario = %s",
                        (int(uid),)
                    )
                    vinculado = cur.fetchone()
        except Exception:
            pass
        if vinculado and vinculado.get('Activo') and vinculado.get('ChatId'):
            chat_ids.append(int(vinculado['ChatId']))
    return chat_ids


def _apply_template(plantilla, datos):
    """Reemplaza {placeholder} en la plantilla con los datos del payload.
    Elimina líneas donde todos los placeholders quedan vacíos.

    Además resuelve los nombres alternativos de cada campo. El texto se
    arma y se guarda YA RENDERIZADO en la cola, asi que si un productor
    (un snapshot viejo de este archivo) manda el payload con los nombres
    internos en vez de los de la plantilla, la alerta sale con los
    {Nombre} y {Descripcion} literales y no hay forma de arreglarlo despues
    desde el worker. Con los alias, cualquier version del productor
    renderiza las 6 variables de la plantilla OXXOGAS_TICKET.
    """
    import re
    texto = plantilla

    # Alias: nombre de la plantilla -> nombres internos equivalentes.
    # Se recorren en cascada para que "Cliente" se resuelva desde "Empresa".
    alias = {
        'Nombre': ('Usuario', 'NombreRegistro'),
        'Cantidad': ('Monto', 'Litros'),
        'Folio': ('FolioTicket',),
        'Auto': ('Automovil', 'Vehiculo'),
        'Cliente': ('Empresa', 'RazonSocial', 'NombreCliente'),
        'Descripcion': ('ProyectoServicio',),
    }
    datos = dict(datos or {})
    for destino, origenes in alias.items():
        if destino in datos:
            continue
        for origen in origenes:
            if datos.get(origen) not in (None, ''):
                datos[destino] = datos[origen]
                break

    for key, val in datos.items():
        texto = texto.replace('{' + key + '}', str(val if val is not None else ''))

    # Si quedo algun placeholder sin sustituir, avisar: antes salia en
    # silencio y el usuario lo descubria en Telegram, ya con el texto roto.
    sin_resolver = sorted(set(re.findall(r'\{(\w+)\}', texto)))
    if sin_resolver:
        print(f'[telegram] WARN plantilla con placeholders sin resolver: '
              f'{sin_resolver}', flush=True)

    # Limpiar líneas que solo tienen espacios/bolding vacío
    lineas = texto.split('\n')
    lineas_limpias = []
    for linea in lineas:
        stripped = linea.strip()
        # Si la línea solo tiene *, espacios, o está vacía, saltar
        if stripped and not re.match(r'^[\*\s]+$', stripped):
            lineas_limpias.append(linea)
    return '\n'.join(lineas_limpias)


def _get_event_template(id_evento):
    """Obtiene la plantilla y config de adjunto de un evento."""
    eventos = db.get_telegram_eventos()
    for e in eventos:
        if e['IdEvento'] == id_evento:
            return e['PlantillaMensaje'], bool(e['AdjuntarArchivo']), bool(e['Activo'])
    return None, False, False


def _queue_alert(id_evento, datos, adjunto=None, adjunto_nombre=None):
    """Función interna genérica: resuelve destinatarios, aplica plantilla, encola."""
    plantilla, _, activo = _get_event_template(id_evento)
    if not activo or not plantilla:
        return 0

    chat_ids = _resolve_chat_ids(id_evento)
    if not chat_ids:
        return 0

    # Si no hay factura vinculada, quitar la sección de factura de la plantilla
    if not datos.get('FacturaFolio'):
        lineas = plantilla.split('\n')
        nueva_plantilla = []
        skip = False
        for linea in lineas:
            if 'Factura vinculada' in linea:
                skip = True
                continue
            if skip and not linea.strip():
                skip = False
                continue
            if skip:
                continue
            nueva_plantilla.append(linea)
        plantilla = '\n'.join(nueva_plantilla)

    texto = _apply_template(plantilla, datos)
    count = 0
    for cid in chat_ids:
        if db.queue_telegram_alerta(id_evento, cid, texto, adjunto, adjunto_nombre):
            count += 1
    return count


# ── Trigger: Kilómetros ─────────────────────────────────────────────────────

def alertar_kilometros(id_automovil, kilometros, id_usuario):
    """Encola alerta de registro de kilómetros con auto, km, usuario y fecha."""
    try:
        auto = None
        usuario = None
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (int(id_automovil),))
                auto = cur.fetchone()
                cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                usuario = cur.fetchone()

        fecha, hora = _now_str()
        datos = {
            'Automovil': f"{auto['MarcaModelo']} ({auto['Placas']})" if auto else f"ID {id_automovil}",
            'Kilometros': f"{kilometros:,}".replace(",", ","),
            'Usuario': usuario['Nombre'] if usuario else f"Usuario {id_usuario}",
            'Fecha': fecha,
            'Hora': hora,
        }
        return _queue_alert('KILOMETROS', datos)
    except Exception as e:
        print(f"alertar_kilometros error: {e}")
        return 0


# ── Trigger: Ticket OxxoGas ─────────────────────────────────────────────────

def alertar_ticket_oxxogas(folio_ticket, id_vehiculo=None, id_cliente=None,
                           foto_bytes=None, foto_nombre=None, id_usuario=None,
                           descripcion=None):
    """Encola alerta de ticket OxxoGas con foto, usuario, fecha y datos de factura si existe."""
    try:
        datos = {
            'FolioTicket': folio_ticket,
            'Automovil': '',
            'Cliente': '',
            'Usuario': '',
            'Descripcion': descripcion or '',
            'FacturaFolio': '',
            'Estacion': '',
            'Litros': '',
            'Producto': '',
            'Monto': '',
        }

        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if id_vehiculo:
                    cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s", (int(id_vehiculo),))
                    auto = cur.fetchone()
                    if auto:
                        datos['Automovil'] = f"{auto['MarcaModelo']} ({auto['Placas']})"
                if id_cliente:
                    cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente,))
                    cli = cur.fetchone()
                    if cli:
                        datos['Cliente'] = cli['Cliente']
                if id_usuario:
                    cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                    u = cur.fetchone()
                    if u:
                        datos['Usuario'] = u['Nombre']

                # Buscar factura/vale vinculado por FolioTicket = XmlFolio
                cur.execute("""
                    SELECT XmlFolio, XmlEstacion, XmlLitros, XmlConcepto, Monto
                    FROM HUB_OxxoGasVales
                    WHERE XmlFolio = %s
                """, (folio_ticket.strip(),))
                vale = cur.fetchone()
                if vale:
                    datos['FacturaFolio'] = vale.get('XmlFolio') or ''
                    datos['Estacion'] = vale.get('XmlEstacion') or ''
                    datos['Litros'] = str(vale.get('XmlLitros') or '')
                    datos['Producto'] = vale.get('XmlConcepto') or ''
                    datos['Monto'] = str(vale.get('Monto') or '')

        if not datos['Usuario']:
            datos['Usuario'] = _get_current_user()

        fecha, hora = _now_str()
        datos['Fecha'] = fecha
        datos['Hora'] = hora
        
        # Nuevos campos para formato de registro (orden: Fecha, Nombre, Cantidad, Folio, Auto, Cliente, Descripción)
        datos['NombreRegistro'] = datos['Usuario']
        datos['Nombre'] = datos['Usuario']
        datos['Total'] = datos['Monto']
        # Cantidad = Monto de la factura enlazada (formato $)
        try:
            if datos.get('Monto'):
                datos['Cantidad'] = f"${float(datos['Monto']):,.2f}"
            else:
                datos['Cantidad'] = ''
        except (TypeError, ValueError):
            datos['Cantidad'] = str(datos.get('Monto') or '')
        datos['Folio'] = datos['FolioTicket']
        datos['Auto'] = datos['Automovil']
        datos['Empresa'] = datos['Cliente']
        datos['ProyectoServicio'] = datos['Descripcion']

        plantilla, adj_config, _ = _get_event_template('OXXOGAS_TICKET')
        adjunto = None
        if adj_config and foto_bytes:
            adjunto = _normalize_foto_para_cola(foto_bytes)
            if adjunto:
                foto_nombre = 'foto.jpg'
            else:
                foto_nombre = None
        return _queue_alert('OXXOGAS_TICKET', datos, adjunto, foto_nombre)
    except Exception as e:
        print(f"alertar_ticket_oxxogas error: {e}")
        return 0


# ── Trigger: Reporte de Servicio Firmado ────────────────────────────────────

def alertar_reporte_firmado(id_reporte):
    """Encola alerta de reporte firmado con PDF, técnico, usuario que firmó y fecha."""
    try:
        reporte = db.get_service_report_by_id(id_reporte)
        if not reporte:
            return 0

        usuario_firma = _get_current_user()
        fecha, hora = _now_str()

        # Get all technicians text
        tecnicos_texto = ''
        try:
            tecnicos_texto = db.get_tecnicos_texto(id_reporte)
        except Exception:
            tecnicos_texto = reporte.get('Tecnico', '')

        datos = {
            'Folio': reporte.get('Folio', ''),
            'Cliente': reporte.get('Cliente', ''),
            'Contacto': reporte.get('Contacto', ''),
            'Tecnico': reporte.get('Tecnico', ''),
            'Tecnicos': tecnicos_texto,
            'Fecha': reporte.get('Fecha', ''),
            'DescripcionServicio': (reporte.get('DescripcionServicio') or '')[:100],
            'UsuarioFirma': usuario_firma,
            'FechaFirma': fecha,
            'HoraFirma': hora,
        }

        plantilla, adj_config, _ = _get_event_template('REPORTE_SERVICIO')
        adjunto = None
        adj_nombre = None
        if adj_config:
            try:
                from pdf_generator import generate_service_report_pdf
                pdf_bytes = generate_service_report_pdf(reporte)
                if pdf_bytes:
                    adjunto = pdf_bytes
                    adj_nombre = f"{reporte.get('Folio', 'reporte')}.pdf"
                else:
                    print(f"alertar_reporte_firmado: generate_service_report_pdf devolvió vacío para {reporte.get('Folio')}")
            except Exception as e:
                print(f"Error generando PDF para alerta Telegram ({reporte.get('Folio')}): {e}")
                traceback.print_exc()

        n = _queue_alert('REPORTE_SERVICIO', datos, adjunto, adj_nombre)
        if adj_config and not adjunto:
            print(f"alertar_reporte_firmado: {reporte.get('Folio')} encolado SIN PDF (adj_config=True)")
        return n
    except Exception as e:
        print(f"alertar_reporte_firmado error: {e}")
        traceback.print_exc()
        return 0


# ── Trigger: Solicitud Vale QR ──────────────────────────────────────────────

def alertar_solicitud_vale_qr(solicitud_id):
    """Encola alerta de solicitud de vale QR con datos del vehículo, empresa, solicitante y kilómetros."""
    try:
        solicitante = _get_current_user()
        fecha, hora = _now_str()
        vehiculo = ''
        empresa = ''
        descripcion = ''
        monto = '$500.00'
        kilometros = ''

        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.IdAutomovil, s.IdCliente, s.Descripcion, s.MontoUnit, s.Kilometros,
                           a.MarcaModelo, a.Placas, cl.Cliente
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN clientes cl ON s.IdCliente = cl.IdCliente
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                sol = cur.fetchone()
                if sol:
                    vehiculo = f"{sol.get('MarcaModelo', '')} ({sol.get('Placas', '')})" if sol.get('MarcaModelo') else '-'
                    empresa = sol.get('Cliente', '-') or '-'
                    descripcion = sol.get('Descripcion', '') or ''
                    monto = f"${float(sol.get('MontoUnit', 500) or 500):,.2f}"
                    km_val = sol.get('Kilometros')
                    kilometros = f"{int(km_val):,} km" if km_val is not None else 'No registrado'

                cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(solicitud_id),))
                # Ya tenemos solicitante del _get_current_user

        datos = {
            'Solicitante': solicitante,
            'Vehiculo': vehiculo,
            'Empresa': empresa,
            'Descripcion': descripcion,
            'Monto': monto,
            'Kilometros': kilometros,
            'Fecha': fecha,
            'Hora': hora,
        }

        return _queue_alert('SOLICITUD_VALE_QR', datos)
    except Exception as e:
        print(f"alertar_solicitud_vale_qr error: {e}")
        return 0


def alertar_vale_generado(solicitud_id):
    """Encola alerta de vale QR generado exitosamente."""
    try:
        fecha, hora = _now_str()
        vehiculo = ''
        empresa = ''
        solicitante = ''
        folio = ''

        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.Placa, s.CodigoQR, s.MontoUnit,
                           a.MarcaModelo, cl.Cliente, u.Nombre AS Solicitante
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN clientes cl ON s.IdCliente = cl.IdCliente
                    LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                sol = cur.fetchone()
                if sol:
                    vehiculo = f"{sol.get('MarcaModelo', '')} ({sol.get('Placas', '')})" if sol.get('MarcaModelo') else '-'
                    empresa = sol.get('Cliente', '-') or '-'
                    solicitante = sol.get('Solicitante', '-') or '-'
                    folio = sol.get('CodigoQR', '') or ''

        datos = {
            'Solicitante': solicitante,
            'Vehiculo': vehiculo,
            'Empresa': empresa,
            'Folio': folio,
            'Monto': f"${float(500):,.2f}",
            'Fecha': fecha,
            'Hora': hora,
        }

        return _queue_alert('VALE_QR_GENERADO', datos)
    except Exception as e:
        print(f"alertar_vale_generado error: {e}")
        return 0


def alertar_vale_rechazado(solicitud_id, motivo=''):
    """Encola alerta de vale QR rechazado."""
    try:
        fecha, hora = _now_str()
        vehiculo = ''
        solicitante = ''

        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.Placa, a.MarcaModelo, u.Nombre AS Solicitante
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                sol = cur.fetchone()
                if sol:
                    vehiculo = f"{sol.get('MarcaModelo', '')} ({sol.get('Placas', '')})" if sol.get('MarcaModelo') else '-'
                    solicitante = sol.get('Solicitante', '-') or '-'

        datos = {
            'Solicitante': solicitante,
            'Vehiculo': vehiculo,
            'Motivo': motivo or 'Sin motivo especificado',
            'Fecha': fecha,
            'Hora': hora,
        }

        return _queue_alert('VALE_QR_RECHAZADO', datos)
    except Exception as e:
        print(f"alertar_vale_rechazado error: {e}")
        return 0


def alertar_solicitud_pendiente(solicitud_id, motivos):
    """Encola alerta de solicitud que no pasó criterios de auto-aprobación."""
    try:
        fecha, hora = _now_str()
        vehiculo = ''
        solicitante = ''

        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT s.Placa, a.MarcaModelo, u.Nombre AS Solicitante
                    FROM HUB_SolicitudVales s
                    LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
                    LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
                    WHERE s.Id = %s
                """, (int(solicitud_id),))
                sol = cur.fetchone()
                if sol:
                    vehiculo = f"{sol.get('MarcaModelo', '')} ({sol.get('Placas', '')})" if sol.get('MarcaModelo') else '-'
                    solicitante = sol.get('Solicitante', '-') or '-'

        datos = {
            'Solicitante': solicitante,
            'Vehiculo': vehiculo,
            'Motivos': '\n'.join(f'• {m}' for m in motivos),
            'Fecha': fecha,
            'Hora': hora,
        }

        return _queue_alert('SOLICITUD_VALE_PENDIENTE', datos)
    except Exception as e:
        print(f"alertar_solicitud_pendiente error: {e}")
        return 0


# ── Trigger: Detección de Red — Cambio de Presencia ─────────────────────────

def alertar_cambio_presencia(device, tipo_evento):
    """Encola alerta de cambio de presencia (ENTRADA/SALIDA) por red."""
    try:
        fecha, hora = _now_str()
        nombre_usuario = device.get('NombreUsuario') or 'Sin asignar'
        nombre_disp = device.get('NombreDispositivo') or device.get('MACAddress', '???')

        evento_id = 'DISPOSITIVO_RED_ENTRADA' if tipo_evento == 'ENTRADA' else 'DISPOSITIVO_RED_SALIDA'

        datos = {
            'Usuario': nombre_usuario,
            'Dispositivo': nombre_disp,
            'MAC': device.get('MACAddress', ''),
            'Tipo': device.get('Tipo', ''),
            'Fecha': fecha,
            'Hora': hora,
        }

        return _queue_alert(evento_id, datos)
    except Exception as e:
        print(f"alertar_cambio_presencia error: {e}")
        return 0



# ── Trigger: Saldo Go Vale ───────────────────────────────────────────────────

def alertar_govale_saldo(saldo, saldo_fecha, umbral=2000.0):
    """Encola alerta de saldo Go Vale diario."""
    try:
        bajo = saldo < umbral
        estado = '⚠️ SALDO BAJO' if bajo else '✅ OK'
        datos = {
            'Saldo': f'{saldo:,.2f}',
            'Estado': estado,
            'Umbral': f'{umbral:,.0f}',
            'UltimaRevision': saldo_fecha or 'sin revisar',
        }
        return _queue_alert('GOVALE_SALDO', datos)
    except Exception as e:
        print(f'alertar_govale_saldo error: {e}')
        return 0

def alertar_dispositivo_nuevo(mac, ip, hostname):
    """Encola alerta de dispositivo desconocido detectado en la red."""
    try:
        fecha, hora = _now_str()
        datos = {
            'MAC': mac,
            'IP': ip or 'N/A',
            'Hostname': hostname or 'N/A',
            'Fecha': fecha,
            'Hora': hora,
        }
        return _queue_alert('DISPOSITIVO_RED_NUEVO', datos)
    except Exception as e:
        print(f"alertar_dispositivo_nuevo error: {e}")
        return 0
