"""
panel/templates.py — Plantillas HTML del panel (tema oscuro ECCSA)
==================================================================
HTML generado en Python puro (sin dependencias, sin JS obligatorio):
todas las acciones son formularios POST con token CSRF y redirección
Post/Redirect/Get, de modo que funcionan también en móvil.
"""
import html
import os
import urllib.parse

from . import config

# ─────────────────────────────────────────────────────────────────────────────
# CSS del panel = shell común (ECCSA-Shell) + CSS propio del panel
# ═════════════════════════════════════════════════════════════════════════════
# · panel/shell.css  → copia CANÓNICA que propaga tools/sync_shell.py del repo
#                      ECCSA-Shell (tokens, base, banner, tab bar, componentes).
#                      NO se edita a mano.
# · panel/panel.css  → lo propio del panel (wcard, chklist, cfg-*, pk-*, …).
# Se leen de disco para que el shell se pueda actualizar sin tocar este código;
# si faltaran, el panel sigue viéndose (usa solo lo que encuentre).
_CSS_FILES = ("shell.css", "panel.css")


def _read_css():
    base = os.path.dirname(os.path.abspath(__file__))
    out = []
    for name in _CSS_FILES:
        path = os.path.join(base, name)
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(fh.read())
        except OSError:
            pass
    return "\n".join(out)


CSS = _read_css()


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
    """Barra de pestañas INFERIOR fija (patrón Field/Admon): icono + etiqueta.
    Sin habilitar = "próxima fase"; sin permiso = gris."""
    from . import auth as _auth
    items = []
    for tab in config.TABS:
        icon, _, text = tab["label"].partition(" ")
        text = text or tab["label"]
        perm = tab.get("perm")
        if perm and user is not None and not _auth.has_perm(user, perm):
            items.append(f'<span class="nav-item off" title="Requiere el permiso {perm}">'
                         f'<span class="nav-icon">{icon}</span>'
                         f'<span>{esc(text)}</span></span>')
        elif tab["enabled"]:
            cls = " active" if tab["id"] == active else ""
            items.append(f'<a class="nav-item{cls}" href="{tab["href"]}">'
                         f'<span class="nav-icon">{icon}</span>'
                         f'<span>{esc(text)}</span></a>')
        else:
            items.append(f'<span class="nav-item off" title="Próxima fase">'
                         f'<span class="nav-icon">{icon}</span>'
                         f'<span>{esc(text)}</span></span>')
    return '<nav class="bottom-nav" aria-label="Secciones del panel">' + "".join(items) + "</nav>"


def flash(message, kind="ok"):
    if not message:
        return ""
    return f'<div class="flash {kind}">{esc(message)}</div>'


# ─── Banner común (ECCSA-Shell · ver docs/CONTRATO.md en el repo) ───────────
# Fijo arriba, semitransparente: estado de sincronización + usuario + si está
# en la oficina o remoto + versiones. Mismos textos en Field y Admon.
BANNER_TEXT = {
    "idle": "Todo sincronizado",
    "syncing": "Sincronizando…",
    "pending": "{n} pendientes — toca para sincronizar",
    "offline": "Sin conexión — modo offline",
    "error": "Error al sincronizar — reintentando",
}


def shell_banner(user=None, sync="idle", pendientes=0, lugar="desconocido", ip=""):
    """Devuelve el HTML del banner, o '' si no hay nada que mostrar."""
    if user is None:
        return ""
    estado = sync if sync in BANNER_TEXT else "idle"
    texto = BANNER_TEXT[estado].replace("{n}", str(pendientes or 0))
    lugar_txt = {"oficina": "Oficina", "remoto": "Remoto"}.get(lugar, "—")
    lugar_icono = {"oficina": "🏢", "remoto": "🏠"}.get(lugar, "📍")
    return (
        f'<div class="shell-banner {estado}">'
        f'<span class="dot"></span><span class="txt">{esc(texto)}</span>'
        f'<span class="sep"></span>'
        f'<span class="who">👤 {esc(user.get("nombre") or user.get("email") or "")}</span>'
        f'<span class="lugar {lugar}" title="{esc(ip)}">'
        f'{lugar_icono} {lugar_txt}</span>'
        f'<span class="vers">v{esc(config.APP_VERSION)} · '
        f'shell {esc(config._shell_version())}</span>'
        f'</div>')


def lugar_de(request_ip):
    """'oficina' si la IP es privada/red ECCSA, 'remoto' si es pública.
    Misma regla que Field (GET /api/online/ubicacion → on_network)."""
    import ipaddress
    ip = (request_ip or "").strip()
    if not ip:
        return "desconocido", ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "desconocido", ip
    return ("oficina" if (addr.is_private or addr.is_loopback) else "remoto"), ip


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

# Auto-refresco de las pestañas de estado. Antes se usaba
# <meta http-equiv="refresh">, que en el iPhone recargaba cada 15 s y además
# botaba al usuario al inicio de la página; esto recarga conservando el scroll
# y solo cuando la pestaña está a la vista.
AUTO_REFRESH_JS = """
<script>
/* Auto-refresco de la vista (patrón Field): conserva el scroll y espera a que
   la pestaña esté a la vista antes de recargar. */
(function () {
  var KEY = 'wa_scroll';
  function restore() {
    try {
      var y = sessionStorage.getItem(KEY);
      if (y !== null) {
        sessionStorage.removeItem(KEY);
        var top = parseInt(y, 10) || 0;
        requestAnimationFrame(function () { window.scrollTo(0, top); });
      }
    } catch (e) {}
  }
  function tick() {
    if (document.visibilityState === 'visible') { window.location.reload(); return; }
    setTimeout(tick, 5000);
  }
  restore();
  window.addEventListener('pagehide', function () {
    try { sessionStorage.setItem(KEY, String(window.scrollY)); } catch (e) {}
  });
  setTimeout(tick, __SECS__ * 1000);
})();
</script>
"""

VIEWPORT = ('<meta name="viewport" content="width=device-width,initial-scale=1,'
            'maximum-scale=1,user-scalable=no,viewport-fit=cover">')


def page(active, body, user=None, flash_ok="", flash_err="", subtitle="", refresh=0,
         conn=None, banner="", sync="idle", lugar="desconocido", ip=""):
    """Layout general: banner + header fijo + contenido + tab bar inferior."""
    if not banner:
        banner = shell_banner(user, sync=sync, lugar=lugar, ip=ip)
    refresh_js = (AUTO_REFRESH_JS.replace("__SECS__", str(int(refresh)))
                  if refresh else "")
    user_html = ""
    if user:
        user_html = (
            f'<div class="sub">👤 {esc(user.get("nombre") or user.get("email"))} · '
            f'<form class="inline" method="post" action="/logout">'
            f'<button class="linklike" type="submit">Cerrar sesión</button>'
            f'</form></div>')
    # conn: "online" (verde) / "offline" (rojo) — lo pasa la vista de Workers
    live_cls = "" if conn is None else ("" if conn == "online" else " off")
    live_txt = "● CONECTADO" if conn != "offline" else "● SIN CONEXIÓN"
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
<title>{esc(config.TITLE)}</title>
{PWA_HEAD}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
{banner}
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
    {f'<div class="badge-live{live_cls}">{live_txt}</div>' if conn else ''}
  </header>
  {flash(flash_ok, "ok")}
  {flash(flash_err, "err")}
  {body}
  <footer>
    <span>WorkersAdmon · config central de workers, notificaciones y apps</span>
    <span>API JSON en <a href="/api/status">/api/status</a></span>
  </footer>
</div>
{_nav(active, user)}
{refresh_js}
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
