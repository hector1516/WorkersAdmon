"""
panel/templates.py — Plantillas HTML del panel (tema oscuro ECCSA)
==================================================================
HTML generado en Python puro (sin dependencias, sin JS obligatorio):
todas las acciones son formularios POST con token CSRF y redirección
Post/Redirect/Get, de modo que funcionan también en móvil.
"""
import html
import urllib.parse

from . import config

# Colores y estilos heredados de status_server.py (tema HUB)
CSS = """
:root{--bg:#0F172A;--panel:#1E293B;--panel2:#273449;--line:#334155;
--txt:#E2E8F0;--muted:#94A3B8;--orange:#FF6B00;--yellow:#FFAE00;
--green:#22C55E;--red:#EF4444;--gray:#64748B;--blue:#38BDF8;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font-family:Outfit,'Segoe UI',system-ui,-apple-system,sans-serif;font-size:15px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;
gap:16px;flex-wrap:wrap;padding-bottom:16px;border-bottom:1px solid var(--line)}
.hd{display:flex;gap:14px;align-items:flex-start;min-width:0}
.logo{width:62px;height:62px;object-fit:contain;flex:0 0 auto;padding:5px;
background:#0B1220;border:1px solid var(--line);border-radius:16px}
.logo-big{display:block;width:104px;height:auto;margin:0 auto 12px;
padding:8px;background:#0B1220;border:1px solid var(--line);border-radius:18px}
h1{font-size:1.5rem;margin:0;letter-spacing:.3px}
h1 span{color:var(--orange)}
.sub{color:var(--muted);font-size:.85rem;margin-top:4px}
.badge-live{background:rgba(34,197,94,.12);color:var(--green);border:1px solid
rgba(34,197,94,.4);padding:6px 12px;border-radius:999px;font-size:.8rem;font-weight:600}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 4px}
.nav a,.nav span{padding:9px 16px;border-radius:10px;text-decoration:none;
font-size:.9rem;font-weight:600;border:1px solid var(--line);background:var(--panel)}
.nav a{color:var(--txt)} .nav a:hover{border-color:var(--orange);color:var(--yellow)}
.nav a.active{background:var(--orange);border-color:var(--orange);color:#0F172A}
.nav span.off{color:var(--gray);opacity:.55;cursor:not-allowed}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
gap:12px;margin:22px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:16px 18px}
.card .n{font-size:1.9rem;font-weight:700;line-height:1.1}
.card .l{color:var(--muted);font-size:.8rem;margin-top:6px;text-transform:uppercase;
letter-spacing:.6px}
.card.green .n{color:var(--green)}.card.orange .n{color:var(--orange)}
.card.gray .n{color:var(--muted)}.card.blue .n{color:var(--blue)}
.card.red .n{color:var(--red)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;
overflow:hidden;margin-top:8px}
.panel h2{font-size:1rem;margin:0;padding:16px 18px;border-bottom:1px solid var(--line);
font-weight:600}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th{text-align:left;color:var(--muted);font-weight:600;font-size:.75rem;
text-transform:uppercase;letter-spacing:.6px;padding:12px 14px;
background:var(--panel2);border-bottom:1px solid var(--line)}
td{padding:13px 14px;border-bottom:1px solid rgba(51,65,85,.55);vertical-align:top}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:rgba(255,107,0,.05)}
.name{font-weight:600}
.tag{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.72rem;
font-weight:700;letter-spacing:.4px}
.t-run{background:rgba(34,197,94,.14);color:var(--green);
border:1px solid rgba(34,197,94,.4)}
.t-stop{background:rgba(100,116,139,.16);color:#CBD5E1;
border:1px solid rgba(100,116,139,.45)}
.t-err{background:rgba(239,68,68,.14);color:var(--red);
border:1px solid rgba(239,68,68,.4)}
.t-off{background:rgba(255,174,0,.12);color:var(--yellow);
border:1px solid rgba(255,174,0,.35)}
.t-yes{background:rgba(56,189,248,.12);color:var(--blue);
border:1px solid rgba(56,189,248,.35)}
.mono{font-variant-numeric:tabular-nums;color:#CBD5E1}
.muted{color:var(--muted)}
.empty{padding:26px 18px;color:var(--muted);text-align:center}
.flash{padding:13px 16px;border-radius:12px;margin:16px 0;font-size:.9rem;
border:1px solid}
.flash.ok{background:rgba(34,197,94,.10);color:var(--green);
border-color:rgba(34,197,94,.4)}
.flash.err{background:rgba(239,68,68,.10);color:#FCA5A5;
border-color:rgba(239,68,68,.4)}
.btn{display:inline-block;padding:7px 13px;border-radius:9px;font-size:.8rem;
font-weight:700;border:1px solid transparent;cursor:pointer;text-decoration:none;
color:#0F172A;background:var(--gray)}
.btn.on{background:var(--green);border-color:var(--green)}
.btn.off{background:var(--yellow);border-color:var(--yellow)}
.btn.re{background:var(--blue);border-color:var(--blue)}
.btn.ghost{background:transparent;color:var(--muted);border-color:var(--line)}
.btn.log{background:var(--panel2);color:var(--txt);border-color:var(--line)}
.btn:disabled{opacity:.4;cursor:not-allowed}
form.inline{display:inline}
button.linklike{background:none;border:none;color:var(--yellow);font:inherit;
cursor:pointer;padding:0;text-decoration:underline}
.actions-cell{white-space:nowrap}
.actions{display:flex;gap:6px;flex-wrap:wrap}
pre.log{margin:0;padding:16px 18px;background:#0B1220;color:#CBD5E1;
font-family:ui-monospace,Consolas,monospace;font-size:.78rem;line-height:1.5;
white-space:pre-wrap;word-break:break-word;max-height:70vh;overflow:auto}
footer{margin-top:26px;color:var(--muted);font-size:.8rem;display:flex;
justify-content:space-between;gap:12px;flex-wrap:wrap}
a{color:var(--yellow);text-decoration:none}
code{background:var(--panel2);padding:2px 6px;border-radius:6px;font-size:.82em}
.box{background:var(--panel);border:1px solid var(--line);border-radius:16px;
padding:26px 24px;max-width:430px;margin:36px auto}
.box h1{font-size:1.3rem;margin-bottom:6px}
label{display:block;font-size:.8rem;color:var(--muted);margin:14px 0 6px;
text-transform:uppercase;letter-spacing:.6px}
input[type=email],input[type=password],input[type=text],
input[type=number]{width:100%;padding:11px 13px;
border-radius:10px;border:1px solid var(--line);background:#0B1220;color:var(--txt);
font-size:.95rem;font-family:inherit}
input:focus{outline:none;border-color:var(--orange)}
.submit{width:100%;margin-top:20px;padding:12px;border:none;border-radius:10px;
background:var(--orange);color:#0F172A;font-weight:700;font-size:1rem;cursor:pointer}
.submit:hover{background:var(--yellow)}
.submit:disabled{opacity:.55;cursor:wait}
.pk-status{min-height:1.15em;margin-top:8px;font-size:.88rem;text-align:center;
color:var(--yellow)}
.pk-note{margin-top:12px;font-size:.82rem;text-align:center;line-height:1.5}
.pk-divider{display:flex;align-items:center;gap:10px;margin:16px 0 0;
color:var(--muted);font-size:.74rem;text-transform:uppercase;letter-spacing:.7px}
.pk-divider::before,.pk-divider::after{content:"";flex:1;height:1px;
background:var(--line)}
.err{color:#FCA5A5;font-size:.87rem;margin-top:12px}
select{width:100%;padding:11px 13px;border-radius:10px;border:1px solid var(--line);
background:#0B1220;color:var(--txt);font-size:.95rem;font-family:inherit}
select:focus{outline:none;border-color:var(--orange)}
textarea{width:100%;padding:11px 13px;border-radius:10px;border:1px solid var(--line);
background:#0B1220;color:var(--txt);font-size:.95rem;font-family:inherit;
resize:vertical;line-height:1.45}
textarea:focus{outline:none;border-color:var(--orange)}
.cfg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));
gap:16px;padding:18px}
.cfg-field label{margin-top:0}
.cfg-field .help{color:var(--muted);font-size:.78rem;margin-top:5px;line-height:1.45}
.ro{background:#0B1220;border:1px solid var(--line);border-radius:10px;
padding:11px 13px;color:#CBD5E1;word-break:break-word;font-size:.9rem;min-height:42px}
.cfg-save{padding:0 18px 18px;display:flex;gap:8px;align-items:center}
.cfg-note{padding:12px 18px;border-top:1px solid var(--line);color:var(--muted);
font-size:.78rem;background:var(--panel2)}
.card-desc{padding:14px 18px 2px;color:var(--muted);font-size:.86rem;line-height:1.55}
.wdesc{margin-top:7px;font-size:.78rem}
.wdesc summary{cursor:pointer;color:var(--muted);font-weight:600;list-style:none}
.wdesc summary::-webkit-details-marker{display:none}
.wdesc summary:hover{color:var(--yellow)}
.wdesc[open] summary{color:var(--orange)}
.wdesc div{margin-top:6px;padding:9px 11px;background:#0B1220;
border:1px solid var(--line);border-left:2px solid var(--orange);
border-radius:8px;line-height:1.55;color:#CBD5E1}
@media(max-width:820px){.hide-sm{display:none}table{font-size:.82rem}
.wrap{padding:16px 12px 50px}}
"""

ST_TAG = {
    "RUNNING": ("t-run", "● EN EJECUCIÓN"),
    "STARTING": ("t-run", "● INICIANDO"),
    "STOPPED": ("t-stop", "■ DETENIDO"),
    "EXITED": ("t-stop", "■ TERMINADO"),
    "BACKOFF": ("t-err", "▲ REINTENTANDO"),
    "FATAL": ("t-err", "▲ FALLO"),
    "UNKNOWN": ("t-off", "? SIN ESTADO"),
    "NO_HABILITADO": ("t-off", "○ NO HABILITADO"),
}


def esc(value):
    return html.escape(str(value if value is not None else ""))


def _nav(active, user=None):
    """Barra de pestañas: sin habilitar = "próxima fase"; sin permiso = gris."""
    from . import auth as _auth
    items = []
    for tab in config.TABS:
        perm = tab.get("perm")
        if perm and user is not None and not _auth.has_perm(user, perm):
            items.append(f'<span class="off" title="Requiere el permiso {perm}">'
                         f'{tab["label"]}</span>')
        elif tab["enabled"]:
            cls = ' class="active"' if tab["id"] == active else ""
            items.append(f'<a href="{tab["href"]}"{cls}>{tab["label"]}</a>')
        else:
            items.append(f'<span class="off" title="Próxima fase">{tab["label"]}</span>')
    return '<div class="nav">' + "".join(items) + "</div>"


def flash(message, kind="ok"):
    if not message:
        return ""
    return f'<div class="flash {kind}">{esc(message)}</div>'


# ─── PWA: metadatos de instalación y registro del service worker ────────────
# Mismos meta/links que Field y Admon: el panel se puede instalar en el
# escritorio/celular desde https://worker.ecc-sa.com.mx.
PWA_HEAD = """
<meta name="theme-color" content="#0F172A">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Workers Admon">
<meta name="mobile-web-app-capable" content="yes">
<meta name="description" content="Panel de control de workers, notificaciones y configuración ECCSA">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png">
<link rel="apple-touch-icon" sizes="152x152" href="/icons/icon-152x152.png">
<link rel="apple-touch-icon" sizes="120x120" href="/icons/icon-120x120.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192x192.png">
<link rel="icon" type="image/png" sizes="512x512" href="/icons/icon-512x512.png">
"""

PWA_JS = """
<script>
/* PWA: registra el service worker (no crítico: si falla, el panel sigue igual). */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}
</script>
"""

VIEWPORT = ('<meta name="viewport" content="width=device-width,initial-scale=1,'
            'maximum-scale=1,user-scalable=no,viewport-fit=cover">')


def page(active, body, user=None, flash_ok="", flash_err="", subtitle="", refresh=0):
    """Layout general: header + pestañas + contenido + footer."""
    refresh_meta = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
                    if refresh else "")
    user_html = ""
    if user:
        user_html = (
            f'<div class="sub">👤 {esc(user.get("nombre") or user.get("email"))} · '
            f'<form class="inline" method="post" action="/logout">'
            f'<button class="linklike" type="submit">Cerrar sesión</button>'
            f'</form></div>')
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
{refresh_meta}
<title>{esc(config.TITLE)}</title>
{PWA_HEAD}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="hd">
      <img class="logo" src="/logo.png" alt="{esc(config.TITLE)}">
      <div>
      <h1>⚙️ {esc(config.TITLE)} <span>· panel de control</span></h1>
      <div class="sub">Contenedor <code>workersadmon</code> · {subtitle}</div>
      {user_html}
      </div>
    </div>
    <div class="badge-live">● CONECTADO</div>
  </header>
  {_nav(active, user)}
  {flash(flash_ok, "ok")}
  {flash(flash_err, "err")}
  {body}
  <footer>
    <span>WorkersAdmon · config central de workers, notificaciones y apps</span>
    <span>API JSON en <a href="/api/status">/api/status</a></span>
  </footer>
</div>
{PWA_JS}
</body>
</html>"""


def login_page(error="", csrf="", locked=False):
    """Formulario de acceso: passkey (WebAuthn) primero y contraseña como respaldo."""
    if locked:
        error = error or "Demasiados intentos. Espera un minuto y vuelve a intentar."
    disabled = " disabled" if locked else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
<title>Iniciar sesión — {esc(config.TITLE)}</title>
{PWA_HEAD}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="box">
    <img class="logo-big" src="/logo.png" alt="{esc(config.TITLE)}">
    <h1>🔐 {esc(config.TITLE)}</h1>
    <div class="muted">Panel de control de workers, notificaciones y
      configuración. Entra con tu cuenta del HUB.</div>

    <div class="pk" id="pk" hidden>
      <button class="submit" type="button" id="pk_btn">🔐 Entrar con passkey</button>
      <div class="pk-status" id="pk_status" role="status"></div>
      <div class="pk-note muted" id="pk_note" hidden></div>
      <div class="pk-divider"><span>o con contraseña</span></div>
    </div>

    <form method="post" action="/login">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <label for="email">Correo</label>
      <input id="email" type="email" name="email" autocomplete="username"
             placeholder="usuario@ecc-ssa.com.mx" required>
      <label for="password">Contraseña</label>
      <input id="password" type="password" name="password"
             autocomplete="current-password" required>
      <button class="submit" type="submit"{disabled}>Entrar</button>
    </form>
    {f'<div class="err">{esc(error)}</div>' if error else ''}
  </div>
</div>
<script>
/* Login con passkey: se muestra SOLO si el navegador lo soporta y la página
   está en contexto seguro (https://worker.ecc-sa.com.mx). */
(function () {{
  function el(id) {{ return document.getElementById(id); }}
  function cookie(name) {{
    var parts = (document.cookie || '').split(';');
    for (var i = 0; i < parts.length; i++) {{
      var kv = parts[i].trim().split('=');
      if (kv[0] === name) return decodeURIComponent(kv.slice(1).join('='));
    }}
    return '';
  }}
  function status(m) {{ el('pk_status').textContent = m || ''; }}
  function b64ToBuf(b64) {{
    var s = (b64 || '').replace(/-/g, '+').replace(/_/g, '/');
    var bin = atob(s + '='.repeat((4 - (s.length % 4)) % 4));
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr.buffer;
  }}
  function bufToB64(buf) {{
    var b = new Uint8Array(buf), s = '';
    for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
  }}
  function serialize(cred) {{
    if (typeof cred.toJSON === 'function') return cred.toJSON();   // Chrome/Safari/FF modernos
    var r = cred.response;
    var out = {{ id: cred.id, rawId: bufToB64(cred.rawId), type: cred.type,
      response: {{ clientDataJSON: bufToB64(r.clientDataJSON),
                   authenticatorData: bufToB64(r.authenticatorData),
                   signature: bufToB64(r.signature),
                   userHandle: r.userHandle ? bufToB64(r.userHandle) : null }},
      clientExtensionResults: {{}} }};
    try {{ out.clientExtensionResults = cred.clientExtensionResults(); }} catch (e) {{}}
    return out;
  }}

  var soporta = false, seguro = !!window.isSecureContext;
  try {{
    soporta = !!(window.PublicKeyCredential && navigator.credentials &&
                 navigator.credentials.get);
  }} catch (e) {{}}
  if (!soporta || !seguro) {{
    var nota = el('pk_note');
    nota.hidden = false;
    nota.textContent = seguro
      ? 'Este navegador no soporta passkeys: usa tu contraseña.'
      : '🔒 El passkey solo funciona en https://worker.ecc-sa.com.mx';
    return;
  }}
  el('pk').hidden = false;

  el('pk_btn').addEventListener('click', async function () {{
    el('pk_btn').disabled = true;
    status('🔄 Preparando passkey…');
    try {{
      var begin = await fetch('/passkey/begin', {{
        method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ csrf: cookie('panel_csrf') }})
      }});
      var bj = await begin.json();
      if (!bj.ok) throw new Error(bj.msg || 'No se pudo iniciar');
      var opts = bj.options;
      if (opts.challenge) opts.challenge = b64ToBuf(opts.challenge);
      if (opts.allowCredentials) opts.allowCredentials.forEach(function (c) {{
        if (c.id) c.id = b64ToBuf(c.id);
      }});
      var cred = await navigator.credentials.get({{ publicKey: opts }});
      var fin = await fetch('/passkey/finish', {{
        method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ credential: serialize(cred), state: bj.state,
                                csrf: cookie('panel_csrf') }})
      }});
      var fj = await fin.json();
      if (!fj.ok) throw new Error(fj.msg || 'Passkey no válida');
      status('✅ ¡Bienvenido' + (fj.nombre ? ', ' + fj.nombre : '') + '!');
      window.location.href = fj.redirect || '/';
    }} catch (e) {{
      var n = (e && e.name) || '';
      if (n === 'NotAllowedError' || n === 'AbortError') {{
        status('⚠️ Passkey cancelada. Usa tu contraseña.');
      }} else {{
        status('❌ ' + ((e && e.message) || e));
      }}
      el('pk_btn').disabled = false;
    }}
  }});
}})();
</script>
{PWA_JS}
</body>
</html>"""


def offline_page():
    """Página que entrega el service worker cuando no hay conexión.

    No lleva datos: solo reintentar. Así el HTML autenticado nunca se sirve
    desde la caché (evita mezclar sesiones en máquinas compartidas).
    """
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
<title>Sin conexión — {esc(config.TITLE)}</title>
{PWA_HEAD}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="box">
    <img class="logo-big" src="/logo.png" alt="{esc(config.TITLE)}">
    <h1>📡 Sin conexión</h1>
    <div class="muted">No se pudo contactar con el panel. Revisa tu red y
      vuelve a intentar.</div>
    <button class="submit" type="button" onclick="location.reload()">
      🔄 Reintentar</button>
  </div>
</div>
{PWA_JS}
</body>
</html>"""


def forbidden_page(reason, csrf=""):
    """Página de 'sin permiso' (usuario válido pero sin acceso a la sección)."""
    body = (f'<div class="panel"><div class="empty">⛔ {esc(reason)}<br><br>'
            f'<a href="/">← Volver al panel</a></div></div>')
    return page("forbidden", body)
