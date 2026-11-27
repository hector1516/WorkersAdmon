"""
panel.views.notifications — Pestaña "Notificaciones" del panel (Fase C)
=========================================================================
Cuatro bloques, cada uno con su permiso del HUB, su formulario y su botón de
prueba:

  Telegram  (AccesoTelegram)      token + eventos/plantillas + destinatarios
  Push      (AccesoConfiguracion) claves VAPID + prueba a suscriptores
  Correo    (AccesoConfigurarCorreo) SMTP + prueba de envío
  IA        (AccesoConfigAI)      Gemini provider/key/model + prueba

Los secretos nunca se pintan en el HTML (campo en blanco = no cambiar) y no
se registran en la bitácora.

Diseño: cada bloque es una tarjeta de Ajustes (panel/DESIGN.md) — cabecera con
icono y estado, formulario en campos `.field` y un solo botón primario por
tarjeta. Las sub-pestañas de Telegram son un `.segmented`; los botones de
prueba son `.btn--quiet` y solo pintan: quien manda de verdad es el POST.
"""
from .. import db, probes, workers
from ..icons import icono
from ..templates import esc, page

import canales
import importlib
import time

_CSRF = {"token": ""}

# Permiso de cada bloque (None = basta con AccesoConfiguracion del panel)
PERMISOS = {
    "canales": (None, "AccesoConfiguracion"),
    "telegram": ("AccesoTelegram", "AccesoTelegram"),
    # WhatsApp ya es su propio módulo: el permiso es AccesoWhatsapp. Mientras la
    # columna no exista en HUB_Users, `auth.has_perm` lo resuelve con
    # `has_perm_whatsapp` (AccesoWhatsapp o AccesoTelegram), que es como se
    # entraba cuando estas pantallas vivían dentro de Notificaciones.
    "openwa": ("AccesoWhatsapp", "AccesoWhatsapp"),
    "push": (None, "AccesoConfiguracion"),
    "correo": ("AccesoConfigurarCorreo", "AccesoConfigurarCorreo"),
    "ia": ("AccesoConfigAI", "AccesoConfigAI"),
}

# Lo poco que apple.css no define: cabecera de tarjeta (icono + estado), pie con
# el botón primario y un par de medidas del .segmented. Todo con los tokens de
# DESIGN.md, nunca con hex sueltos. Se inyecta una vez por página (este módulo y
# el de WhatsApp comparten los bloques de abajo).
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


def _badge(texto, clase="badge--info"):
    return f'<span class="badge {clase}">{esc(texto)}</span>'


# ─── fragmentos de formulario ────────────────────────────────────────────────
def _text(name, value="", label="", help_="", tipo="text", attrs=""):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    lab = f'<label>{esc(label)}</label>' if label else ""
    # class="input" es obligatorio (sin ella sale la caja blanca del navegador)
    # y attrs va con un espacio delante: puede venir vacío o con style=….
    extra = f" {attrs.strip()}" if attrs.strip() else ""
    return (f'<div class="field">{lab}<input type="{tipo}" '
            f'name="{esc(name)}" value="{esc(value)}" class="input"{extra}>'
            f'{h}</div>')


def _secret(name, has_value, label="", help_=""):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    ph = "(guardado)" if has_value else "(sin definir)"
    return (f'<div class="field"><label>{esc(label)}</label>'
            f'<input type="password" name="{esc(name)}" value="" '
            f'autocomplete="new-password" placeholder="{esc(ph)}" class="input">{h}</div>')


def _number(name, value, label="", mn=None, mx=None, help_=""):
    attrs = ""
    if mn is not None:
        attrs += f' min="{int(mn)}"'
    if mx is not None:
        attrs += f' max="{int(mx)}"'
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="field"><label>{esc(label)}</label>'
            f'<input type="number" name="{esc(name)}" value="{esc(value)}"'
            f'{attrs} class="input">{h}</div>')


def _bool(name, value, label="", help_=""):
    val = "1" if value in (1, "1", True, "true") else "0"
    opts = "".join(f'<option value="{v}"{" selected" if val == v else ""}>{l}</option>'
                   for v, l in (("1", "Sí"), ("0", "No")))
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="field"><label>{esc(label)}</label>'
            f'<select class="input" name="{esc(name)}">{opts}</select>{h}</div>')


def _textarea(name, value, label="", help_="", rows=3):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    lab = f'<label>{esc(label)}</label>' if label else ""
    return (f'<div class="field">{lab}'
            f'<textarea class="input" name="{esc(name)}" rows="{rows}">{esc(value)}</textarea>{h}</div>')


def _multi(name, options, selected, label=""):
    """select multiple: options = [(valor, texto)], selected = set/iterable de ids."""
    sel = set(selected or ())
    opts = "".join(
        f'<option value="{esc(v)}"{" selected" if str(v) in sel else ""}>{esc(t)}</option>'
        for v, t in options)
    return (f'<div class="field"><label>{esc(label)}</label>'
            f'<select class="input" name="{esc(name)}" multiple size="{min(8, max(3, len(options)))}">'
            f'{opts}</select></div>')


def _card(titulo, perm_label, permitido, cuerpo, accion, prueba="",
          icono_nombre="bell", badge=""):
    """Tarjeta de Ajustes: cabecera con icono, estado y un formulario propio.

    El formulario nunca se anida: la prueba va en el pie como formulario
    hermano, y el guardado es el único botón primario de la tarjeta.
    """
    if not permitido:
        return f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono(icono_nombre, 22)}</span>
      <div class="ncard__titles"><h2>{esc(titulo)}</h2>
        <p>Requiere el permiso <code>{esc(perm_label)}</code> en el HUB.</p></div>
      {_badge("Sin permiso", "badge--warn")}
    </div>
    <div class="empty">{icono("alert")}<p>Pídeselo al administrador para
      configurar este canal.</p></div>
  </section>"""
    head = (f'<div class="ncard__head">'
            f'<span class="ncard__icon">{icono(icono_nombre, 22)}</span>'
            f'<div class="ncard__titles"><h2>{esc(titulo)}</h2></div>'
            f'{badge}</div>')
    pie = (f'<div class="ncard__foot">{prueba}</div>') if prueba else ""
    return f"""
  <section class="card ncard">
    {head}
    <form method="post" action="{esc(accion)}">{_csrf_field()}{cuerpo}</form>
    {pie}
  </section>"""


def _btn_prueba(accion, etiqueta="Probar"):
    """Botón de prueba: solo pinta. El POST es quien manda de verdad."""
    return (f'<form class="inline" method="post" action="{esc(accion)}">'
            f'{_csrf_field()}'
            f'<button class="btn btn--quiet" type="submit" '
            f'aria-label="{esc(etiqueta)}">{icono("play")}'
            f'<span>{esc(etiqueta)}</span></button></form>')


def _btn_prueba_con_campo(accion, campo, etiqueta="Probar"):
    """Formulario de prueba con su casilla: mandar un WhatsApp de verdad
    necesita saber a quien."""
    return (f'<form class="inline" method="post" action="{esc(accion)}">'
            f'{_csrf_field()}'
            f'<input type="text" name="{esc(campo)}" placeholder="5218123211516" '
            f'class="input" aria-label="Número al que se manda la prueba">'
            f'<button class="btn btn--quiet" type="submit" '
            f'aria-label="{esc(etiqueta)}">{icono("play")}'
            f'<span>{esc(etiqueta)}</span></button></form>')


def _save_bar(etiqueta="Guardar"):
    return (f'<div class="ncard__foot">'
            f'<button class="btn btn--primary" type="submit">'
            f'{icono("check")}<span>{esc(etiqueta)}</span></button></div>')


def _seg(items, vista, href):
    """Control segmentado del módulo (antes .tnav con botones sueltos).

    `href` es una plantilla con un `%s`: la clave de la sub-pestaña va ahí.
    """
    trozos = []
    for clave, etiqueta, nombre_icono in items:
        activa = clave == vista
        cls = ' class="is-active"' if activa else ""
        aria = ' aria-current="page"' if activa else ""
        trozos.append(f'<a{cls}{aria} href="{esc(href % clave)}">'
                      f'{icono(nombre_icono, 18)}<span>{esc(etiqueta)}</span></a>')
    return ('<nav class="segmented" aria-label="Secciones">'
            + "".join(trozos) + "</nav>")


def _encabezado(titulo, texto, icono_nombre="bell"):
    """Título + descripción de la sub-pestaña (como los markdown del HUB)."""
    return (f'<section class="card ncard">'
            f'<div class="ncard__head">'
            f'<span class="ncard__icon">{icono(icono_nombre, 22)}</span>'
            f'<div class="ncard__titles"><h2>{esc(titulo)}</h2>'
            f'<p>{texto}</p></div></div></section>')


def _tabla(headers, filas, vacio):
    """Tabla scrolleable; cada fila es una lista de celdas ya escapadas."""
    if not filas:
        return f'<div class="card"><div class="empty">{vacio}</div></div>'
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in fila) + "</tr>"
                 for fila in filas)
    return ('<div class="card card--flush"><div class="table-wrap">'
            '<table class="table">'
            f"<thead><tr>{th}</tr></thead><tbody>{tr}</tbody>"
            "</table></div></div>")


def _fval(v):
    """Fecha a dd/mm/aaaa hh:mm (o cadena vacía)."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y %H:%M")
    return str(v)


# ─── sub-pestaña: Conexión ───────────────────────────────────────────────────
def _tg_conexion():
    token = db.get_config_values(["telegram_bot_token"]).get("telegram_bot_token", "")
    m = db.telegram_metrics()
    cuerpo = (
        '<div class="ncard__grid">'
        + _secret("telegram_bot_token", bool(token), "Token del bot (@BotFather)",
                  "Se guarda en HUB_Config; lo usan todas las alertas del HUB.")
        + f'<div class="field"><label>Resumen</label><div class="ro">'
          f'vinculados {esc(m.get("vinculados", 0))} · eventos {esc(m.get("eventos", 0))} · '
          f'destinatarios {esc(m.get("destinatarios", 0))} · '
          f'cola {esc(m.get("pendientes", 0))} pend / {esc(m.get("fallados", 0))} fall / '
          f'{esc(m.get("enviados", 0))} enviados</div></div>'
        + "</div>" + _save_bar())
    principal = _card("Conexión · Bot de Telegram", "AccesoTelegram", True,
                      cuerpo, "/notificaciones/telegram",
                      prueba=_btn_prueba("/notificaciones/telegram/probar",
                                         "Probar bot"),
                      icono_nombre="chat",
                      badge=_badge("Configurado" if token else "Falta el token",
                                   "badge--ok" if token else "badge--warn"))
    instr = f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("alert", 22)}</span>
      <div class="ncard__titles"><h2>Cómo se vinculan los usuarios</h2></div>
    </div>
    <ol class="desc">
      <li>Crear el bot con <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> y pegar el token arriba.</li>
      <li>Cada usuario abre Telegram, busca <b>@el bot</b> y presiona <b>Start</b>.</li>
      <li>Presiona <b>Compartir teléfono</b> (si está habilitado): el sistema
          vincula su chat_id con su cuenta del HUB automáticamente.</li>
      <li>Si no puede compartir el teléfono, un admin lo vincula a mano en la
          sub-pestaña <b>Vinculados</b>.</li>
    </ol>
    <p class="help">Mismas tablas que el HUB (<code>HUB_TelegramEventos</code>,
      <code>HUB_TelegramDestinatarios</code>): los cambios aplican en el próximo
      disparo de la alerta, sin reiniciar nada.</p>
  </section>"""
    return principal + instr


# ─── sub-pestaña: Eventos ────────────────────────────────────────────────────
def _tg_eventos():
    out = [_encabezado(
        "Eventos de Alerta",
        "Configure qué alertas se envían y el texto de cada una. Placeholders: "
        "<code>{Folio}</code> <code>{Fecha}</code> <code>{Nombre}</code> "
        "<code>{Cantidad}</code> <code>{Automovil}</code> <code>{Cliente}</code> "
        "<code>{Descripcion}</code> <code>{Tecnico}</code> "
        "<code>{Kilometros}</code> <code>{Usuario}</code>.",
        "bell")]
    eventos = db.get_telegram_eventos()
    if not eventos:
        out.append('<div class="card"><div class="empty">'
                   'No hay eventos configurados (migración 0018 del HUB).</div></div>')
        return "".join(out)
    for ev in eventos:
        eid = ev["IdEvento"]
        activo = bool(ev.get("Activo"))
        estado = _badge("Activo" if activo else "Inactivo",
                        "badge--ok" if activo else "badge--bad")
        forms = (
            '<div class="ncard__grid">'
            + _textarea("PlantillaMensaje", ev.get("PlantillaMensaje") or "",
                        label="Plantilla del mensaje",
                        help_="Texto que llega al chat. Use los placeholders de arriba.")
            + _bool("AdjuntarArchivo", ev.get("AdjuntarArchivo"), "Adjuntar archivo")
            + _bool("Activo", ev.get("Activo"), "Activo")
            + "</div>" + _save_bar())
        out.append(f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("bell", 22)}</span>
      <div class="ncard__titles"><h2>{esc(ev.get('Nombre'))}</h2>
        <p><code>{esc(eid)}</code></p></div>
      {estado}
    </div>
    <form method="post" action="/notificaciones/telegram/evento">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </section>""")
    return "".join(out)


# ─── sub-pestaña: Destinatarios ──────────────────────────────────────────────
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
        "Destinatarios por Evento",
        "El <b>resumen</b> de abajo muestra, de un vistazo, qué usuarios recibe "
        "cada alerta. Para cambiar la selección, marque/desmarque en la tarjeta "
        "del evento y guarde.",
        "users")]
    eventos = db.get_telegram_eventos()
    if not eventos:
        out.append('<div class="card"><div class="empty">'
                   'No hay eventos configurados (migración 0018 del HUB).</div></div>')
        return "".join(out)
    usuarios = db.get_active_users()
    mapa = {int(u["Id"]): u for u in usuarios}

    # ── resumen: quién recibe cada evento, en una sola tabla ────────────────
    filas = []
    for ev in eventos:
        eid = ev["IdEvento"]
        ids = db.get_telegram_destinatarios(eid)
        activo = bool(ev.get("Activo"))
        filas.append([_badge("Activo" if activo else "Inactivo",
                             "badge--ok" if activo else "badge--bad")
                      + "",
                      esc(ev.get("Nombre") or ""),
                      f'<code>{esc(eid)}</code>', _chips(ids, mapa),
                      f'<b>{len(ids)}</b>'])
    out.append(_tabla(["", "Evento", "Clave", "Usuarios que la reciben", "N.º"],
                      filas, ""))

    # ── edición por evento: checklist en lugar del select múltiple ──────────
    if not usuarios:
        out.append('<div class="card"><div class="empty">'
                   'No hay usuarios activos en el HUB.</div></div>')
        return "".join(out)
    for ev in eventos:
        eid = ev["IdEvento"]
        activo = bool(ev.get("Activo"))
        estado = _badge("Activo" if activo else "Inactivo",
                        "badge--ok" if activo else "badge--bad")
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
            '<div class="field">'
            f'<label>Usuarios que reciben esta alerta</label>'
            f'<div class="chklist">{checks}</div>'
            f'<p class="help">Seleccionados ahora: <b>{n}</b>. Los cambios '
            f'aplican en el próximo disparo de la alerta, sin reiniciar nada.'
            f'</p></div>' + _save_bar())
        out.append(f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("users", 22)}</span>
      <div class="ncard__titles"><h2>{esc(ev.get('Nombre'))}</h2>
        <p><code>{esc(eid)}</code> · {n} seleccionados</p></div>
      {estado}
    </div>
    <div class="chiprow">{_chips(sorted(sel), mapa)}</div>
    <form method="post" action="/notificaciones/telegram/destinatarios">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </section>""")
    return "".join(out)


# ─── sub-pestaña: Vinculados ─────────────────────────────────────────────────
def _tg_vinculados():
    out = [_encabezado(
        "Usuarios Vinculados a Telegram",
        "Gestione la vinculación entre usuarios del HUB y sus cuentas de Telegram.",
        "check")]
    vinc = db.get_telegram_vinculados()
    filas = []
    for v in vinc:
        uid = int(v.get("IdUsuario") or 0)
        btn = ('<form class="inline" method="post" '
               'action="/notificaciones/telegram/desvincular">'
               f'{_csrf_field()}<input type="hidden" name="IdUsuario" value="{uid}">'
               '<button class="btn btn--quiet btn--sm" type="submit">'
               'Desvincular</button></form>')
        filas.append([
            esc(v.get("Nombre") or ""), esc(v.get("Email") or ""),
            esc(v.get("ChatId") or ""), esc(v.get("NombreTelegram") or ""),
            esc(v.get("TelefonoMAC") or ""),
            _badge("Sí" if v.get("Activo") else "No",
                   "badge--ok" if v.get("Activo") else "badge--bad"),
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
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("plus", 22)}</span>
      <div class="ncard__titles"><h2>Vincular usuario manualmente</h2>
        <p>Para quien no puede compartir su teléfono con el bot.</p></div>
    </div>
    <form method="post" action="/notificaciones/telegram/vincular">{_csrf_field()}
      <div class="ncard__grid">
        <div class="field"><label>Usuario sin vincular</label>{campo_usuario}</div>
        <div class="field"><label>Chat ID de Telegram</label>
          <input type="number" name="ChatId" min="1" step="1" required
                 placeholder="Ej: 512345678" class="input"></div>
        <div class="field"><label>Nombre en Telegram (opcional)</label>
          <input type="text" name="NombreTelegram" maxlength="60"
                 placeholder="@usuario o nombre visible" class="input"></div>
      </div>
      <div class="ncard__foot">
        <button class="btn btn--primary" type="submit"{btn_on}>
          {icono("plus")}<span>Vincular</span></button>
      </div>
    </form>
  </section>""")
    return "".join(out)


# ─── sub-pestaña: Historial ──────────────────────────────────────────────────
def _tg_historial():
    out = [_encabezado(
        "Historial de Envíos",
        "Últimos 100 mensajes de la cola de alertas "
        "(<code>HUB_TelegramQueue</code>).",
        "clock")]
    hist = db.get_telegram_historial(limite=100)
    filas = []
    for h in hist:
        estado = str(h.get("Estado") or "")
        clase = {"ENVIADO": "badge--ok", "FALLADO": "badge--bad"}.get(
            estado, "badge--warn")
        filas.append([
            esc(h.get("Id")), esc(h.get("IdEvento")), esc(h.get("ChatId")),
            esc(h.get("Texto") or ""),
            _badge(estado, clase),
            esc(h.get("Intentos")), esc(_fval(h.get("Creado")))])
    out.append(_tabla(["#", "Evento", "Chat ID", "Texto", "Estado", "Intentos",
                       "Creado"], filas,
                      "Todavía no hay envíos registrados."))
    if hist:
        out.append('<section class="card ncard">'
                   '<form method="post" action="/notificaciones/telegram/limpiar">'
                   f'{_csrf_field()}'
                   '<div class="ncard__foot ncard__foot--pegar">'
                   '<button class="btn btn--quiet" type="submit">'
                   f'{icono("trash")}<span>Limpiar historial (&gt;30 días)</span>'
                   '</button></div></form></section>')
    return "".join(out)


VISTAS_TG = [
    ("conexion", "Conexión", "chat"),
    ("eventos", "Eventos", "bell"),
    ("destinatarios", "Destinatarios", "users"),
    ("vinculados", "Vinculados", "check"),
    ("historial", "Historial", "clock"),
]


def _bloque_telegram(user, vista="conexion"):
    """Bloque Telegram completo: barra + sub-pestaña activa (como el HUB)."""
    if not _allowed(user, "telegram"):
        return _card("Bot de Telegram", "AccesoTelegram", False, "", "",
                     icono_nombre="chat")
    vistas = {"conexion": _tg_conexion, "eventos": _tg_eventos,
              "destinatarios": _tg_destinatarios,
              "vinculados": _tg_vinculados, "historial": _tg_historial}
    if vista not in vistas:
        vista = "conexion"
    return (_seg(VISTAS_TG, vista, "/notificaciones?tg=%s")
            + vistas[vista]())


# ─── OpenWA / WhatsApp ───────────────────────────────────────────────────────

def _wa_conexion(base="/notificaciones"):
    cfg = db.get_openwa_config()
    sesion = cfg.get("session_id") or ""
    cuerpo = (
        '<div class="ncard__grid">'
        + _text("openwa_base_url", cfg.get("base_url") or "", "Dirección de OpenWA",
                "Dentro de Docker se llama por hostname: <code>http://openwa:2785</code>. "
                "No se pone la IP: OpenWA rechaza callbacks a IPs internas.")
        + _text("openwa_session_id", sesion, "ID de sesión",
                "La sesión de WhatsApp ya iniciada (la <code>principal</code>).")
        + _secret("openwa_api_key", bool(cfg.get("api_key")),
                  "API key de OpenWA",
                  "Se guarda en HUB_Config. Para cambiarla pega la nueva; para "
                  "borrarla escribe BORRAR. Vacío = no se toca.")
        + "</div>" + _save_bar())
    principal = _card("Conexión · OpenWA (WhatsApp)", "AccesoTelegram", True,
                      cuerpo, f"{base}/openwa/guardar",
                      prueba=_btn_prueba_con_campo(
                          f"{base}/openwa/probar", "openwa_prueba_numero",
                          "Mandar prueba"),
                      icono_nombre="whatsapp",
                      badge=_badge(f"Sesión {sesion}" if sesion
                                   else "Sin sesión",
                                   "badge--ok" if sesion else "badge--warn"))
    return principal + _wa_instrucciones()


def _wa_instrucciones():
    return f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("alert", 22)}</span>
      <div class="ncard__titles"><h2>Cómo se enciende</h2></div>
    </div>
    <ol class="desc">
      <li>Pega la <b>API key</b> de OpenWA arriba y guarda.</li>
      <li>En <b>Avisos</b>, pega los <b>teléfonos</b> separados por
          comas en cada aviso que quieras recibir. Con 10 digitos se asume
          México (agrega el 52).</li>
      <li>Para un <b>grupo</b>, elígelo en <b>Grupos</b> y presiona
          «Usar en aviso»: su id (<code>&hellip;@g.us</code>) se agrega a los
          destinatarios de ese aviso.</li>
      <li>Presiona <b>Mandar prueba</b>: llega un WhatsApp de verdad. Si llega,
          ya funcionó.</li>
    </ol>
    <p class="help">La cola es independiente de la de
      Telegram: los avisos que no se pueden entregar se reintentan 3 veces y
      quedan como <b>FALLADO</b> en el Historial, con el motivo.</p>
  </section>"""


# Avisos que el boton sabe reproducir. El saldo tambien entra, pero lo que
# manda es el estado de ahora (no hay "ultimo evento": es una revision
# periodica), y la pantalla lo dice.
REPRODUCIBLES = {"KILOMETROS", "OXXOGAS_TICKET", "REPORTE_SERVICIO",
                 "GOVALE_SALDO", "GOVALE_SALDO_BAJO"}

# Quien dispara cada aviso. Es texto fijo por IdEvento: el productor vive en otra
# app (Field o el cron del saldo) y no hay columna que lo diga. Si se agrega un
# evento nuevo y no se declara aqui, la pantalla lo dice en vez de inventarlo.
# OJO: los avisos del vigilante de vales salen hoy solo por Telegram; NO van por
# WhatsApp y por eso no aparecen en este mapa.
PRODUCTORES = {
    "KILOMETROS": "Field, al registrar los kilómetros del vehículo",
    "OXXOGAS_TICKET": "Field, al capturar un ticket de OXXO Gas",
    "REPORTE_SERVICIO": "Field, al firmarse un reporte de servicio",
    "GOVALE_SALDO": "cron diario de saldo de GoVale",
    "GOVALE_SALDO_BAJO": "cron diario de saldo de GoVale, cuando baja del umbral",
}

# Cache corto de los grupos: la vista de Avisos resuelve el nombre de los
# grupos y la sub-pestaña Grupos hace la misma llamada al navegar. OpenWA tarda
# unos segundos y no tiene caso pedirla dos veces seguidas.
_GRUPOS_CACHE = {"cuando": 0.0, "grupos": None}


def _grupos_de_la_sesion(max_edad=90):
    """`(ok, grupos|detalle)` de la sesion, con cache solo si salio bien."""
    if (_GRUPOS_CACHE["grupos"] is not None
            and time.time() - _GRUPOS_CACHE["cuando"] < max_edad):
        return True, _GRUPOS_CACHE["grupos"]
    try:
        cliente = importlib.import_module("openwa_client")
        ok, datos = cliente.listar_grupos()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if ok:
        _GRUPOS_CACHE["grupos"] = datos
        _GRUPOS_CACHE["cuando"] = time.time()
    return ok, datos


def _nombres_de_grupos():
    """`{id: nombre}` de los grupos, o `{}` si la API no respondio."""
    ok, datos = _grupos_de_la_sesion()
    if not ok:
        return {}
    return {g["id"]: g["nombre"] for g in datos if g.get("nombre")}


def _wa_contexto(eid, validos, ultimos, nombres):
    """
    Linea de contexto de un aviso: quien lo manda, a quien llega y como termino
    el ultimo envio. Es lo que pide ver de un vistazo quien administra: una
    alerta programada sin destinatarios se ve como alerta que no avisa nada.
    """
    tel = [c for c in validos if c.endswith("@c.us")]
    grupos = [c for c in validos if c.endswith("@g.us")]
    quien = esc(PRODUCTORES.get(eid, "origen no documentado"))
    partes = [f'<div><b>Quién lo manda:</b> {quien}</div>']

    if tel:
        destinos = esc(", ".join(tel))
    else:
        destinos = '<span class="muted">ningún número</span>'
    if grupos:
        con_nombre = []
        for gid in grupos:
            nombre = nombres.get(gid)
            con_nombre.append(f'{esc(nombre)} ({esc(gid)})' if nombre else esc(gid))
        destinos += (" · <b>Grupos:</b> " + ", ".join(con_nombre))
    partes.append(f'<div><b>Destinatarios:</b> {destinos}</div>')

    ult = ultimos.get(eid)
    if ult:
        clase = {"ENVIADO": "badge--ok", "FALLADO": "badge--bad"}.get(
            str(ult.get("Estado") or ""), "badge--warn")
        partes.append(
            f'<div><b>Último envío:</b> '
            f'{_badge(ult.get("Estado"), clase)} '
            f'{esc(_fval(ult.get("Creado")))}</div>')
    else:
        partes.append('<div><b>Último envío:</b> '
                      '<span class="muted">sin envíos</span></div>')
    return '<div class="help wa-contexto">' + "".join(partes) + "</div>"


def _wa_eventos(base="/notificaciones"):
    out = [_encabezado(
        "Avisos de WhatsApp",
        "A quién llega cada aviso y con qué texto. Los <b>teléfonos</b> van "
        "separados por comas: <code>5218123211516, 5512345678</code>. Los "
        "<b>grupos</b> se eligen en la sub-pestaña Grupos.",
        "bell")]
    eventos = db.get_openwa_eventos()
    if not eventos:
        out.append('<div class="card"><div class="empty">'
                   'No hay avisos configurados (falta la migración 0043).</div></div>')
        return "".join(out)

    ultimos = db.get_openwa_ultimos_envios()
    nombres = None
    for ev in eventos:
        eid = ev.get("IdEvento")
        validos, rechazados = db.get_openwa_destinatarios_resueltos(eid)
        activo = bool(ev.get("Activo"))
        estado = _badge("Activo" if activo else "Inactivo",
                        "badge--ok" if activo else "badge--bad")
        if not validos:
            destino = _badge("sin teléfonos: no se enviará nada", "badge--warn")
        else:
            tels = [c for c in validos if c.endswith("@c.us")]
            grupos = [c for c in validos if c.endswith("@g.us")]
            partes = []
            if tels:
                partes.append(f'{icono("send", 14)} {len(tels)} número(s): '
                              f'{esc(", ".join(tels))}')
            if grupos:
                if nombres is None:
                    nombres = _nombres_de_grupos()
                resueltos = ", ".join(
                    f'{esc(nombres.get(g))} ({esc(g)})' if nombres.get(g)
                    else esc(g) for g in grupos)
                partes.append(f'{icono("users", 14)} {resueltos}')
            destino = _badge(" · ".join(partes), "badge--ok")
        if rechazados:
            destino += (f'<div class="help texto-peligro">'
                        f'No se usaron: {esc(", ".join(rechazados))} '
                        f'(no son números)</div>')
        contexto = _wa_contexto(eid, validos, ultimos, nombres or {})

        # Boton de prueba: reenvia el ultimo caso real de este aviso. No es
        # inventing: el aviso no se guarda como evento, asi que se relee el
        # ultimo registro de la tabla del modulo (ver openwa_alerts.reenviar_ultimo).
        probar = ""
        if eid in REPRODUCIBLES:
            probar = (
                '<form class="inline" method="post" '
                f'action="{esc(base)}/openwa/reenviar">'
                + _csrf_field()
                + f'<input type="hidden" name="IdEvento" value="{esc(eid)}">'
                '<button class="btn btn--quiet" type="submit" '
                'title="Manda a los teléfonos de este aviso el último caso real">'
                f'{icono("refresh")}<span>Reenviar último</span></button></form>')

        desc = ev.get("Descripcion") or ""
        forms = (
            (f'<p class="help">{esc(desc)}</p>')
            if desc else "") + contexto + (
            '<div class="ncard__grid">'
            + _textarea("PlantillaMensaje", ev.get("PlantillaMensaje") or "",
                        label="Texto del mensaje",
                        help_="Placeholders: {Folio} {Fecha} {Nombre} {Cantidad} "
                              "{Auto} {Cliente} {Descripcion} {Saldo} {Estado} "
                              "{Kilometros} {UsuarioFirma}…")
            + _textarea("Telefonos", ev.get("Telefonos") or "",
                        label="Teléfonos destinatarios",
                        help_="Separados por comas. Ej: 5218123211516, "
                              "5512345678. Un grupo se pega como "
                              "<code>…@g.us</code> (se elige en Grupos).")
            + _bool("AdjuntarArchivo", ev.get("AdjuntarArchivo"), "Adjuntar archivo")
            + _bool("Activo", ev.get("Activo"), "Activo")
            + "</div>"
            f'<div class="ncard__foot">{destino}'
            '<button class="btn btn--primary" type="submit">'
            f'{icono("check")}<span>Guardar</span></button></div>')
        out.append(f"""
  <section class="card ncard">
    <div class="ncard__head">
      <span class="ncard__icon">{icono("whatsapp", 22)}</span>
      <div class="ncard__titles"><h2>{esc(ev.get("Nombre") or eid)}</h2>
        <p>Acceso por <code>{esc(eid)}</code></p></div>
      {estado}
    </div>
    <form method="post" action="{esc(base)}/openwa/evento">{_csrf_field()}
      <input type="hidden" name="IdEvento" value="{esc(eid)}">{forms}
    </form>
    <div class="ncard__foot">{probar}</div>
  </section>""")
    return "".join(out)


def _wa_grupos(base="/notificaciones"):
    """
    Grupos de WhatsApp de la sesion, con su id y cuantos participantes tienen.

    Sin JS: el id se puede seleccionar/copiar de un campo de solo lectura y el
    boton "Usar en aviso" lo agrega al `Telefonos` del evento elegido. Si la API
    no responde se dice, en vez de pintar una lista vacia que parezca "no hay
    grupos".
    """
    out = [_encabezado(
        "Grupos de WhatsApp",
        "Grupos a los que esta sesión de OpenWA puede mandar mensajes y el PDF "
        "del reporte. El id completo (<code>…@g.us</code>) se puede copiar; "
        "«Usar en aviso» lo suma a los destinatarios de ese aviso.",
        "users")]
    ok, datos = _grupos_de_la_sesion()
    if not ok:
        out.append(
            f'<div class="card"><div class="empty">{icono("alert")}'
            f'<p>No se pudieron leer los grupos: {esc(datos)}</p>'
            '<p>Revisa la conexión de OpenWA (API key y sesión) en la '
            'sub-pestaña Conexión. Mientras la API no responda, esta lista no '
            'se puede pintar.</p></div></div>')
        return "".join(out)
    if not datos:
        out.append('<div class="card"><div class="empty">La sesión no tiene '
                   'grupos de WhatsApp.</div></div>')
        return "".join(out)

    eventos = db.get_openwa_eventos()
    if eventos:
        opciones = "".join(
            f'<option value="{esc(e.get("IdEvento"))}">'
            f'{esc(e.get("Nombre") or e.get("IdEvento"))}</option>'
            for e in eventos)
        accion = (
            f'<select class="input" name="IdEvento" aria-label="Aviso">'
            f'{opciones}</select>'
            '<button class="btn btn--primary btn--sm" type="submit">'
            f'{icono("plus", 18)}<span>Usar en aviso</span></button>')
    else:
        accion = ('<span class="help">No hay avisos configurados: crea uno en '
                  'Avisos.</span>')

    filas = []
    for g in datos:
        copiar = (f'<input class="input" value="{esc(g["id"])}" readonly '
                  'aria-label="Id del grupo" onfocus="this.select()">')
        usar = ""
        if eventos:
            usar = ('<form method="post" '
                    f'action="{esc(base)}/openwa/grupo">'
                    + _csrf_field()
                    + f'<input type="hidden" name="ChatId" value="{esc(g["id"])}">'
                    + accion + '</form>')
        filas.append(
            '<li class="list__row">'
            f'<div class="list__main"><b>{esc(g.get("nombre") or "(sin nombre)")}</b>'
            f'<div class="list__meta">Participantes: '
            f'{esc(g.get("participantes") or 0)}</div>{copiar}</div>'
            f'<div class="list__meta wrap-normal">{usar}</div></li>')
    out.append('<section class="card ncard"><ul class="list">'
               + "".join(filas) + "</ul></section>")
    return "".join(out)


def _wa_historial():
    out = [_encabezado(
        "Historial de WhatsApp",
        "Últimos 100 mensajes de la cola (<code>HUB_WhatsappQueue</code>).",
        "clock")]
    filas = []
    for h in db.get_openwa_historial(limite=100):
        estado = str(h.get("Estado") or "")
        clase = {"ENVIADO": "badge--ok", "FALLADO": "badge--bad"}.get(
            estado, "badge--warn")
        error = h.get("Error") or ""
        filas.append([
            esc(h.get("Id")), esc(h.get("IdEvento")), esc(h.get("ChatId")),
            _badge(estado, clase),
            esc(h.get("Intentos")), esc(_fval(h.get("Creado"))),
            f'<span class="help">{esc(error)}</span>' if error else ""])
    out.append(_tabla(["#", "Aviso", "Chat ID", "Estado", "Intentos", "Creado", "Error"],
                      filas, "Todavía no hay avisos por WhatsApp."))
    return "".join(out)


def _fila_canal(canal, etiqueta, nota, valor):
    """Interruptor de un canal: checkbox + etiqueta + estado."""
    marca = _badge("Activo" if valor else "Apagado",
                   "badge--ok" if valor else "badge--warn")
    checked = " checked" if valor else ""
    ayuda = f'<div class="help">{esc(nota)}</div>' if nota else ""
    return (
        f'<div class="field"><label>'
        f'<input type="hidden" name="{esc(canal)}" value="0">'
        f'<input type="checkbox" name="{esc(canal)}" value="1"{checked}>'
        f' {esc(etiqueta)}</label>{marca}{ayuda}</div>')


def _bloque_canales(user):
    """Interruptores maestros: lo apagado no sale aunque el worker corra."""
    valores = canales.estado()
    activos = sum(1 for v in valores.values() if v)
    cuerpo = ('<div class="ncard__grid">'
              + _fila_canal(
                  "telegram", "Telegram",
                  "Se va a retirar; WhatsApp/OpenWA lo reemplaza.",
                  valores.get("telegram", True))
              + _fila_canal(
                  "whatsapp", "WhatsApp",
                  "Avisos, PDFs firmados y respuestas del bot.",
                  valores.get("whatsapp", True))
              + _fila_canal(
                  "push", "Notificaciones PWA",
                  "Push a los navegadores suscritos de Admon y Field.",
                  valores.get("push", True))
              + "</div>" + _save_bar())
    return _card("Canales", "AccesoConfiguracion", True, cuerpo,
                 "/notificaciones/canales", icono_nombre="bell",
                 badge=_badge(f"{activos} de {len(valores)} activos",
                              "badge--ok" if activos else "badge--warn"))


def _bloque_push(user):
    cfg = db.get_push_config()
    subs = db.get_push_subscriptions()
    cuerpo = ('<div class="ncard__grid">'
              + _text("VapidPublicKey", cfg.get("public", ""), "Clave pública VAPID",
                      "Se entrega al navegador para suscribirse (no es secreta).")
              + _secret("VapidPrivateKey", bool(cfg.get("private")),
                        "Clave privada VAPID",
                        "Firma los mensajes push; jamás sale del servidor.")
              + _text("VapidEmail", cfg.get("email", ""), "Correo de contacto",
                      "Se usa como claim mailto: de VAPID.")
              + f'<div class="field"><label>Suscriptores</label><div class="ro">'
                f'{len(subs)} navegador(es) suscrito(s) · '
                f'actualizado {esc(str(cfg.get("updated") or "—"))}</div></div>'
              + "</div>" + _save_bar())
    return _card("Push Web (VAPID)", "AccesoConfiguracion", True, cuerpo,
                 "/notificaciones/push",
                 prueba=_btn_prueba("/notificaciones/push/probar",
                                    "Enviar prueba"),
                 icono_nombre="bell",
                 badge=_badge(f"{len(subs)} suscriptor(es)", "badge--info"))


def _bloque_correo(user, correo_prueba):
    if not _allowed(user, "correo"):
        return _card("Correo SMTP", "AccesoConfigurarCorreo", False, "", "",
                     icono_nombre="mail")
    cfg = db.get_email_config()
    cuerpo = ('<div class="ncard__grid">'
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
              f'placeholder="para@correo.com" '
              f'aria-label="Correo al que se manda la prueba" required class="input">'
              f'<button class="btn btn--quiet" type="submit">'
              f'{icono("play")}<span>Enviar prueba</span></button></form>')
    return _card("Correo SMTP", "AccesoConfigurarCorreo", True, cuerpo,
                 "/notificaciones/correo", prueba=prueba, icono_nombre="mail",
                 badge=_badge("Configurado" if cfg.get("smtp_server")
                              else "Falta el servidor",
                              "badge--ok" if cfg.get("smtp_server")
                              else "badge--warn"))


def _bloque_ia(user):
    if not _allowed(user, "ia"):
        return _card("IA (Gemini)", "AccesoConfigAI", False, "", "",
                     icono_nombre="apps")
    cfg = db.get_ai_config()
    cuerpo = ('<div class="ncard__grid">'
              + _text("provider", cfg.get("provider", "google_gemini"), "Proveedor",
                      "El HUB usa google_gemini.")
              + _secret("api_key", bool(cfg.get("api_key")), "API key",
                        "Misma clave que usan Cotizaciones, Jarvis, Órdenes, etc.")
              + _text("model", cfg.get("model", ""), "Modelo",
                      "Debe existir en la cuenta (p.ej. gemini-3.5-flash-lite); "
                      "si el modelo fue retirado, la IA del HUB fallará.")
              + "</div>" + _save_bar())
    return _card("IA (Gemini)", "AccesoConfigAI", True, cuerpo,
                 "/notificaciones/ia",
                 prueba=_btn_prueba("/notificaciones/ia/probar", "Probar"),
                 icono_nombre="apps",
                 badge=_badge(cfg.get("model") or "sin modelo",
                              "badge--info"))


# ─── página ──────────────────────────────────────────────────────────────────
def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido",
           ip="", correo_prueba="",
           vista_tg="conexion"):
    set_csrf(csrf)
    m = db.telegram_metrics()
    subs = db.get_push_subscriptions()

    kpis = f"""
  <div class="grid">
    <div class="kpi"><span class="kpi__label">Usuarios con Telegram</span>
      <span class="kpi__value">{esc(m.get('vinculados', 0))}</span></div>
    <div class="kpi"><span class="kpi__label">Eventos de alerta</span>
      <span class="kpi__value">{esc(m.get('eventos', 0))}</span></div>
    <div class="kpi"><span class="kpi__label">Suscriptores push</span>
      <span class="kpi__value">{len(subs)}</span></div>
    <div class="kpi"><span class="kpi__label">Alertas en cola</span>
      <span class="kpi__value">{esc(m.get('pendientes', 0))}</span></div>
  </div>"""

    cuerpo = (kpis
              + _bloque_canales(user)
              + _bloque_telegram(user, vista_tg)
              + _bloque_push(user)
              + _bloque_correo(user, correo_prueba)
              + _bloque_ia(user))
    return page("notificaciones", cuerpo, user=user, flash_ok=flash_ok,
                lugar=lugar, ip=ip,
                flash_err=flash_err,
                subtitle="Telegram (Conexión · Eventos · Destinatarios · "
                         "Vinculados · Historial) · Push · Correo · IA")
