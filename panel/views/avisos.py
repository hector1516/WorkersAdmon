"""
panel.views.avisos — Módulo "📣 Avisos" del panel
================================================
Configuración de los avisos push que manda `cron_avisos_push.py`, y solo de eso.
Lo demás de notificaciones (Telegram, WhatsApp, claves VAPID, SMTP, IA) sigue en
la pestaña "🔔 Notificaciones": esta es la pantalla de los avisos PWA por app.

POR QUÉ ES UN MÓDULO APARTE Y NO OTRA TARJETA EN "NOTIFICACIONES"
-----------------------------------------------------------------
Porque el objeto que se configura aquí es otro. La pestaña Notificaciones
configura CANALES (a dónde sale el aviso: Telegram, correo, WhatsApp). Esta
configura QUÉ AVISOS EXISTEN, quién los recibe y CUÁNDO; el canal ya está
decidido: el push de la PWA del propio usuario.

La idea de fondo es que la lista de destinatarios NO se escribe a mano en ningún
lado: se deduce de los permisos que ya usa la sesión de las apps. Si alguien tiene
`AccesoCotizaciones` es porque puede abrir el módulo de Cotizaciones, y por eso
recibe el aviso de una cotización firmada. Duplicar esa decisión en una lista del
panel haría que algún día los dos se contradijeran, y siempre con la misma
consecuencia: alguien recibe avisos de un módulo que no puede abrir.

LO QUE SE VE, Y POR QUÉ
-----------------------
Por cada tipo de aviso:

  * su interruptor (encendido/apagado), que escribe en `HUB_Config` y por eso
    también aparece en la pestaña "⚙️ Apps";
  * a CUÁNTAS personas le llegaría ahora mismo, y cuántos dispositivos hay en
    total, con un detalle que suele ser el que falta: puede haber veinte personas
    con el permiso y cero con el teléfono suscrito, y en ese caso el aviso no le
    llega a nadie aunque todo esté "bien";
  * su permiso de origen, para no tener que buscarlo en HUB_Users.

El horario aparece arriba porque es lo que explica por qué nada sale a las ocho de
la mañana: fuera de lunes a viernes 09:00-18:30 lo que llega se acumula y sale un
solo resumen al día siguiente.

Reglas de la vista:
  * Requiere el permiso `AccesoAppConfig` (es lo mismo que puede cambiar la
    configuración de las apps, que es casi todo lo que se hace aquí).
  * Todo POST lleva CSRF y responde con Post/Redirect/Get.
  * Sin JavaScript: el interruptor es un `<form>` con un botón, no un checkbox con
    JS que lo mande. El panel entero funciona sin JS y este módulo no es la
    excepción que rompe la regla.
"""

import notif_dispatch as nd

from .. import db
from ..templates import esc, page

_CSRF = {"token": ""}


# ── Datos ─────────────────────────────────────────────────────────────────────


def _cfg_todos():
    """Los interruptores y el horario, leídos de una sola vez."""
    claves = [f"avisos_push_{t.lower()}" for t in nd.TIPOS]
    claves += ["avisos_push_horario_inicio", "avisos_push_horario_fin",
               "avisos_push_resumen_activo", "avisos_ultimo_resumen"]
    return db.get_config_values(claves)


def _encendido(valor):
    return (valor or "").strip().lower() in ("1", "true", "si", "sí", "yes")


def _destinatarios(tipo, app):
    """
    A quién le llegaría este tipo ahora mismo.

    Se le pregunta a `notif_dispatch` y no se cuenta a mano, porque la cuenta
    correcta tiene tres condiciones y basta con olvidar una para mentir: usuario
    activo, con el permiso, y con un dispositivo suscrito PARA ESTA APP.
    """
    defn = nd.TIPOS.get(tipo)
    if not defn:
        return []
    try:
        return nd.usuarios_con_suscripcion(app=app, permiso=defn["permiso"])
    except Exception:
        return []


# ── Piezas de HTML ────────────────────────────────────────────────────────────


def _csrf_field():
    return f'<input type="hidden" name="csrf" value="{esc(_CSRF["token"])}">'


def _btn(accion, etiqueta, oculto=""):
    return (f'<form method="post" action="{esc(accion)}" class="inline">'
            f'{_csrf_field()}{oculto}'
            f'<button class="btn btn-sm btn-secondary" type="submit">{etiqueta}'
            f'</button></form>')


def _tarjeta_tipo(tipo, defn, cfg, subs, app):
    """Una tarjeta por tipo de aviso: interruptor + a quién le llega."""
    on = _encendido(cfg.get(f"avisos_push_{tipo.lower()}"))
    destino = _destinatarios(tipo, app)
    sin_suscribir = db.get_usuarios_con_permiso_sin_suscribir(defn["permiso"], app)

    # El interruptor es un formulario que lleva el estado CONTRARIO como valor: no
    # hay checkbox que mandar, hay un botón que dice lo que va a pasar.
    if on:
        interruptor = _btn(f"/avisos/{tipo}/apagar", "🔴 Apagar")
        estado = '<span class="badge badge-success">Encendido</span>'
    else:
        interruptor = _btn(f"/avisos/{tipo}/encender", "🟢 Encender")
        estado = '<span class="badge badge-warning">Apagado</span>'

    cuerpo = (
        '<div class="cfg-grid">'
        f'<div class="field cfg-field"><label>Estado</label>'
        f'<div class="ro">{estado}</div></div>'
        f'<div class="field cfg-field"><label>A quién le llega</label>'
        f'<div class="ro"><strong>{len(destino)}</strong> persona(s) · '
        f'{subs} susceptor(es) en total</div></div>'
        f'<div class="field cfg-field"><label>Permiso que lo habilita</label>'
        f'<div class="ro">{esc(defn["permiso"])}</div></div>'
        f'<div class="field cfg-field"><label>Destino</label>'
        f'<div class="ro">{esc(defn["url"])}</div></div>'
        '</div>'
    )

    # El aviso de "no llega a nadie" es el que más se pierde: con el interruptor
    # encendido y ningún móvil suscrito, quien mire esta pantalla tiene que
    # verlo sin tener que cruzarse.
    aviso = ""
    if on and not destino:
        aviso = ('<div class="wgroup"><span class="badge badge-danger">'
                 'No le llegaría a nadie todavía</span> '
                 f'{sin_suscribir} persona(s) tienen el permiso pero ningún '
                 'dispositivo suscrito: hay que activarlas desde su app.'
                 '</div>')
    elif on and sin_suscribir:
        aviso = ('<div class="wgroup"><span class="badge badge-info">'
                 f'{sin_suscribir} con el permiso sin teléfono suscrito'
                 '</span></div>')

    return (f'<div class="card" style="margin-bottom:1rem">'
            f'<h3>{esc(defn["titulo"])}</h3>{cuerpo}'
            f'<div class="actions-cell" style="margin-top:.6rem">'
            f'{interruptor}</div>{aviso}</div>')


# ── Vista ─────────────────────────────────────────────────────────────────────


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido", ip=""):
    """La pantalla del módulo."""
    _CSRF["token"] = csrf or ""
    app = nd.APP_ADMON
    cfg = _cfg_todos()
    subs = db.get_suscripciones_por_app().get(app, 0)
    cola = db.get_avisos_en_cola(app)
    plataformas = db.get_plataformas_suscripciones(app)

    inicio, fin = nd.horario_desde(cfg)
    momento = nd.ahora_mx()
    abierto = nd.dentro_de_horario(momento, inicio, fin)
    proximo = nd.proximo_envio(momento, inicio)

    tarjetas = "".join(_tarjeta_tipo(t, d, cfg, subs, app)
                       for t, d in nd.TIPOS.items())

    horario_txt = (f'{nd.hora_legible(inicio)} – {nd.hora_legible(fin)} '
                   f'(lunes a viernes, hora de la Ciudad de México)')
    if abierto:
        ventana = ('<span class="badge badge-success">Abierta ahora</span> '
                   'los avisos salen en cuanto se detectan')
    else:
        ventana = ('<span class="badge badge-warning">Cerrada</span> '
                   f'lo que llegue se acumula y sale {nd.hora_legible(inicio)} '
                   'del próximo día laboral'
                   + (f' ({proximo.strftime("%d/%m %H:%M")})' if proximo else ''))

    plats = " · ".join(f"{n} {p}" for p, n in plataformas) or "ninguno"

    resumen = (
        '<div class="cards">'
        f'<div class="card blue"><div class="n">{subs}</div>'
        f'<div class="l">Suscripciones de {esc(app)}</div></div>'
        f'<div class="card orange"><div class="n">{cola}</div>'
        f'<div class="l">Avisos en cola</div></div>'
        f'<div class="card gray"><div class="n">{len(nd.TIPOS)}</div>'
        f'<div class="l">Tipos de aviso</div></div>'
        '</div>'
        f'<div class="wgroup"><strong>Horario:</strong> {esc(horario_txt)} '
        f'{ventana}</div>'
        f'<div class="wgroup"><strong>Dispositivos:</strong> {esc(plats)}</div>'
    )

    cuerpo = (resumen
              + '<h3>Tipos de aviso</h3>'
              + tarjetas
              + '<div class="wgroup"><strong>Dónde se decide quién recibe cada '
                'aviso:</strong> de los permisos de cada usuario en HUB_Users, no '
                'de una lista aquí. Un tipo encendido sin ningún teléfono '
                'suscrito no llega a nadie, y por eso la tarjeta lo avisa.</div>')

    return page("avisos", cuerpo, user=user, flash_ok=flash_ok, flash_err=flash_err,
                lugar=lugar, ip=ip,
                subtitle="Cinco avisos push, filtrados por los permisos de cada "
                         "usuario · lunes a viernes dentro del horario, fuera de "
                         "él se acumulan y salen juntos")
