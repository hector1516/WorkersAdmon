"""
panel.views.triaje — Pestaña "🛡️ Triaje" (correo huérfano)
============================================================

Muestra lo que el worker clasificó y deja al operador decidir. En **modo
revisión** nada sale sin aprobación: aprobar encola el correo en
`HUB_MailboxColaEnvio`, que es la misma cola que usa la app de Mailbox.

El criterio de "ya no está" es el que definió el usuario: una dirección de
nuestro dominio que no aparece en `HUB_Users` (o está con `Activo = 0`).
"""
from mailbox_worker import triaje as _t

from ..templates import esc, page

EXPLICACION = """
<div class="panel">
  <h2>🛡️ Triaje del correo</h2>
  <p class="hint">
    Clasifica cada mensaje nuevo con reglas, sin IA y sin costo: publicidad,
    rebotes y correo interno se descartan por cabeceras y por el remitente.
    Solo los correos de una persona real a alguien que <b>ya no está</b> llegan
    aquí, con un borrador listo. Nada se envía hasta que lo apruebes.
  </p>
</div>
"""


def _tarjeta(c, csrf):
    cat = (c.get("Categoria") or "").lower()
    icono = {"huerfano": "❓", "bulk": "📢", "rebote": "↩️",
             "interno": "🏠", "activo": "✅"}.get(cat, "•")
    metodo = (c.get("Metodo") or "regla").lower()
    etiqueta_ia = f'<span class="badge">{esc(metodo)}</span>'
    conf = c.get("Confianza")
    conf_txt = f"{float(conf):.2f}" if conf is not None else "—"

    borrador = c.get("BorradorTexto") or ""
    return f"""
<div class="card" style="margin-bottom:14px">
  <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
    <div>
      <b>{icono} {esc(c.get('RemitenteEmail') or '')}</b>
      {etiqueta_ia}
      <span class="badge">{esc(cat)}</span>
      <span class="badge">conf. {conf_txt}</span>
    </div>
    <div class="hint">{esc(str(c.get('Creado') or ''))}</div>
  </div>
  <p class="hint" style="margin:6px 0">
    Llegó a <b>{esc(c.get('CuentaEmail') or '')}</b> ·
    destinatario huérfano: <b>{esc(c.get('Destinatario') or '—')}</b>
    → se sugiere <b>{esc(c.get('DestinoSugerido') or '—')}</b>
  </p>
  <p class="hint" style="margin:4px 0">{esc(c.get('Motivo') or '')}</p>
  <p style="margin:6px 0"><b>Asunto:</b> {esc(c.get('Asunto') or '')}</p>
  <details>
    <summary class="hint">Ver el borrador de respuesta</summary>
    <pre style="white-space:pre-wrap;background:#0b1220;padding:10px;border-radius:8px">{esc(borrador)}</pre>
  </details>
  <form method="post" action="/triaje" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <input type="hidden" name="id" value="{esc(str(c.get('Id')))}">
    <button class="btn btn-success" name="accion" value="aprobar">Aprobar y enviar</button>
    <button class="btn btn-secondary" name="accion" value="rechazar">Rechazar</button>
    <button class="btn btn-warning" name="accion" value="bloquear_remitente">Bloquear remitente</button>
    <button class="btn btn-warning" name="accion" value="bloquear_dominio">Bloquear dominio</button>
  </form>
</div>"""


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido", ip=""):
    r = _t.resumen()
    pend = _t.pendientes(100)

    cuerpo = EXPLICACION
    cuerpo += f"""
<div class="panel">
  <div style="display:flex;gap:18px;flex-wrap:wrap">
    <div><div class="hint">Por revisar</div><b style="font-size:22px">{r.get('nueva', 0)}</b></div>
    <div><div class="hint">Aprobadas</div><b style="font-size:22px">{r.get('aprobada', 0)}</b></div>
    <div><div class="hint">Rechazadas</div><b style="font-size:22px">{r.get('rechazada', 0)}</b></div>
    <div><div class="hint">Huérfanos (30 d)</div><b style="font-size:22px">{r.get('huerfanos_mes', 0)}</b></div>
    <div><div class="hint">Remitentes bloqueados</div><b style="font-size:22px">{r.get('bloqueos', 0)}</b></div>
  </div>
</div>"""

    if not pend:
        cuerpo += ('<div class="callout">No hay nada por revisar. El worker '
                   'clasifica cada mensaje nuevo en su próximo ciclo.</div>')
    else:
        cuerpo += f'<h2>Por revisar ({len(pend)})</h2>'
        for c in pend:
            cuerpo += _tarjeta(c, csrf)

    cuerpo += _bloqueos(csrf)

    return page("triaje", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip)


def _bloqueos(csrf):
    """Lista negra global: alta manual, importación de listas y baja."""
    filas_b = _t.listar_bloqueos()
    cuerpo = """
<div class="panel">
  <h2>Lista de bloqueo global</h2>
  <p class="hint">
    Un remitente bloqueado se mueve a Spam en <b>las 13 cuentas</b>, no solo en
    la que lo recibió. Se evalúa antes que las reglas personales y antes de
    cualquier clasificación.
  </p>
  <form method="post" action="/triaje" style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">
    <input type="hidden" name="csrf" value="{csrf}">
    <input class="input" name="valor" placeholder="correo@dominio.com o dominio.com"
           style="flex:1;min-width:260px" required>
    <select class="input" name="tipo">
      <option value="">(deducir)</option>
      <option value="email">correo</option>
      <option value="dominio">dominio</option>
    </select>
    <button class="btn btn-warning" name="accion" value="bloquear">Bloquear</button>
  </form>

  <form method="post" action="/triaje" style="margin:10px 0">
    <input type="hidden" name="csrf" value="{csrf}">
    <label class="hint" for="bl-lista">Importar una lista (una entrada por línea; # comenta)</label>
    <textarea id="bl-lista" class="input" name="lista" rows="4"
              style="width:100%;font-family:monospace"
              placeholder="spam.com&#10;@otro-dominio.com&#10;ventas@molesto.com"></textarea>
    <button class="btn btn-secondary" name="accion" value="importar">Importar</button>
  </form>
""".replace("{csrf}", esc(csrf))

    if not filas_b:
        cuerpo += '<div class="callout">La lista está vacía.</div>'
    else:
        cuerpo += ('<table style="width:100%"><thead><tr><th>Tipo</th><th>Valor</th>'
                   '<th>Origen</th><th>Motivo</th><th></th></tr></thead><tbody>')
        for b in filas_b:
            cuerpo += (
                f"<tr><td>{esc(b['Tipo'])}</td><td><b>{esc(b['Valor'])}</b></td>"
                f"<td>{esc(b.get('Origen') or '')}</td>"
                f"<td class=\"hint\">{esc(b.get('Motivo') or '')}</td>"
                f"<td><form method=\"post\" action=\"/triaje\">"
                f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(csrf)}\">"
                f"<input type=\"hidden\" name=\"id\" value=\"{esc(str(b['Id']))}\">"
                f"<button class=\"btn btn-sm btn-secondary\" name=\"accion\" "
                f"value=\"desbloquear\">Quitar</button></form></td></tr>")
        cuerpo += "</tbody></table>"
    return cuerpo + "</div>"
