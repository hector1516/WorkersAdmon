"""
panel/views/wa_conversaciones.py — Bandeja de conversaciones de WhatsApp
=======================================================================
La sub-pestaña "🧾 Conversaciones" del módulo WhatsApp.

Dos pantallas, sin JavaScript:

  * LISTA (sin `chat`): cada chat con su contacto, el último mensaje recortado,
    la hora, el estado y quién lo tiene asignado. Máximo 100, el más reciente
    primero.
  * DETALLE (`?wa=conversaciones&chat=<id>`): el historial completo del chat en
    orden, con la dirección de cada mensaje (entrante del contacto / saliente
    del equipo) y la caja para responder.

El estado de la conversación es lo que decide si el bot contesta o no:

  BOT      → el motor puede responder solo (default al nacer el chat).
  HUMANO   → una persona tomó el chat; el bot NO contesta.
  CERRADA  → el chat quedó terminado; el bot tampoco contesta.

Aquí solo se escribe la columna `Estado`; quien pregunta si puede contestar es el
motor, al recibir el mensaje. Duplicar esa regla en dos lugares es cómo terminan
contestando los dos a la vez.

Escribir respuestas manda por `openwa_client.enviar_texto` y deja constancia en
`HUB_WhatsappEntrante` con `Direccion='OUT'` y `Estado='ENVIADO'`. Si OpenWA
falla, NO se inserta la fila: el historial no debe decir que salió algo que no
salió.

Seguridad: todo el texto se escapa con `esc()`, cada POST lleva CSRF y las
consultas van siempre filtradas por el `ChatId` de la conversación abierta.
"""
import urllib.parse

from .. import db
from ..templates import esc

_CSRF = {"token": ""}

MAX_LISTA = 100
RECORTE = 90

ESTADOS = {
    "BOT": ("Bot", "badge-success"),
    "HUMANO": ("Humano", "badge-warning"),
    "CERRADA": ("Cerrada", "badge-info"),
}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return f'<input type="hidden" name="csrf" value="{esc(_CSRF["token"])}">'


def _fecha(valor):
    """dd/mm/aaaa hh:mm (o cadena vacía). El formato que usa todo el panel."""
    if valor is None:
        return ""
    if hasattr(valor, "strftime"):
        return valor.strftime("%d/%m/%Y %H:%M")
    return esc(valor)


def telefono_de(chat_id):
    """Los dígitos del chat (`521812...@c.us` → `521812...`)."""
    if not chat_id:
        return ""
    return "".join(c for c in str(chat_id).split("@")[0] if c.isdigit())


def _contacto(fila):
    """Nombre del contacto; si el webhook no lo trajo, su teléfono."""
    nombre = (fila.get("Nombre") or "").strip()
    if nombre:
        return nombre
    tel = (fila.get("Telefono") or "").strip() or telefono_de(fila.get("ChatId"))
    return tel or "(sin nombre)"


def _recorte(texto, largo=RECORTE):
    txt = (texto or "").strip().replace("\r\n", "\n")
    if not txt:
        return '<span class="muted">(sin texto)</span>'
    if len(txt) > largo:
        txt = txt[:largo - 1].rstrip() + "…"
    return esc(txt)


def _badge(estado):
    etiqueta, cls = ESTADOS.get(str(estado or "").upper(),
                               (str(estado or "—"), "badge-info"))
    return f'<span class="badge {cls}">{esc(etiqueta)}</span>'


def _url_chat(chat_id, accion=None):
    ruta = "/whatsapp"
    if accion:
        ruta += f"/conversaciones/{accion}"
    params = {"wa": "conversaciones"}
    if chat_id:
        params["chat"] = chat_id
    return ruta + "?" + urllib.parse.urlencode(params)


def _btn_accion(chat_id, accion, etiqueta, clase="btn-secondary"):
    return (f'<form class="inline" method="post" '
            f'action="{esc(_url_chat(chat_id, accion))}">'
            f'{_csrf_field()}<input type="hidden" name="chat" '
            f'value="{esc(chat_id)}">'
            f'<button class="btn btn-sm {clase}" type="submit">'
            f'{esc(etiqueta)}</button></form>')


# ── Lista ─────────────────────────────────────────────────────────────────────
def lista():
    filas = []
    for c in db.get_wa_conversaciones(limite=MAX_LISTA):
        chat_id = c.get("ChatId") or ""
        asignado = (c.get("Asignado") or "").strip() or "—"
        abrir = (f'<a class="btn btn-sm btn-secondary" '
                 f'href="{esc(_url_chat(chat_id))}">Abrir</a>')
        filas.append([
            f'<strong>{esc(_contacto(c))}</strong>'
            f'<div class="help">{esc(telefono_de(chat_id))}</div>',
            _recorte(c.get("Texto")),
            esc(_fecha(c.get("Ultima"))),
            _badge(c.get("Estado")),
            esc(asignado),
            abrir,
        ])
    cuerpo = [
        '<div class="panel"><h2>\U0001f9fe Conversaciones</h2>'
        '<p class="desc">Los últimos 100 chats de la sesión de WhatsApp, del '
        'más reciente al más viejo. <b>Bot</b> contesta solo; al tomarlo pasa a '
        '<b>Humano</b> y el bot deja de responder.</p>']
    if filas:
        th = "".join(f"<th>{h}</th>" for h in
                     ("Contacto", "Último mensaje", "Hora", "Estado",
                      "Asignado a", ""))
        tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in fila) + "</tr>"
                     for fila in filas)
        cuerpo.append('<div class="tscroll"><table>'
                      f"<thead><tr>{th}</tr></thead><tbody>{tr}</tbody>"
                      "</table></div>")
    else:
        cuerpo.append('<div class="empty">Todavía no hay conversaciones: '
                      'aparecen cuando OpenWA entrega el primer mensaje en '
                      '<code>/api/wa/entrante/&lt;token&gt;</code>.</div>')
    cuerpo.append("</div>")
    return "".join(cuerpo)


# ── Detalle ───────────────────────────────────────────────────────────────────
def _historial(chat_id):
    filas = []
    for m in db.get_wa_mensajes(chat_id):
        salida = str(m.get("Direccion") or "IN").upper() == "OUT"
        quien = (m.get("Nombre") or "").strip() or (
            "Equipo" if salida else _contacto(m))
        direccion = ('<span class="badge badge-info">Saliente</span>' if salida
                     else '<span class="badge badge-success">Entrante</span>')
        filas.append([
            direccion,
            esc(quien),
            f'<span style="white-space:pre-wrap">{esc(m.get("Texto") or "")}</span>',
            esc(_fecha(m.get("Fecha"))),
        ])
    if not filas:
        return ('<div class="empty">Este chat no tiene mensajes guardados.</div>')
    th = "".join(f"<th>{h}</th>" for h in
                 ("Dirección", "Autor", "Mensaje", "Hora"))
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in fila) + "</tr>"
                 for fila in filas)
    return ('<div class="tscroll"><table>'
            f"<thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>")


def detalle(chat_id):
    conv = db.get_wa_conversacion(chat_id)
    if not conv:
        return ('<div class="panel"><div class="empty">Ese chat no existe o se '
                'borró. <a href="/whatsapp?wa=conversaciones">Volver a la '
                'bandeja</a>.</div></div>')

    asignado = (conv.get("Asignado") or "").strip() or "nadie"
    acciones = " ".join((
        _btn_accion(chat_id, "tomar", "\U0001f64b Tomar", "btn-success"),
        _btn_accion(chat_id, "devolver", "\U0001f916 Devolver al bot"),
        _btn_accion(chat_id, "cerrar", "\u2705 Cerrar", "btn-warning"),
    ))
    encabezado = (
        '<div class="panel"><h2>\U0001f4ac '
        f'{esc(_contacto(conv))} {_badge(conv.get("Estado"))}'
        '<span class="panel-actions">'
        f'<a class="btn btn-sm btn-secondary" '
        f'href="/whatsapp?wa=conversaciones">← Bandeja</a></span></h2>'
        f'<p class="desc">Teléfono <b>{esc(telefono_de(chat_id))}</b> · '
        f'asignado a <b>{esc(asignado)}</b> · último mensaje '
        f'{esc(_fecha(conv.get("Ultima")))} · el bot no contesta si el estado '
        'no es <b>Bot</b>.</p>'
        f'<div class="actions-cell">{acciones}</div></div>')

    responder = (
        '<div class="panel"><h2>\u2709\ufe0f Responder</h2>'
        '<form method="post" '
        f'action="{esc(_url_chat(chat_id, "enviar"))}">{_csrf_field()}'
        f'<input type="hidden" name="chat" value="{esc(chat_id)}">'
        '<div class="field cfg-field">'
        '<textarea class="input" name="texto" rows="3" '
        'placeholder="Escribe la respuesta…"></textarea>'
        '<div class="help">Sale por OpenWA en el momento y queda en el '
        'historial como saliente.</div></div>'
        '<div class="cfg-save">'
        '<button class="btn btn-sm btn-success" type="submit">'
        '\U0001f4e4 Enviar</button></div></form></div>')

    historial = ('<div class="panel"><h2>\U0001f4dc Historial del chat</h2>'
                 + _historial(chat_id) + "</div>")
    return encabezado + historial + responder


def render(chat=""):
    """Lista o detalle según venga (o no) el `ChatId`."""
    chat = (chat or "").strip()
    if chat:
        return detalle(chat)
    return lista()
