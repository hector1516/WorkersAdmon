"""
panel.views.avisos — Módulo "Avisos" del panel
==============================================
Configuración de los avisos push que manda `cron_avisos_push.py`, y solo de eso.
Lo demás de notificaciones (Telegram, WhatsApp, claves VAPID, SMTP, IA) sigue en
la pestaña "Notificaciones": esta es la pantalla de los avisos PWA por app.

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
    también aparece en la pestaña "Apps";
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

from ..icons import icono
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


def _btn(accion, etiqueta, icono_nombre="play", oculto=""):
    return (f'<form method="post" action="{esc(accion)}" class="inline">'
            f'{_csrf_field()}{oculto}'
            f'<button class="btn btn--primary" type="submit">'
            f'{icono(icono_nombre, 18)}<span>{etiqueta}</span>'
            f'</button></form>')


def _dato(etiqueta, valor_html, ayuda=""):
    """Campo de solo lectura dentro de la tarjeta (etiqueta arriba)."""
    return (f'<div class="field cfg-field"><label>{esc(etiqueta)}</label>'
            f'<div class="ro">{valor_html}</div>'
            + (f'<div class="help">{esc(ayuda)}</div>' if ayuda else "")
            + '</div>')


def _tarjeta_tipo(tipo, defn, cfg, subs, app):
    """Una tarjeta por tipo de aviso: interruptor + a quién le llega."""
    on = _encendido(cfg.get(f"avisos_push_{tipo.lower()}"))
    destino = _destinatarios(tipo, app)
    sin_suscribir = db.get_usuarios_con_permiso_sin_suscribir(defn["permiso"], app)

    # El interruptor es un formulario que lleva el estado CONTRARIO como valor: no
    # hay checkbox que mandar, hay un botón que dice lo que va a pasar.
    if on:
        interruptor = _btn(f"/avisos/{tipo}/apagar", "Apagar", "pause")
        estado = '<span class="badge badge-success">Encendido</span>'
    else:
        interruptor = _btn(f"/avisos/{tipo}/encender", "Encender", "play")
        estado = '<span class="badge badge-warning">Apagado</span>'

    cuerpo = (
        '<div class="grid2">'
        + _dato("A quién le llega",
                f'<strong>{len(destino)}</strong> persona(s) · '
                f'{subs} suscriptor(es) en total',
                "Solo cuenta usuarios activos con el permiso y con un "
                "dispositivo suscrito a esta app.")
        + _dato("Permiso que lo habilita", esc(defn["permiso"]))
        + _dato("Destino", esc(defn["url"]))
        + '</div>'
    )

    # El aviso de "no llega a nadie" es el que más se pierde: con el interruptor
    # encendido y ningún móvil suscrito, quien mire esta pantalla tiene que
    # verlo sin tener que cruzarse.
    aviso = ""
    if on and not destino:
        aviso = ('<div class="callout bad">'
                 '<span class="badge badge-danger">'
                 'No le llegaría a nadie todavía</span> '
                 f'{sin_suscribir} persona(s) tienen el permiso pero ningún '
                 'dispositivo suscrito: hay que activarlas desde su app.'
                 '</div>')
    elif on and sin_suscribir:
        aviso = ('<div class="callout">'
                 f'<span class="badge badge-info">{sin_suscribir} con el permiso '
                 'sin teléfono suscrito</span></div>')

    return (f'<div class="card">'
            f'<div class="wcard-top">'
            f'<div class="wcard-name">{icono("bell", 20)} {esc(defn["titulo"])}</div>'
            f'{estado}</div>'
            f'{cuerpo}'
            f'<div class="actions">{interruptor}</div>'
            f'{aviso}</div>')


def _resumen(subs, cola, plazo, plats, ultimo, ventana_badge):
    """Cifras y horario: lo que explica por qué un aviso no ha salido."""
    kpis = (
        '<div class="grid">'
        f'<div class="kpi"><span class="kpi__label">Suscripciones de '
        f'{esc(nd.APP_ADMON)}</span>'
        f'<span class="kpi__value">{subs}</span></div>'
        f'<div class="kpi"><span class="kpi__label">Avisos en cola</span>'
        f'<span class="kpi__value">{cola}</span></div>'
        f'<div class="kpi"><span class="kpi__label">Tipos de aviso</span>'
        f'<span class="kpi__value">{len(nd.TIPOS)}</span></div>'
        '</div>'
    )
    filas = (
        f'<li class="list__row"><span class="list__main">Horario</span>'
        f'<span class="list__meta">{esc(plazo)}</span></li>'
        f'<li class="list__row"><span class="list__main">Dispositivos</span>'
        f'<span class="list__meta">{esc(plats)}</span></li>'
        f'<li class="list__row"><span class="list__main">Último resumen '
        f'entregado</span><span class="list__meta">{esc(ultimo or "sin registros")}'
        f'</span></li>'
    )
    return (f'{kpis}'
            f'<div class="card">'
            f'<div class="wcard-top">'
            f'<div class="wcard-name">{icono("clock", 20)} Horario de entrega</div>'
            f'{ventana_badge}</div>'
            f'<ul class="list">{filas}</ul></div>')


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
        ventana_badge = ('<span class="badge badge-success">Abierta ahora</span>')
    else:
        ventana_badge = ('<span class="badge badge-warning">Cerrada</span>')
    ventana = (f'{horario_txt}. '
               + ('Los avisos salen en cuanto se detectan.'
                  if abierto else
                  f'Lo que llegue se acumula y sale {nd.hora_legible(inicio)} '
                  'del próximo día laboral'
                  + (f' ({proximo.strftime("%d/%m %H:%M")})' if proximo else '')))

    plats = " · ".join(f"{n} {p}" for p, n in plataformas) or "ninguno"
    ultimo = cfg.get("avisos_ultimo_resumen", "")

    cuerpo = (_resumen(subs, cola, ventana, plats, ultimo, ventana_badge)
              + '<h2 class="wgroup">Tipos de aviso</h2>'
              + tarjetas
              + '<p class="help"><b>Dónde se decide quién recibe cada aviso:</b> '
                'de los permisos de cada usuario en HUB_Users, no de una lista '
                'aquí. Un tipo encendido sin ningún teléfono suscrito no llega a '
                'nadie, y por eso la tarjeta lo avisa.</p>')

    return page("avisos", cuerpo, user=user, flash_ok=flash_ok, flash_err=flash_err,
                lugar=lugar, ip=ip,
                subtitle="Cuatro avisos push, filtrados por los permisos de cada "
                         "usuario · lunes a viernes dentro del horario, fuera de "
                         "él se acumulan y salen juntos · el reporte firmado va "
                         "directo al creador y a los involucrados")
