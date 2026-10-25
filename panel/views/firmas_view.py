"""
panel.views.firmas_view — Pestaña "✍️ Firmas" del panel de Mailbox.

Donde cada usuario crea y edita su firma de correo electrónico.
La firma se aplica a TODAS las cuentas IMAP asignadas a ese usuario.

El editor es visual: elige una plantilla prediseñada de ECCSA, rellena sus
datos (nombre, cargo, teléfono, correo, dirección), sube su foto opcional,
elige una frase aleatoria y guarda. Todo en HTML, compatible con PWA y
escritorio, y se ve igual en Outlook, Gmail y Apple Mail.

La firma resultante se guarda en HUB_MailboxFirmas con su HTML. Cuando el
usuario envía un correo desde Mailbox, smtp.py lee IdFirma y adjunta las
imágenes inline (logo ECCSA + foto) como cid:.
"""

import json

from .. import auth, config
from ..templates import esc, page
from ..mailbox_worker import firmas as firmas_mod


# ═════════════════════════════════════════════════════════════════════════════
# Editor visual embebido (HTML/JS/CSS autónomo, tema oscuro del panel)
# ═════════════════════════════════════════════════════════════════════════════

def _editor_html(user: dict, csrf: str) -> str:
    """HTML del editor de firmas con los datos del usuario inyectados."""
    id_usuario = user.get("Id", 0)
    nombre = user.get("nombre", "") or ""
    correo = user.get("email", "") or ""
    telefono = user.get("telefono", "") or ""
    cargo = user.get("cargo", "") or ""
    direccion = user.get("direccion", "") or "Monterrey, Nuevo León, México"

    json_usuario = json.dumps({
        "nombre": nombre,
        "cargo": cargo,
        "telefono": telefono,
        "correo": correo,
        "direccion": direccion,
        "web": "www.ecc-sa.com.mx",
        "fotoUrl": "",
    })

    return f"""<div id="ef-editor">
  <div class="ef-header">
    <h2>✍️ Editor de Firma de Correo</h2>
    <p class="ef-sub">Crea tu firma profesional para todos tus correos de ECCSA</p>
  </div>

  <div class="ef-grid">
    <!-- Columna izquierda: plantillas y datos -->
    <div class="ef-panel">
      <div class="ef-section">
        <h3>📋 Plantilla</h3>
        <div class="ef-templates" id="ef-templates">
          <!-- Las plantillas se inyectan por JS -->
        </div>
      </div>

      <div class="ef-section">
        <h3>👤 Tus datos</h3>
        <label>Nombre</label>
        <input type="text" id="ef-nombre" placeholder="Héctor Peña" value="{esc(nombre)}">

        <label>Cargo</label>
        <input type="text" id="ef-cargo" placeholder="Ingeniero de Sistemas" value="{esc(cargo)}">

        <label>Teléfono</label>
        <input type="tel" id="ef-telefono" placeholder="+52 81 1234 5678" value="{esc(telefono)}">

        <label>Correo</label>
        <input type="email" id="ef-correo" placeholder="hector.pena@ecc-sa.com.mx" value="{esc(correo)}">

        <label>Dirección</label>
        <input type="text" id="ef-direccion" placeholder="Monterrey, Nuevo León, México" value="{esc(direccion)}">

        <label>Web</label>
        <input type="url" id="ef-web" value="www.ecc-sa.com.mx">

        <label>Foto (opcional)</label>
        <input type="file" id="ef-foto" accept="image/*">
        <div class="ef-foto-preview" id="ef-foto-preview"></div>
      </div>

      <div class="ef-section ef-frase-section">
        <label>💭 Frase aleatoria</label>
        <button type="button" class="ef-btn-frase" id="ef-btn-frase">Nueva frase</button>
        <p class="ef-frase-actual" id="ef-frase-actual">Cargando frase...</p>
      </div>

      <div class="ef-actions">
        <button type="button" class="ef-btn ef-btn-primary" id="ef-btn-vista-previa">👁️ Vista previa</button>
        <button type="button" class="ef-btn ef-btn-success" id="ef-btn-guardar">💾 Guardar firma</button>
      </div>
      <input type="hidden" id="ef-csrf" value="{esc(csrf)}">
      <input type="hidden" id="ef-id-usuario" value="{id_usuario}">
    </div>

    <!-- Columna derecha: vista previa -->
    <div class="ef-panel ef-preview-panel">
      <div class="ef-section">
        <h3>👁️ Vista previa</h3>
      </div>
      <div class="ef-preview" id="ef-preview">
        <div class="ef-preview-empty">Tu firma aparecerá aquí</div>
      </div>
      <div class="ef-preview-nota">
        La firma se verá igual en Outlook, Gmail, Apple Mail y cualquier cliente de correo.
      </div>
    </div>
  </div>

  <div class="ef-mensaje" id="ef-mensaje"></div>
</div>

<style>
  /* Editor de firmas — tema oscuro del panel */
  #ef-editor {{ max-width: 1100px; margin: 0 auto; padding: 20px; font-family:Segoe UI,Tahoma,Geneva,sans-serif; }}
  #ef-editor * {{ box-sizing: border-box; }}
  .ef-header {{ margin-bottom: 24px; }}
  .ef-header h2 {{ margin: 0; color: #F8FAFC; font-size: 24px; }}
  .ef-header .ef-sub {{ color: #94A3B8; margin-top: 4px; }}
  .ef-grid {{ display: grid; grid-template-columns: 420px 1fr; gap: 24px; align-items: start; }}
  @media (max-width: 900px) {{ .ef-grid {{ grid-template-columns: 1fr; }} }}
  .ef-panel {{ background: #1E293B; border: 1px solid #334155; border-radius: 12px; padding: 20px; }}
  .ef-preview-panel {{ min-height: 500px; display: flex; flex-direction: column; }}
  .ef-section {{ margin-bottom: 20px; }}
  .ef-section:last-child {{ margin-bottom: 0; }}
  .ef-section h3 {{ margin: 0 0 12px 0; font-size: 16px; color: #F8FAFC; }}
  .ef-templates {{ display: flex; flex-direction: column; gap: 8px; }}
  .ef-tpl-btn {{
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px; border: 1px solid #334155; border-radius: 8px;
    background: #0F172A; color: #F8FAFC; font-size: 13px; cursor: pointer;
    transition: all 0.15s;
  }}
  .ef-tpl-btn:hover {{ border-color: #FF6B00; background: #1E1B18; }}
  .ef-tpl-btn.activo {{ border-color: #FF6B00; background: #1E1B18; color: #FFAE00; font-weight: 600; }}
  .ef-tpl-icon {{ font-size: 18px; width: 24px; }}
  .ef-tpl-info .ef-tpl-nombre {{ font-weight: 600; color: #F8FAFC; }}
  .ef-tpl-info .ef-tpl-desc {{ font-size: 11px; color: #94A3B8; }}
  .ef-panel label {{ display: block; font-size: 12px; font-weight: 600; color: #CBD5E1; margin-bottom: 4px; margin-top: 10px; }}
  .ef-panel input[type="text"],
  .ef-panel input[type="email"],
  .ef-panel input[type="url"],
  .ef-panel input[type="tel"] {{
    width: 100%; padding: 8px 12px; border: 1px solid #334155; border-radius: 6px;
    font-size: 13px; color: #F8FAFC; background: #0F172A;
  }}
  .ef-panel input[type="text"]:focus,
  .ef-panel input[type="email"]:focus,
  .ef-panel input[type="url"]:focus,
  .ef-panel input[type="tel"]:focus {{
    outline: none; border-color: #FF6B00; box-shadow: 0 0 0 3px rgba(255,107,0,0.1);
  }}
  .ef-foto-preview {{ margin-top: 8px; width: 80px; height: 80px; border: 1px dashed #334155; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #64748B; font-size: 11px; }}
  .ef-foto-preview img {{ width: 100%; height: 100%; object-fit: cover; border-radius: 8px; }}
  .ef-frase-section {{ background: #0F172A; border-radius: 8px; padding: 12px 16px; }}
  .ef-btn-frase {{
    background: #1E293B; border: 1px solid #334155; color: #F8FAFC;
    padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;
  }}
  .ef-btn-frase:hover {{ border-color: #FF6B00; color: #FFAE00; }}
  .ef-frase-actual {{ margin-top: 8px; font-size: 12px; color: #94A3B8; font-style: italic; }}
  .ef-actions {{ display: flex; gap: 10px; margin-top: 4px; }}
  .ef-btn {{ padding: 10px 18px; border: none; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; }}
  .ef-btn-primary {{ background: #1E293B; color: #F8FAFC; }}
  .ef-btn-primary:hover {{ background: #334155; }}
  .ef-btn-success {{ background: #FF6B00; color: #0F172A; }}
  .ef-btn-success:hover {{ background: #E65F00; }}
  .ef-preview {{ flex: 1; border: 1px solid #334155; border-radius: 10px; padding: 20px; background: #0F172A; overflow: auto; display: flex; align-items: flex-start; justify-content: center; }}
  .ef-preview-empty {{ color: #64748B; font-size: 13px; text-align: center; padding: 40px; }}
  .ef-preview-nota {{ font-size: 11px; color: #64748B; margin-top: 8px; text-align: center; }}
  .ef-mensaje {{ margin-top: 16px; padding: 10px 14px; border-radius: 6px; font-size: 13px; display: none; }}
  .ef-mensaje.ok {{ background: #166534; color: #DCFCE7; border: 1px solid #22C55E; display: block; }}
  .ef-mensaje.error {{ background: #991B1B; color: #FEE2E2; border: 1px solid #EF4444; display: block; }}
</style>

<script>
// ═════════════════════════════════════════════════════════════════════════════
// CONFIGURACIÓN del editor (inyectada desde el panel)
// ═════════════════════════════════════════════════════════════════════════════
const CONFIG = {{
  apiListarFirmas:   '/api/mailbox/firmas/listar?id_usuario={id_usuario}',
  apiGuardarFirma:   '/api/mailbox/firmas/guardar',
  apiListarPlantillas: '/api/mailbox/firmas/plantillas',
  apiSubirFoto:      '/api/mailbox/firmas/subir-foto',
  idUsuario:         {id_usuario},
  usuario: {json_usuario}
}};

let estado = {{
  plantillaSeleccionada: null,
  fotoFile: null,
  fraseActual: ''
}};

function init(){{
  cargarPlantillas();
  cargarFraseAleatoria();
  configurarEventos();
  renderVistaPrevia();
}}

async function cargarPlantillas(){{
  const contenedor = document.getElementById('ef-templates');
  try {{
    const resp = await fetch(CONFIG.apiListarPlantillas);
    const plantillas = await resp.json();
    renderPlantillas(plantillas || []);
  }} catch (e) {{
    renderPlantillas([]);
  }}
}}

function renderPlantillas(plantillas){{
  const contenedor = document.getElementById('ef-templates');
  contenedor.innerHTML = '';
  const defaults = [
    {{ nombre: 'Corporativa ECCSA', desc: 'Tabla con logo a la izquierda', categoria: 'corporativa' }},
    {{ nombre: 'Moderna ECCSA', desc: 'Barra lateral naranja', categoria: 'moderna' }},
    {{ nombre: 'Simple ECCSA', desc: 'Mínima y elegante', categoria: 'simple' }},
  ];
  (plantillas.length ? plantillas : defaults).forEach((p, i) => {{
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ef-tpl-btn' + (i === 0 ? ' activo' : '');
    btn.innerHTML = `
      <span class="ef-tpl-icon">📄</span>
      <div class="ef-tpl-info">
        <div class="ef-tpl-nombre">${{p.nombre}}</div>
        <div class="ef-tpl-desc">${{p.desc || ''}}</div>
      </div>
    `;
    btn.onclick = () => {{
      document.querySelectorAll('.ef-tpl-btn').forEach(b => b.classList.remove('activo'));
      btn.classList.add('activo');
      estado.plantillaSeleccionada = p;
      renderVistaPrevia();
    }};
    contenedor.appendChild(btn);
  }});
}}

async function cargarFraseAleatoria(){{
  try {{
    const resp = await fetch('/api/mailbox/firmas/frase-aleatoria');
    const data = await resp.json();
    if (data.frase) {{
      estado.fraseActual = data.frase;
      document.getElementById('ef-frase-actual').textContent = data.frase;
    }}
  }} catch (e) {{
    estado.fraseActual = 'El éxito es la suma de pequeños esfuerzos repetidos día tras día. — ECCSA';
    document.getElementById('ef-frase-actual').textContent = estado.fraseActual;
  }}
  renderVistaPrevia();
}}

document.getElementById('ef-btn-frase').onclick = async () => {{
  const btn = document.getElementById('ef-btn-frase');
  btn.textContent = 'Cargando...';
  btn.disabled = true;
  await cargarFraseAleatoria();
  btn.textContent = 'Nueva frase';
  btn.disabled = false;
}};

function obtenerDatosUsuario(){{
  const u = CONFIG.usuario;
  return {{
    nombre: document.getElementById('ef-nombre').value || u.nombre || '',
    cargo: document.getElementById('ef-cargo').value || u.cargo || '',
    telefono: document.getElementById('ef-telefono').value || u.telefono || '',
    correo: document.getElementById('ef-correo').value || u.correo || '',
    direccion: document.getElementById('ef-direccion').value || u.direccion || 'Monterrey, Nuevo León, México',
    web: document.getElementById('ef-web').value || u.web || 'www.ecc-sa.com.mx',
    fotoUrl: u.fotoUrl || '',
    frase: estado.fraseActual,
  }};
}}

function renderVistaPrevia(){{
  const preview = document.getElementById('ef-preview');
  const datos = obtenerDatosUsuario();
  let html = '<div style="font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#F8FAFC;">';
  html += '<!-- Firma ECCSA -->\\n';
  html += '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">';
  html += '<tr><td align="left" valign="top" style="padding:0 0 10px 0;">';
  html += '<table role="presentation" cellspacing="0" cellpadding="0" border="0">';
  html += '<tr>';
  html += '<td align="left" valign="middle" style="padding-right:12px;">';
  html += '<img src="cid:eccsa-logo" width="48" height="48" alt="ECCSA" style="display:block;border:0;" />';
  html += '</td>';
  html += '<td align="left" valign="middle" style="font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:14px;color:#F8FAFC;">';
  html += `<div style="font-weight:bold;color:#FFAE00;font-size:15px;">${{escapeHtml(datos.nombre)}}</div>`;
  html += `<div style="font-size:12px;color:#94A3B8;">${{escapeHtml(datos.cargo)}}</div>`;
  html += '</td></tr></table></td></tr>';
  html += '<tr><td align="left" valign="top" style="padding:8px 0 0 0;border-top:1px solid #FFAE00;">';
  html += '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>';
  html += `<td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#CBD5E1;">📞 ${{escapeHtml(datos.telefono)}}</div></td>`;
  html += `<td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#CBD5E1;">✉️ ${{escapeHtml(datos.correo)}}</div></td>`;
  html += `<td align="left" valign="top"><div style="font-size:12px;color:#CBD5E1;">🌐 ${{escapeHtml(datos.web)}}</div></td>`;
  html += '</tr></table></td></tr>';
  html += '<tr><td align="left" valign="top" style="padding:6px 0 0 0;"><div style="font-size:11px;color:#64748B;">${{escapeHtml(datos.direccion)}}</div></td></tr>';
  html += '<tr><td align="left" valign="top" style="padding:10px 0 0 0;"><div style="font-size:12px;color:#64748B;font-style:italic;">"${{escapeHtml(datos.frase)}}"</div></td></tr>';
  html += '</table></div>';
  preview.innerHTML = html;
}}

function escapeHtml(texto){{
  if (!texto) return '';
  return String(texto)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}}

const inputFoto = document.getElementById('ef-foto');
const previewFoto = document.getElementById('ef-foto-preview');
inputFoto.onchange = function(e){{
  const file = e.target.files[0];
  if (!file) return;
  estado.fotoFile = file;
  const reader = new FileReader();
  reader.onload = function(ev){{
    previewFoto.innerHTML = `<img src="${{ev.target.result}}" alt="foto" />`;
  }};
  reader.readAsDataURL(file);
}};

function configurarEventos(){{
  ['ef-nombre','ef-cargo','ef-telefono','ef-correo','ef-direccion','ef-web'].forEach(id => {{
    document.getElementById(id).addEventListener('input', renderVistaPrevia);
  }});
  document.getElementById('ef-btn-vista-previa').onclick = renderVistaPrevia;
  document.getElementById('ef-btn-guardar').onclick = async () => {{
    const btn = document.getElementById('ef-btn-guardar');
    const btnOriginal = btn.textContent;
    btn.textContent = 'Guardando...';
    btn.disabled = true;
    try {{
      const datos = obtenerDatosUsuario();
      const htmlFirma = document.getElementById('ef-preview').innerHTML;
      let fotoUrl = datos.fotoUrl;
      if (estado.fotoFile) {{
        const formData = new FormData();
        formData.append('foto', estado.fotoFile);
        formData.append('id_usuario', CONFIG.idUsuario);
        const respFoto = await fetch(CONFIG.apiSubirFoto, {{ method: 'POST', body: formData }});
        if (respFoto.ok) {{
          const dataFoto = await respFoto.json();
          fotoUrl = dataFoto.url || fotoUrl;
        }}
      }}
      const payload = {{
        id_usuario: CONFIG.idUsuario,
        nombre: 'Firma ' + (datos.nombre || 'usuario'),
        html: htmlFirma,
        texto: htmlFirma.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim(),
        predeterminada: true,
      }};
      const resp = await fetch(CONFIG.apiGuardarFirma, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload),
      }});
      if (resp.ok) {{
        mostrarMensaje('Firma guardada correctamente. Se aplicará a tus próximos correos.', 'ok');
      }} else {{
        mostrarMensaje('Error al guardar la firma.', 'error');
      }}
    }} catch (e) {{
      mostrarMensaje('Error: ' + e.message, 'error');
    }} finally {{
      btn.textContent = btnOriginal;
      btn.disabled = false;
    }}
  }};
}}

function mostrarMensaje(texto, tipo){{
  const el = document.getElementById('ef-mensaje');
  el.textContent = texto;
  el.className = 'ef-mensaje ' + (tipo === 'ok' ? 'ok' : 'error');
  el.style.display = 'block';
  setTimeout(() => {{ el.style.display = 'none'; }}, 5000);
}}

if (document.readyState === 'loading') {{
  document.addEventListener('DOMContentLoaded', init);
}} else {{
  init();
}}
</script>"""


def render(user: dict, csrf: str, flash_ok: str = "", flash_err: str = "",
          banner: str = "", sync: str = "idle", lugar: str = "desconocido",
          ip: str = "") -> str:
    """Renderiza la página completa de la pestaña Firmas."""
    from . import templates

    # Flash messages
    flash_html = ""
    if flash_ok:
        flash_html = f'<div class="flash ok">{esc(flash_ok)}</div>'
    if flash_err:
        flash_html = f'<div class="flash err">{esc(flash_err)}</div>'

    editor_html = _editor_html(user, csrf)

    # Inyectar flash messages antes del editor
    if flash_html:
        editor_html = flash_html + editor_html

    return page(
        active="firmas",
        body=editor_html,
        user=user,
        flash_ok=flash_ok,
        flash_err=flash_err,
        banner=banner,
        sync=sync,
        lugar=lugar,
        ip=ip,
        subtitle="Crea y edita tu firma de correo para todos tus buzones",
    )
