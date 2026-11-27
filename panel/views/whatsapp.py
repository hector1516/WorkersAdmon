"""
panel/views/whatsapp.py — Módulo "💬 WhatsApp" del panel
========================================================
Todo lo de WhatsApp en un solo módulo: la conexión con OpenWA, los avisos, los
grupos, el historial de la cola y la bandeja de conversaciones.

Antes estas pantallas eran sub-pestañas dentro de "🔔 Notificaciones". Se
sacaron porque el objeto es otro: Notificaciones configura CANALES de salida
(Telegram, push, correo, IA) y aquí hay un buzón que se atiende, con mensajes que
entran y personas que toman el chat. Mezclarlos obligaba a buscar la bandeja
entre formularios de SMTP.

Las cuatro primeras sub-pestañas NO se reescribieron: son los bloques de
`views/notifications.py` (mismo guardado, mismas pruebas), con la ruta base
`/whatsapp` para que los formularios vuelvan aquí. Solo se duplicaría lógica si
se copiaran, y entonces el día que cambie el MERGE de un aviso habría dos
verdades.

Reglas:
  * permiso `AccesoWhatsapp` (ver `auth.has_perm_whatsapp`: mientras la columna
    no exista en HUB_Users, `AccesoTelegram` sigue abriendo el módulo);
  * todo POST con CSRF y Post/Redirect/Get, sin JS obligatorio;
  * los secretos (API key de OpenWA) nunca se pintan.
"""
from .. import auth
from ..templates import esc, page
from . import notifications as _notif
from . import wa_conversaciones as _conv

_CSRF = {"token": ""}

# (id, etiqueta). El id es lo que viaja en ?wa= y lo que el servidor valida.
VISTAS = [
    ("conexion", "\U0001f4ac Conexión"),
    ("avisos", "\U0001f4e3 Avisos"),
    ("grupos", "\U0001f465 Grupos"),
    ("historial", "\U0001f4cb Historial"),
    ("conversaciones", "\U0001f9fe Conversaciones"),
]

# Nombres de la etapa en que estas pantallas vivían en Notificaciones: un enlace
# o marcador viejo con ?wa=eventos tiene que abrir Avisos, no caer en Conexión.
ALIAS = {"eventos": "avisos"}


def set_csrf(token):
    _CSRF["token"] = token or ""
    _notif.set_csrf(token)
    _conv.set_csrf(token)


def _nav(vista):
    """Barra `.tnav`; la activa va como .btn-primary y con aria-current."""
    items = []
    for v, label in VISTAS:
        activa = (v == vista)
        cls = "btn btn-sm btn-primary" if activa else "btn btn-sm btn-secondary"
        aria = ' aria-current="page"' if activa else ""
        items.append(f'<a class="{cls}"{aria} href="/whatsapp?wa={v}">'
                     f'{esc(label)}</a>')
    return '<div class="tnav">' + "".join(items) + "</div>"


def _cuerpo(vista, chat=""):
    if vista == "avisos":
        return _notif._wa_eventos("/whatsapp")
    if vista == "grupos":
        return _notif._wa_grupos("/whatsapp")
    if vista == "historial":
        return _notif._wa_historial()
    if vista == "conversaciones":
        return _conv.render(chat)
    return _notif._wa_conexion("/whatsapp")


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido",
           ip="", vista="conexion", chat=""):
    """La pantalla del módulo (una sub-pestaña a la vez)."""
    set_csrf(csrf)
    vista = ALIAS.get(vista, vista)
    if vista not in dict(VISTAS):
        vista = "conexion"

    if not auth.has_perm_whatsapp(user):
        cuerpo = ('<div class="panel"><div class="empty">🔒 Requiere el permiso '
                  '<code>AccesoWhatsapp</code> (o <code>AccesoTelegram</code> '
                  'mientras exista el anterior) en el HUB.</div></div>')
        return page("whatsapp", cuerpo, user=user, flash_ok=flash_ok,
                    flash_err=flash_err, lugar=lugar, ip=ip,
                    subtitle="Conexión de OpenWA y bandeja de conversaciones")

    cuerpo = _nav(vista) + _cuerpo(vista, chat)
    return page("whatsapp", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip,
                subtitle="OpenWA: conexión, avisos, grupos, historial y la "
                         "bandeja de conversaciones")
