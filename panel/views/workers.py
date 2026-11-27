"""
panel.views.workers — Módulos "📊 Estado" y "📄 Logs" del panel

Estado: tablero sin scroll de página. Arriba una fila de KPIs y abajo una
rejilla de tarjetas que llena el alto restante; lo único que se desplaza es la
lista DENTRO de cada tarjeta. Los datos son los mismos que ya calcula
panel.workers.build_status() (no se consulta nada nuevo).

Logs: índice de workers como lista (nombre, último latido, estado, «Ver log») y
la página de log en bruto, donde solo el <pre> tiene scroll.
"""
import datetime
import html
import re

from .. import workers
from ..icons import icono
from ..templates import ST_TAG, esc, page

# tail_log vive en workers.py; se expose acá para el módulo de Logs.
tail_log = workers.tail_log

# Un latido más viejo que esto se considera rancio (se avisa en el tablero).
_LATIDO_VIEJO = datetime.timedelta(hours=24)

# Token CSRF de la petición actual (lo fija render() antes de construir el HTML).
_CSRF = {"token": ""}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


# ── estado de cada programa ──────────────────────────────────────────────────
def _badge(e):
    """Badge del estado de un programa con las clases del contrato."""
    clase, label = ST_TAG.get(e.get("state"), ("badge-info", e.get("state") or "?"))
    # ST_TAG trae las clases viejas (badge-success…); apple.css las mapea.
    return f'<span class="badge {clase}">{esc(label)}</span>'


def _latido(e):
    """Texto relativo del último latido, o 'sin latido'."""
    dt = workers.parse_dt(e.get("last_run"))
    return workers.rel_time(dt) if dt else "sin latido"


def _latidos_viejos(programs):
    """Programas cuyo último latido pasó de _LATIDO_VIEJO (derivado, sin consultar)."""
    ahora = workers.now()
    out = []
    for e in programs:
        dt = workers.parse_dt(e.get("last_run"))
        if dt is not None and (ahora - dt) > _LATIDO_VIEJO:
            out.append(e)
    return out


def _avisos(status, chk, vchk):
    """Lista de (nivel, texto) con lo que requiere atención. nivel: ok/warn/bad."""
    out = []
    if status.get("supervisor_error"):
        out.append(("bad", f'supervisord no responde: {status["supervisor_error"]}'))

    if chk is None:
        out.append(("warn", "Sin datos del chequeo del shell todavía."))
    elif chk.get("ok"):
        out.append(("ok", f'Shell {chk.get("shell_version", "?")} al día · '
                          f'revisado {chk.get("revisado", "?")}'))
    else:
        probs = "; ".join(str(p) for p in (chk.get("problemas") or [])[:2])
        out.append(("bad", f"Shell desincronizado: {probs or 'revisar'}"))

    if vchk is None:
        out.append(("warn", "Sin datos del chequeo de versiones todavía."))
    elif vchk.get("ok"):
        out.append(("ok", f'Versiones al día · revisado {vchk.get("revisado", "?")}'))
    else:
        probs = "; ".join(str(p) for p in (vchk.get("problemas") or [])[:2])
        out.append(("bad", f"Versiones desincronizadas: {probs or 'revisar'}"))

    for e in status["programs"]:
        if e.get("state") in ("FATAL", "BACKOFF"):
            label = ST_TAG.get(e["state"], ("", e["state"]))[1]
            out.append(("bad", f'{e["name"]} · {label}'))

    viejos = _latidos_viejos(status["programs"])
    if viejos:
        nombres = ", ".join(e["name"] for e in viejos[:3])
        resto = "" if len(viejos) <= 3 else f" (+{len(viejos) - 3})"
        out.append(("warn", f"{len(viejos)} sin latido en 24 h: {nombres}{resto}"))
    return out


# ── acciones (activar / deshabilitar / reiniciar) ────────────────────────────
def _actions(e):
    """Formularios de control del programa. Rutas y campos sin cambios."""
    if e.get("protected"):
        return '<span class="badge badge--info">' + icono("dashboard", 14) + \
               ' Este es el panel</span>'
    name = esc(e["name"])
    botones = []
    if e.get("enabled"):
        botones.append(
            f'<form class="inline" method="post" action="/workers/{name}/restart">'
            f'{_csrf_field()}'
            f'<button class="btn btn--sm btn--quiet" type="submit">'
            f'{icono("refresh", 16)}<span>Reiniciar</span></button></form>')
        botones.append(
            f'<form class="inline" method="post" action="/workers/{name}/disable" '
            f'onsubmit="return confirm(\'¿Deshabilitar {name}?\')">'
            f'{_csrf_field()}'
            f'<button class="btn btn--sm btn--quiet" type="submit">'
            f'{icono("pause", 16)}<span>Deshabilitar</span></button></form>')
    else:
        botones.append(
            f'<form class="inline" method="post" action="/workers/{name}/enable">'
            f'{_csrf_field()}'
            f'<button class="btn btn--sm btn--primary" type="submit">'
            f'{icono("play", 16)}<span>Activar</span></button></form>')
    cfg = (f'<a class="btn btn--sm btn--quiet" '
           f'href="/configuracion#cfg-{name}">'
           f'{icono("settings", 16)}<span>Config</span></a>')
    return '<div class="actions">' + "".join(botones) + cfg + "</div>"


# ── piezas del tablero ───────────────────────────────────────────────────────
def _kpi(icono_nombre, label, valor, pista="", tono=""):
    clase = f"kpi dashx__kpi--{tono}" if tono else "kpi"
    pista_html = f'<span class="kpi__hint">{esc(pista)}</span>' if pista else ""
    return (f'<div class="{clase}">'
            f'<span class="kpi__label">{icono(icono_nombre, 14)}{esc(label)}</span>'
            f'<span class="kpi__value">{esc(valor)}</span>'
            f'{pista_html}</div>')


def _fila_worker(e):
    """Fila de la tarjeta Workers: nombre, latido, estado y acceso al log."""
    nombre = esc(e["name"])
    app = f' · {esc(e["app"])}' if e.get("app") else ""
    log = (f'<a class="btn btn--sm btn--quiet" href="/workers/{nombre}/logs" '
           f'aria-label="Ver log de {nombre}">{icono("logs", 16)}'
           f'<span>Logs</span></a>'
           if e.get("log_ok") else '<span class="muted">sin log</span>')
    # La descripción («qué hace») sigue disponible, plegada, para no perder
    # la documentación del worker al pasar de tarjeta a lista.
    desc = ""
    if e.get("descripcion"):
        desc = (f'<details class="wdesc"><summary>{icono("info", 16)}'
                f' Qué hace</summary>'
                f'<div>{esc(e["descripcion"])}</div></details>')
    elif e.get("desc"):
        desc = f'<div class="muted dashx__sub">{esc(e["desc"])}</div>'
    return (f'<li class="list__row">'
            f'<div class="list__main">'
            f'<div class="wcard-name">{nombre}</div>'
            f'<div class="muted dashx__sub">Último latido {esc(_latido(e))}{app}</div>'
            f'{desc}'
            f'</div>'
            f'<div class="dashx__rowmeta">{_badge(e)}{log}</div>'
            f'</li>')


def _fila_control(e):
    """Fila de la tarjeta Control: nombre y sus formularios."""
    return (f'<li class="list__row dashx__ctrl">'
            f'<div class="list__main"><div class="wcard-name">{esc(e["name"])}</div>'
            f'<div class="muted dashx__sub">{esc(e.get("app") or "sin app")}</div></div>'
            f'{_actions(e)}</li>')


def _aviso_fila(nivel, texto):
    clases = {"ok": "badge--ok", "warn": "badge--warn", "bad": "badge--bad"}
    etiquetas = {"ok": "Bien", "warn": "Aviso", "bad": "Revisar"}
    ico = {"ok": "check", "warn": "alert", "bad": "alert"}[nivel]
    return (f'<li class="list__row">'
            f'<div class="list__main dashx__sub">{esc(texto)}</div>'
            f'<span class="badge {clases[nivel]}">{icono(ico, 14)}'
            f'{etiquetas[nivel]}</span></li>')


def render(status, user, flash_ok="", flash_err="", csrf="",
           lugar="desconocido", ip=""):
    """Tablero de Estado: KPIs arriba y tarjetas que llenan el alto restante."""
    set_csrf(csrf)
    programs = status["programs"]
    t = status["totals"]

    # chequeos diarios (ya se leían acá: mismo dato, otra presentación)
    chk = workers.shell_check()
    vchk = workers.versiones_check()
    avisos = _avisos(status, chk, vchk)
    pendientes = sum(1 for nivel, _ in avisos if nivel in ("warn", "bad"))
    viejos = _latidos_viejos(programs)

    kpis = "".join([
        _kpi("play", "Corriendo", t["running"],
             f'de {t["total"]} programas', "ok"),
        _kpi("alert", "Con error", t["errors"], "FATAL o reintentando",
             "bad" if t["errors"] else ""),
        _kpi("pause", "Detenidos", t["stopped"],
             f'{t["enabled"]} habilitados', "warn" if t["stopped"] else ""),
        _kpi("clock", "Latidos viejos", len(viejos), "más de 24 h sin latir",
             "warn" if viejos else ""),
        _kpi("bell", "Avisos", pendientes, "shell, versiones y fallas",
             "bad" if pendientes else "ok"),
    ])

    if programs:
        filas_worker = "".join(_fila_worker(e) for e in programs)
        lista_worker = (f'<ul class="list dashx__list dashx__scroll" '
                        f'aria-label="Workers del contenedor">{filas_worker}</ul>')
        fechas = [workers.parse_dt(e.get("last_run")) for e in programs]
        ultimo = max((f for f in fechas if f is not None), default=None)
        facts = (f'<div class="wcard-facts">'
                 f'<div><b>En ejecución</b>{t["running"]} de {t["total"]}</div>'
                 f'<div><b>Último latido</b>{esc(workers.rel_time(ultimo))}</div>'
                 f'</div>')
    else:
        lista_worker = (f'<div class="empty">{icono("users", 24)}'
                        f'<p>No hay workers en el contenedor.</p></div>')
        facts = ""

    filas_control = "".join(_fila_control(e) for e in programs)
    lista_control = (f'<ul class="list dashx__list dashx__scroll" '
                     f'aria-label="Controles de los workers">{filas_control}</ul>'
                     if programs else "")

    lista_avisos = (f'<ul class="list dashx__list dashx__scroll" aria-label="Avisos">'
                    + "".join(_aviso_fila(n, txt) for n, txt in avisos)
                    + '</ul>')

    body = f"""
<div class="dash dash--estado">
  <div class="grid dashx__kpis dash__kpis" role="group" aria-label="Indicadores de los workers">{kpis}</div>
  <div class="wgrid">
    <article class="wcard dashx__card">
      <h2 class="wgroup">{icono("users")} Workers · {t['running']} en ejecución</h2>
      {facts}
      {lista_worker}
    </article>
    <article class="wcard dashx__card">
      <h2 class="wgroup">{icono("alert")} Avisos · {pendientes}</h2>
      {lista_avisos}
    </article>
    <article class="wcard dashx__card">
      <h2 class="wgroup">{icono("settings")} Control</h2>
      {lista_control}
    </article>
  </div>
</div>"""

    conn = "offline" if status.get("supervisor_error") else "online"
    # Estado del banner común: el panel no tiene cola offline, así que
    # "sincronizado" = todos los programas bien; si hay FATAL/BACKOFF, error.
    if status.get("supervisor_error"):
        sync = "offline"
    elif [e for e in programs if e.get("state") in ("FATAL", "BACKOFF", "EXITED")]:
        sync = "error"
    else:
        sync = "idle"
    return page("estado", body, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip, sync=sync,
                subtitle=f"zona horaria {esc(status['timezone'])} · datos en "
                         f"<code>{esc(status['data_dir'])}</code> · "
                         f"actualizado {esc(status['now'])}",
                refresh=15, conn=conn)


# ── log crudo de un worker ───────────────────────────────────────────────────
def _estado_worker(name):
    """Estado de supervisord del programa, o '' si no se pudo leer."""
    filas, _ = workers.supervisor_status()
    for nombre, estado, _pid, _up in (filas or []):
        if nombre == name:
            return estado
    return ""


def logs_page(name, text, error, user, estado=""):
    """Log en bruto: cabecera fija y scroll SOLO dentro del <pre>."""
    if not estado:
        estado = _estado_worker(name)
    clase, label = ST_TAG.get(estado, ("badge--info", estado or "sin estado"))
    if error:
        content = f'<div class="empty">{icono("alert", 24)}<p>{esc(error)}</p></div>'
    else:
        content = f'<pre class="log">{html.escape(text) or "(log vacío)"}</pre>'
    body = f"""
<div class="card dashx__log">
  <div class="dashx__loghead">
    <h2>{icono("logs")} {esc(name)}</h2>
    <span class="badge {clase}">{esc(label)}</span>
    <a class="btn btn--sm btn--quiet" href="/estado">← Volver a Estado</a>
  </div>
  {content}
</div>"""
    # Vuelve a Estado, no a la home: el log es una ficha de un worker y el
    # usuario viene de ahí.
    return page("estado", body, user=user, back_href="/estado",
                back_label="← Volver a Estado",
                subtitle=f"últimas líneas de <code>{esc(name)}.log</code>")


# ── Módulo de Logs ────────────────────────────────────────────────────────────
# El índice junta a todos los workers en una lista: de ahí se baja al log
# completo de cada uno. Los eventos guardados en la base van aparte porque
# sobreviven a recrear el contenedor (los de archivo, no).
_ERROR_RE = re.compile(
    r"\b(traceback|exception|error|failed|failure|fatal|critical)\b|"
    r"\b(error|err)\b\s*[:=]", re.IGNORECASE)


def _error_resaltes(texto, maximo=4):
    """Últimas líneas que parecen error, para no hacer falta abrir el log."""
    if not texto:
        return []
    out = []
    for linea in reversed(texto.splitlines()):
        if _ERROR_RE.search(linea):
            limpia = linea.strip()
            if limpia and limpia not in out:
                out.insert(0, limpia[-160:])
            if len(out) >= maximo:
                break
    return list(reversed(out))


def _log_bd(limite=120):
    """Tabla con lo último que los workers dijeron a la base."""
    from .. import db

    filas = db.get_workers_log(limite=limite)
    head = ('<div class="card"><div class="wcard-name">Eventos de los workers</div>'
            '<div class="muted nota-sm">Guardados en la base: '
            'sobreviven a recrear el contenedor.</div>')
    if not filas:
        return (head + '<div class="muted nota-sm nota--sep">'
                'Todavía no hay eventos guardados, o la base no responde ahora '
                'mismo.</div></div>')

    clases = {"ERROR": "badge--bad", "WARN": "badge--warn"}
    trs = []
    for f in filas:
        nivel = (f.get("Nivel") or "INFO").upper()
        creado = f.get("Creado")
        hora = creado.strftime("%d/%m %H:%M:%S") if hasattr(creado, "strftime") else (creado or "")
        trs.append(
            f'<tr><td class="hide-sm">{esc(hora)}</td>'
            f'<td>{esc(f.get("Worker") or "")}</td>'
            f'<td><span class="badge {clases.get(nivel, "badge--info")}">{esc(nivel)}</span></td>'
            f'<td>{esc(f.get("Mensaje") or "")}'
            + (f'<div class="muted nota-xs">{esc(f.get("Detalle"))}</div>'
               if f.get("Detalle") else "")
            + '</td></tr>')

    return (head + '<div class="table-wrap tscroll dashx__tablawrap">'
            '<table class="table"><thead><tr>'
            '<th class="hide-sm">Hora</th><th>Worker</th><th>Nivel</th><th>Mensaje</th>'
            '</tr></thead><tbody>' + "".join(trs) + "</tbody></table></div></div>")


def _fila_log(e):
    """Fila del índice: nombre, último latido, estado y «Ver log»."""
    nombre = esc(e["name"])
    if e.get("log_ok"):
        texto, error = tail_log(e["name"], 120)
    else:
        texto, error = "", f'no hay log para \'{e["name"]}\''
    errores = 0 if error else len(_error_resaltes(texto))
    if error:
        accion = '<span class="muted">sin log</span>'
    else:
        accion = (f'<a class="btn btn--sm btn--quiet" '
                  f'href="/workers/{nombre}/logs" '
                  f'aria-label="Ver log de {nombre}">Ver log</a>')
    marca_err = ""
    if errores:
        txt = "1 error" if errores == 1 else f"{errores} errores"
        marca_err = (f'<span class="badge badge--bad">{icono("alert", 14)}'
                     f'{txt}</span>')
    return (f'<li class="list__row">'
            f'<div class="list__main">'
            f'<div class="wcard-name">{nombre}</div>'
            f'<div class="muted dashx__sub">Último latido {esc(_latido(e))}</div>'
            f'</div>'
            f'<div class="dashx__rowmeta">{_badge(e)}{marca_err}{accion}</div>'
            f'</li>')


def logs_index(status, user, flash_ok="", flash_err="", csrf="",
               lugar="desconocido", ip=""):
    """Módulo Logs: índice de workers y los eventos guardados en la base."""
    set_csrf(csrf)
    programs = sorted(status["programs"], key=lambda x: x.get("name", ""))
    if programs:
        filas = "".join(_fila_log(e) for e in programs)
        cuerpo = (f'<div class="card"><div class="wgroup dashx__grouph">'
                  f'{icono("logs", 16)} Workers · {len(programs)}</div>'
                  f'<ul class="list dashx__list" '
                  f'aria-label="Workers con log">{filas}</ul></div>')
    else:
        cuerpo = (f'<div class="card"><div class="empty">{icono("logs", 24)}'
                  f'<p>Todavía no hay workers en el contenedor.</p></div></div>')

    body = f"{cuerpo}{_log_bd()}"
    return page("logs", body, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip,
                subtitle="últimas líneas de cada worker · abrí uno para el log completo")
