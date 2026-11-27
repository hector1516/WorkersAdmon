"""
panel.views.triaje — Pestaña "Triaje" (correo huérfano)
============================================================

Muestra lo que el worker clasificó y deja al operador decidir. En **modo
revisión** nada sale sin aprobación: aprobar encola el correo en
`HUB_MailboxColaEnvio`, que es la misma cola que usa la app de Mailbox.

El criterio de "ya no está" es el que definió el usuario: una dirección de
nuestro dominio que no aparece en `HUB_Users` (o está con `Activo = 0`).

La pendiente es una lista (`.list`), no una tarjeta por correo: con cien
pendientes, cien tarjetas son cien marcos; una fila por correo deja ver el
remitente, el asunto y la fecha de un vistazo, con las acciones al lado.
"""
from mailbox_worker import triaje as _t

from ..icons import icono
from ..templates import esc, page

EXPLICACION = """
<div class="card tr-sec">
  <h2 class="tr-h">
    """ + icono("alert") + """ Triaje del correo</h2>
  <p class="hint tr-margen-0">
    Clasifica cada mensaje nuevo con reglas, sin IA y sin costo: publicidad,
    rebotes y correo interno se descartan por cabeceras y por el remitente.
    Solo los correos de una persona real a alguien que <b>ya no está</b> llegan
    aquí, con un borrador listo. Nada se envía hasta que lo apruebes.
  </p>
</div>
"""

# Clase de badge por categoría del clasificador.
BADGE_CAT = {
    "huerfano": "badge--warn",
    "bulk": "",
    "rebote": "",
    "interno": "",
    "activo": "badge--ok",
}


def _kpi(etiqueta, valor):
    return (f'<div class="kpi"><span class="kpi__label">{esc(etiqueta)}</span>'
            f'<span class="kpi__value">{esc(valor)}</span></div>')


def _filtros():
    """Control segmentado: las dos secciones de la misma página.

    No filtra la consulta (el worker ya entrega solo lo pendiente); es
    navegación entre «por revisar» y la lista de bloqueo, por eso son anclas y
    no un formulario.
    """
    return f"""
  <nav class="segmented tr-sec" aria-label="Secciones de Triaje"
       >
    <a class="is-active" href="#por-revisar"
       aria-current="page">{icono("mail", 18)} Por revisar</a>
    <a href="#bloqueos">{icono("alert", 18)} Bloqueos</a>
  </nav>"""


def _tarjeta(c, csrf):
    """Una fila de la lista: remitente, asunto, fecha y acciones."""
    cat = (c.get("Categoria") or "").lower()
    import re as _re
    motivo_txt = c.get("Motivo") or ""
    m_susp = _re.search(r"sospecha\s*(\d+)", motivo_txt)
    badge_susp = ""
    if m_susp:
        badge_susp = (f'<span class="badge badge--warn">'
                      f'sospecha {m_susp.group(1)}</span>')
    conf = c.get("Confianza")
    conf_txt = f"{float(conf):.2f}" if conf is not None else "—"

    borrador = c.get("BorradorTexto") or ""
    return f"""
    <div class="list__row tr-fila tr-fila--top">
      <div class="list__main tr-campo">
        <strong>{esc(c.get('RemitenteEmail') or '')}</strong>
        <div class="tr-nota">{esc(c.get('Asunto') or '(sin asunto)')}</div>
        <div class="hint">
          Llegó a <b>{esc(c.get('CuentaEmail') or '')}</b> ·
          destinatario huérfano: <b>{esc(c.get('Destinatario') or '—')}</b>
          → se sugiere <b>{esc(c.get('DestinoSugerido') or '—')}</b>
        </div>
        <div class="hint tr-nota--4">{esc(motivo_txt)}</div>
        <details class="wdesc tr-nota--6">
          <summary>Ver el borrador de respuesta</summary>
          <div><pre class="tr-pre">{esc(borrador)}</pre></div>
        </details>
      </div>
      <div class="list__meta tr-acciones tr-acciones--center">
        <span class="badge {BADGE_CAT.get(cat, '')}">{esc(cat)}</span>
        <span class="badge">conf. {esc(conf_txt)}</span>
        {badge_susp}
        <span class="hint">{esc(str(c.get('Creado') or ''))}</span>
      </div>
      <div class="tr-ancho">
        <form method="post" action="/triaje"
              class="tr-acciones tr-acciones--sep">
          <input type="hidden" name="csrf" value="{esc(csrf)}">
          <input type="hidden" name="id" value="{esc(str(c.get('Id')))}">
          <button class="btn btn--sm btn-success" name="accion" value="aprobar">
            {icono("check", 18)} Aprobar y enviar</button>
          <button class="btn btn--sm btn--quiet" name="accion" value="rechazar">
            Rechazar</button>
          <button class="btn btn--sm btn-warning" name="accion" value="bloquear_remitente">
            Bloquear remitente</button>
          <button class="btn btn--sm btn-warning" name="accion" value="bloquear_dominio">
            Bloquear dominio</button>
        </form>
      </div>
    </div>"""


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido", ip=""):
    r = _t.resumen()
    pend = _t.pendientes(100)

    cuerpo = EXPLICACION
    cuerpo += f"""
  <div class="grid">
    {_kpi("Por revisar", r.get('nueva', 0))}
    {_kpi("Aprobadas", r.get('aprobada', 0))}
    {_kpi("Rechazadas", r.get('rechazada', 0))}
    {_kpi("Huérfanos (30 d)", r.get('huerfanos_mes', 0))}
    {_kpi("Remitentes bloqueados", r.get('bloqueos', 0))}
    {_kpi("Auto-rechazadas (30 d)", r.get('auto_rechazados_mes', 0))}
    {_kpi("Sospechosos (30 d)", r.get('sospechosos_mes', 0))}
  </div>"""
    cuerpo += _filtros()

    if not pend:
        cuerpo += ('<div class="empty">' + icono("check", 22)
                   + '<p>No hay nada por revisar. El worker '
                   'clasifica cada mensaje nuevo en su próximo ciclo.</p></div>')
    else:
        filas = "".join(_tarjeta(c, csrf) for c in pend)
        cuerpo += f"""
  <div class="card tr-sec" id="por-revisar" >
    <h2 class="tr-h">
      {icono("mail")} Por revisar
      <span class="badge badge--warn">{len(pend)}</span></h2>
    <div class="list tr-lista">
      {filas}
    </div>
  </div>"""

    cuerpo += _bloqueos(csrf)

    return page("triaje", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip)


def _bloqueos(csrf):
    """Lista negra global: alta manual, importación de listas y baja."""
    filas_b = _t.listar_bloqueos()
    cuerpo = f"""
  <div class="card tr-sec" id="bloqueos" >
    <h2 class="tr-h">
      {icono("alert")} Lista de bloqueo global</h2>
    <p class="hint">
      Un remitente bloqueado se mueve a Spam en <b>las 13 cuentas</b>, no solo en
      la que lo recibió. Se evalúa antes que las reglas personales y antes de
      cualquier clasificación.
    </p>
    <form method="post" action="/triaje"
          class="tr-acciones tr-acciones--bloque">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field tr-campo tr-campo--sinmargen">
        <label for="bl-valor">Correo o dominio</label>
        <input id="bl-valor" class="input" name="valor"
               placeholder="correo@dominio.com o dominio.com"
               autocomplete="off" required>
      </div>
      <div class="field tr-margen-0">
        <label for="bl-tipo">Tipo</label>
        <select id="bl-tipo" class="input" name="tipo">
          <option value="">(deducir)</option>
          <option value="email">correo</option>
          <option value="dominio">dominio</option>
        </select>
      </div>
      <div class="field tr-fila tr-fila--end">
        <button class="btn btn-warning" name="accion" value="bloquear">
          Bloquear</button>
      </div>
    </form>

    <form method="post" action="/triaje" class="tr-bloque">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field">
        <label for="bl-lista">Importar una lista (una entrada por línea; # comenta)</label>
        <textarea id="bl-lista" class="input tr-mono" name="lista" rows="4"

                  placeholder="spam.com&#10;@otro-dominio.com&#10;ventas@molesto.com"></textarea>
      </div>
      <button class="btn btn--quiet" name="accion" value="importar">Importar</button>
    </form>
"""

    if not filas_b:
        cuerpo += ('<div class="empty"><p>La lista está vacía.</p></div>')
    else:
        filas = []
        for b in filas_b:
            filas.append(f"""
      <div class="list__row tr-wrap">
        <div class="list__main">
          <b>{esc(b['Valor'])}</b>
          <div class="hint">{esc(b['Tipo'])} · {esc(b.get('Origen') or '—')}
            · {esc(b.get('Motivo') or '—')}</div>
        </div>
        <form method="post" action="/triaje">
          <input type="hidden" name="csrf" value="{esc(csrf)}">
          <input type="hidden" name="id" value="{esc(str(b['Id']))}">
          <button class="btn btn--sm btn--quiet" name="accion"
                  value="desbloquear">Quitar</button>
        </form>
      </div>""")
        cuerpo += ('<div class="list tr-lista tr-lista--corta">' + "".join(filas) + '</div>')
    return cuerpo + "</div>"
