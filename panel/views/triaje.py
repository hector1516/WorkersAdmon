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
    Clasifica cada mensaje nuevo sin gastar IA primero: publicidad, rebotes y
    correo interno se descartan por cabeceras. Solo los correos de una persona
    real a alguien que <b>ya no está</b> llegan aquí, con un borrador listo.
    Nada se envía hasta que lo apruebes.
  </p>
</div>
"""


def _tarjeta(c, csrf):
    cat = (c.get("Categoria") or "").lower()
    icono = {"huerfano": "❓", "bulk": "📢", "rebote": "↩️",
             "interno": "🏠", "activo": "✅"}.get(cat, "•")
    metodo = (c.get("Metodo") or "").lower()
    etiqueta_ia = ('<span class="badge badge-info">IA</span>'
                   if metodo == "ia" else
                   '<span class="badge">regla</span>')
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
  <form method="post" action="/triaje" style="margin-top:10px;display:flex;gap:8px">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <input type="hidden" name="id" value="{esc(str(c.get('Id')))}">
    <button class="btn btn-success" name="accion" value="aprobar">Aprobar y enviar</button>
    <button class="btn btn-secondary" name="accion" value="rechazar">Rechazar</button>
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
    <div><div class="hint">Llamadas IA (30 d)</div><b style="font-size:22px">{r.get('ia_mes', 0)}</b></div>
    <div><div class="hint">Tokens (30 d)</div><b style="font-size:22px">{r.get('tokens_mes', 0)}</b></div>
  </div>
</div>"""

    if not pend:
        cuerpo += ('<div class="callout">No hay nada por revisar. El worker '
                   'clasifica cada mensaje nuevo en su próximo ciclo.</div>')
    else:
        cuerpo += f'<h2>Por revisar ({len(pend)})</h2>'
        for c in pend:
            cuerpo += _tarjeta(c, csrf)

    return page("triaje", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip)
