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
    """Botones disponibles para un programa según su estado."""
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
    return '<div class="actions">' + "".join(buttons) + "</div>"


def render(status, user, flash_ok="", flash_err="", csrf=""):
    """Devuelve el HTML completo de la pestaña Workers."""
    set_csrf(csrf)
    rows = []
    for e in status["programs"]:
        cls, label = ST_TAG.get(e["state"], ("t-off", e["state"]))
        enabled = ('<span class="tag t-yes">SÍ</span>' if e.get("enabled")
                   else '<span class="tag t-off">NO</span>')
        started = (f'<span class="mono">{esc(e.get("started_at"))}</span><br>'
                   f'<span class="muted">{workers.rel_time(workers.parse_dt(e.get("started_at")))}</span>'
                   if e.get("started_at") else '<span class="muted">—</span>')
        if e.get("last_run"):
            last = (f'<span class="mono">{esc(e["last_run"])}</span><br>'
                    f'<span class="muted">{workers.rel_time(workers.parse_dt(e["last_run"]))}</span>')
        else:
            last = '<span class="muted">sin registros</span>'
        detail = esc(e.get("detail") or "")
        if e.get("count") is not None:
            detail = (f'{detail}<br><span class="muted">registros: {e["count"]}</span>'
                      if detail else f'<span class="muted">registros: {e["count"]}</span>')
        meta_bits = []
        if e.get("cadencia"):
            meta_bits.append(f'<span class="tag t-off">{esc(e["cadencia"])}</span>')
        if e.get("app"):
            meta_bits.append(f'<span class="tag t-yes">{esc(e["app"])}</span>')
        desc_html = ""
        if e.get("desc"):
            desc_html = ('<div class="muted" style="font-weight:400;font-size:.82rem">'
                         + esc(e["desc"]) + "</div>")
        # Párrafo "qué hace" (plegado; sin JS, con <details> del navegador)
        if e.get("descripcion"):
            desc_html += (f'<details class="wdesc"><summary>ℹ️ Qué hace</summary>'
                          f'<div>{esc(e["descripcion"])}</div></details>')
        meta_html = " ".join(meta_bits)
        logs = (f'<a class="btn log" href="/workers/{esc(e["name"])}/logs">📄 Logs</a>'
                if e.get("log_ok") else "")
        cfg = (f'<a class="btn ghost" href="/configuracion#cfg-{esc(e["name"])}" '
               f'title="Configuración de {esc(e["name"])}">⚙️ Config</a>')
        rows.append(f"""<tr>
  <td class="name">{esc(e['name'])}
    {desc_html}
    <div style="margin-top:6px">{meta_html}</div></td>
  <td><span class="tag {cls}">{label}</span></td>
  <td>{enabled}</td>
  <td>{started}</td>
  <td>{last}</td>
  <td class="actions-cell">{_actions(e)}</td>
  <td class="hide-sm">{cfg} {logs}</td>
</tr>""")

    body_rows = "\n".join(rows) if rows else ""
    err = (f'<div class="empty" style="color:var(--red)">supervisord no responde: '
           f'{esc(status.get("supervisor_error"))}</div>'
           if status.get("supervisor_error") else "")

    otros = [e for e in status["programs"] if e["name"] != "status_web"]
    if not otros:
        hint = ('<div class="empty">No hay programas en '
                 '<code>docker/conf.d.available/</code>.</div>')
    elif not [e for e in otros if e.get("enabled")]:
        hint = (f'<div class="empty" style="color:var(--yellow)">'
                f'🔸 Ningún worker activo todavía — {len(otros)} en standby. '
                f'Pulsa <b>Activar</b> en la fila del worker que quieras encender.</div>')
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

    table = f"""
  {err}
  <div class="panel">
    <h2>Programas del contenedor</h2>
    <table>
      <thead><tr>
        <th>Programa</th><th>Estado</th><th>Habilitado</th>
        <th>Activo desde</th><th>Última ejecución</th>
        <th>Acciones</th><th class="hide-sm">Logs</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
    {hint}
  </div>"""

    body = cards + table
    return page("workers", body, user=user, flash_ok=flash_ok,
                flash_err=flash_err,
                subtitle=f"zona horaria {esc(status['timezone'])} · datos en "
                         f"<code>{esc(status['data_dir'])}</code> · "
                         f"actualizado {esc(status['now'])}",
                refresh=15)


def logs_page(name, text, error, user):
    """Página de logs en bruto de un programa."""
    if error:
        content = f'<div class="empty">⚠️ {esc(error)}</div>'
    else:
        content = (f'<pre class="log">{html.escape(text) or "(log vacío)"}</pre>')
    body = f"""
  <div class="panel">
    <h2>📄 Logs · {esc(name)}
      <a class="btn ghost" style="float:right" href="/">← Volver</a></h2>
    {content}
  </div>"""
    return page("workers", body, user=user,
                subtitle=f"últimas líneas de <code>{esc(name)}.log</code>")
