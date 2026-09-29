"""
panel.views.workers — Módulos "📊 Estado" y "📄 Logs" del panel

Estado: tarjetas con el estado de cada worker y sus acciones (activar /
deshabilitar / reiniciar) + acceso al log.
Logs: la salida reciente de TODOS los workers junto, con los errores
resaltados, para no tener que recorrer todas las tarjetas para encontrar el
que falló.
"""
import html
import re

from .. import workers
from ..templates import ST_TAG, esc, page

# tail_log vive en workers.py; se expose acá para el módulo de Logs.
tail_log = workers.tail_log


# Token CSRF de la petición actual (lo fija render() antes de construir la tabla).
_CSRF = {"token": ""}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _actions(e):
    """Botones disponibles para un programa según su estado (≥44px de alto)."""
    if e.get("protected"):
        # Badge directo del .wcard (no dentro de .actions): así no se estira a
        # todo el ancho de la tarjeta y no parece un botón.
        return '<span class="badge badge-info">🖥️ Este es el panel</span>'
    buttons = []
    if e.get("enabled"):
        name = esc(e["name"])
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/restart">'
            f'{_csrf_field()}'
            f'<button class="btn btn-sm btn-secondary" type="submit">↺ Reiniciar</button></form>')
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/disable" '
            f'onsubmit="return confirm(\'¿Deshabilitar {name}?\')">'
            f'{_csrf_field()}'
            f'<button class="btn btn-sm btn-warning" type="submit">■ Deshabilitar</button></form>')
    else:
        name = esc(e["name"])
        buttons.append(
            f'<form class="inline" method="post" action="/workers/{name}/enable">'
            f'{_csrf_field()}'
            f'<button class="btn btn-sm btn-success" type="submit">▶ Activar</button></form>')
    logs = (f'<a class="btn btn-sm btn-secondary" href="/workers/{esc(e["name"])}/logs">📄 Logs</a>'
            if e.get("log_ok") else "")
    cfg = (f'<a class="btn btn-sm btn-secondary" href="/configuracion#cfg-{esc(e["name"])}">'
           f'⚙️ Config</a>')
    return '<div class="actions">' + "".join(buttons) + cfg + logs + "</div>"


# Orden de los grupos de tarjetas: panel, luego HUB, Field y el resto.
_GROUP_ORDER = {"workersadmon": 0, "HUB": 1, "Field": 2, "HUB / Field": 3}
_GROUP_ICON = {"workersadmon": "🖥️", "HUB": "🏢", "Field": "📱", "HUB / Field": "🔗"}


def _card(e):
    """Tarjeta de un programa: legible en el iPhone sin scroll horizontal."""
    cls, label = ST_TAG.get(e["state"], ("badge-info", e["state"]))
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
    chips.append('<span class="badge badge-info">HABILITADO</span>' if e.get("enabled")
                 else '<span class="badge badge-info">NO HABILITADO</span>')

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
    <span class="badge {cls}">{esc(label)}</span>
  </div>
  <div class="wcard-meta">{''.join(chips)}</div>
  <div class="wcard-facts">{''.join(facts)}</div>
  {desc}
  {_actions(e)}
</article>"""


def render(status, user, flash_ok="", flash_err="", csrf="",
           lugar="desconocido", ip=""):
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

    # Estado del shell (lo escribe el chequeo diario: shell/tools/check_daily.py)
    # OJO con las etiquetas: shell_version es VERSION del shell y app_version es
    # ECCSA_SHELL_VERSION, la COPIA que trae este repo. Decir "app" junto al
    # número del shell hacía parecer que el panel era una 1.10.x.
    chk = workers.shell_check()
    if chk:
        if chk.get("ok"):
            shell_html = (
                f'<div class="callout callout-row ok">'
                f'<span class="badge badge-success">● SHELL AL DÍA</span>'
                f'<span class="muted" style="font-size:.8rem">'
                f'shell {esc(chk.get("shell_version", "?"))} · copia '
                f'{esc(chk.get("app_version", "?"))} · revisado '
                f'{esc(chk.get("revisado", "?"))}</span></div>')
        else:
            probs = "<br>".join(esc(p) for p in (chk.get("problemas") or []))
            shell_html = (
                f'<div class="callout bad">'
                f'<span class="badge badge-danger">▲ SHELL DESINCRONIZADO</span>'
                f'<div class="muted" style="font-size:.8rem;margin-top:6px">'
                f'{probs}<br>Revisado: {esc(chk.get("revisado", "?"))} · '
                f'corrige con: python tools/sync_shell.py --all</div></div>')
    else:
        shell_html = (
            '<div class="card-desc" style="font-size:.8rem">ℹ️ Sin datos del '
            'chequeo del shell todavía (se genera diario en ServerVM).</div>')

    # Estado de versiones de las 3 apps contra el mandato de Field.
    # Mismo chequeo diario que el shell: si esto se pone rojo, alguien instaló
    # algo distinto a lo que dice ECCSA-Shell/versiones/requisitos-canonicos.txt.
    vchk = workers.versiones_check()
    if vchk:
        resumen = []
        for app in ("field", "admon", "workersadmon"):
            filas = (vchk.get("apps") or {}).get(app) or {}
            if filas:
                ok = sum(1 for f in filas.values() if f.get("ok"))
                resumen.append(f"{app} {ok}/{len(filas)}")
        detalle = " · ".join(resumen)
        if vchk.get("ok"):
            versiones_html = (
                f'<div class="callout callout-row ok">'
                f'<span class="badge badge-success">● VERSIONES AL DÍA</span>'
                f'<span class="muted" style="font-size:.8rem">'
                f'{esc(detalle)} · revisado '
                f'{esc(vchk.get("revisado", "?"))}</span></div>')
        else:
            probs_v = "<br>".join(esc(p) for p in (vchk.get("problemas") or []))
            versiones_html = (
                f'<div class="callout bad">'
                f'<span class="badge badge-danger">▲ VERSIONES DESINCRONIZADAS</span>'
                f'<div class="muted" style="font-size:.8rem;margin-top:6px">'
                f'{probs_v}<br>Mandato: ECCSA-Shell/versiones/'
                f'requisitos-canonicos.txt (= field/api/requirements.txt). '
                f'Revisado: {esc(vchk.get("revisado", "?"))}</div></div>')
    else:
        versiones_html = (
            '<div class="card-desc" style="font-size:.8rem">ℹ️ Sin datos del '
            'chequeo de versiones todavía (se genera diario en ServerVM).</div>')

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

    body = f"{err}{shell_html}{versiones_html}{cards}{''.join(secciones)}{hint}"
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


def logs_page(name, text, error, user):
    """Página de logs en bruto de un programa."""
    if error:
        content = f'<div class="empty">⚠️ {esc(error)}</div>'
    else:
        content = (f'<pre class="log">{html.escape(text) or "(log vacío)"}</pre>')
    body = f"""
  <div class="panel">
    <h2><span class="panel-titulo">📄 Logs · {esc(name)}</span></h2>
    {content}
  </div>"""
    # Vuelve a Estado, no a la home: el log es una ficha de un worker y el
    # usuario viene de ahí. La flecha sin texto que había en el <h2> la reemplaza
    # el botón de volver que lleva todo módulo.
    return page("estado", body, user=user, back_href="/estado",
                back_label="← Volver a Estado",
                subtitle=f"últimas líneas de <code>{esc(name)}.log</code>")


# ── Módulo de Logs ────────────────────────────────────────────────────────────
# Antes los logs solo se veían entrando al worker uno por uno
# (/workers/<nombre>/logs). Este módulo los junta: primero se ven los errores
# de todos, y desde ahí se baja al log completo de cada uno. Es la respuesta a
# "algo falló, ¿de quién?" sin tener que recorrer 14 tarjetas.
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


def logs_index(status, user, flash_ok="", flash_err="", csrf="",
               lugar="desconocido", ip=""):
    """Módulo Logs: un bloque por worker con su log y los errores marcados."""
    set_csrf(csrf)
    lineas_por_defecto = 120
    bloques = []
    con_error = 0
    for e in sorted(status["programs"], key=lambda x: x.get("name", "")):
        nombre = e.get("name", "")
        texto, error = tail_log(nombre, lineas_por_defecto)
        if error:
            # Una línea compacta, no un .empty (padding de 3rem): con varios
            # workers sin log la página quedaba hecha de cajas vacías de
            # ~110px. El nombre va igual que en los bloques que sí tienen log
            # (wcard-name), no en versalitas de encabezado de sección.
            bloques.append(
                f'<div class="card"><div class="wcard-name">{esc(nombre)}</div>'
                f'<div class="muted" style="font-size:.85rem">⚠️ {esc(error)}'
                f'</div></div>')
            continue
        cox = _error_resaltes(texto)
        if cox:
            con_error += 1
        bloques.append(_log_bloque(nombre, texto, cox, e))

    if not bloques:
        cuerpo = '<div class="empty">Todavía no hay workers en el contenedor.</div>'
    else:
        cuerpo = "".join(bloques)

    aviso = ""
    if con_error:
        aviso = (f'<div class="flash err">⚠️ {con_error} worker'
                 f'{"s" if con_error != 1 else ""} '
                 f'tiene salida que parece un error. Mirá los bloques marcados.</div>')

    body = f"""
  {aviso}
  <div class="wgroup">Salida reciente por worker</div>
  {cuerpo}"""
    return page("logs", body, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip,
                subtitle="últimas líneas de cada worker · tocá uno para el log completo")


def _log_bloque(nombre, texto, errores, e):
    """Bloque de un worker: resumen + errores + enlace al log completo."""
    estado = e.get("state", "?")
    clase = {"RUNNING": "badge-success", "STOPPED": "badge-warning",
             "FATAL": "badge-danger", "EXITED": "badge-danger",
             "STARTING": "badge-info"}.get(estado, "badge-info")
    if errores:
        lista = "".join(f"<li>{esc(x)}</li>" for x in errores)
        bloque_err = f'<ul class="shell-modal-list">{lista}</ul>'
    else:
        bloque_err = '<div class="empty">Sin errores en las últimas líneas.</div>'
    cola = "\n".join((texto or "").splitlines()[-12:])
    return f"""
  <div class="card">
    <div class="wcard-top">
      <div class="wcard-name">{esc(nombre)}</div>
      <div class="wcard-meta">
        <span class="badge {clase}">{esc(estado)}</span>
        <a class="btn btn-sm btn-secondary" href="/workers/{esc(nombre)}/logs">
          Ver completo</a>
      </div>
    </div>
    {bloque_err}
    <pre class="log">{html.escape(cola) or "(log vacío)"}</pre>
  </div>"""
