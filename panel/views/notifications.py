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
            f'autocomplete="new-password" placeholder="{esc(ph)}">{h}</div>')


def _number(name, value, label="", mn=None, mx=None, help_=""):
    attrs = ""
    if mn is not None:
        attrs += f' min="{int(mn)}"'
    if mx is not None:
        attrs += f' max="{int(mx)}"'
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="cfg-field"><label>{esc(label)}</label>'
            f'<input type="number" name="{esc(name)}" value="{esc(value)}"'
            f'{attrs}>{h}</div>')


def _bool(name, value, label="", help_=""):
    val = "1" if value in (1, "1", True, "true") else "0"
    opts = "".join(f'<option value="{v}"{" selected" if val == v else ""}>{l}</option>'
                   for v, l in (("1", "Sí"), ("0", "No")))
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    return (f'<div class="cfg-field"><label>{esc(label)}</label>'
            f'<select name="{esc(name)}">{opts}</select>{h}</div>')


def _textarea(name, value, label="", help_="", rows=3):
    h = f'<div class="help">{esc(help_)}</div>' if help_ else ""
    lab = f'<label>{esc(label)}</label>' if label else ""
    return (f'<div class="cfg-field" style="grid-column:1/-1">{lab}'
            f'<textarea name="{esc(name)}" rows="{rows}">{esc(value)}</textarea>{h}</div>')


def _multi(name, options, selected, label=""):
    """select multiple: options = [(valor, texto)], selected = set/iterable de ids."""
    sel = set(selected or ())
    opts = "".join(
        f'<option value="{esc(v)}"{" selected" if str(v) in sel else ""}>{esc(t)}</option>'
        for v, t in options)
    return (f'<div class="cfg-field" style="grid-column:1/-1"><label>{esc(label)}</label>'
            f'<select name="{esc(name)}" multiple size="{min(8, max(3, len(options)))}">'
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
            f'<button class="btn log" type="submit">{esc(etiqueta)}</button></form>')


def _save_bar():
    return ('<div class="cfg-save">'
            '<button class="btn on" type="submit">💾 Guardar</button></div>')


# ─── bloques ─────────────────────────────────────────────────────────────────
def _bloque_telegram(user):
    if not _allowed(user, "telegram"):
        return _card("🔔 Bot de Telegram", "AccesoTelegram", False, "", "")

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
    principal = _card("🔔 Bot de Telegram", "AccesoTelegram", True, cuerpo,
                      "/notificaciones/telegram",
                      prueba=_btn_prueba("/notificaciones/telegram/probar"))

    # ── Eventos y plantillas ────────────────────────────────────────────────
    eventos = db.get_telegram_eventos()
    tarjetas_eventos = []
    if not eventos:
        tarjetas_eventos.append('<div class="panel"><div class="empty">'
                                'No hay eventos configurados (migración 0018).</div></div>')
    for ev in eventos:
        eid = ev["IdEvento"]
        estado = "🟢" if ev.get("Activo") else "🔴"
        forms = (
            '<div class="cfg-grid">'
            + _textarea("PlantillaMensaje", ev.get("PlantillaMensaje") or "",
                        label="Plantilla del mensaje",
                        help_="Placeholders: {Folio} {Fecha} {Nombre} {Cantidad} "
                              "{Automovil} {Cliente} {Descripcion} {Tecnico} "
                              "{Kilometros} {Usuario}")
            + _bool("AdjuntarArchivo", ev.get("AdjuntarArchivo"), "Adjuntar archivo")
            + _bool("Activo", ev.get("Activo"), "Activo")
            + "</div>" + _save_bar())
        tarjetas_eventos.append(f"""
  <div class="panel">
    <h2>{estado} Evento <code>{esc(eid)}</code> · {esc(ev.get("Nombre"))}</h2>
    <form method="post" action="/notificaciones/telegram/evento">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </div>""")

    # ── Destinatarios por evento ────────────────────────────────────────────
    usuarios = db.get_active_users()
    opciones = [(u["Id"], f'{u.get("Nombre") or ""} ({u.get("Email") or ""})')
                for u in usuarios]
    tarjetas_dest = []
    for ev in eventos:
        eid = ev["IdEvento"]
        actuales = db.get_telegram_destinatarios(eid)
        forms = ('<div class="cfg-grid">'
                 + _multi("ids", opciones, actuales, label="Usuarios que reciben esta alerta")
                 + "</div>" + _save_bar())
        tarjetas_dest.append(f"""
  <div class="panel">
    <h2>👥 Destinatarios · <code>{esc(eid)}</code> ({len(actuales)} seleccionados)</h2>
    <form method="post" action="/notificaciones/telegram/destinatarios">
      {_csrf_field()}<input type="hidden" name="IdEvento" value="{esc(eid)}">
      {forms}
    </form>
  </div>""")

    nota = ('<div class="empty" style="text-align:left;padding-top:0">'
            'Mismas tablas que el HUB (<code>HUB_TelegramEventos</code>, '
            '<code>HUB_TelegramDestinatarios</code>): los cambios aplican en el '
            'próximo disparo de la alerta, sin reiniciar nada.</div>')
    return nota + principal + "".join(tarjetas_eventos) + "".join(tarjetas_dest)


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
              f'placeholder="para@correo.com" style="width:190px" required>'
              f'<button class="btn log" type="submit">🧪 Enviar prueba</button></form>')
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
def render(user, flash_ok="", flash_err="", csrf="", correo_prueba=""):
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
              + _bloque_telegram(user)
              + _bloque_push(user)
              + _bloque_correo(user, correo_prueba)
              + _bloque_ia(user))
    return page("notificaciones", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err,
                subtitle="Telegram · Push · Correo · IA — mismas tablas que el HUB")
