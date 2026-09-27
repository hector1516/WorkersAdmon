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
from .lugar import lugar_de_ip

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


# Estados -> (clase de badge, texto). Usa los .badge del shell
# (.badge-success / .badge-warning / .badge-danger / .badge-info) en vez de las
# clases propias .t-run / .t-stop / .t-err / .t-off, que eran el mismo
# concepto con otro color y otro padding: por eso los estados se veían
# distintos a los de Field y Admon.
ST_TAG = {
    "RUNNING": ("badge-success", "● EN EJECUCIÓN"),
    "STARTING": ("badge-info", "● INICIANDO"),
    "STOPPED": ("badge-warning", "■ DETENIDO"),
    "EXITED": ("badge-warning", "■ TERMINADO"),
    "BACKOFF": ("badge-danger", "▲ REINTENTANDO"),
    "FATAL": ("badge-danger", "▲ FALLO"),
    "UNKNOWN": ("badge-info", "? SIN ESTADO"),
    "NO_HABILITADO": ("badge-info", "○ NO HABILITADO"),
}


def esc(value):
    return html.escape(str(value if value is not None else ""))


def _nav(active, user=None):
    """Barra de pestañas INFERIOR fija (patrón Field/Admon): icono + etiqueta.
    Sin habilitar = "próxima fase"; sin permiso = gris."""
    from . import auth as _auth
    items = []
    for tab in config.TABS:
        # La home no va en la barra: es la grilla de módulos, y un botón
        # "Inicio" al lado de los módulos sería redundante. Se entra con el
        # logo del header.
        if tab["id"] == config.MODULO_INICIO["id"]:
            continue
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
# El markup y los textos son los del shell, iguales en las 3 apps. Lo que
# cambia en el panel es que el banner es de solo lectura (no hay cola offline
# que empujar) y por eso es un <div> y no un <button>.
BANNER_TEXT = {
    "idle": "Todo sincronizado",
    "syncing": "Sincronizando...",
    "error": "Error al sincronizar — reintentando",
    "offline": "Sin conexión — modo offline",
}

# 'pending' no tiene texto fijo: se arma con el número de pendientes.
BANNER_ESTADOS = ("idle", "syncing", "pending", "offline", "error")

# Clase del <span class="dot"> según el estado. El CSS (.sync-header .dot.*)
# lo pone el shell, así que el panel no define ningún color propio.
BANNER_DOT = {
    "offline": "offline",
    "syncing": "syncing",
    "error": "error",
    "pending": "pending",
    "idle": "ok",
}

# Fondo del banner por estado; lo usa la clase del <div>.
BANNER_CLAVE = {
    "offline": "offline",
    "syncing": "syncing",
    "error": "",
    "pending": "has-items",
    "idle": "",
}

LUGAR_TXT = {"oficina": ("🏢", "Oficina"),
             "remoto": ("🏠", "Remoto"),
             "desconocido": ("📍", "—")}


def shell_banner(user=None, sync="idle", pendientes=0, lugar="desconocido", ip=""):
    """Devuelve el HTML del banner, o '' si no hay nada que mostrar.

    Réplica de banner/SyncHeader.svelte → el CSS es shell.css (.sync-header).
    """
    if user is None:
        return ""
    estado = sync if sync in BANNER_ESTADOS else "idle"
    if estado == "pending":
        texto = f"{pendientes or 0} pendiente{(pendientes or 0) != 1 and 's' or ''} — toca para sincronizar"
    else:
        texto = BANNER_TEXT[estado]
    icono, lugar_texto = LUGAR_TXT.get(lugar, LUGAR_TXT["desconocido"])
    clase = BANNER_CLAVE.get(estado, "")
    return (
        f'<div class="sync-header {clase}">'
        f'<span class="dot {BANNER_DOT.get(estado, "ok")}"></span>'
        f'<span>{esc(texto)}</span>'
        f'<span class="who">👤 {esc(user.get("nombre") or user.get("email") or "")}'
        f'<span class="lugar {lugar}" title="{esc(ip)}">'
        f'{icono} {lugar_texto}</span></span>'
        f'<span class="vers">v{esc(config.APP_VERSION)} · '
        f'shell {esc(config._shell_version())}</span>'
        f'</div>')


def shell_actions(user=None, logout="/logout", changelog=True):
    """Barra de acciones del shell (banner/ActionsBar.svelte).

    El panel ya tiene Configuración y Notificaciones como pestañas de su tab
    bar, así que aquí solo se expone 🚪 Salir: un botón se pinta únicamente si
    su manejador existe, y esas dos no se pasan. En el panel las acciones son
    navegaciones (<a>) o <button data-changelog-abrir>, porque es HTML plano
    con formularios POST y Post/Redirect/Get, no manejadores JS como en las
    apps Svelte.
    """
    if user is None:
        return ""
    partes = []
    if logout:
        partes.append(f'<a class="btn btn-sm btn-secondary" href="{esc(logout)}" '
                      f'title="Cerrar sesión">🚪 Salir</a>')
    if changelog:
        # El 📋 no necesita manejador: el script del changelog lo enlaza al
        # modal. Mismo comportamiento que el prop onchangelog por defecto del
        # componente de Svelte.
        partes.append('<button class="btn btn-sm btn-secondary" '
                      'data-changelog-abrir title="Novedades">📋</button>')
    if not partes:
        return ""
    return '<div class="shell-actions">' + "".join(partes) + "</div>"


# ─── Novedades (changelog) ───────────────────────────────────────────────────
def _changelog():
    """(app, version, cambios) leyendo static/changelog.json.

    El texto vive en un JSON, igual que en Field y Admon, para que se pueda
    editar sin tocar Python. Si falta o está roto, el popup simplemente no
    aparece: nunca debe tumbar el panel.
    """
    import json
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "static", "changelog.json"),
                  encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return config.APP_ID, config.APP_VERSION, []
    return (d.get("app") or config.APP_ID,
            d.get("version") or config.APP_VERSION,
            [c for c in (d.get("cambios") or []) if c])


def changelog_modal():
    """Modal de novedades del shell (banner/changelog.py.html).

    El panel no tiene build, así que el HTML se pinta siempre oculto y un
    script pequeño decide si lo muestra. La lógica es la misma que en las apps
    Svelte, con la misma clave: `eccsa:changelog:<appId>`.
    """
    app_id, version, cambios = _changelog()
    if not version or not cambios:
        return ""
    items = "".join(f"<li>{esc(c)}</li>" for c in cambios)
    return (
        f'<div class="shell-modal" id="shellChangelog" hidden '
        f'data-app="{esc(app_id)}" data-version="{esc(version)}">'
        f'<div class="shell-modal-card">'
        f'<div class="shell-modal-hd"><h2>📋 Novedades</h2>'
        f'<span class="shell-modal-ver">v{esc(version)}</span></div>'
        f'<p class="shell-modal-sub">Cambios de {esc(config.TITLE)}</p>'
        f'<ul class="shell-modal-list">{items}</ul>'
        f'<button class="btn btn-primary btn-block" data-changelog-cerrar>'
        f'Entendido</button>'
        f'</div></div>')


# Script del popup de novedades: misma regla que Changelog.svelte — salta solo
# la primera vez que se ve cada versión, y el botón 📋 lo abre a mano.
CHANGELOG_JS = """
(function () {
  var el = document.getElementById('shellChangelog');
  if (!el) return;
  var app = el.dataset.app || 'app';
  var ver = el.dataset.version || '';
  var clave = 'eccsa:changelog:' + app;
  var vista = null;
  try { vista = localStorage.getItem(clave); } catch (e) {}
  function cerrar() { el.hidden = true; }
  var cerrarBtn = el.querySelector('[data-changelog-cerrar]');
  if (cerrarBtn) cerrarBtn.addEventListener('click', cerrar);
  el.addEventListener('click', function (ev) { if (ev.target === el) cerrar(); });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') cerrar();
  });
  var abrir = document.querySelector('[data-changelog-abrir]');
  if (abrir) abrir.addEventListener('click', function () { el.hidden = false; });
  if (!ver || vista === ver) return;
  el.hidden = false;
  try { localStorage.setItem(clave, ver); } catch (e) {}
})();
"""


def lugar_de(request_ip):
    """'oficina' si la IP es privada/red ECCSA, 'remoto' si es pública.

    NO usar: la regla vive UNA sola vez en panel/lugar.py (copia canónica del
    ECCSA-Shell) y el server la pide con lugar_de_handler(self), que además
    resuelve la IP con la precedencia correcta de cabeceras. Esta función se
    queda solo como atajo para cuando ya se tiene la IP resuelta.
    """
    return lugar_de_ip(request_ip), (request_ip or "").strip()


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

# La fuente del shell. El token --font-family del shell pide 'Outfit', y Field
# (src/app.html) y Admon (index.html) la CARGAN desde Google Fonts. El panel
# solo la nominaba en el fallback stack, sin cargarla nunca: por eso se veía en
# Segoe UI mientras las otras dos apps se veían en Outfit. Mismo href que ellas.
FUENTES = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
           '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
           'family=Outfit:wght@300;400;500;600;700&display=swap">')


def page(active, body, user=None, flash_ok="", flash_err="", subtitle="", refresh=0,
         conn=None, banner="", sync="idle", lugar="desconocido", ip=""):
    """Layout general: banner + header fijo + contenido + tab bar inferior."""
    if not banner:
        banner = shell_banner(user, sync=sync, lugar=lugar, ip=ip)
    refresh_js = (AUTO_REFRESH_JS.replace("__SECS__", str(int(refresh)))
                  if refresh else "")
    # Barra de acciones del shell (⚙️ config · 🚪 salir). El panel ya tiene
    # Configuración y Notificaciones como pestañas, así que solo va 🚪.
    # Reemplaza el "Cerrar sesión" suelto que estaba en el <header>.
    actions_html = shell_actions(user)
    # El usuario NO va en el header: en Field y Admon aparece solo en el
    # banner (.sync-header .who). Tenerlo en los dos lados era una diferencia
    # mas con las otras dos apps, y ademas duplicaba el mismo dato.
    user_html = ""
    # conn: "online" (verde) / "offline" (rojo) — lo pasa la vista de Workers
    live_cls = "" if conn is None else ("" if conn == "online" else " off")
    live_txt = "● CONECTADO" if conn != "offline" else "● SIN CONEXIÓN"
    # El banner es fijo (position: fixed), así que el contenido necesita el
    # padding de .shell-below-banner o el header queda tapado. Sin banner
    # (login, o sin sesión) no se aplica.
    wrap_cls = "wrap shell-below-banner" if banner else "wrap"

    # Encabezado del MÓDULO. El header de arriba es la marca (identidad de la
    # app, igual que en Field y Admon) y acá va de qué módulo se trata y qué
    # hace. Antes el nombre de la sección solo se veía en la barra inferior,
    # que es lo último en mirarse; con el contenido de una vistaarga larga no
    # se sabía dónde estabas.
    tab = config.tab_por_id(active)
    if tab and tab.get("modulo"):
        # El subtitulo que pasa cada vista (p.ej. "config central por app")
        # antes vivia en el header. Field y Admon no lo tienen ahi — su header
        # es solo la marca — asi que bajo al bloque de modulo, que es donde
        # aporta: el titulo de la seccion, su descripcion y este detalle.
        extra = f'<div class="sub">{esc(subtitle)}</div>' if subtitle else ""
        modulo_html = (
            f'<div class="modulo">'
            f'<h2 class="modulo-titulo">{esc(tab["modulo"])}</h2>'
            f'<div class="sub">{esc(tab.get("desc", ""))}</div>'
            f'{extra}'
            f'</div>')
    else:
        modulo_html = ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
<title>{esc(config.TITLE)}</title>
{PWA_HEAD}
{FUENTES}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
{banner}
<div class="{wrap_cls}">
  <header class="header">
    <div class="brand-col">
      <a class="logo-link" href="/"
         title="Volver al inicio"><h1 class="brand">
        <img class="brand-logo" src="/logo.png" alt="">
        <span class="brand-name">{esc(config.TITLE)}</span>
      </h1></a>
    </div>
    {actions_html}
  </header>
  {f'<div class="badge-live{live_cls}">{live_txt}</div>' if conn else ''}
  {modulo_html}
  {flash(flash_ok, "ok")}
  {flash(flash_err, "err")}
  {body}
  <footer>
    <span>WorkersAdmon · config central de workers, notificaciones y apps</span>
    <span>API JSON en <a href="/api/status">/api/status</a></span>
  </footer>
  <div class="version-badge">WorkersAdmon v{esc(config.APP_VERSION)}</div>
</div>
{_nav(active, user)}
{changelog_modal()}
{refresh_js}
{CHANGELOG_JS}
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
{FUENTES}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="wrap login-page">
  <header class="header">
    <div class="brand-col">
      <div class="brand">
        <h1 class="brand">
          <img class="brand-logo" src="/logo.png" alt="">
          <span class="brand-name">{esc(config.TITLE)}</span>
        </h1>
      </div>
    </div>
  </header>

  <div class="card login-card">
    <div class="modulo">
      <h2 class="modulo-titulo">🔐 Entrar</h2>
      <div class="sub">Con tu cuenta del HUB.</div>
    </div>

    <div class="pk" id="pk" hidden>
      <button class="btn btn-secondary btn-block" type="button" id="pk_btn">
        🔐 Entrar con passkey</button>
      <div class="pk-status" id="pk_status" role="status"></div>
      <div class="pk-note muted" id="pk_note" hidden></div>
      <div class="pk-divider"><span>o con contraseña</span></div>
    </div>

    <form method="post" action="/login">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field">
        <label for="email">Correo</label>
        <input class="input" id="email" type="email" name="email" autocomplete="username"
               placeholder="usuario@ecc-ssa.com.mx" required>
      </div>
      <div class="field">
        <label for="password">Contraseña</label>
        <input class="input" id="password" type="password" name="password"
               autocomplete="current-password" required>
      </div>
      <button class="btn btn-primary btn-block" type="submit"{disabled}>Entrar</button>
    </form>
    {f'<div class="flash err">{esc(error)}</div>' if error else ''}
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
{FUENTES}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="wrap login-page">
  <header class="header">
    <div class="brand-col">
      <div class="brand">
        <h1 class="brand">
          <img class="brand-logo" src="/logo.png" alt="">
          <span class="brand-name">{esc(config.TITLE)}</span>
        </h1>
      </div>
    </div>
  </header>
  <div class="card login-card">
    <div class="modulo">
      <h2 class="modulo-titulo">📡 Sin conexión</h2>
      <div class="sub">No se pudo contactar con el panel. Revisa tu red y
        vuelve a intentar.</div>
    </div>
    <button class="btn btn-primary btn-block" type="button" onclick="location.reload()">
      🔄 Reintentar</button>
  </div>
</div>
{PWA_JS}
</body>
</html>"""


def forbidden_page(reason, csrf=""):
    """Página de 'sin permiso' (usuario válido pero sin acceso a la sección)."""
    body = (f'<div class="card"><div class="empty">⛔ {esc(reason)}<br><br>'
            f'<a class="btn btn-sm btn-secondary" href="/">← Volver al panel</a>'
            f'</div></div>')
    return page("forbidden", body)
