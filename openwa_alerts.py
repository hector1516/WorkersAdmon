"""
Dispatcher de los avisos por WhatsApp (OpenWA).

Es el espejo de `telegram_alerts`, pero con una diferencia que define el
comportamiento: aqui **no hay destinatarios por usuario**, sino una lista de
telefonos escrita a mano en cada evento (separada por comas). Por eso no hace
falta vincular cuentas ni resolver permisos: quien administra pega a quien debe
recibir el aviso.

Uso desde cualquier punto:

    from openwa_alerts import alertar_kilometros, alertar_saldo_govale

El worker `cron_sync_openwa.py` consume la cola y entrega por la API de OpenWA.

Lo que se dice lo arma `notif_messages`, igual que Telegram: cambiar el texto de
un aviso se hace en un solo lado.
"""

import traceback

import eccsa_db as db
import notif_messages as nm
import openwa_client as ow

# Cuando un evento pide adjunto pero no llego (el PDF no se genero, la foto no
# llego), se manda el texto solo. Perder el aviso entero por un adjunto seria
# peor que mandarlo sin imagen.
SIN_ADJUNTO = 'sin adjunto'


def _encolar(id_evento, datos, adjunto=None, adjunto_nombre=None, adjunto_tipo=None):
    """
    Paso comun de todos los avisos: lee el evento, normaliza a quien va, arma el
    texto y mete un mensaje por telefono en la cola.

    Devuelve cuantos mensajes se encolaron (0 = el aviso no salio, y casi
    siempre es por una de estas dos razones: el evento esta apagado, o no tiene
    telefonos pegados).
    """
    try:
        evento = db.get_openwa_evento(id_evento)
    except Exception as e:
        print(f"openwa_alerts: no se pudo leer el evento {id_evento} ({e})")
        return 0

    if not evento:
        print(f"openwa_alerts: el evento {id_evento} no existe en HUB_WhatsappEventos")
        return 0
    if not evento.get('Activo'):
        return 0
    plantilla = evento.get('PlantillaMensaje') or ''
    if not plantilla.strip():
        return 0

    telefonos = evento.get('Telefonos') or ''
    chats, rechazados = ow.normalizar_chat_ids(telefonos)
    if rechazados:
        print(f"openwa_alerts: {id_evento} tiene numeros que no son validos: {rechazados}")
    if not chats:
        # Es el caso normal hasta que se peguen los numeros: no es un error que
        # tumbe el aviso, pero conviene que se note en el log.
        print(f"openwa_alerts: {id_evento} no tiene telefonos, no se encola nada")
        return 0

    if not evento.get('AdjuntarArchivo'):
        adjunto, adjunto_nombre, adjunto_tipo = None, None, None
    elif not adjunto:
        print(f"openwa_alerts: {id_evento} pide adjunto pero no llego ({SIN_ADJUNTO}); "
              f"se mandara solo el texto")
        adjunto, adjunto_nombre, adjunto_tipo = None, None, None

    texto = nm.componer(plantilla, datos)

    encolados = 0
    for chat in chats:
        if db.queue_openwa_alerta(id_evento, chat, texto,
                                  adjunto, adjunto_nombre, adjunto_tipo):
            encolados += 1
    if encolados:
        print(f"openwa_alerts: {id_evento} encolado a {encolados} chat(s)")
    return encolados


# ── Aviso: registro de kilometros ────────────────────────────────────────────

def alertar_kilometros(id_automovil, kilometros, id_usuario, usuario_actual=''):
    """
    Cada vez que se registra un odometro.

    `usuario_actual` se usa cuando no hay id de usuario (el sync desde Field
    manda el nombre, no el id).
    """
    try:
        fecha, hora = nm.ahora_mx()
        datos = nm.datos_kilometros(id_automovil, kilometros, id_usuario, fecha, hora,
                                    usuario_actual=usuario_actual)
        return _encolar('KILOMETROS', datos)
    except Exception as e:
        print(f"alertar_kilometros (whatsapp) error: {e}")
        traceback.print_exc()
        return 0


# ── Aviso: ticket OxxoGas (con la foto) ──────────────────────────────────────

def alertar_ticket_oxxogas(folio_ticket, id_vehiculo=None, id_cliente=None,
                           id_usuario=None, descripcion=None, foto_bytes=None,
                           usuario_actual=''):
    """
    Cuando se registra un ticket, con la foto. `usuario_actual` es el nombre de
    quien lo registro: en el panel no hay sesion de Streamlit de la que sacarlo,
    y sin esto el mensaje sale con el nombre vacio.
    """
    try:
        fecha, hora = nm.ahora_mx()
        datos = nm.datos_ticket(folio_ticket, id_vehiculo, id_cliente, id_usuario,
                                descripcion, fecha, hora, usuario_actual=usuario_actual)
        foto = nm.normalizar_foto(foto_bytes)
        return _encolar('OXXOGAS_TICKET', datos, foto,
                        'foto.jpg' if foto else None, 'imagen' if foto else None)
    except Exception as e:
        print(f"alertar_ticket_oxxogas (whatsapp) error: {e}")
        traceback.print_exc()
        return 0


# ── Aviso: reporte de servicio firmado (con el PDF) ──────────────────────────

def alertar_reporte_firmado(id_reporte, usuario_firma=''):
    """
    Cuando un tecnico firma un reporte, con el PDF adjunto. El PDF se genera
    aqui (no se guarda en disco en ningun lado): es el mismo que ve el cliente.
    """
    try:
        reporte = db.get_service_report_by_id(id_reporte)
        if not reporte:
            return 0

        fecha, hora = nm.ahora_mx()
        try:
            tecnicos = db.get_tecnicos_texto(id_reporte)
        except Exception:
            tecnicos = reporte.get('Tecnico', '')

        datos = nm.datos_reporte(reporte, tecnicos, usuario_firma, fecha, hora)

        pdf, nombre = None, None
        try:
            from pdf_generator import generate_service_report_pdf
            pdf = generate_service_report_pdf(reporte)
            nombre = f"{reporte.get('Folio', 'reporte')}.pdf" if pdf else None
            if not pdf:
                print(f"alertar_reporte_firmado: el PDF de {reporte.get('Folio')} "
                      f"salio vacio; se mandara solo el texto")
        except Exception as e:
            print(f"alertar_reporte_firmado: no se pudo generar el PDF ({e}); "
                  f"se mandara solo el texto")

        return _encolar('REPORTE_SERVICIO', datos, pdf, nombre,
                        'documento' if pdf else None)
    except Exception as e:
        print(f"alertar_reporte_firmado (whatsapp) error: {e}")
        traceback.print_exc()
        return 0


# ── Aviso: saldo Go Vale (diario y por umbral) ───────────────────────────────

def alertar_saldo_govale(saldo, saldo_fecha, umbral=2000.0, solo_si_bajo=False):
    """
    Avisa el saldo del monedero.

    Sale por dos eventos distintos a proposito: el diario (GOVALE_SALDO), que se
    lee de arriba y nadie mira; y el de umbral (GOVALE_SALDO_BAJO), que es el
    que hace que alguien vaya a refactoriar.

    `solo_si_bajo=True` manda unicamente el de umbral: es lo que quieres cuando
    el worker revisa cada hora, porque si no cada revision con saldo bajo
    mandaria el aviso diario otra vez.
    """
    try:
        datos = nm.datos_saldo(saldo, saldo_fecha, umbral)
        enviados = 0
        if not solo_si_bajo:
            enviados += _encolar('GOVALE_SALDO', datos)
        if 'BAJO' in datos['Estado']:
            enviados += _encolar('GOVALE_SALDO_BAJO', datos)
        return enviados
    except Exception as e:
        print(f"alertar_saldo_govale (whatsapp) error: {e}")
        traceback.print_exc()
        return 0


# ── Botón "reenviar el último evento" ────────────────────────────────────────

def reenviar_ultimo(id_evento):
    """
    Vuelve a mandar el último caso real de un aviso, para probar que el
    mensaje y el adjunto salen bien sin tener que esperar a que pase algo.

    Por qué reconstruye y no reenvía: el aviso no se guarda. Lo que queda en
    `HUB_WhatsappQueue` es el texto ya rendido (y se borra a los 30 días), no el
    evento. Así que se relee el último registro de la tabla del módulo y se
    arma el mensaje igual que cuando ocurrió de verdad.

    El saldo no es un evento sino una revisión periódica, así que ahí lo que se
    manda es el estado de este momento, y se dice.

    Devuelve `(ok, mensaje)` para poder ponerlo en la pantalla.
    """
    try:
        if id_evento == 'KILOMETROS':
            reg = db.get_ultimo_registro_kilometros()
            if not reg:
                return False, 'No hay ningún kilometraje registrado todavía.'
            fecha, hora = nm.ahora_mx()
            datos = nm.datos_kilometros(reg.get('IdAutomovil'), reg.get('Kilometros'),
                                        reg.get('IdUsuario'), fecha, hora)
            km = reg.get('Kilometros')
            detalle = (f"último registro: {km:,} km de {datos['Usuario']}"
                       if isinstance(km, (int, float))
                       else f"último registro de {datos['Usuario']}")
            return _resultado(_encolar('KILOMETROS', datos), detalle)

        elif id_evento == 'OXXOGAS_TICKET':
            reg = db.get_ultimo_ticket_oxxogas(con_foto=True)
            if not reg:
                return False, 'No hay ningún ticket registrado todavía.'
            fecha, hora = nm.ahora_mx()
            # El nombre lo resuelve datos_ticket a partir del IdUsuario; aqui
            # solo se pasa el id.
            datos = nm.datos_ticket(reg.get('FolioTicket'), reg.get('IdVehiculo'),
                                    reg.get('IdCliente'), reg.get('IdUsuario'),
                                    reg.get('Descripcion'), fecha, hora)
            foto = nm.normalizar_foto(reg.get('ImagenTicket'))
            n = _encolar('OXXOGAS_TICKET', datos, foto,
                         'foto.jpg' if foto else None, 'imagen' if foto else None)
            return _resultado(n, f"último ticket: folio {reg.get('FolioTicket')}"
                                 + ("" if foto else " (sin foto legible)"))

        elif id_evento == 'REPORTE_SERVICIO':
            reg = db.get_ultimo_reporte_firmado()
            if not reg:
                return False, 'No hay ningún reporte firmado todavía.'
            datos, pdf, nombre = _datos_reporte_para_reenvio(reg)
            n = _encolar('REPORTE_SERVICIO', datos, pdf, nombre,
                         'documento' if pdf else None)
            return _resultado(n, f"último reporte firmado: {reg.get('Folio')}"
                                 + ("" if pdf else " (sin PDF)"))

        elif id_evento in ('GOVALE_SALDO', 'GOVALE_SALDO_BAJO'):
            saldo = db.get_govale_config('govale_saldo') or '0'
            fecha = db.get_govale_config('govale_saldo_fecha') or 'sin revisar'
            try:
                saldo = float(saldo)
            except (TypeError, ValueError):
                return False, 'No hay saldo registrado todavía.'
            # Se manda el que se pidio, no los dos: si pides el diario y estas
            # en $9,709 no tiene sentido que salga tambien el de "saldo bajo".
            datos = nm.datos_saldo(saldo, fecha)
            if id_evento == 'GOVALE_SALDO_BAJO' and 'BAJO' not in datos['Estado']:
                # En produccion este aviso solo se dispara cuando el saldo cae
                # del umbral, asi que aqui solo se puede llegar pulsando el
                # boton a proposito. Mandarlo seria un WhatsApp que dice
                # "saldo bajo" con un "OK" en la misma pantalla.
                return False, (f"El saldo NO está bajo: ${saldo:,.2f} contra un "
                               f"umbral de ${datos['Umbral']}. No se envió nada "
                               f"(este aviso solo sale cuando baja del umbral).")
            n = _encolar(id_evento, datos)
            return _resultado(n, f"saldo actual: ${saldo:,.2f} (revisado {fecha})")

        else:
            return False, f'No se sabe reproducir el aviso {id_evento}.'

    except Exception as e:
        print(f"reenviar_ultimo({id_evento}) error: {e}")
        traceback.print_exc()
        return False, 'Hubo un error al reconstruir el aviso (ver el log).'


def _datos_reporte_para_reenvio(reg):
    """Arma los datos del reporte firmado usando la misma ruta del aviso real."""
    fecha, hora = nm.ahora_mx()
    completo = None
    try:
        completo = db.get_service_report_by_id(reg.get('IdReporte'))
    except Exception as e:
        print(f"reenviar_ultimo: no se pudo leer el reporte completo ({e})")
    if not completo:
        completo = {'Folio': reg.get('Folio'), 'Cliente': reg.get('Cliente')}
    pdf, nombre = None, None
    try:
        from pdf_generator import generate_service_report_pdf
        pdf = generate_service_report_pdf(completo)
        nombre = f"{completo.get('Folio', 'reporte')}.pdf" if pdf else None
    except Exception as e:
        print(f"reenviar_ultimo: no se pudo generar el PDF ({e})")
    datos = nm.datos_reporte(completo, completo.get('Tecnico', ''), 'Panel', fecha, hora)
    return datos, pdf, nombre


def _resultado(n, detalle):
    if n:
        return True, f"Reenviado a {n} destinatario(s) — {detalle}."
    return False, (f"No se encoló nada ({detalle}). Revisa que el aviso tenga "
                   f"teléfonos y esté activo.")
