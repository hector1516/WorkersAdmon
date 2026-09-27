"""
panel.views.config — Pestaña "⚙️ Configuración" del panel
=========================================================
Una tarjeta por worker con todos sus parámetros editables, agrupados por
origen (variables de entorno del conf / claves de HUB_Config / constantes del
código). Un formulario por worker → POST /configuracion/<worker>.
"""
from .. import db, envconf, spec, workers
from ..templates import ST_TAG, esc, page

# Token CSRF de la petición actual (lo fija render() antes del HTML).
_CSRF = {"token": ""}


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _tag(origen):
    return {"env": ('t-off', "ENTORNO"),
            "hub_config": ('t-yes', "HUB_CONFIG"),
            "const": ('t-stop', "CÓDIGO"),
            "info": ('t-stop', "INFO")}.get(origen, ("t-off", origen))


def _placeholder_secret(has_value):
    return "(guardado)" if has_value else "(sin definir)"


def _field_html(f, env_vals, cfg_vals, worker):
    """Input/read-only de un campo según su tipo y el valor vigente."""
    tipo, origen, fid = f["tipo"], f["origen"], f["id"]
    tag_cls, tag_txt = _tag(origen)

    if tipo == "info" or origen == "info":
        value = esc(f.get("valor", ""))
        return (f'<div class="cfg-field"><label>{esc(f["label"])} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<div class="ro">{value}</div></div>')

    if tipo == "const" or origen == "const":
        return (f'<div class="cfg-field"><label>{esc(f["label"])} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<div class="ro">{esc(f.get("valor", ""))}</div>'
                f'<div class="help">{esc(f.get("ayuda", ""))}</div></div>')

    # ── valor vigente ────────────────────────────────────────────────────────
    if origen == "env":
        current = env_vals.get(fid)
        default = f.get("default")
        has_value = current is not None
        shown = current if has_value else ("" if default is None else str(default))
    else:                                   # hub_config
        clave = f.get("clave") or fid
        current = cfg_vals.get(clave, "")
        has_value = bool(current)
        shown = current if has_value else str(f.get("default", ""))

    if tipo == "readonly":
        return (f'<div class="cfg-field"><label>{esc(f["label"])} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<div class="ro">{esc(shown) or "—"}</div>'
                f'<div class="help">{esc(f.get("ayuda", ""))}</div></div>')

    help_html = (f'<div class="help">{esc(f["ayuda"])}</div>'
                 if f.get("ayuda") else "")
    unit = (f' <span class="muted" style="font-weight:400">({esc(f["unidad"])})</span>'
            if f.get("unidad") else "")

    if tipo == "secret":
        # El valor nunca viaja al navegador: en blanco = no cambiar.
        return (f'<div class="cfg-field"><label>{esc(f["label"])}{unit} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<input type="password" name="{esc(fid)}" value="" '
                f'autocomplete="new-password" '
                f'placeholder="{esc(_placeholder_secret(has_value))}">'
                f'{help_html}</div>')

    if tipo == "bool":
        val = str(shown if shown in ("0", "1") else f.get("default", "1"))
        opts = "".join(
            f'<option value="{v}"{" selected" if val == v else ""}>{lbl}</option>'
            for v, lbl in (("1", "Sí"), ("0", "No")))
        return (f'<div class="cfg-field"><label>{esc(f["label"])} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<select name="{esc(fid)}">{opts}</select>{help_html}</div>')

    if tipo in ("number", "hour", "minute"):
        attrs = ""
        for bound in ("min", "max"):
            if f.get(bound) is not None:
                attrs += f' {bound}="{int(f[bound])}"'
        return (f'<div class="cfg-field"><label>{esc(f["label"])}{unit} '
                f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
                f'<input type="number" name="{esc(fid)}" value="{esc(shown)}"'
                f'{attrs}>{help_html}</div>')

    return (f'<div class="cfg-field"><label>{esc(f["label"])} '
            f'<span class="tag {tag_cls}">{tag_txt}</span></label>'
            f'<input type="text" name="{esc(fid)}" value="{esc(shown)}">'
            f'{help_html}</div>')


def _card(name, estado, fields, env_vals, cfg_vals):
    """Tarjeta de un worker: cabecera + formulario con todos sus campos."""
    cls, label = ST_TAG.get(estado, ("t-off", estado or "SIN ESTADO"))
    fields_html = "".join(_field_html(f, env_vals, cfg_vals, name) for f in fields)

    test_form = ""
    if any(f.get("test") for f in fields):
        # Va FUERA del formulario principal: HTML no permite formularios anidados
        test_form = ('<div class="cfg-save">'
                     f'<form class="inline" method="post" '
                     f'action="/configuracion/{esc(name)}/probar">'
                     f'{_csrf_field()}'
                     f'<button class="btn btn-secondary" type="submit">🧪 Probar</button>'
                     f'</form></div>')

    logs = (f'<a class="btn btn-secondary" href="/workers/{esc(name)}/logs">📄 Logs</a>'
            if workers.log_path(name) else "")

    # Qué hace el worker (misimo texto que la pestaña Workers)
    desc = workers.CATALOGO.get(name, {}).get("descripcion", "")
    desc_html = (f'<div class="card-desc">{esc(desc)}</div>' if desc else "")

    hint = ""
    env_fields = [f for f in fields if f["origen"] == "env"]
    if env_fields:
        hint = ('<div class="cfg-note">Los cambios de <b>ENTORNO</b> se guardan en '
                'el conf y se <b>reaplican en cada arranque</b> (volumen /data). '
                'Si el worker está corriendo se reinicia al guardar.</div>')
    elif any(f["origen"] == "hub_config" for f in fields):
        hint = ('<div class="cfg-note">Los cambios de <b>HUB_CONFIG</b> van a la '
                'misma tabla que edita el HUB y aplican <b>de inmediato</b> '
                '(sin reiniciar).</div>')

    editable = [f for f in fields
                if f["origen"] in ("env", "hub_config")
                and f["tipo"] not in ("readonly", "info", "const")]
    if editable:
        save_bar = ('<div class="cfg-save">'
                    '<button class="btn btn-success" type="submit">💾 Guardar</button></div>')
        inner = (f'<form method="post" action="/configuracion/{esc(name)}">'
                 f'{_csrf_field()}'
                 f'<div class="cfg-grid">{fields_html}</div>{save_bar}</form>')
    else:
        inner = f'<div class="cfg-grid">{fields_html}</div>'

    return f"""
  <div class="panel" id="cfg-{esc(name)}">
    <h2><code>{esc(name)}</code>
      <span class="tag {cls}">{esc(label)}</span>
      <span style="float:right">{logs}</span></h2>
    {desc_html}
    {inner}
    {test_form}
    {hint}
  </div>"""


def render(status, user, flash_ok="", flash_err="", csrf="", lugar="desconocido", ip=""):
    """Devuelve el HTML completo de la pestaña Configuración."""
    set_csrf(csrf)
    progs = {p["name"]: p for p in status.get("programs", [])}

    # Valores vigentes: variables de entorno (confs) y HUB_Config (SQL)
    env_vals = {w: envconf.read_env(w) for w in spec.all_workers()}
    cfg_claves = [c for w in spec.all_workers() for c in spec.hub_config_keys(w)]
    cfg_vals = db.get_config_values(cfg_claves)     # {} si la BD no responde

    cards = []
    for name in spec.all_workers():
        fields = spec.spec_for(name)
        estado = (progs.get(name) or {}).get("state", "NO_HABILITADO")
        cards.append(_card(name, estado, fields, env_vals.get(name, {}), cfg_vals))

    n_env = sum(1 for w in spec.all_workers() for f in spec.spec_for(w)
                if f["origen"] == "env")
    n_cfg = sum(1 for w in spec.all_workers() for f in spec.spec_for(w)
                if f["origen"] == "hub_config")
    n_const = sum(1 for w in spec.all_workers() for f in spec.spec_for(w)
                  if f["origen"] in ("const", "info"))
    overrides = envconf.load_overrides()

    kpis = f"""
  <div class="cards">
    <div class="card blue"><div class="n">{n_env}</div>
      <div class="l">Variables de entorno</div></div>
    <div class="card green"><div class="n">{n_cfg}</div>
      <div class="l">Claves HUB_Config</div></div>
    <div class="card gray"><div class="n">{n_const}</div>
      <div class="l">Constantes / notas</div></div>
    <div class="card orange"><div class="n">{len(overrides)}</div>
      <div class="l">Workers con ajustes guardados</div></div>
  </div>"""

    intro = ('<div class="empty" style="text-align:left;padding-top:0">'
             'Misma configuración que el HUB, centralizada por worker: '
             '<span class="tag t-yes">HUB_CONFIG</span> aplica al instante desde SQL, '
             '<span class="tag t-off">ENTORNO</span> se persiste en el volumen y '
             '<span class="tag t-stop">CÓDIGO</span> es solo lectura '
             '(la fuente de verdad es el repo HUB).</div>')

    body = kpis + intro + "".join(cards)
    return page("config", body, user=user, flash_ok=flash_ok, flash_err=flash_err,
                lugar=lugar, ip=ip,
                subtitle=f"{len(spec.all_workers())} workers con catálogo "
                         f"de configuración",
                refresh=0)
