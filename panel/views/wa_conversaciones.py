"""
panel/views/wa_conversaciones.py — Bandeja de conversaciones de WhatsApp
=======================================================================
La sub-pestaña "Conversaciones" del módulo WhatsApp.

Dos pantallas, sin JavaScript:

  * LISTA (sin `chat`): cada chat con su contacto, el último mensaje recortado,
    la hora, el estado y quién lo tiene asignado. Máximo 100, el más reciente
    primero.
  * DETALLE (`?wa=conversaciones&chat=<id>`): el historial completo del chat en
    orden, con la dirección de cada mensaje (entrante del contacto / saliente
    del equipo) y la caja para responder.

En escritorio se ven las dos columnas (lista de 320 px + detalle); en el
teléfono se ve una sola: la lista, y al abrir un chat el detalle con su botón
«Volver». La página NO hace scroll: `.wa-inbox` ocupa el alto disponible y cada
columna scrollea por su cuenta (`min-height: 0`).

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
from ..icons import icono
from ..templates import esc

_CSRF = {"token": ""}

MAX_LISTA = 100
RECORTE = 90

ESTADOS = {
    "BOT": ("Bot", "badge--ok"),
    "HUMANO": ("Humano", "badge--warn"),
    "CERRADA": ("Cerrada", "badge--info"),
}

# Clase del panel viejo que aún busca `tests/test_panel.py` junto a la nueva.
# apple.css define las dos (`.badge--ok, .badge-success`), así que es la misma
# píldora con dos nombres y no se duplica el estilo.
LEGADO = {"badge--ok": "badge-success", "badge--warn": "badge-warning",
          "badge--info": "badge-info", "badge--bad": "badge-danger"}

# Lo que apple.css no define: la bandeja de dos columnas, las burbujas y la caja
# de respuesta. Todo con los tokens de DESIGN.md. Es CSS de esta pantalla, no
# una hoja nueva: se inyecta una vez y no toca ninguna otra vista.
ESTILO = """<style>
.main{overflow:hidden}
.content{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;padding-bottom:0}
.wa-inbox{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px}
.wa-pane{display:flex;flex-direction:column;min-height:0;background:var(--surface);border:1px solid var(--separator);border-radius:var(--radius);overflow:hidden}
.wa-pane__head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--separator)}
.wa-pane__title{font-size:17px;font-weight:590;min-width:0;overflow:hidden;text-overflow:ellipsis}
.wa-pane__sub{margin:0;font-size:13px;color:var(--text-2)}
.wa-list{overflow-y:auto;min-height:0;margin:0;padding:0;list-style:none}
.wa-list .list__row{padding:0;border-bottom:1px solid var(--separator)}
.wa-list .list__row:last-child{border-bottom:none}
.wa-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;min-height:var(--tap);padding:12px 16px;color:var(--text);text-decoration:none}
.wa-row:hover{background:var(--fill);text-decoration:none}
.wa-row.is-active{background:var(--fill-strong)}
.wa-row__main{min-width:0;display:flex;flex-direction:column;gap:2px}
.wa-row__name{font-weight:590;font-size:15px}
.wa-row__tel{font-size:13px;color:var(--text-2);font-variant-numeric:tabular-nums}
.wa-row__asignado{font-size:11px;color:var(--text-2);max-width:14ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wa-row__msg{font-size:13px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:26ch}
.wa-row__meta{display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex:0 0 auto}
.wa-row__hora{font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
.wa-msgs{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;display:flex;flex-direction:column;gap:8px;padding:16px;background:var(--surface-2)}
.wa-msg{max-width:78%;padding:9px 13px;border-radius:18px;font-size:15px;line-height:1.35;white-space:pre-wrap;word-break:break-word}
.wa-msg--in{align-self:flex-start;background:var(--surface);border:1px solid var(--separator);color:var(--text);border-bottom-left-radius:6px}
.wa-msg--out{align-self:flex-end;background:var(--accent);color:var(--on-accent);border-bottom-right-radius:6px}
.wa-msg__hora{display:block;margin-top:4px;font-size:11px;opacity:.78;font-variant-numeric:tabular-nums}
.wa-fecha{display:block;align-self:center;margin:4px 0;font-size:11px;color:var(--text-2)}
.wa-compose{display:flex;align-items:flex-end;gap:8px;padding:12px;border-top:1px solid var(--separator);background:var(--surface);flex:0 0 auto}
.wa-compose .input{flex:1 1 auto;min-height:var(--tap);resize:none}
.wa-compose .btn{flex:0 0 auto;width:var(--tap);min-height:var(--tap);padding:0}
.wa-acciones{display:flex;flex-wrap:wrap;gap:8px;margin-left:auto}
.wa-back{display:none}
@media (max-width:899px){
  .wa-inbox{grid-template-columns:minmax(0,1fr)}
  .wa-inbox .wa-pane--detail{display:none}
  .wa-inbox--detalle .wa-pane--list{display:none}
  .wa-inbox--detalle .wa-pane--detail{display:flex}
  .wa-inbox:not(.wa-inbox--detalle) .wa-pane--detail{display:none}
  .wa-back{display:inline-flex}
  .wa-msgs{padding:12px}
  .wa-msg{max-width:88%}
}
@media (max-width:767px){
  .content{padding-bottom:calc(var(--bottom-h) + env(safe-area-inset-bottom))}
  .foot{display:none}
}
</style>"""


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


def _hora(valor):
    """Hora corta para la lista: hh:mm, o la fecha si no es de hoy."""
    if hasattr(valor, "strftime"):
        return valor.strftime("%H:%M")
    return esc(valor or "")


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
                               (str(estado or "—"), "badge--info"))
    legado = LEGADO.get(cls, "")
    # El nombre legado va primero para que la píldora se lea igual en el HTML
    # que en la hoja (`badge badge-success badge--ok`).
    clases = f"badge {legado} {cls}".replace("  ", " ").strip()
    return f'<span class="{clases}">{esc(etiqueta)}</span>'


def _url_chat(chat_id, accion=None):
    ruta = "/whatsapp"
    if accion:
        ruta += f"/conversaciones/{accion}"
    params = {"wa": "conversaciones"}
    if chat_id:
        params["chat"] = chat_id
    return ruta + "?" + urllib.parse.urlencode(params)


def _btn_accion(chat_id, accion, etiqueta, clase="btn--quiet"):
    return (f'<form class="inline" method="post" '
            f'action="{esc(_url_chat(chat_id, accion))}">'
            f'{_csrf_field()}<input type="hidden" name="chat" '
            f'value="{esc(chat_id)}">'
            f'<button class="btn {clase} btn--sm" type="submit">'
            f'{esc(etiqueta)}</button></form>')


# ── Lista ─────────────────────────────────────────────────────────────────────
def _fila(c, activo=False):
    chat_id = c.get("ChatId") or ""
    estado = c.get("Estado") or ""
    asignado = (c.get("Asignado") or "").strip()
    tel = (c.get("Telefono") or "").strip() or telefono_de(chat_id)
    nombre = _contacto(c)
    # El teléfono va siempre: el contacto puede tener nombre y aun así quien
    # atiende necesita el número para llamarle.
    quien = (f'<span class="wa-row__asignado">{esc(asignado)}</span>'
             if asignado else "")
    tel_html = ("" if not tel or tel == nombre
                else f'<span class="wa-row__tel">{esc(tel)}</span>')
    return (
        f'<li class="list__row"><a class="wa-row{" is-active" if activo else ""}" '
        f'href="{esc(_url_chat(chat_id))}">'
        f'<span class="wa-row__main">'
        f'<span class="wa-row__name">{esc(nombre)}</span>{tel_html}'
        f'<span class="wa-row__msg">{_recorte(c.get("Texto"))}</span></span>'
        f'<span class="wa-row__meta">{_badge(estado)}'
        f'<span class="wa-row__hora">{esc(_hora(c.get("Ultima")))}</span>'
        f'{quien}</span></a></li>')


def lista(chat_activo=""):
    """Columna izquierda: las conversaciones, la más reciente primero."""
    filas = db.get_wa_conversaciones(limite=MAX_LISTA)
    if filas:
        cuerpo = ('<ul class="wa-list tscroll">'
                  + "".join(_fila(c, chat_activo and
                                  (c.get("ChatId") or "") == chat_activo)
                            for c in filas)
                  + "</ul>")
    else:
        cuerpo = (f'<div class="empty">{icono("chat")}'
                  '<p>Todavía no hay conversaciones: aparecen cuando OpenWA '
                  'entrega el primer mensaje en '
                  '<code>/api/wa/entrante/&lt;token&gt;</code>.</p></div>')
    return ('<section class="wa-pane wa-pane--list" aria-label="Conversaciones">'
            '<div class="wa-pane__head"><span class="wa-pane__title">'
            f'{icono("chat", 18)} Conversaciones</span></div>{cuerpo}</section>')


# ── Detalle ───────────────────────────────────────────────────────────────────
def _burbujas(chat_id):
    """Historial del chat en burbujas: entrantes a la izquierda, salientes a la
    derecha. El orden es el de la base (viejo → nuevo)."""
    mensajes = db.get_wa_mensajes(chat_id, limite=200)
    if not mensajes:
        return (f'<div class="empty">{icono("chat")}'
                '<p>Este chat todavía no tiene mensajes.</p></div>')
    trozos = []
    for m in mensajes:
        saliente = str(m.get("Direccion") or "").upper() == "OUT"
        clase = "wa-msg--out" if saliente else "wa-msg--in"
        etiqueta = "Saliente" if saliente else "Entrante"
        trozos.append(
            f'<div class="wa-msg {clase}" data-direccion="'
            f'{"OUT" if saliente else "IN"}">'
            f'{esc(m.get("Texto") or "")}'
            f'<span class="wa-msg__hora">{esc(etiqueta)} · '
            f'{esc(_hora(m.get("Fecha")))}</span></div>')
    return "".join(trozos)


def detalle(chat_id, conv=None):
    """Columna derecha: historial + acciones + caja de respuesta."""
    conv = conv if conv is not None else db.get_wa_conversacion(chat_id)
    estado = (conv or {}).get("Estado") or ""
    nombre = _contacto({"ChatId": chat_id,
                        "Nombre": (conv or {}).get("Nombre")})
    if not nombre or nombre == "(sin nombre)":
        nombre = telefono_de(chat_id) or "(sin nombre)"
    tel = telefono_de(chat_id)

    # Las tres acciones van siempre: tomar y devolver son idempotentes y tener
    # solo la que "toca" obliga a recargar para corregir de un clic.
    acciones = [
        _btn_accion(chat_id, "tomar", "Tomar conversación"),
        _btn_accion(chat_id, "devolver", "Devolver al bot"),
        _btn_accion(chat_id, "cerrar", "Cerrar chat"),
    ]

    compose = (
        f'<form class="wa-compose" method="post" '
        f'action="{esc(_url_chat(chat_id, "enviar"))}">'
        f'{_csrf_field()}'
        f'<input type="hidden" name="chat" value="{esc(chat_id)}">'
        f'<textarea class="input" name="texto" rows="1" required '
        f'placeholder="Responder…" '
        f'aria-label="Respuesta para {esc(tel or nombre)}"></textarea>'
        f'<button class="btn btn--primary" type="submit" '
        f'aria-label="Enviar respuesta">{icono("send")}</button></form>')

    return (
        '<section class="wa-pane wa-pane--detail" aria-label="Conversación">'
        '<div class="wa-pane__head">'
        f'<a class="btn btn--quiet btn--sm wa-back" '
        f'href="{esc("/whatsapp?wa=conversaciones")}" '
        f'aria-label="Volver a la lista">{icono("chevron", 18)}Volver</a>'
        f'<span class="wa-pane__title">{esc(nombre)}</span>'
        f'{_badge(estado)}'
        f'<p class="wa-pane__sub">{esc(tel)}</p>'
        f'<div class="wa-acciones">{"".join(acciones)}</div></div>'
        f'<div class="wa-msgs">{_burbujas(chat_id)}</div>'
        f'{compose}</section>')


# ── Página ────────────────────────────────────────────────────────────────────
def render(chat=""):
    """La bandeja completa: lista + detalle (o solo lista si no hay chat)."""
    chat = (chat or "").strip()
    conv = db.get_wa_conversacion(chat) if chat else None
    if chat and not conv:
        aviso = (f'<section class="wa-pane wa-pane--detail">'
                 f'<div class="empty">{icono("alert")}'
                 '<p>Ese chat no existe en la bandeja.</p>'
                 '<a class="btn btn--quiet" href="/whatsapp?wa=conversaciones">'
                 'Volver a la lista</a></div></section>')
        clase = " wa-inbox--detalle"
        return (ESTILO + f'<div class="wa-inbox{clase}">'
                + lista() + aviso + "</div>")

    clase = " wa-inbox--detalle" if chat else ""
    panes = lista(chat)
    if chat:
        panes += detalle(chat, conv)
    else:
        panes += ('<section class="wa-pane wa-pane--detail" '
                  'aria-label="Conversación"><div class="empty">'
                  f'{icono("chat")}<p>Elige una conversación de la lista.</p>'
                  '</div></section>')
    return ESTILO + f'<div class="wa-inbox{clase}">' + panes + "</div>"
