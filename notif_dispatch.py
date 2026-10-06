"""
notif_dispatch — Despacho de avisos push por aplicación.

Este módulo es el CEREBRO de las notificaciones: decide a quién le toca un aviso,
si se manda ya o se acumula, y cómo se resume lo acumulado. No envía nada por sí
mismo (eso lo hace `eccsa_db.send_push_notification_to_users`) ni detecta eventos
en un bucle: es una biblioteca de funciones que el worker invoca. La razón es que
la lógica de horario y de destinatarios es lo que hay que poder PROBAR sin base
de datos y sin esperar a que sea el día correcto.

De dónde sale la decisión de "por aplicación"
--------------------------------------------
Cada suscripción push pertenece a un ORIGEN (admon.ecc-sa.com.mx,
field.ecc-sa.com.mx, mailbox.ecc-sa.com.mx). Un endpoint de push service está
atado al service worker de ese origen: si el dispatcher mandara un aviso de
Field a una suscripción creada en Mailbox, la notificación la mostraría el SW de
Mailbox. Por eso toda suscripción lleva su columna `App` y el despacho filtra
por ella. Sin eso el usuario recibe el aviso de kilómetros de Field dentro de
Mailbox, y eso es exactamente lo que hace que la gente apague los permisos.

De dónde sale el horario
-----------------------
Lunes a viernes de 09:00 a 18:30, hora de la Ciudad de México. Fuera de ese
rango NO se manda nada: se acumula, y al siguiente día laboral sale **un solo
aviso resumen**. Un usuario que_no mira el teléfono un viernes no quiere ocho
avisos en la mañana del lunes; quiere saber que hubo ocho y abrir la app.

El tiempo de México es UTC-6 fijo: desde 2022 el país no aplica horario de
verano, así que el desplazamiento no cambia en el año. Aun así se intenta
`zoneinfo` primero y solo se cae al desplazamiento fijo si la imagen no trae la
base de datos de zonas horarias (`python:3.11-slim` no la trae por defecto).
"""

import datetime
import re

import eccsa_db as db

# ── Configuración ─────────────────────────────────────────────────────────────

# Aplicación que despacha este módulo. El resto de apps (Field, Mailbox) usarán
# el mismo módulo con otro valor; por eso el parámetro está en cada función y no
# es una constante global.
APP_ADMON = "admon"

# Zona horaria de la Ciudad de México.
TZ_MEXICO = datetime.timezone(datetime.timedelta(hours=-6), "Mexico")

# Los cinco avisos de Admon. Cada uno declara el PERMISO que hay que tener para
# recibirlo: así el filtrado sale del mismo catálogo que usa la sesión de Admon y
# no de una lista escrita a mano en el worker.
#
# `titulo_func` arma el texto con el nombre de quien causó el aviso, que es lo
# que hace útil la notificación ("Rosa firmó el reporte RS-123" y no un
# "Hay un reporte nuevo").
TIPOS = {
    "REPORTE_FIRMADO": {
        "permiso": "AccesoReportes",
        "titulo": "✍️ Reporte firmado",
        "url": "/reportes",
        "titulo_func": lambda d: f"✍️ {d['actor']} firmó el reporte {d['ref']}",
        "mensaje_func": lambda d: (
            f"{d['actor']} firmó el reporte {d['ref']} del cliente {d['extra']}."
        ),
    },
    "KILOMETROS": {
        "permiso": "AccesoRegistroKilometros",
        "titulo": "🚗 Registra tus kilómetros",
        "url": "/kilometros",
        # El texto lo arma el detector porque depende de si la semana está o no
        # completa; se sobreescribe en el propio detector.
        "titulo_func": lambda d: "🚗 Registra tus kilómetros",
        "mensaje_func": lambda d: (
            f"{d['actor']}, tu vehículo {d['extra']} no tiene kilometraje registrado "
            f"esta semana. Ábrelo en la app para anotarlo."
        ),
    },
    "TICKET_OXXOGAS": {
        "permiso": "AccesoValesOxxoGas",
        "titulo": "⛽ Ticket OxxoGas registrado",
        "url": "/vales-oxxogas",
        "titulo_func": lambda d: "⛽ Ticket OxxoGas registrado",
        "mensaje_func": lambda d: (
            f"{d['actor']} registró el ticket {d['ref']} ({d['extra']})."
        ),
    },
    "COTIZACION_FIRMADA": {
        "permiso": "AccesoCotizaciones",
        "titulo": "📝 Cotización firmada",
        "url": "/cotizaciones",
        "titulo_func": lambda d: "📝 Cotización firmada",
        "mensaje_func": lambda d: (
            f"{d['actor']} firmó la cotización {d['ref']}. Ya está lista para facturar."
        ),
    },
    "COTIZACION_FACTURADA": {
        "permiso": "AccesoCotizaciones",
        "titulo": "🧾 Cotización facturada",
        "url": "/cotizaciones",
        "titulo_func": lambda d: "🧾 Cotización facturada",
        "mensaje_func": lambda d: (
            f"La cotización {d['ref']} pasó a FACTURADA{(': ' + d['extra']) if d['extra'] else ''}."
        ),
    },
}

# Nombres de columna de permiso. Se validan contra esta lista antes de
# interpolarlos en el SQL: llegan de un diccionario de código, pero el día que
# ese diccionario venga de una tabla el chequeo sigue siendo lo que impide que
# un nombre de columna llegue a la sentencia.
_PERMISO_RE = re.compile(r"^Acceso[A-Za-z]{3,40}$")


def _validar_permiso(permiso):
    """True si el nombre de permiso tiene la forma esperada; si no, se aborta."""
    return bool(_PERMISO_RE.match(permiso or ""))


# ── Reloj (functions puras: sin BD, se prueban directo) ────────────────────────


def ahora_mx():
    """La hora de la Ciudad de México, con zona horaria real si se puede."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Mexico_City"))
    except Exception:
        # Sin tzdata en la imagen (es el caso normal: python:3.11-slim no la
        # trae). El desplazamiento es fijo porque México no aplica horario de
        # verano desde 2022, así que el resultado es el correcto.
        return datetime.datetime.now(TZ_MEXICO)


def _fraccion_a_hora(valor):
    """Convierte una hora decimal a (hora, minuto). 18.5 -> (18, 30)."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        valor = 9.0
    hora = int(valor)
    minuto = int(round((valor - hora) * 60))
    # Un redondeo como 18.999 puede dar 60 y hay que normalizarlo.
    if minuto >= 60:
        hora, minuto = hora + 1, 0
    return max(0, min(23, hora)), max(0, min(59, minuto))


def hora_legible(valor):
    """18.5 -> '18:30'. Para pintar el horario en el panel."""
    h, m = _fraccion_a_hora(valor)
    return f"{h:02d}:{m:02d}"


def es_dia_laboral(momento):
    """Lunes a viernes. `weekday()` da 0=lunes .. 6=domingo."""
    return momento.weekday() < 5


def dentro_de_horario(momento, inicio, fin):
    """
    ¿Se puede mandar este aviso AHORA?

    Tres condiciones, y la última es la que suele olvidarse: un fin de semana no
    es horario de trabajo aunque las horas coincidan, así que el aviso del lunes
    a las 9:05 no debe estar esperando desde el sábado.
    """
    if not es_dia_laboral(momento):
        return False
    h, m = momento.hour, momento.minute
    actual = h + m / 60.0
    return float(inicio) <= actual < float(fin)


def proximo_envio(momento, inicio):
    """
    Cuándo vuelve a abrirse la ventana de envío: el próximo día laboral a la
    hora de inicio. Sirve para saber si lo acumulado ya debe salir.

    Devuelve el instante en hora de México; el dispatcher compara contra
    `ahora_mx()`.
    """
    h, m = _fraccion_a_hora(inicio)
    candidato = momento.replace(hour=h, minute=m, second=0, microsecond=0)
    # Si hoy ya pasó la hora (o no es día laboral), se avanza de día en día hasta
    # encontrar uno que sirva. El bucle no puede dar más de 7 vueltas.
    for _ in range(8):
        if es_dia_laboral(candidato) and candidato > momento:
            return candidato
        candidato = candidato + datetime.timedelta(days=1)
    return candidato


# ── Lectura de configuración (HUB_Config) ──────────────────────────────────────


def cfg_get(cur, clave, defecto=""):
    cur.execute("SELECT CAST(Valor AS VARCHAR(MAX)) AS v FROM HUB_Config WHERE Clave = %s",
                (clave,))
    fila = cur.fetchone()
    return (fila.get("v") if fila else None) or defecto


def cfg_set(cur, clave, valor):
    """Upsert. UPDATE primero y, si no tocó filas, INSERT (SQL 2014 no tiene
    ON CONFLICT y un MERGE con triggers es difícil de depurar)."""
    cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = %s",
                (str(valor), clave))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO HUB_Config (Clave, Valor) VALUES (%s, %s)", (clave, str(valor)))


def tipo_activo(cur, tipo, app=APP_ADMON):
    """¿Este tipo de aviso está encendido? La clave vive en HUB_Config.

    El nombre del tipo en MAYÚSCULAS es exactamente el sufijo de la clave:
    TICKET_OXXOGAS -> avisos_push_ticket_oxxogas.
    """
    clave = f"avisos_push_{str(tipo).lower()}"
    return cfg_get(cur, clave, "1").strip().lower() in ("1", "true", "si", "sí", "yes")


def _horario_de(leer):
    """
    (inicio, fin) en hora decimal, calculados por una función de lectura.

    Se factoriza así para que el panel pueda mostrar el MISMO horario que aplica
    el worker sin abrir la base: le pasa un dict ya leído en vez de un cursor. Si
    el cálculo se duplicara en la pantalla, el día que cambiara una regla los dos
   mostrarían cosas distintas y nadie lo notaría hasta un aviso perdido.
    """
    def _num(clave, defecto):
        try:
            return float(str(leer(clave, str(defecto))).strip())
        except (TypeError, ValueError):
            return float(defecto)
    return _num("avisos_push_horario_inicio", 9), _num("avisos_push_horario_fin", 18.5)


def horario(cur):
    """(inicio, fin) leyendo de HUB_Config con un cursor."""
    return _horario_de(lambda clave, defecto: cfg_get(cur, clave, defecto))


def horario_desde(cfg):
    """(inicio, fin) a partir de un {clave: valor} ya leído. Para el panel."""
    return _horario_de(lambda clave, defecto: cfg.get(clave) or defecto)


def resumen_activo(cur):
    return cfg_get(cur, "avisos_push_resumen_activo", "1").strip().lower() in ("1", "true", "si", "sí")


def resumen_activo_desde(cfg):
    """Igual que `resumen_activo`, pero sobre un dict (para el panel)."""
    return str(cfg.get("avisos_push_resumen_activo") or "1").strip().lower() in (
        "1", "true", "si", "sí")


# ── Marcadores de "ya avisado" ────────────────────────────────────────────────
#
# Sin un marcador por evento, cualquier reinicio del worker volvería a avisar
# todo lo anterior. El marcador es un id o una fecha guardados en HUB_Config, y
# se avanza EN LA MISMA transacción que encola: si el worker muere entre una cosa
# y la otra, se prefiere duplicar un aviso a perder la interacción con la base.


def marcador_get(cur, nombre, defecto=""):
    return cfg_get(cur, f"avisos_mk_{nombre}", defecto)


def marcador_set(cur, nombre, valor):
    cfg_set(cur, f"avisos_mk_{nombre}", valor)


# ── Destinatarios ─────────────────────────────────────────────────────────────


def usuarios_con_suscripcion(app=APP_ADMON, permiso=None, conn=None):
    """
    Quién recibe: usuarios ACTIVOS que tienen el permiso Y un dispositivo
    suscrito para ESTA app.

    El filtro de permiso es el mismo que usa la sesión de Admon, así que nadie
    recibe avisos de un módulo que no puede abrir. Y el filtro por app es lo que
    evita el cruce de notificaciones entre aplicaciones.

    Devuelve lista de dicts {Id, Nombre, Email}.
    """
    if permiso and not _validar_permiso(permiso):
        raise ValueError(f"permiso con nombre invalido: {permiso!r}")

    where = ["u.Activo = 1", "s.Activo = 1", "s.App = %s"]
    params = [app]
    if permiso:
        # Nombre de columna interpolado: ya validado contra _PERMISO_RE.
        where.append(f"u.{permiso} = 1")
    # Solo usuarios con correo: sin él no hay a quién mandar (es la columna que
    # se cruza contra la suscripción).
    where.append("ISNULL(u.Email, '') <> ''")

    sql = (
        "SELECT DISTINCT u.Id, u.Nombre, u.Email "
        "FROM HUB_Users u "
        "INNER JOIN HUB_PushSuscripciones s ON s.IdUsuario = u.Id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY u.Nombre"
    )
    return _filas(sql, tuple(params), conn=conn)


def _filas(sql, params=(), conn=None):
    """SELECT que devuelve lista de dicts; nunca lanza (una caída del detector no
    puede tumbar al worker completo)."""
    try:
        if conn is not None:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        with db.get_connection() as c:
            with c.cursor(as_dict=True) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as exc:
        print(f"[avisos] consulta fallo: {exc}")
        return []


# ── Cola ──────────────────────────────────────────────────────────────────────


def encolar(tipo, actor, ref, extra="", url=None, solo_ids=None, app=APP_ADMON,
            conn=None):
    """
    Encola el aviso para todos los destinatarios que correspondan.

    El reparto se hace AQUÍ, al detectar el evento, y no al enviar. La razón es
    que los permisos pueden cambiar entre el evento y el envío: si alguien pierde
    el permiso de Cotizaciones un minuto después de que se firmó algo, no debe
    enterarse de esa cotización concreta por un aviso que llevaba diez minutos
    esperando a las 9 de la mañana.

    `solo_ids` acota el reparto (lo usa el de kilómetros, que es por vehículo).

    `conn` es OBLIGATORIO en la práctica dentro de un detector. `get_connection()`
    de eccsa_db devuelve una conexión cacheada por hilo y cerrarla la invalida
    para todo el que la tenga en la mano: si el detector abría una conexión, la
    daba prestada a `encolar` y esta abría la suya con `with`, al volver el
    detector tenía un cursor muerto y el marcador ya no se guardaba. Pasando la
    conexión se abre y se cierra una sola vez por ciclo.

    Devuelve cuántas filas se encolaron.
    """
    defn = TIPOS.get(tipo)
    if not defn:
        return 0

    def _usuarios():
        if conn is not None:
            return usuarios_con_suscripcion(app=app, permiso=defn["permiso"], conn=conn)
        with db.get_connection() as c:
            return usuarios_con_suscripcion(app=app, permiso=defn["permiso"], conn=c)

    destinatarios = _usuarios()
    if solo_ids is not None:
        permitidos = set(solo_ids)
        destinatarios = [d for d in destinatarios if d["Id"] in permitidos]
    if not destinatarios:
        return 0

    titulo = defn["titulo_func"]({"actor": actor, "ref": ref, "extra": extra})
    mensaje = defn["mensaje_func"]({"actor": actor, "ref": ref, "extra": extra})
    enlace = url or defn["url"]

    def _inserta(cur, app_, tipo_, id_usuario):
        try:
            cur.execute(
                "INSERT INTO HUB_AvisosCola "
                "(App, Tipo, IdUsuario, Estado, EsResumen, Titulo, Mensaje, Url, Creado) "
                "VALUES (%s, %s, %s, 'PENDIENTE', 0, %s, %s, %s, GETDATE())",
                (app_, tipo_, id_usuario, titulo[:200], mensaje[:1000], (enlace or "")[:200]),
            )
            return 1
        except Exception as exc:
            # El índice único es (IdUsuario, Tipo, Creado): dos inserts del mismo
            # usuario en el mismo segundo son el MISMO aviso, no dos.
            if "duplicate" in str(exc).lower() or "2627" in str(exc):
                return 0
            raise

    inserted = 0
    try:
        if conn is not None:
            cur = conn.cursor()
            for d in destinatarios:
                inserted += _inserta(cur, app, tipo, d["Id"])
        else:
            with db.get_connection() as c:
                cur = c.cursor()
                for d in destinatarios:
                    inserted += _inserta(cur, app, tipo, d["Id"])
                c.commit()
    except Exception as exc:
        print(f"[avisos] no se pudo encolar {tipo}: {exc}")
        return inserted
    return inserted


def pendientes(app=APP_ADMON, limite=500):
    # El TOP va concatenado y NO con %d: la sentencia también lleva un %s (el
    # de `App`), y un solo operador `%` sobre los dos marcadores se come los dos
    # argumentos a la vez. Puesto como `TOP (N)` no hay formato que aplicar.
    sql = (
        "SELECT TOP (" + str(int(limite)) + ") Id, App, Tipo, IdUsuario, "
        "Titulo, Mensaje, Url FROM HUB_AvisosCola "
        "WHERE App = %s AND Estado = 'PENDIENTE' ORDER BY Creado ASC"
    )
    return _filas(sql, (app,))


def marcar(ids, estado, detalle=None):
    if not ids:
        return 0
    lista = ",".join(str(int(i)) for i in ids)
    conn = db.get_connection()
    cur = conn.cursor()
    if detalle:
        cur.execute(
            f"UPDATE HUB_AvisosCola SET Estado = %s, Enviado = GETDATE(), Detalle = %s "
            f"WHERE Id IN ({lista})",
            (estado, str(detalle)[:400]),
        )
    else:
        cur.execute(
            f"UPDATE HUB_AvisosCola SET Estado = %s, Enviado = GETDATE() WHERE Id IN ({lista})",
            (estado,),
        )
    conn.commit()


def limpiar(dias=30):
    """Borra lo entregado hace más de N días. Nunca lo pendiente: es lo único que
    sostiene el resumen."""
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM HUB_AvisosCola "
            "WHERE Estado <> 'PENDIENTE' AND Enviado < DATEADD(day, %s, GETDATE())",
            (-int(dias),),
        )
        return cur.rowcount


# ── Detección de eventos ──────────────────────────────────────────────────────
#
# Los cinco avisos salen de datos que ALGUIEN ya escribe (Admon al firmar un
# reporte o cambiar el estatus de una cotización, Field al registrar un ticket).
# No se toca ninguna app para avisar: el worker pregunta "¿qué hay de nuevo?".
# Esa es la diferencia entre un aviso y un webhook, y es lo que permite que un
# evento que ocurre en Field llegue a un usuario que solo entra por Admon.
#
# El marcador de cada detector es un id o una fecha ya leídos. Se avanza en la
# misma transacción que encola, así que un reinicio a mitad de ciclo no repite.


def detectar_reportes_firmados(app=APP_ADMON):
    """
    Reportes de servicio recién firmados.

    `ReportesServicio` no tiene columna de fecha de firma (solo `FirmaConformidad`),
    así que el marcador es el IdReporte más alto visto. Es igual de fiable para
    esto: los ids son monotónicos y solo nos interesan los nuevos.
    """
    with db.get_connection() as conn:
        marcador = int(_marcador("reporte_firmado", "0", conn=conn) or 0)
        filas = _filas(
            "SELECT r.IdReporte, r.Folio, r.Cliente, ISNULL(u.Nombre, 'Alguien') AS Actor "
            "FROM ReportesServicio r "
            "LEFT JOIN HUB_Users u ON u.Nombre = r.Tecnico "
            "WHERE r.IdReporte > %s "
            "  AND r.FirmaConformidad IS NOT NULL "
            "  AND LTRIM(RTRIM(r.FirmaConformidad)) <> '' "
            "  AND ISNULL(r.Eliminado, 0) = 0 "
            "ORDER BY r.IdReporte ASC",
            (marcador,), conn=conn,
        )
        if not filas:
            return 0
        enviados = 0
        cur = conn.cursor()
        for f in filas:
            enviados += encolar("REPORTE_FIRMADO", f["Actor"] or "Alguien",
                                f["Folio"] or f"#{f['IdReporte']}",
                                extra=f["Cliente"] or "", app=app, conn=conn)
            marcador = max(marcador, int(f["IdReporte"]))
        marcador_set(cur, "reporte_firmado", marcador)
        conn.commit()
    return enviados


def detectar_tickets_oxxogas(app=APP_ADMON):
    """Tickets de OxxoGas recién registrados (los captura Field, los ve Admon)."""
    with db.get_connection() as conn:
        marcador = int(_marcador("ticket_oxxogas", "0", conn=conn) or 0)
        filas = _filas(
            "SELECT t.Id, t.FolioTicket, t.Estacion, ISNULL(u.Nombre, 'Alguien') AS Actor "
            "FROM HUB_OxxoGasTickets t "
            "LEFT JOIN HUB_Users u ON u.Id = t.IdUsuario "
            "WHERE t.Id > %s ORDER BY t.Id ASC",
            (marcador,), conn=conn,
        )
        if not filas:
            return 0
        enviados = 0
        cur = conn.cursor()
        for f in filas:
            enviados += encolar("TICKET_OXXOGAS", f["Actor"] or "Alguien",
                                f["FolioTicket"] or f"#{f['Id']}",
                                extra=f["Estacion"] or "", app=app, conn=conn)
            marcador = max(marcador, int(f["Id"]))
        marcador_set(cur, "ticket_oxxogas", marcador)
        conn.commit()
    return enviados


def detectar_cotizaciones_firmadas(app=APP_ADMON):
    """
    Cotizaciones que quedaron firmadas (las firma el cliente desde Field).

    `IndiceRemisiones` sí tiene `FechaFirma`, así que aquí el marcador es la
    fecha y no el id: así un reporte importado con fecha pasada no dispara un
    aviso que nadie pidió.
    """
    with db.get_connection() as conn:
        marcador = _marcador("cotizacion_firmada", "", conn=conn)
        filas = _filas(
            "SELECT m.IdRemision, m.FolioRemision, m.FolioCotizacion, m.FechaFirma, "
            "       ISNULL(u.Nombre, 'El cliente') AS Actor "
            "FROM IndiceRemisiones m "
            "LEFT JOIN HUB_Users u ON u.Id = m.CreadoPor "
            "WHERE m.FirmaConformidad IS NOT NULL "
            "  AND LTRIM(RTRIM(m.FirmaConformidad)) <> '' "
            "  AND m.FechaFirma IS NOT NULL "
            + ("  AND CONVERT(VARCHAR(19), m.FechaFirma, 120) > %s " if marcador else "")
            + "ORDER BY m.IdRemision ASC",
            ((marcador,) if marcador else ()), conn=conn,
        )
        if not filas:
            return 0
        enviados = 0
        nuevo_marcador = marcador
        cur = conn.cursor()
        for f in filas:
            enviados += encolar("COTIZACION_FIRMADA", f["Actor"] or "El cliente",
                                f["FolioRemision"] or f"Folio #{f['IdRemision']}",
                                app=app, conn=conn)
            iso = f["FechaFirma"].strftime("%Y-%m-%d %H:%M:%S")
            nuevo_marcador = max(nuevo_marcador, iso) if nuevo_marcador else iso
        marcador_set(cur, "cotizacion_firmada", nuevo_marcador)
        conn.commit()
    return enviados


def detectar_cotizaciones_facturadas(app=APP_ADMON):
    """
    Cotizaciones que pasaron a FACTURADA.

    En `Indice_CotizacionesServProy` el estado es una columna `Color`: 0=enviada,
    1=lista para facturar, 2=facturada. Una fila que salta de 0 a 2 no se
    distingue de una que lleva semanas en 2 con el marcador sin avanzar, así que
    el marcador también es `FechaActualizado`: solo salta lo que cambió de verdad.
    """
    with db.get_connection() as conn:
        marcador = _marcador("cotizacion_facturada", "", conn=conn)
        filas = _filas(
            "SELECT c.Id, c.Folio, c.Realizado, c.Factura, c.FechaActualizado "
            "FROM Indice_CotizacionesServProy c "
            "WHERE c.Color = 2 AND c.FechaActualizado IS NOT NULL "
            + ("  AND CONVERT(VARCHAR(19), c.FechaActualizado, 120) > %s " if marcador else "")
            + "ORDER BY c.Id ASC",
            ((marcador,) if marcador else ()), conn=conn,
        )
        if not filas:
            return 0
        enviados = 0
        nuevo_marcador = marcador
        cur = conn.cursor()
        for f in filas:
            extra = f["Factura"] or f["Realizado"] or ""
            enviados += encolar("COTIZACION_FACTURADA", "Alguien",
                                f["Folio"] or f"#{f['Id']}", extra=extra, app=app, conn=conn)
            iso = f["FechaActualizado"].strftime("%Y-%m-%d %H:%M:%S")
            nuevo_marcador = max(nuevo_marcador, iso) if nuevo_marcador else iso
        marcador_set(cur, "cotizacion_facturada", nuevo_marcador)
        conn.commit()
    return enviados


def detectar_kilometros(app=APP_ADMON):
    """
    Un aviso por VEHÍCULO y por semana, al responsable que lo tiene asignado.

    Es el único de los cinco que NO avisa de un evento: avisa de una ausencia
    (que no haya kilometraje). Por eso se salta al que ya registró algo esta
    semana —es el comportamiento del cron que ya existía en Field y no hay
    razón para nagginguearlo— y por eso el marcador es la semana ISO, no un id.

    Se avisa aunque el evento "kilómetros registrados" haya ocurrido: el
    requisito es un recordatorio semanal por auto.
    """
    hoy = ahora_mx()
    # Clave de semana: año y número de semana, para que al cambiar de semana el
    # marcador deje de coincidir y todos los autos vuelvan a entrar.
    iso = hoy.isocalendar()
    semana = f"{iso[0]}-W{iso[1]:02d}"

    with db.get_connection() as conn:
      filas = _filas(
        "SELECT a.Id AS IdAuto, a.MarcaModelo, a.Placas, a.IdUsuarioAsignado "
        "FROM HUB_Automoviles a "
        "WHERE ISNULL(a.IdUsuarioAsignado, 0) > 0", (), conn=conn,
      )
      if not filas:
        return 0

      con_permiso = {
          d["Id"] for d in usuarios_con_suscripcion(
              app=app, permiso=TIPOS["KILOMETROS"]["permiso"], conn=conn)
      }
      enviados = 0
      cur = conn.cursor()
      for a in filas:
            if a["IdUsuarioAsignado"] not in con_permiso:
                continue
            nombre_auto = a["Placas"] or a["MarcaModelo"] or f"#{a['IdAuto']}"
            if marcador_get(cur, f"km_{a['IdAuto']}", "") == semana:
                continue                      # ya se avisó esta semana
            # ¿Registró algo desde el lunes? Si sí, no hay nada que recordar.
            desde = (hoy - datetime.timedelta(days=hoy.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0)
            n = _filas(
                "SELECT COUNT(*) AS n FROM HUB_RegistroKilometros "
                "WHERE IdAutomovil = %s AND FechaHora >= %s",
                (a["IdAuto"], desde.strftime("%Y-%m-%d %H:%M:%S")), conn=conn,
            )
            if n and int(n[0]["n"]) > 0:
                marcador_set(cur, f"km_{a['IdAuto']}", semana)
                continue
            nombre = _nombre_usuario(a["IdUsuarioAsignado"], conn=conn)
            enviados += encolar("KILOMETROS", nombre, nombre_auto, extra=nombre_auto,
                                solo_ids=[a["IdUsuarioAsignado"]], app=app, conn=conn)
            marcador_set(cur, f"km_{a['IdAuto']}", semana)
      conn.commit()
    return enviados


def _nombre_usuario(id_usuario, conn=None):
    filas = _filas("SELECT Nombre FROM HUB_Users WHERE Id = %s", (id_usuario,), conn=conn)
    return (filas[0]["Nombre"] if filas else "") or "El responsable"


def _marcador(nombre, defecto="", conn=None):
    """
    Lee un marcador abriendo y cerrando la conexión en el acto.

    `eccsa_db.get_connection()` devuelve una conexión CACHEADA por hilo, así que
    abrirla con `with` la cierra de verdad. Si un detector abría una conexión y la
    daba prestada a alguien que abría la suya con `with`, al volver tenía un
    cursor muerto: el INSERT entraba pero el marcador se perdía y el siguiente
    ciclo volvía a avisar lo mismo. Por eso se recibe `conn` y, sin ella, se
    abre y se cierra explícitamente sin `with`.
    """
    propia = conn is None
    try:
        if propia:
            conn = db.get_connection()
        cur = conn.cursor(as_dict=True)
        valor = cfg_get(cur, f"avisos_mk_{nombre}", defecto)
        cur.close()
        return valor
    except Exception as exc:
        print(f"[avisos] marcador {nombre} ilegible: {exc}")
        return defecto
    finally:
        if propia and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


DETECTORES = {
    "REPORTE_FIRMADO": detectar_reportes_firmados,
    "KILOMETROS": detectar_kilometros,
    "TICKET_OXXOGAS": detectar_tickets_oxxogas,
    "COTIZACION_FIRMADA": detectar_cotizaciones_firmadas,
    "COTIZACION_FACTURADA": detectar_cotizaciones_facturadas,
}


def detectar(app=APP_ADMON):
    """
    Corre los cinco detectores y devuelve {tipo: encolados}.

    Cada detector se aísla: uno que falle —porque su tabla no existe todavía, o
    porque le cambiaron una columna— no puede tumbar a los otros cuatro.
    """
    resultado = {}
    for tipo, fn in DETECTORES.items():
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    if not tipo_activo(cur, tipo, app=app):
                        resultado[tipo] = 0
                        continue
            resultado[tipo] = fn(app=app)
        except Exception as exc:
            print(f"[avisos] detector {tipo} fallo: {exc}")
            resultado[tipo] = 0
    return resultado


# ── Resumen (digest) ──────────────────────────────────────────────────────────


def _texto_resumen(filas):
    """Un texto que diga cuántos son y los primeros, sin volcar una tabla."""
    n = len(filas)
    titulos = [f["Titulo"] for f in filas if f.get("Titulo")]
    primeros = titulos[:4]
    cuerpo = ", ".join(primeros) + ("…" if n > 4 else "")
    plural = "s" if n != 1 else ""
    return (f"Tienes {n} aviso{plural} pendiente{plural}: {cuerpo}. "
            f"Ábrelos en la app para verlos.")


def enviar_resumen(app=APP_ADMON):
    """
    Junta TODO lo acumulado en un solo aviso por usuario y lo entrega.

    Por usuario y no uno global porque los avisos son de módulos distintos: al
    que solo tiene permiso de Cotizaciones no le interesa que se haya
    registrado un kilometer. Se manda uno por usuario con la lista de los suyos.
    """
    filas = pendientes(app=app, limite=2000)
    if not filas:
        return 0
    por_usuario = {}
    for f in filas:
        por_usuario.setdefault(f["IdUsuario"], []).append(f)

    enviados = 0
    for id_usuario, propias in por_usuario.items():
        correos = _correos_de([id_usuario])
        if not correos:
            marcar([f["Id"] for f in propias], "ENVIADO", "sin suscripcion activa")
            continue
        n = len(propias)
        plural = "s" if n != 1 else ""
        titulo = f"🔔 Tienes {n} aviso{plural}"
        extra = {"tag": f"admon-digest-{id_usuario}", "url": "/",
                 "badge_count": n}
        ok, _fallo = db.send_push_notification_to_users(titulo, _texto_resumen(propias),
                                                        correos, extra=extra)
        if ok > 0:
            enviados += 1
            marcar([f["Id"] for f in propias], "ENVIADO")
        else:
            marcar([f["Id"] for f in propias], "FALLADO", "sin dispositivos que acepten")
    return enviados


def _correos_de(ids_usuario):
    """Correos de los usuarios indicados que tienen suscripción para esta app."""
    if not ids_usuario:
        return []
    lista = ",".join(str(int(i)) for i in ids_usuario)
    filas = _filas(
        f"SELECT DISTINCT u.Email FROM HUB_Users u "
        f"INNER JOIN HUB_PushSuscripciones s ON s.IdUsuario = u.Id "
        f"WHERE u.Id IN ({lista}) AND u.Activo = 1 AND s.Activo = 1 AND s.App = %s "
        f"AND ISNULL(u.Email, '') <> ''",
        (APP_ADMON,),
    )
    return [f["Email"] for f in filas]


# ── Ciclo de despacho ─────────────────────────────────────────────────────────


def despachar(app=APP_ADMON):
    """
    Un turno del despacho. Devuelve un texto con lo que hizo, para el log.

    La decisión es de UNA vez y en un solo lugar:

      · dentro de horario  -> se envía cada aviso por separado, en el orden en que
        ocurrieron. Es el caso normal: el usuario está trabajando y quiere enterarse
        ya.
      · fuera de horario    -> no se manda NADA. Se quedan en la cola.

    Y el resumen no se dispara "al primer ciclo de la mañana" sino cuando la
    ventana vuelve a abrir: se compara contra el último día laboral en que sí se
    envió, así que un reinicio del worker a las 8:50 no se come el resumen ni lo
    manda dos veces.
    """
    momento = ahora_mx()
    with db.get_connection() as conn:
        with conn.cursor(as_dict=True) as cur:
            inicio, fin = horario(cur)
            resumen_on = resumen_activo(cur)
            ultimo_dia = cfg_get(cur, "avisos_ultimo_resumen", "")

    pendientes_n = len(pendientes(app=app))

    if not dentro_de_horario(momento, inicio, fin):
        # Fuera de horario: nada sale. Se revisa si ya se pudo entregar lo viejo.
        if pendientes_n and _corresponde_resumen(momento, inicio, ultimo_dia, resumen_on):
            n = enviar_resumen(app=app)
            with db.get_connection() as conn:
                cur = conn.cursor()
                cfg_set(cur, "avisos_ultimo_resumen", momento.strftime("%Y-%m-%d"))
                conn.commit()
            return f"fuera de horario ({hora_legible(inicio)}-{hora_legible(fin)}); resumen entregado a {n} usuario(s)"
        return f"fuera de horario ({hora_legible(inicio)}-{hora_legible(fin)}); {pendientes_n} en cola"

    enviados = 0
    for f in pendientes(app=app):
        extra = {
            "tag": f"admon-{f['Tipo'].lower()}",
            "url": f.get("Url") or "/",
        }
        ok, _fallo = db.send_push_notification_to_users(
            f["Titulo"], f["Mensaje"], _correos_de([f["IdUsuario"]]), extra=extra)
        if ok > 0:
            enviados += 1
            marcar([f["Id"]], "ENVIADO")
        else:
            marcar([f["Id"]], "FALLADO", "sin dispositivos que acepten")

    # Ya estamos en horario y se vació lo viejo: se anota el día para que el
    # resumen de mañana no se dispare otra vez por lo mismo.
    if resumen_on and ultimo_dia != momento.strftime("%Y-%m-%d"):
        with db.get_connection() as conn:
            cur = conn.cursor()
            cfg_set(cur, "avisos_ultimo_resumen", momento.strftime("%Y-%m-%d"))
            conn.commit()

    return f"dentro de horario; {enviados} enviado(s), {pendientes_n - enviados} sin destino"


def _corresponde_resumen(momento, inicio, ultimo_dia, resumen_on):
    """¿Toca sacar el resumen acumulado?

    Sí cuando está habilitado, hay algo pendiente, ya se pasó la hora de apertura
    de HOY (o hoy no es día laboral, y entonces corresponde al próximo) y no se
    entregó el resumen de hoy.
    """
    if not resumen_on or not ultimo_dia:
        return False
    h, m = _fraccion_a_hora(inicio)
    apertura = momento.replace(hour=h, minute=m, second=0, microsecond=0)
    if es_dia_laboral(momento):
        return momento >= apertura and ultimo_dia != momento.strftime("%Y-%m-%d")
    return ultimo_dia < momento.strftime("%Y-%m-%d")
