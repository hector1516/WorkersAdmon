"""
panel.views.notifications — Pestaña "🔔 Notificaciones" del panel (Fase C)
=========================================================================
Cuatro bloques, cada uno con su permiso del HUB, su formulario y su botón de
prueba:

  🔔 Telegram  (AccesoTelegram)      token + eventos/plantillas + destinatarios
  🔔 Push      (AccesoConfiguracion) claves VAPID + prueba a suscriptores
  📧 Correo    (AccesoConfigurarCorreo) SMTP + prueba de envío
  🤖 IA        (AccesoConfigAI)      Gemini provider/key/model + prueba

Los secretos nunca se pintan en el HTML (campo en blanco = no cambiar) y no
se registran en la bitácora.
"""
from .. import db, probes, workers
from ..templates import esc, page

_CSRF = {"token": ""}

# Permiso de cada bloque (None = basta con AccesoConfiguracion del panel)
PERMISOS = {
    "telegram": ("AccesoTelegram", "AccesoTelegram"),
    "push": (None, "AccesoConfiguracion"),
    "correo": ("AccesoConfigurarCorreo", "AccesoConfigurarCorreo"),
    "ia": ("AccesoConfigAI", "AccesoConfigAI"),
}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _allowed(user, bloque):
    perm, _ = PERMISOS.get(bloque, (None, ""))
    if not perm:
        return True
    perms = (user or {}).get("perms", {})
    return bool(perms.get(perm))


# ─── fragmentos de formulario ────────────────────────────────────────────────
def _text(name, value="", label="", help_="", tipo="text", attrs=""):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    lab = f'<label>{esc(label)}</label>' if label else ""
    return (f'<div class="cfg-field">{lab}<input type="{tipo}" name="{esc(name)}" '
            f'value="{esc(value)}"{attrs}>{h}</div>')


def _secret(name, has_value, label="", help_=""):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    ph = "(guardado)" if has_value else "(sin definir)"
    return (f'<div class="cfg-field"><label>{esc(label)}</label>'
            f'<input type="password" name="{esc(name)}" value="" '
            f'autocomplete="new-password" placeholder="{esc(ph)}" class="input">{h}</div>')


def _number(name, value, label="", mn=None, mx=None, help_=""):
    attrs = ""
    if mn is not None:
        attrs += f' min="{int(mn)}"'
    if mx is not None:
        attrs += f' max="{int(mx)}"'
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="cfg-field"><label>{esc(label)}</label>'
            f'<input type="number" name="{esc(name)}" value="{esc(value)}"'
            f'{attrs} class="input">{h}</div>')


def _bool(name, value, label="", help_=""):
    val = "1" if value in (1, "1", True, "true") else "0"
    opts = "".join(f'<option value="{v}"{" selected" if val == v else ""}>{l}</option>'
                   for v, l in (("1", "Sí"), ("0", "No")))
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="cfg-field"><label>{esc(label)}</label>'
            f'<select class="input" name="{esc(name)}">{opts}</select>{h}</div>')


def _textarea(name, value, label="", help_="", rows=3):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    lab = f'<label>{esc(label)}</label>' if label else ""
    return (f'<div class="cfg-field" style="grid-column:1/-1">{lab}'
            f'<textarea class="input" name="{esc(name)}" rows="{rows}">{esc(value)}</textarea>{h}</div>')


def _multi(name, options, selected, label=""):
    """select multiple: options = [(valor, texto)], selected = set/iterable de ids."""
    sel = set(selected or ())
    opts = "".join(
        f'<option value="{esc(v)}"{" selected" if str(v) in sel else ""}>{esc(t)}</option>'
        for v, t in options)
    return (f'<div class="cfg-field" style="grid-column:1/-1"><label>{esc(label)}</label>'
            f'<select class="input" name="{esc(name)}" multiple size="{min(8, max(3, len(options)))}">'
            f'{opts}</select></div>')


def _card(titulo, perm_label, permitido, cuerpo, accion, prueba=""):
    """Tarjeta genérica de bloque (formulario propio, nunca anidados)."""
    if not permitido:
        return f"""
  <div class="panel">
    <h2>{titulo}</h2>
    <div class="empty">🔒 Requiere el permiso <code>{esc(perm_label)}</code> en el HUB.</div>
  </div>"""
    head = f"<h2>{titulo}"
    if prueba:
        head += f'<span style="float:right">{prueba}</span>'
    head += "</h2>"
    forms = cuerpo
    return f"""
  <div class="panel">{head}
    <form method="post" action="{esc(accion)}">{_csrf_field()}{forms}</form>
  </div>"""


def _btn_prueba(accion, etiqueta="🧪 Probar"):
    return (f'<form class="inline" method="post" action="{esc(accion)}">'
            f'{_csrf_field()}'
            f'<button class="btn btn-secondary" type="submit">{esc(etiqueta)}</button></form>')


def _save_bar():
    return ('<div class="cfg-save">'
            '<button class="btn btn-success" type="submit">💾 Guardar</button></div>')


# ─── bloques ─────────────────────────────────────────────────────────────────
# ─── Telegram: sub-pestañas (misma organización que views/telegram.py del HUB)
VISTAS_TG = [
    ("conexion", "\U0001f50c Conexión"),
    ("eventos", "\u269f\ufe0f Eventos"),
    ("destinatarios", "\U0001f465 Destinatarios"),
    ("vinculados", "\U0001f517 Vinculados"),
    ("historial", "\U0001f4cb Historial"),
]


def _tnav(vista):
    """Barra de sub-pestañas del bloque Telegram (equivale a st.tabs del HUB)."""
    items = []
    for v, label in VISTAS_TG:
        cls = ' class="on"' if v == vista else ""
        items.append(f'<a{cls} href="/notificaciones?tg={v}">{esc(label)}</a>')
    return '<div class="tnav">' + "".join(items) + "</div>"


def _encabezado(titulo, texto):
    """Título + descripción de la sub-pestaña (como los markdown del HUB)."""
    return (f'<div class="panel"><h2>{titulo}</h2>'
            f'<p class="desc">{texto}</p></div>')


def _tabla(headers, filas, vacio):
    """Tabla scrolleable; cada fila es una lista de celdas ya escapadas."""
    if not filas:
        return f'<div class="panel"><div class="empty">{vacio}</div></div>'
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in fila) + "</tr>"
                 for fila in filas)
    return ('<div class="panel"><div class="tscroll"><table>'
            f"<thead><tr>{th}</tr></thead><tbody>{tr}</tbody>"
            "</table></div></div>")


def _fval(v):
    """Fecha a dd/mm/aaaa hh:mm (o cadena vacía)."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y %H:%M")
    return str(v)


# ── sub-pestaña: Conexión ────────────────────────────────────────────────────
def _tg_conexion():
    token = db.get_config_values(["telegram_bot_token"]).get("telegram_bot_token", "")
    m = db.telegram_metrics()
    cuerpo = (
        '<div class="cfg-grid">'
        + _secret("telegram_bot_token", bool(token), "Token del bot (@BotFather)",
                  "Se guarda en HUB_Config; lo usan todas las alertas del HUB.")
        + f'<div class="cfg-field"><label>Resumen</label><div class="ro">'
          f'vinculados {esc(m.get("vinculados", 0))} · eventos {esc(m.get("eventos", 0))} · '
          f'destinatarios {esc(m.get("destinatarios", 0))} · '
          f'cola {esc(m.get("pendientes", 0))} pend / {esc(m.get("fallados", 0))} fall / '
          f'{esc(m.get("enviados", 0))} enviados</div></div>'
        + "</div>" + _save_bar())
    principal = _card("\U0001f50c Conexión · Bot de Telegram", "AccesoTelegram", True,
                      cuerpo, "/notificaciones/telegram",
                      prueba=_btn_prueba("/notificaciones/telegram/probar",
                                         "\U0001f9ea Probar Bot"))
    instr = """
  <div class="panel"><h2>\U0001f4f1 Cómo se vinculan los usuarios</h2>
    <ol class="desc">
      <li>Crear el bot con <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> y pegar el token arriba.</li>
      <li>Cada usuario abre Telegram, busca <b>@el bot</b> y presiona <b>Start</b>.</li>
      <li>Presiona <b>Compartir teléfono</b> (si está habilitado): el sistema
          vincula su chat_id con su cuenta del HUB automáticamente.</li>
      <li>Si no puede compartir el teléfono, un admin lo vincula a mano en la
          sub-pestaña <b>\U0001f517 Vinculados</b>.</li>
    </ol>
  </div>"""
    nota = ('<div class="empty" style="text-align:left;padding-top:0">'
            'Mismas tablas que el HUB (<code>HUB_TelegramEventos</code>, '
            '<code>HUB_TelegramDestinatarios</code>): los cambios aplican en el '
            'próximo disparo de la alerta, sin reiniciar nada.</div>')
    return principal + instr + nota


# ── sub-pestaña: Eventos ─────────────────────────────────────────────────────
def _tg_eventos():
    out = [_encabezado(
        "\u269f\ufe0f Eventos de Alerta",
        "Configure qué alertas se envían y el texto de cada una. Placeholders: "
        "{Folio} {Fecha} {Nombre} {Cantidad} {Automovil} {Cliente} {Descripcion} "
        "{Tecnico} {Kilometros} {Usuario}.")]
    eventos = db.get_telegram_eventos()
    if not eventos:
        out.append('<div class="panel"><div class="empty">'
                   'No hay eventos configurados (migración 0018 del HUB).</div></div>')
        return "".join(out)
    for ev in eventos:
        eid = ev["IdEvento"]
        estado = "\U0001f7e2" if ev.get("Activo") else "\U0001f534"
        forms = (
            '<div class="cfg-grid">'
            + _textarea("PlantillaMensaje", ev.get("PlantillaMensaje") or "",
                        label="Plantilla del mensaje",
                        help_="Texto que llega al chat. Use los placeholders de arriba.")
            + _bool("AdjuntarArchivo", ev.get("AdjuntarArchivo"), "Adjuntar archivo")
            + _bool("Activo", ev.get("Activo"), "Activo")
            + "</div>" + _save_bar())
        out.append(f"""
  <div class="panel">
    <h2>{estado} {esc(ev.get('Nombre'))} · <code>{esc(eid)}</code></h2>
    <form method="post" action="/notificaciones/telegram/evento">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </div>""")
    return "".join(out)


# ── sub-pestaña: Destinatarios ───────────────────────────────────────────────
def _chips(ids, mapa):
    """Chips visibles con los destinatarios seleccionados de un evento."""
    partes = []
    for i in ids:
        try:
            uid = int(i)
        except (TypeError, ValueError):
            continue
        u = mapa.get(uid)
        if u:
            partes.append(f'<span class="chip">{esc(u.get("Nombre") or "")}'
                          f'<em> {esc(u.get("Email") or "")}</em></span>')
        else:
            partes.append(f'<span class="chip off">#{uid} (inactivo)</span>')
    return "".join(partes) or '<span class="chip warn">Sin destinatarios</span>'


def _tg_destinatarios():
    out = [_encabezado(
        "\U0001f465 Destinatarios por Evento",
        "El <b>resumen</b> de abajo muestra, de un vistazo, qué usuarios recibe "
        "cada alerta. Para cambiar la selección, marque/desmarque en la tarjeta "
        "del evento y guarde.")]
    eventos = db.get_telegram_eventos()
    if not eventos:
        out.append('<div class="panel"><div class="empty">'
                   'No hay eventos configurados (migración 0018 del HUB).</div></div>')
        return "".join(out)
    usuarios = db.get_active_users()
    mapa = {int(u["Id"]): u for u in usuarios}

    # ── resumen: quién recibe cada evento, en una sola tabla ────────────────
    filas = []
    for ev in eventos:
        eid = ev["IdEvento"]
        ids = db.get_telegram_destinatarios(eid)
        estado = "\U0001f7e2" if ev.get("Activo") else "\U0001f534"
        filas.append([estado, esc(ev.get("Nombre") or ""),
                      f'<code>{esc(eid)}</code>', _chips(ids, mapa),
                      f'<b>{len(ids)}</b>'])
    out.append(_tabla(["", "Evento", "Clave", "Usuarios que la reciben", "N.º"],
                      filas, ""))

    # ── edición por evento: checklist en lugar del select múltiple ─────────
    if not usuarios:
        out.append('<div class="panel"><div class="empty">'
                   'No hay usuarios activos en el HUB.</div></div>')
        return "".join(out)
    for ev in eventos:
        eid = ev["IdEvento"]
        estado = "\U0001f7e2" if ev.get("Activo") else "\U0001f534"
        sel = {int(i) for i in db.get_telegram_destinatarios(eid)}
        n = len(sel)
        checks = "".join(
            f'<label class="chk"><input type="checkbox" name="ids" '
            f'value="{int(u["Id"])}"'
            f'{" checked" if int(u["Id"]) in sel else ""}>'
            f'<span>{esc(u.get("Nombre") or "")}'
            f'<em> {esc(u.get("Email") or "")}</em></span></label>'
            for u in usuarios)
        forms = (
            '<div class="cfg-grid">'
            f'<div class="cfg-field" style="grid-column:1/-1">'
            f'<label>Usuarios que reciben esta alerta</label>'
            f'<div class="chklist">{checks}</div>'
            f'<p class="desc">Seleccionados ahora: <b>{n}</b>. Los cambios '
            f'aplican en el próximo disparo de la alerta, sin reiniciar nada.'
            f'</p></div>'
            '</div>' + _save_bar())
        out.append(f"""
  <div class="panel">
    <h2>{estado} {esc(ev.get('Nombre'))} · <code>{esc(eid)}</code>
        ({n} seleccionados)</h2>
    <div class="chiprow">{_chips(sorted(sel), mapa)}</div>
    <form method="post" action="/notificaciones/telegram/destinatarios">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </div>""")
    return "".join(out)


# ── sub-pestaña: Vinculados ──────────────────────────────────────────────────
def _tg_vinculados():
    out = [_encabezado(
        "\U0001f517 Usuarios Vinculados a Telegram",
        "Gestione la vinculación entre usuarios del HUB y sus cuentas de Telegram.")]
    vinc = db.get_telegram_vinculados()
    filas = []
    for v in vinc:
        uid = int(v.get("IdUsuario") or 0)
        btn = ('<form class="inline" method="post" '
               'action="/notificaciones/telegram/desvincular">'
               f'{_csrf_field()}<input type="hidden" name="IdUsuario" value="{uid}">'
               '<button class="btn btn-secondary" type="submit">Desvincular</button></form>')
        filas.append([
            esc(v.get("Nombre") or ""), esc(v.get("Email") or ""),
            esc(v.get("ChatId") or ""), esc(v.get("NombreTelegram") or ""),
            esc(v.get("TelefonoMAC") or ""),
            ("\U0001f7e2" if v.get("Activo") else "\U0001f534"),
            esc(_fval(v.get("FechaVinculado"))), btn])
    out.append(_tabla(
        ["Nombre HUB", "Email", "Chat ID", "Telegram", "Tel. MAC", "Activo",
         "Vinculado", ""], filas,
        "Aún no hay usuarios vinculados: se vinculan solos al compartir el "
        "teléfono con el bot, o agréguelos abajo a mano."))

    vinc_ids = {int(v.get("IdUsuario") or 0) for v in vinc}
    sin = [u for u in db.get_active_users() if int(u["Id"]) not in vinc_ids]
    if sin:
        opts = "".join(
            f'<option value="{int(u["Id"])}">{esc(u.get("Nombre") or "")} '
            f'({esc(u.get("Email") or "")})</option>' for u in sin)
        campo_usuario = f'<select class="input" name="IdUsuario">{opts}</select>'
        btn_on = ""
    else:
        campo_usuario = ('<div class="ro">Todos los usuarios activos ya están '
                         'vinculados.</div>')
        btn_on = " disabled"
    out.append(f"""
  <div class="panel"><h2>\u2795 Vincular usuario manualmente</h2>
    <form method="post" action="/notificaciones/telegram/vincular">{_csrf_field()}
      <div class="cfg-grid">
        <div class="cfg-field"><label>Usuario sin vincular</label>{campo_usuario}</div>
        <div class="cfg-field"><label>Chat ID de Telegram</label>
          <input type="number" name="ChatId" min="1" step="1" required
                 placeholder="Ej: 512345678" class="input"></div>
        <div class="cfg-field"><label>Nombre en Telegram (opcional)</label>
          <input type="text" name="NombreTelegram" maxlength="60"
                 placeholder="@usuario o nombre visible" class="input"></div>
      </div>
      <div class="cfg-save"><button class="btn btn-success" type="submit"{btn_on}>\u2795 Vincular</button></div>
    </form>
  </div>""")
    return "".join(out)


# ── sub-pestaña: Historial ───────────────────────────────────────────────────
def _tg_historial():
    out = [_encabezado(
        "\U0001f4cb Historial de Envíos",
        "Últimos 100 mensajes de la cola de alertas (<code>HUB_TelegramQueue</code>).")]
    hist = db.get_telegram_historial(limite=100)
    filas = []
    for h in hist:
        estado = str(h.get("Estado") or "")
        cls = {"ENVIADO": "ok", "FALLADO": "err"}.get(estado, "wait")
        filas.append([
            esc(h.get("Id")), esc(h.get("IdEvento")), esc(h.get("ChatId")),
            esc(h.get("Texto") or ""),
            f'<span class="pill {cls}">{esc(estado)}</span>',
            esc(h.get("Intentos")), esc(_fval(h.get("Creado")))])
    out.append(_tabla(["#", "Evento", "Chat ID", "Texto", "Estado", "Intentos",
                       "Creado"], filas,
                      "Todavía no hay envíos registrados."))
    if hist:
        out.append('<div class="panel"><form method="post" '
                   'action="/notificaciones/telegram/limpiar">'
                   f'{_csrf_field()}'
                   '<button class="btn btn-secondary" type="submit">'
                   '\U0001f5d1\ufe0f Limpiar historial (&gt;30 días)</button>'
                   '</form></div>')
    return "".join(out)


def _bloque_telegram(user, vista="conexion"):
    """Bloque Telegram completo: barra + sub-pestaña activa (como el HUB)."""
    if not _allowed(user, "telegram"):
        return _card("🔔 Bot de Telegram", "AccesoTelegram", False, "", "")
    vistas = {"conexion": _tg_conexion, "eventos": _tg_eventos,
              "destinatarios": _tg_destinatarios,
              "vinculados": _tg_vinculados, "historial": _tg_historial}
    if vista not in vistas:
        vista = "conexion"
    return _tnav(vista) + vistas[vista]()


def _bloque_push(user):
    cfg = db.get_push_config()
    subs = db.get_push_subscriptions()
    cuerpo = ('<div class="cfg-grid">'
              + _text("VapidPublicKey", cfg.get("public", ""), "Clave pública VAPID",
                      "Se entrega al navegador para suscribirse (no es secreta).")
              + _secret("VapidPrivateKey", bool(cfg.get("private")),
                        "Clave privada VAPID",
                        "Firma los mensajes push; jamás sale del servidor.")
              + _text("VapidEmail", cfg.get("email", ""), "Correo de contacto",
                      "Se usa como claim mailto: de VAPID.")
              + f'<div class="cfg-field"><label>Suscriptores</label><div class="ro">'
                f'{len(subs)} navegador(es) suscrito(s) · '
                f'actualizado {esc(str(cfg.get("updated") or "—"))}</div></div>'
              + "</div>" + _save_bar())
    return _card("🔔 Push Web (VAPID)", "AccesoConfiguracion", True, cuerpo,
                 "/notificaciones/push",
                 prueba=_btn_prueba("/notificaciones/push/probar",
                                    "🧪 Enviar prueba"))


def _bloque_correo(user, correo_prueba):
    if not _allowed(user, "correo"):
        return _card("📧 Correo SMTP", "AccesoConfigurarCorreo", False, "", "")
    cfg = db.get_email_config()
    cuerpo = ('<div class="cfg-grid">'
              + _text("smtp_server", cfg.get("smtp_server", ""), "Servidor SMTP")
              + _number("port", cfg.get("port", 465), "Puerto", 1, 65535)
              + _text("username", cfg.get("username", ""), "Usuario")
              + _secret("password", bool(cfg.get("password")), "Contraseña")
              + _bool("use_ssl", cfg.get("use_ssl"), "SSL",
                      "El HUB usa SSL en el puerto 465.")
              + _bool("use_tls", cfg.get("use_tls"), "TLS")
              + _bool("require_auth", cfg.get("require_auth"), "Requiere autenticación")
              + "</div>" + _save_bar())

    prueba = (f'<form class="inline" method="post" action="/notificaciones/correo/probar">'
              f'{_csrf_field()}'
              f'<input type="email" name="destino" value="{esc(correo_prueba)}" '
              f'placeholder="para@correo.com" style="width:190px" required class="input">'
              f'<button class="btn btn-secondary" type="submit">🧪 Enviar prueba</button></form>')
    return _card("📧 Correo SMTP", "AccesoConfigurarCorreo", True, cuerpo,
                 "/notificaciones/correo", prueba=prueba)


def _bloque_ia(user):
    if not _allowed(user, "ia"):
        return _card("🤖 IA (Gemini)", "AccesoConfigAI", False, "", "")
    cfg = db.get_ai_config()
    cuerpo = ('<div class="cfg-grid">'
              + _text("provider", cfg.get("provider", "google_gemini"), "Proveedor",
                      "El HUB usa google_gemini.")
              + _secret("api_key", bool(cfg.get("api_key")), "API key",
                        "Misma clave que usan Cotizaciones, Jarvis, Órdenes, etc.")
              + _text("model", cfg.get("model", ""), "Modelo",
                      "Debe existir en la cuenta (p.ej. gemini-3.5-flash-lite); "
                      "si el modelo fue retirado, la IA del HUB fallará.")
              + "</div>" + _save_bar())
    return _card("🤖 IA (Gemini)", "AccesoConfigAI", True, cuerpo,
                 "/notificaciones/ia",
                 prueba=_btn_prueba("/notificaciones/ia/probar"))


# ─── página ──────────────────────────────────────────────────────────────────
def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido",
           ip="", correo_prueba="",
           vista_tg="conexion"):
    set_csrf(csrf)
    m = db.telegram_metrics()
    subs = db.get_push_subscriptions()

    kpis = f"""
  <div class="cards">
    <div class="card blue"><div class="n">{esc(m.get('vinculados', 0))}</div>
      <div class="l">Usuarios con Telegram</div></div>
    <div class="card green"><div class="n">{esc(m.get('eventos', 0))}</div>
      <div class="l">Eventos de alerta</div></div>
    <div class="card orange"><div class="n">{len(subs)}</div>
      <div class="l">Suscriptores push</div></div>
    <div class="card gray"><div class="n">{esc(m.get('pendientes', 0))}</div>
      <div class="l">Alertas en cola</div></div>
  </div>"""

    cuerpo = (kpis
              + _bloque_telegram(user, vista_tg)
              + _bloque_push(user)
              + _bloque_correo(user, correo_prueba)
              + _bloque_ia(user))
    return page("notificaciones", cuerpo, user=user, flash_ok=flash_ok,
                lugar=lugar, ip=ip,
                flash_err=flash_err,
                subtitle="Telegram (Conexión · Eventos · Destinatarios · "
                         "Vinculados · Historial) · Push · Correo · IA")
