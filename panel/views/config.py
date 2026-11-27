"""
panel.views.config — Pestaña "⚙️ Configuración" del panel
=========================================================
Una tarjeta por worker con todos sus parámetros editables, agrupados por
origen (variables de entorno del conf / claves de HUB_Config / constantes del
código). Un formulario por worker → POST /configuracion/<worker>.

La tarjeta es la unidad de contenido (patrón Ajustes de iOS): cabecera con
icono, nombre y estado; secciones por origen; un solo botón primario para
guardar. Los campos no cambian de nombre ni de tipo: el POST es el mismo.
"""
from .. import db, envconf, spec, workers
from ..icons import icono
from ..templates import ST_TAG, esc, page

# Token CSRF de la petición actual (lo fija render() antes del HTML).
_CSRF = {"token": ""}

# Orden de las secciones dentro de la tarjeta y su etiqueta visible.
ORIGENES = (
    ("env", "ENTORNO"),
    ("hub_config", "HUB_CONFIG"),
    ("const", "CÓDIGO"),
    ("info", "INFO"),
)


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _tag(origen):
    """De dónde sale el valor. Usa los .badge del contrato, como ST_TAG."""
    return {"env": ("badge-info", "ENTORNO"),
            "hub_config": ("badge-info", "HUB_CONFIG"),
            "const": ("badge-warning", "CÓDIGO"),
            "info": ("badge-warning", "INFO")}.get(origen, ("badge-info", origen))


def _placeholder_secret(has_value):
    return "(guardado)" if has_value else "(sin definir)"


def _field_html(f, env_vals, cfg_vals, worker):
    """Input/read-only de un campo según su tipo y el valor vigente."""
    tipo, origen, fid = f["tipo"], f["origen"], f["id"]
    tag_cls, tag_txt = _tag(origen)

    if tipo == "info" or origen == "info":
        value = esc(f.get("valor", ""))
        return (f'<div class="field cfg-field"><label>{esc(f["label"])} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
                f'<div class="ro">{value}</div></div>')

    if tipo == "const" or origen == "const":
        return (f'<div class="field cfg-field"><label>{esc(f["label"])} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
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
        return (f'<div class="field cfg-field"><label>{esc(f["label"])} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
                f'<div class="ro">{esc(shown) or "—"}</div>'
                f'<div class="help">{esc(f.get("ayuda", ""))}</div></div>')

    help_html = (f'<div class="help">{esc(f["ayuda"])}</div>'
                 if f.get("ayuda") else "")
    unit = (f' <span class="muted peso-400">({esc(f["unidad"])})</span>'
            if f.get("unidad") else "")

    if tipo == "secret":
        # El valor nunca viaja al navegador: en blanco = no cambiar.
        return (f'<div class="field cfg-field"><label>{esc(f["label"])}{unit} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
                f'<input type="password" name="{esc(fid)}" value="" '
                f'autocomplete="new-password" spellcheck="false" '
                f'placeholder="{esc(_placeholder_secret(has_value))}" class="input">'
                f'{help_html}</div>')

    if tipo == "bool":
        val = str(shown if shown in ("0", "1") else f.get("default", "1"))
        opts = "".join(
            f'<option value="{v}"{" selected" if val == v else ""}>{lbl}</option>'
            for v, lbl in (("1", "Sí"), ("0", "No")))
        return (f'<div class="field cfg-field"><label>{esc(f["label"])} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
                f'<select class="input" name="{esc(fid)}">{opts}</select>{help_html}</div>')

    if tipo in ("number", "hour", "minute"):
        attrs = ""
        for bound in ("min", "max"):
            if f.get(bound) is not None:
                attrs += f' {bound}="{int(f[bound])}"'
        return (f'<div class="field cfg-field"><label>{esc(f["label"])}{unit} '
                f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
                f'<input type="number" inputmode="numeric" name="{esc(fid)}" '
                f'value="{esc(shown)}"{attrs} class="input">{help_html}</div>')

    return (f'<div class="field cfg-field"><label>{esc(f["label"])} '
            f'<span class="badge {tag_cls}">{tag_txt}</span></label>'
            f'<input type="text" name="{esc(fid)}" value="{esc(shown)}" class="input">'
            f'{help_html}</div>')


def _secciones(fields, env_vals, cfg_vals, worker):
    """Campos agrupados por origen, en el orden de ORIGENES."""
    bloques = []
    for origen, titulo in ORIGENES:
        grupo = [f for f in fields if f.get("origen") == origen]
        if not grupo:
            continue
        campos = "".join(_field_html(f, env_vals, cfg_vals, worker) for f in grupo)
        bloques.append(f'<h3 class="wgroup">{esc(titulo)}</h3>'
                       f'<div class="grid2">{campos}</div>')
    return "".join(bloques)


def _card(name, estado, fields, env_vals, cfg_vals):
    """Tarjeta de un worker: cabecera + formulario con todos sus campos."""
    cls, label = ST_TAG.get(estado, ("badge-info", estado or "SIN ESTADO"))

    test_form = ""
    if any(f.get("test") for f in fields):
        # Va FUERA del formulario principal: HTML no permite formularios anidados
        test_form = (f'<form class="inline" method="post" '
                     f'action="/configuracion/{esc(name)}/probar">'
                     f'{_csrf_field()}'
                     f'<button class="btn btn--quiet" type="submit">'
                     f'{icono("play", 18)}<span>Probar</span></button>'
                     f'</form>')

    logs = (f'<a class="btn btn--sm btn--quiet" href="/workers/{esc(name)}/logs">'
            f'{icono("logs", 18)}<span>Logs</span></a>'
            if workers.log_path(name) else "")

    # Qué hace el worker (mismo texto que la pestaña Workers)
    desc = workers.CATALOGO.get(name, {}).get("descripcion", "")
    desc_html = (f'<p class="desc">{esc(desc)}</p>' if desc else "")

    hint = ""
    env_fields = [f for f in fields if f["origen"] == "env"]
    if env_fields:
        hint = ('<p class="help">Los cambios de <b>ENTORNO</b> se guardan en '
                'el conf y se <b>reaplican en cada arranque</b> (volumen /data). '
                'Si el worker está corriendo se reinicia al guardar.</p>')
    elif any(f["origen"] == "hub_config" for f in fields):
        hint = ('<p class="help">Los cambios de <b>HUB_CONFIG</b> van a la '
                'misma tabla que edita el HUB y aplican <b>de inmediato</b> '
                '(sin reiniciar).</p>')

    editable = [f for f in fields
                if f["origen"] in ("env", "hub_config")
                and f["tipo"] not in ("readonly", "info", "const")]
    if editable:
        inner = (f'<form method="post" action="/configuracion/{esc(name)}">'
                 f'{_csrf_field()}'
                 f'{_secciones(fields, env_vals, cfg_vals, name)}'
                 f'<div class="actions">'
                 f'<button class="btn btn--primary" type="submit">'
                 f'{icono("check", 18)}<span>Guardar</span></button>'
                 f'</div></form>')
    else:
        inner = _secciones(fields, env_vals, cfg_vals, name)

    acciones = f'<div class="actions">{test_form}{logs}</div>' if (test_form or logs) else ""

    return f"""
  <div class="card" id="cfg-{esc(name)}">
    <div class="wcard-top">
      <div class="wcard-name">{icono("settings", 20)} {esc(name)}</div>
      <span class="badge {cls}">{esc(label)}</span>
    </div>
    {desc_html}
    {inner}
    {acciones}
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
  <div class="grid">
    <div class="kpi"><span class="kpi__label">Variables de entorno</span>
      <span class="kpi__value">{n_env}</span></div>
    <div class="kpi"><span class="kpi__label">Claves HUB_Config</span>
      <span class="kpi__value">{n_cfg}</span></div>
    <div class="kpi"><span class="kpi__label">Constantes / notas</span>
      <span class="kpi__value">{n_const}</span></div>
    <div class="kpi"><span class="kpi__label">Workers con ajustes guardados</span>
      <span class="kpi__value">{len(overrides)}</span></div>
  </div>"""

    intro = ('<p class="desc">Misma configuración que el HUB, centralizada por '
             'worker: <span class="badge badge-info">HUB_CONFIG</span> aplica al '
             'instante desde SQL, <span class="badge badge-info">ENTORNO</span> se '
             'persiste en el volumen y <span class="badge badge-warning">CÓDIGO</span> '
             'es solo lectura (la fuente de verdad es el repo HUB).</p>')

    body = kpis + intro + "".join(cards)
    return page("config", body, user=user, flash_ok=flash_ok, flash_err=flash_err,
                lugar=lugar, ip=ip,
                subtitle=f"{len(spec.all_workers())} workers con catálogo "
                         f"de configuración",
                refresh=0)
