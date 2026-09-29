"""
Mensajes de los avisos, compartidos por los canales.

Telegram y WhatsApp cuentan lo mismo: mismo texto base, mismos datos. Si cada
canal armara su propio texto, terminaria pasando que un dia dicen "quedo
$1,234.50" y al otro "quedo 1234.5", o que en uno se adjunta el PDF y en el otro
no. Este modulo es el unico lugar donde se decide que dice un aviso y con que
datos; los dos canales solo deciden a quien se lo mandan y por donde sale.

Tres cosas van aqui:

1. `aplicar_plantilla`: mete los datos en el texto y limpia lo que quedo
  amlibio. Es identico al que usaba Telegram, ahora compartido.

2. Los `datos_*`: leen lo que ya sabemos del modulo que disparo el aviso (el
   auto, el cliente, el vale ligado) y lo dejan en un diccionario con los
   nombres que usan las plantillas. A proposito reciben `fecha`/`hora` del
   llamador: en el panel no existe la sesion de Streamlit de la que Telegram
   sacaba la hora, y si cada quien sacara la suya los mensajes saldrian con
   horas distintas segun por donde se disparar.

3. `PLANTILLAS`: el texto base de cada aviso. Es lo que se siembra al dar de
   alta los eventos de WhatsApp, y se puede cambiar despues desde la interfaz.
"""

import datetime
import re

import eccsa_db as db

# Cuantos caracteres del servicio caben en el mensaje. El resumen va completo
# en el reporte; aqui es solo para que se sepa de que se trata.
MAX_DESCRIPCION = 100

# Alias: nombre de la plantilla -> nombres internos equivalentes, en cascada.
# Sin esto un productor que manda "Usuario" deja un {Nombre} literal en el
# mensaje, y como el texto se guarda YA renderizado en la cola, despues no hay
# forma de arreglarlo.
ALIAS = {
    'Nombre': ('Usuario', 'NombreRegistro'),
    'Cantidad': ('Monto', 'Litros'),
    'Folio': ('FolioTicket',),
    'Auto': ('Automovil', 'Vehiculo'),
    'Cliente': ('Empresa', 'RazonSocial', 'NombreCliente'),
    'Descripcion': ('ProyectoServicio',),
}

# Union de todo lo que los productores llenan. Las plantillas solo pueden usar
# estos nombres: uno que no este en la lista saldría como {llave} en el
# mensaje de una persona, y hay una prueba que lo revisa.
CAMPOS_CONOCIDOS = {
    # comunes
    'Fecha', 'Hora', 'Usuario', 'Nombre', 'Automovil', 'Auto', 'Cliente',
    'Descripcion', 'Folio', 'Cantidad',
    # kilometros
    'Kilometros',
    # reporte de servicio
    'UsuarioFirma', 'FechaFirma', 'HoraFirma', 'Tecnico', 'Tecnicos',
    'Contacto', 'DescripcionServicio',
    # ticket oxxogas
    'FolioTicket', 'Estacion', 'Litros', 'Producto', 'Monto', 'FacturaFolio',
    'ProyectoServicio', 'NombreRegistro', 'Empresa',
    # saldo go vale
    'Saldo', 'Estado', 'Umbral', 'UltimaRevision',
    # avisos de red
    'MAC', 'IP', 'Hostname',
}

# ── Plantillas ───────────────────────────────────────────────────────────────

PLANTILLAS = {
    'KILOMETROS': (
        "Registro de Kilomtros\n"
        "🚗*{Automovil}*\n"
        "📊 {Kilometros} km\n"
        "👤 {Usuario}\n"
        "📅 {Fecha} {Hora}"
    ),
    'OXXOGAS_TICKET': (
        "Ticket de OXXO Gas\n"
        "Fecha: {Fecha}\n"
        "Nombre: {Nombre}\n"
        "Cantidad: {Cantidad}\n"
        "Folio ticket: {Folio}\n"
        "Auto: {Auto}\n"
        "Cliente: {Cliente}\n"
        "Descripción: {Descripcion}"
    ),
    'REPORTE_SERVICIO': (
        "*{Folio}*\n"
        "👥 {Cliente}\n"
        "👤 Firmado: {UsuarioFirma}\n"
        "📅 {FechaFirma} {HoraFirma}"
    ),
    'GOVALE_SALDO': (
        "💰 *Saldo Go Vale* — ${Saldo}\n"
        "Estado: {Estado}\n"
        "Umbral alerta: ${Umbral}\n"
        "Última revisión: {UltimaRevision}"
    ),
    # El aviso de saldo bajo lleva lo mismo que el diario pero dicho como
    # alerta: el diario se lee de arriba, este es el que hace que alguien
    # vaya a refactoriar.
    'GOVALE_SALDO_BAJO': (
        "⚠️ *Saldo bajo en Go Vale* — ${Saldo}\n"
        "Estado: {Estado}\n"
        "Umbral de alerta: ${Umbral}\n"
        "Última revisión: {UltimaRevision}\n"
        "Conviene refactorizar antes de que se acaben los vales."
    ),
    'DISPOSITIVO_RED_NUEVO': (
        "📡 *Dispositivo nuevo en la red*\n"
        "{Hostname} · {IP}\n"
        "MAC: {MAC}\n"
        "📅 {Fecha} {Hora}"
    ),
}


def ahora_mx():
    """Fecha y hora de Mexico, que es donde se reportan los avisos."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo('America/Mexico_City'))
    except Exception:
        now = datetime.datetime.utcnow() - datetime.timedelta(hours=6)
    return now.strftime('%d/%m/%Y'), now.strftime('%H:%M')


# ── Render ───────────────────────────────────────────────────────────────────

def aplicar_plantilla(plantilla, datos):
    """
    Sustituye los {placeholders} y limpia las lineas que quedaron solo con
    negritas o espacios: si un campo opcional no venia, "* *" o una linea en
    blanco se verian rotos en el telefono de quien lo recibe.
    """
    texto = plantilla or ''
    datos = dict(datos or {})

    for destino, origenes in ALIAS.items():
        if destino in datos:
            continue
        for origen in origenes:
            if datos.get(origen) not in (None, ''):
                datos[destino] = datos[origen]
                break

    for key, val in datos.items():
        texto = texto.replace('{' + key + '}', str(val if val is not None else ''))

    sin_resolver = sorted(set(re.findall(r'\{(\w+)\}', texto)))
    if sin_resolver:
        print(f'[notif] WARN plantilla con placeholders sin resolver: {sin_resolver}', flush=True)

    def _linea_vacia(linea):
        """Una linea que no trae nada: solo negritas, o una etiqueta sin valor
        ("👤 Firmado:" cuando no se sabe quien firmo). Se van para que el
        mensaje no llegue con colones colgando."""
        s = linea.strip()
        if not s:
            return True
        if re.match(r'^[\*\s]+$', s):
            return True
        return bool(re.match(r'^[^:\n]{1,30}:\s*[\*\s]*$', s))

    lineas = [l for l in texto.split('\n') if not _linea_vacia(l)]
    return '\n'.join(lineas)


def quitar_bloque(plantilla, marcador):
    """
    Quita una seccion completa de la plantilla, desde la linea que contiene el
    marcador hasta la primera linea vacia. Sirve para los bloques opcionales
    (por ejemplo "Factura vinculada") cuando no hay dato que poner ahi.

    OJO: el bloque se come todo lo que sigue hasta el proximo hueco, asi que
    las plantillas deben separar cada seccion con una linea en blanco. Es el
    comportamiento que traia Telegram, aqui queda escrito porque es facil de
    romper sin querer.
    """
    if not plantilla or not marcador:
        return plantilla
    salida, saltando = [], False
    for linea in plantilla.split('\n'):
        if marcador in linea:
            saltando = True
            continue
        if saltando:
            if not linea.strip():
                saltando = False
            continue
        salida.append(linea)
    return '\n'.join(salida)


def componer(plantilla, datos):
    """Atajo: quita los bloques sin dato y luego renderiza."""
    if not datos.get('FacturaFolio'):
        plantilla = quitar_bloque(plantilla, 'Factura vinculada')
    return aplicar_plantilla(plantilla, datos)


# ── Datos de cada aviso ──────────────────────────────────────────────────────

def datos_saldo(saldo, saldo_fecha, umbral=2000.0):
    """Saldo Go Vale. El mismo dato sirve para el aviso diario y el de bajo."""
    try:
        saldo = float(saldo)
    except (TypeError, ValueError):
        saldo = 0.0
    try:
        umbral = float(umbral)
    except (TypeError, ValueError):
        umbral = 2000.0
    return {
        'Saldo': f'{saldo:,.2f}',
        'Estado': '⚠️ SALDO BAJO' if saldo < umbral else '✅ OK',
        'Umbral': f'{umbral:,.0f}',
        'UltimaRevision': saldo_fecha or 'sin revisar',
    }


def datos_kilometros(id_automovil, kilometros, id_usuario, fecha, hora,
                     usuario_actual=''):
    """
    Auto, kilometraje y quien lo registro.

    `usuario_actual` es el escape cuando no hay id de usuario (por ejemplo en el
    sync desde Field, que manda el nombre pero no el id). Sin esto el mensaje
    llegaba diciendo "Usuario None", que es peor que no decir nada.
    """
    auto, usuario = None, None
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if id_automovil:
                    cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s",
                                (int(id_automovil),))
                    auto = cur.fetchone()
                if id_usuario:
                    cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                    usuario = cur.fetchone()
    except Exception as e:
        print(f"datos_kilometros: no se pudo leer auto/usuario ({e})")

    nombre_usuario = ''
    if usuario:
        nombre_usuario = usuario['Nombre']
    elif usuario_actual:
        nombre_usuario = usuario_actual
    elif id_usuario:
        nombre_usuario = f'Usuario {id_usuario}'

    try:
        km = f'{int(kilometros):,}'
    except (TypeError, ValueError):
        km = str(kilometros or '')
    return {
        'Automovil': (f"{auto['MarcaModelo']} ({auto['Placas']})" if auto
                      else f'ID {id_automovil}'),
        'Auto': (f"{auto['MarcaModelo']} ({auto['Placas']})" if auto
                 else f'ID {id_automovil}'),
        'Kilometros': km,
        'Usuario': nombre_usuario,
        'Nombre': nombre_usuario,
        'Fecha': fecha,
        'Hora': hora,
    }


def datos_reporte(reporte, tecnicos_texto, usuario_firma, fecha, hora):
    """Resumen del reporte de servicio ya firmado."""
    rep = reporte or {}
    return {
        'Folio': rep.get('Folio', ''),
        'Cliente': rep.get('Cliente', ''),
        'Contacto': rep.get('Contacto', ''),
        'Tecnico': rep.get('Tecnico', ''),
        'Tecnicos': tecnicos_texto or rep.get('Tecnico', ''),
        'Fecha': rep.get('Fecha', ''),
        'DescripcionServicio': (rep.get('DescripcionServicio') or '')[:MAX_DESCRIPCION],
        'UsuarioFirma': usuario_firma or '',
        'FechaFirma': fecha,
        'HoraFirma': hora,
    }


def datos_ticket(folio_ticket, id_vehiculo, id_cliente, id_usuario, descripcion,
                 fecha, hora, usuario_actual=''):
    """
    Ticket de OxxoGas. Ademas del folio y la foto (que va aparte) junta el auto,
    el cliente y, si el vale ya llego en el sistema, los datos de la factura
    para que el mensaje diga cuanto fue.
    """
    datos = {
        'FolioTicket': folio_ticket or '',
        'Folio': folio_ticket or '',
        'Automovil': '', 'Auto': '',
        'Cliente': '', 'Empresa': '',
        'Usuario': '', 'Nombre': '',
        'Descripcion': descripcion or '', 'ProyectoServicio': descripcion or '',
        'FacturaFolio': '', 'Estacion': '', 'Litros': '', 'Producto': '',
        'Monto': '', 'Cantidad': '',
        'Fecha': fecha, 'Hora': hora,
    }

    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                if id_vehiculo:
                    cur.execute("SELECT MarcaModelo, Placas FROM HUB_Automoviles WHERE Id = %s",
                                (int(id_vehiculo),))
                    auto = cur.fetchone()
                    if auto:
                        datos['Automovil'] = datos['Auto'] = f"{auto['MarcaModelo']} ({auto['Placas']})"
                if id_cliente:
                    cur.execute("SELECT Cliente FROM clientes WHERE IdCliente = %s", (id_cliente,))
                    cli = cur.fetchone()
                    if cli:
                        datos['Cliente'] = datos['Empresa'] = cli['Cliente']
                if id_usuario:
                    cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (int(id_usuario),))
                    u = cur.fetchone()
                    if u:
                        datos['Usuario'] = datos['Nombre'] = u['Nombre']

                # El vale se busca por XmlFolio: es el mismo folio del ticket.
                if folio_ticket:
                    cur.execute(
                        "SELECT XmlFolio, XmlEstacion, XmlLitros, XmlConcepto, Monto "
                        "FROM HUB_OxxoGasVales WHERE XmlFolio = %s",
                        (str(folio_ticket).strip(),))
                    vale = cur.fetchone()
                    if vale:
                        datos['FacturaFolio'] = vale.get('XmlFolio') or ''
                        datos['Estacion'] = vale.get('XmlEstacion') or ''
                        datos['Litros'] = str(vale.get('XmlLitros') or '')
                        datos['Producto'] = vale.get('XmlConcepto') or ''
                        datos['Monto'] = str(vale.get('Monto') or '')
    except Exception as e:
        print(f"datos_ticket: no se pudieron leer los datos del ticket ({e})")

    if not datos['Usuario']:
        # En el panel no hay sesion de Streamlit, asi que el nombre llega desde
        # el punto que registro el ticket. Sin esto el mensaje sale sin nombre.
        datos['Usuario'] = datos['Nombre'] = usuario_actual or ''

    try:
        datos['Cantidad'] = f"${float(datos['Monto']):,.2f}" if datos.get('Monto') else ''
    except (TypeError, ValueError):
        datos['Cantidad'] = str(datos.get('Monto') or '')
    return datos


# ── Fotos ────────────────────────────────────────────────────────────────────

def normalizar_foto(foto_bytes, max_dim=1600, quality=85):
    """
    Recodifica una foto a JPEG baseline antes de mandarla.

    Sirve para los dos canales: una foto de celular en HEIC o con canal alfa no
    la acepta ni Telegram ni WhatsApp, y sin esto el aviso sale sin imagen.
    Tambien la encoge: un JPEG de 4 MB tarda en subir y se come los datos.

    Si no es legible devuelve None: es preferible mandar el texto solo a no
    perder el aviso entero.
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
        print(f'notif_messages: foto no normalizable ({e}); se omite el adjunto')
        return None
