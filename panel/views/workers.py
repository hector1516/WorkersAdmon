"""
panel.views.workers — Pestaña "🔧 Workers" del panel
Tabla de estado + acciones (activar / deshabilitar / reiniciar) + acceso a logs.
"""
import html

from .. import workers
from ..templates import ST_TAG, esc, page


# Token CSRF de la petición actual (lo fija render() antes de construir la tabla).
_CSRF = {"token": ""}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _actions(e):
    """Botones disponibles para un programa según su estado (≥44px de alto)."""
    if e.get("protected"):
        return '<span class="tag t-yes">PANEL</span>'
    buttons = []
    if e.get("enabled"):
        name = esc(e["name"])
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/restart">'
            f'{_csrf_field()}'
            f'<button class="btn re" type="submit">↺ Reiniciar</button></form>')
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/disable" '
            f'onsubmit="return confirm(\'¿Deshabilitar {name}?\')">'
            f'{_csrf_field()}'
            f'<button class="btn off" type="submit">■ Deshabilitar</button></form>')
    else:
        name = esc(e["name"])
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/enable">'
            f'{_csrf_field()}'
            f'<button class="btn on" type="submit">▶ Activar</button></form>')
    logs = (f'<a class="btn log" href="/workers/{esc(e["name"])}/logs">📄 Logs</a>'
            if e.get("log_ok") else "")
    cfg = (f'<a class="btn ghost" href="/configuracion#cfg-{esc(e["name"])}">'
           f'⚙️ Config</a>')
    return '<div class="actions">' + "".join(buttons) + cfg + logs + "</div>"


# Orden de los grupos de tarjetas: panel, luego HUB, Field y el resto.
_GROUP_ORDER = {"workersadmon": 0, "HUB": 1, "Field": 2, "HUB / Field": 3}
_GROUP_ICON = {"workersadmon": "🖥️", "HUB": "🏢", "Field": "📱", "HUB / Field": "🔗"}


def _card(e):
    """Tarjeta de un programa: legible en el iPhone sin scroll horizontal."""
    cls, label = ST_TAG.get(e["state"], ("t-off", e["state"]))
    card_cls = "wcard"
    if e["state"] in ("FATAL", "BACKOFF", "EXITED", "UNKNOWN"):
        card_cls += " bad"
    elif not e.get("enabled"):
        card_cls += " off"

    chips = []
    if e.get("app"):
        chips.append(f'<span class="chip">{esc(e["app"])}</span>')
    if e.get("cadencia"):
        chips.append(f'<span class="chip off">{esc(e["cadencia"])}</span>')
    chips.append('<span class="tag t-yes">HABILITADO</span>' if e.get("enabled")
                 else '<span class="tag t-off">NO HABILITADO</span>')

    # Hechos: "activo desde" y "última ejecución" con tiempo relativo.
    facts = []
    if e.get("started_at"):
        facts.append(
            f'<div><b>Activo desde</b>{esc(workers.rel_time(workers.parse_dt(e["started_at"])))}'
            f'<br><span class="mono">{esc(e["started_at"])}</span></div>')
    else:
        facts.append('<div><b>Activo desde</b>—</div>')
    if e.get("last_run"):
        facts.append(
            f'<div><b>Última ejecución</b>{esc(workers.rel_time(workers.parse_dt(e["last_run"])))}'
            f'<br><span class="mono">{esc(e["last_run"])}</span></div>')
    else:
        facts.append('<div><b>Última ejecución</b>sin registros</div>')
    if e.get("count") is not None:
        facts.append(f'<div><b>Registros</b>{esc(e["count"])}</div>')
    if e.get("detail"):
        facts.append(f'<div><b>Detalle</b>{esc(e["detail"])}</div>')

    desc = ""
    if e.get("descripcion"):
        desc = (f'<details class="wdesc"><summary>ℹ️ Qué hace</summary>'
                f'<div>{esc(e["descripcion"])}</div></details>')
    elif e.get("desc"):
        desc = f'<div class="muted" style="font-size:.82rem">{esc(e["desc"])}</div>'

    return f"""<article class="{card_cls}">
  <div class="wcard-top">
    <div class="wcard-name">{esc(e['name'])}</div>
    <span class="tag {cls}">{esc(label)}</span>
  </div>
  <div class="wcard-meta">{''.join(chips)}</div>
  <div class="wcard-facts">{''.join(facts)}</div>
  {desc}
  {_actions(e)}
</article>"""


def render(status, user, flash_ok="", flash_err="", csrf=""):
    """Devuelve el HTML completo de la pestaña Workers (tarjetas, sin tabla)."""
    set_csrf(csrf)
    programs = status["programs"]

    # Agrupar por app para que en el celular se lea por bloques y no como tabla.
    grupos = {}
    for e in programs:
        key = e.get("app") or "otros"
        grupos.setdefault(key, []).append(e)
    ordered = sorted(grupos.items(),
                     key=lambda kv: (_GROUP_ORDER.get(kv[0], 9), kv[0].lower()))

    secciones = []
    for key, items in ordered:
        cards = "".join(_card(e) for e in items)
        run = sum(1 for e in items if e.get("state") == "RUNNING")
        icono = _GROUP_ICON.get(key, "📦")
        plural = "" if len(items) == 1 else "s"
        secciones.append(
            f'<h3 class="wgroup">{icono} {esc(key)} · {len(items)} programa{plural}'
            f' · {run} en ejecución</h3>'
            f'<div class="wgrid">{cards}</div>')

    err = (f'<div class="empty" style="color:var(--red)">supervisord no responde: '
           f'{esc(status.get("supervisor_error"))}</div>'
           if status.get("supervisor_error") else "")

    otros = [e for e in programs if e["name"] != "status_web"]
    if not otros:
        hint = ('<div class="empty">No hay programas en '
                '<code>docker/conf.d.available/</code>.</div>')
    elif not [e for e in otros if e.get("enabled")]:
        hint = (f'<div class="empty" style="color:var(--yellow)">'
                f'🔸 Ningún worker activo todavía — {len(otros)} en standby. '
                f'Pulsa <b>Activar</b> en la tarjeta del worker que quieras encender.</div>')
    else:
        hint = ""

    t = status["totals"]
    cards = f"""
  <div class="cards">
    <div class="card green"><div class="n">{t['running']}</div>
      <div class="l">En ejecución</div></div>
    <div class="card gray"><div class="n">{t['stopped']}</div>
      <div class="l">Detenidos / no habilitados</div></div>
    <div class="card orange"><div class="n">{t['enabled']}</div>
      <div class="l">Habilitados</div></div>
    <div class="card blue"><div class="n">{t['available']}</div>
      <div class="l">Disponibles en el repo</div></div>
  </div>"""

    body = f"{err}{cards}{''.join(secciones)}{hint}"
    conn = "offline" if status.get("supervisor_error") else "online"
    return page("workers", body, user=user, flash_ok=flash_ok,
                flash_err=flash_err,
                subtitle=f"zona horaria {esc(status['timezone'])} · datos en "
                         f"<code>{esc(status['data_dir'])}</code> · "
                         f"actualizado {esc(status['now'])}",
                refresh=15, conn=conn)


def logs_page(name, text, error, user):
    """Página de logs en bruto de un programa."""
    if error:
        content = f'<div class="empty">⚠️ {esc(error)}</div>'
    else:
        content = (f'<pre class="log">{html.escape(text) or "(log vacío)"}</pre>')
    body = f"""
  <div class="panel">
    <h2>📄 Logs · {esc(name)}
      <a class="btn sm ghost" style="float:right" href="/">← Volver</a></h2>
    {content}
  </div>"""
    return page("workers", body, user=user,
                subtitle=f"últimas líneas de <code>{esc(name)}.log</code>")
