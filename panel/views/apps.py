"""
panel.views.apps — Pestaña "⚙️ Apps" del panel (Fase D)
========================================================
Catálogo de claves de configuración **agrupadas por aplicación** (HUB, Field,
admon y cualquier futura). Los METADATOS viven en `HUB_ConfigCatalogo`
(app, título, tipo, unidad, orden, descripción) y el VALOR sigue en
`HUB_Config`, así que un cambio hecho aquí lo ve al instante la app que lo
lee (no hay que reiniciar nada).

Tres bloques por página, cada uno con su propia tarjeta:

  Apps               una tarjeta por app con sus claves y un guardado por bloque
  Claves sin clasificar  las que ya existen en HUB_Config y no están en el catálogo
  Nueva clave        alta (crea la app si no existe)

Reglas de UI/seguridad:
  * Un SOLO formulario por bloque (HTML no admite formularios anidados); los
    botones se distinguen por su `name` y el servidor hace el dispatch.
  * Los valores de tipo `secret` nunca se pintan: el campo va en blanco con
    el marcador "(guardado)" y un valor vacío significa "no cambiar".
  * Las claves sin clasificar no muestran su valor (solo "(vacío)"/"(con
    valor)"): no sabemos todavía si son secretas.
  * La bitácora registra CLAVES, jamás valores.
"""
from .. import db
from ..icons import icono
from ..templates import esc, page

_CSRF = {"token": ""}

ETIQUETA_TIPO = {
    "text": "TEXTO", "secret": "SECRETO", "number": "NÚMERO",
    "bool": "SÍ/NO", "readonly": "SOLO LECTURA",
}
MAX_GRUPO = 300        # filas mostradas por grupo (tope de rendimiento)


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return '<input type="hidden" name="csrf" value="' + esc(_CSRF["token"]) + '">'


def _select_tipo(name, actual, con_vacio=False):
    """<select class="input"> de tipo de valor. Si con_vacio, agrega la opción '(elegir)'."""
    opciones = [("", "(elegir)")] if con_vacio else []
    opciones += [(t, ETIQUETA_TIPO[t]) for t in db.TIPOS_APP]
    opts = "".join(
        f'<option value="{v}"{" selected" if v == (actual or "") else ""}>{l}</option>'
        for v, l in opciones)
    # sel-tipo: ancho mínimo para que la celda no corte la etiqueta elegida
    # ("TEXTO"/"SOLO"/"SECR" se cortaban porque el ancho mínimo de un select
    # lo da la opción SELECCIONADA).
    return f'<select class="input sel-tipo" name="{esc(name)}">{opts}</select>'


def _campo_valor(tipo, valor, name):
    """Input de valor según el tipo; en los secretos el valor jamás se pinta."""
    if tipo == "readonly":
        return f'<div class="ro">{esc(valor) if valor else "—"}</div>'
    if tipo == "secret":
        marcador = "(guardado)" if valor else "(sin valor)"
        return (f'<input type="password" name="{esc(name)}" value="" '
                f'autocomplete="new-password" spellcheck="false" '
                f'placeholder="{marcador}" class="input">')
    if tipo == "bool":
        # Sin valor en HUB_Config se muestra "(sin valor)" para no inventar un "No"
        if not str(valor or "").strip():
            opts = ('<option value="" selected>(sin valor)</option>'
                    '<option value="1">Sí</option><option value="0">No</option>')
        else:
            val = "1" if str(valor) in ("1", "true", "True", "Sí", "si") else "0"
            opts = "".join(
                f'<option value="{v}"{" selected" if val == v else ""}>{l}</option>'
                for v, l in (("1", "Sí"), ("0", "No")))
        # sel-tipo: ancho mínimo para que la celda no corte la etiqueta elegida.
        return f'<select class="input sel-tipo" name="{esc(name)}">{opts}</select>'
    if tipo == "number":
        # inputmode: teclado numérico en el teléfono, no solo type="number".
        return (f'<input type="number" inputmode="numeric" name="{esc(name)}" '
                f'value="{esc(valor)}" class="input">')
    # class="input" es obligatorio: sin la clase el input toma el estilo por
    # defecto del navegador (caja blanca) y queda el único campo raro de la
    # tabla, al lado de los que sí la traen.
    return (f'<input type="text" name="{esc(name)}" '
            f'value="{esc(valor)}" class="input">')


def _fila_catalogo(f):
    """Fila editable de una clave ya clasificada."""
    fid = f["Id"]
    unidad = (f'<div class="muted" style="font-size:.72rem;font-weight:400">'
              f'{esc(f["Unidad"])}</div>' if f.get("Unidad") else "")
    return f"""<tr>
  <td><input type="text" name="t{fid}" value="{esc(f["Titulo"])}" maxlength="120" class="input"></td>
  <td><code>{esc(f["Clave"])}</code>{unidad}</td>
  <td class="vcol">{_campo_valor(f["Tipo"], f["Valor"], f"val{fid}")}</td>
  <td>{_select_tipo(f"tipo{fid}", f["Tipo"])}</td>
  <td class="hide-sm"><input type="text" name="d{fid}"
     value="{esc(f["Descripcion"])}" maxlength="400" class="input"></td>
  <td class="actions-cell"><div class="actions">
    <button class="btn btn--sm btn--quiet" type="submit" name="edit" value="{fid}"
            aria-label="Guardar esta fila" title="Guardar esta fila">{icono("check", 18)}</button>
    <button class="btn btn--sm btn--danger" type="submit" name="del" value="{fid}"
            aria-label="Quitar del catálogo" title="Quitar del catálogo"
            onclick="return confirm('¿Quitar la clave del catálogo? El valor en HUB_Config se conserva.')">{icono("trash", 18)}</button>
  </div></td>
</tr>"""


def _panel_app(app, filas):
    """Una tarjeta por app: sus claves y un solo guardado para el bloque."""
    filas = sorted(filas[:MAX_GRUPO], key=lambda f: (f.get("Orden") or 0, f["Clave"]))
    filas_html = "".join(_fila_catalogo(f) for f in filas)
    nota = (f'<p class="help">Los cambios van a <code>HUB_Config</code> y '
            f'aplican <b>de inmediato</b> en {esc(app)} (sin reiniciar). '
            f'Los valores <b>secretos</b> en blanco no se modifican.</p>')
    return f"""
  <div class="card" id="app-{esc(app)}">
    <div class="wcard-top">
      <div class="wcard-name">{icono("apps", 20)} {esc(app)}</div>
      <span class="badge badge-info">{len(filas)} claves</span>
    </div>
    <form method="post" action="/apps">
      {_csrf_field()}
      <input type="hidden" name="grupo" value="cat">
      <input type="hidden" name="app" value="{esc(app)}">
      <div class="table-wrap"><div class="tscroll"><table class="table apps-tabla">
        <thead><tr>
          <th>Título</th><th>Clave</th><th>Valor</th><th>Tipo</th>
          <th class="hide-sm">Descripción</th><th></th>
        </tr></thead>
        <tbody>{filas_html}</tbody>
      </table></div></div>
      <div class="actions">
        <button class="btn btn--primary" type="submit" name="save_all" value="1">
          {icono("check", 18)}<span>Guardar valores</span></button>
        <span class="muted" style="font-size:.78rem">Varios de una vez · «Quitar» solo quita el catálogo</span>
      </div>
    </form>
    {nota}
  </div>"""


def _panel_libres(libres, apps):
    """Claves de HUB_Config que todavía no están en el catálogo."""
    if not libres:
        return ""
    apps_opt = "".join(f'<option value="{esc(a)}">' for a in apps)
    filas = []
    for i, clave in enumerate(libres[:MAX_GRUPO]):
        filas.append(f"""<tr>
  <td><input type="hidden" name="k{i}" value="{esc(clave)}"><code>{esc(clave)}</code></td>
  <td><span class="badge badge-info">sin clasificar</span></td>
  <td><input type="text" name="app{i}" list="apps-list"
     placeholder="HUB / Field / admon" maxlength="40" class="input"></td>
  <td><input type="text" name="tit{i}" placeholder="Título visible"
     maxlength="120" class="input"></td>
  <td>{_select_tipo(f"tipo{i}", "text")}</td>
  <td class="actions-cell"><div class="actions">
    <button class="btn btn--sm btn--quiet" type="submit" name="clasificar" value="{i}"
            title="Pasar al catálogo de esa app">Clasificar</button>
  </div></td>
</tr>""")
    return f"""
  <div class="card" id="app-libres">
    <div class="wcard-top">
      <div class="wcard-name">{icono("folder", 20)} Sin clasificar</div>
      <span class="badge badge-info">{len(libres)} claves</span>
    </div>
    <form method="post" action="/apps">
      {_csrf_field()}
      <input type="hidden" name="grupo" value="libre">
      <datalist id="apps-list">{apps_opt}</datalist>
      <div class="table-wrap"><div class="tscroll"><table class="table">
        <thead><tr>
          <th>Clave</th><th>Estado</th><th>App</th><th>Título</th>
          <th>Tipo</th><th></th>
        </tr></thead>
        <tbody>{''.join(filas)}</tbody>
      </table></div></div>
      <p class="help">Sus valores no se muestran hasta que se clasifiquen
        (pueden ser secretas). «Clasificar» las mueve al catálogo de la app
        indicada.</p>
    </form>
  </div>"""


def _panel_alta(apps):
    """Alta de una clave nueva (y de la app si no existe)."""
    apps_opt = "".join(f'<option value="{esc(a)}">' for a in apps)
    return f"""
  <div class="card" id="app-alta">
    <div class="wcard-top">
      <div class="wcard-name">{icono("plus", 20)} Nueva clave</div>
    </div>
    <form method="post" action="/apps">
      {_csrf_field()}
      <input type="hidden" name="grupo" value="alta">
      <datalist id="apps-list-alta">{apps_opt}</datalist>
      <div class="grid2">
        <div class="field cfg-field"><label>App</label>
          <input type="text" name="app" list="apps-list-alta"
                 placeholder="HUB / Field / admon / …" maxlength="40" required class="input"></div>
        <div class="field cfg-field"><label>Clave (HUB_Config)</label>
          <input type="text" name="clave" placeholder="mi_clave" maxlength="50"
                 required class="input"></div>
        <div class="field cfg-field"><label>Título</label>
          <input type="text" name="titulo" maxlength="120" required class="input"></div>
        <div class="field cfg-field"><label>Tipo</label>
          {_select_tipo("tipo", "text", con_vacio=True)}</div>
        <div class="field cfg-field"><label>Unidad (opcional)</label>
          <input type="text" name="unidad" placeholder="seg / min / …"
                 maxlength="20" class="input"></div>
        <div class="field cfg-field"><label>Orden</label>
          <input type="number" inputmode="numeric" name="orden" value="100"
                 min="0" max="9999" class="input"></div>
        <div class="field cfg-field"><label>Valor inicial (opcional)</label>
          <input type="text" name="valor" maxlength="500" class="input"></div>
        <div class="field cfg-field"><label>Descripción</label>
          <input type="text" name="descripcion" maxlength="400" class="input"></div>
      </div>
      <div class="actions">
        <button class="btn btn--primary" type="submit" name="add" value="1">
          {icono("plus", 18)}<span>Agregar al catálogo</span></button>
        <span class="muted" style="font-size:.78rem">Si la clave ya existe en
        HUB_Config solo se cataloga: su valor actual no se toca.</span>
      </div>
    </form>
  </div>"""


def _sin_migracion(todos):
    """Aviso + listado de solo lectura cuando falta la migración 0036."""
    filas = "".join(
        f'<tr><td><code>{esc(k)}</code></td>'
        f'<td class="muted">{esc(v[:60])}{"…" if len(v) > 60 else ""}</td></tr>'
        for k, v in sorted(todos.items())[:MAX_GRUPO])
    return f"""
  <div class="card">
    <div class="wcard-top">
      <div class="wcard-name">{icono("alert", 20)} Falta la migración 0036</div>
    </div>
    <div class="empty">
      La tabla <code>HUB_ConfigCatalogo</code> no existe en esta base de datos.
      Aplica la migración desde el repo HUB y vuelve a cargar esta página:
      <pre class="log" style="margin-top:10px">HUB_DB_DATABASE=ECCSA_Admon_Pruebas python apply_migrations.py
# producción (explícito):
HUB_DB_DATABASE=ECCSA_Admon HUB_MIGRATE_PRODUCTION=1 python apply_migrations.py</pre>
      Mientras tanto, las {len(todos)} claves de <code>HUB_Config</code> se
      listan solo para consulta.
    </div>
    <div class="table-wrap"><div class="tscroll"><table class="table">
      <thead><tr><th>Clave</th><th>Valor</th></tr></thead>
      <tbody>{filas}</tbody>
    </table></div></div>
  </div>"""


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido", ip=""):
    """Devuelve el HTML completo de la pestaña Apps."""
    set_csrf(csrf)
    tiene_tabla = db.config_catalog_exists()
    cat = db.get_config_catalog() if tiene_tabla else []
    todos = db.get_all_config_values()
    catalogadas = {f["Clave"] for f in cat}
    libres = [k for k in sorted(todos) if k not in catalogadas]

    apps = sorted({f["App"] for f in cat})
    grupos = {a: [f for f in cat if f["App"] == a] for a in apps}
    n_secretos = sum(1 for f in cat if f["Tipo"] == "secret")

    kpis = f"""
  <div class="grid">
    <div class="kpi"><span class="kpi__label">Apps con claves</span>
      <span class="kpi__value">{len(apps)}</span></div>
    <div class="kpi"><span class="kpi__label">Claves catalogadas</span>
      <span class="kpi__value">{len(cat)}</span></div>
    <div class="kpi"><span class="kpi__label">Sin clasificar</span>
      <span class="kpi__value">{len(libres)}</span></div>
    <div class="kpi"><span class="kpi__label">Secretos protegidos</span>
      <span class="kpi__value">{n_secretos}</span></div>
  </div>"""

    if not tiene_tabla:
        cuerpo = kpis + _sin_migracion(todos)
    else:
        intro = ('<p class="desc">Catálogo '
                 '<span class="badge badge-info">HUB_ConfigCatalogo</span> '
                 '(metadatos) + valores en '
                 '<span class="badge badge-info">HUB_Config</span>: lo que '
                 'guardes aquí lo ve al instante HUB, Field o admon, sin '
                 'reiniciar. Ordenado por app y por <code>Orden</code>.</p>')
        cuerpo = (kpis + intro
                  + "".join(_panel_app(a, grupos[a]) for a in apps)
                  + _panel_libres(libres, apps)
                  + _panel_alta(apps))

    return page("apps", cuerpo, user=user, flash_ok=flash_ok,
                lugar=lugar, ip=ip,
                flash_err=flash_err,
                subtitle=f"config central por app · {len(todos)} claves en "
                         f"HUB_Config")
