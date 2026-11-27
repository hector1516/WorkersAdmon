"""
panel/templates.py — Plantillas HTML del panel (lenguaje Apple / HIG)
===================================================================
HTML generado en Python puro (sin dependencias, sin JS obligatorio):
todas las acciones son formularios POST con token CSRF y redirección
Post/Redirect/Get, de modo que funcionan también en móvil.
"""
import html
import os
import urllib.parse

from . import config
from .icons import icono
from .lugar import lugar_de_ip

# ─────────────────────────────────────────────────────────────────────────────
# CSS del panel: sistema propio, lenguaje Apple/HIG
# ═════════════════════════════════════════════════════════════════════════════
# · panel/apple.css → TODO el diseño (tokens, layout, componentes y la capa de
#                     compatibilidad con las clases viejas de las vistas que
#                     todavía se están migrando). Ver panel/DESIGN.md.
# Ya NO se carga panel/shell.css: el panel dejó de seguir el look de Field y
# Admon. panel/panel.css tampoco se carga (sus clases se traducen en apple.css),
# pero el archivo se queda: el script de chequeo del shell y algunas pruebas lo
# leen del disco.
# Se lee de disco para poder ajustar el diseño sin tocar Python; si el archivo
# faltara, el panel sigue sirviendo HTML (solo se vería sin estilos).
_CSS_FILES = ("apple.css",)


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


# Etiquetas que un subtítulo puede traer escritas a mano. Se listan sin
# atributos a propósito: con eso alcanza para marcar rutas y comandos y no se
# abre la puerta a inyectar un <a href=...> o un <img onerror=...>.
_SUB_TAGS = ("code", "b", "strong", "em", "br")


def esc_sub(texto):
    """Escapa el subtítulo conservando las pocas etiquetas que trae a mano.

    esc() convierte cualquier etiqueta en texto visible, y los subtítulos usan
    <code> para los directorios y archivos (p.ej. "datos en <code>/data</code>"):
    al escaparlo el usuario veía literalmente "datos en <code>/data</code>" en
    Logs y Estado. Se escapa TODO y luego se devuelven solo las etiquetas de la
    lista, así lo que venga del usuario (nombre del worker, por ejemplo) sigue
    escapado igual que antes.
    """
    out = esc(texto)
    for tag in _SUB_TAGS:
        out = out.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        out = out.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return out


def flash(message, kind="ok"):
    if not message:
        return ""
    return f'<div class="flash {kind}">{esc(message)}</div>'


# ─── Navegación del panel ────────────────────────────────────────────────────
# Icono de cada destino (panel/DESIGN.md → Iconos). El texto visible es el
# `modulo` del tab, sin emoji: el icono lo pone esta navegación con icono().
NAV_ICONS = {
    "estado": "dashboard",
    "logs": "logs",
    "config": "settings",
    "apps": "apps",
    "notificaciones": "bell",
    "whatsapp": "whatsapp",
    "eccsita": "chat",
    "avisos": "send",
    "asistencia": "clock",
    "correo": "mail",
    "triaje": "ticket",
}

# Los cuatro destinos diarios de la barra inferior; el resto cabe en «Más»,
# que es el quinto hueco. Seis objetivos no cabían a 390 px sin apretar.
NAV_INFERIOR = ("estado", "logs", "whatsapp", "notificaciones")


def _puede_ver(user, tab):
    """Si el usuario puede abrir el destino. Sin usuario, todo se pinta normal
    (el login no tiene sesión y no debe enseñar módulos en gris)."""
    perm = tab.get("perm")
    if not perm or user is None:
        return True
    from . import auth          # import tardío: templates no debe depender de auth
    try:
        return auth.has_perm(user, perm)
    except Exception:
        return False


def _etiqueta(tab):
    return tab.get("modulo") or (tab.get("label") or "").strip()


def _nav_item(tab, active, user):
    """Un destino: enlace si se puede abrir, texto apagado si falta permiso.

    Las clases se escriben `rail__link nav-item` (no al revés) porque
    `rail__link` es la que manda en el CSS nuevo y `nav-item` se conserva como
    nombre del contrato de DESIGN.md.

    `aria-label` + `title` van siempre: entre 768 y 1023 px la barra lateral
    oculta el texto con `display:none`, y un `display:none` también le quita el
    nombre accesible al enlace.
    """
    icono_html = icono(NAV_ICONS.get(tab["id"], "folder"))
    nombre = _etiqueta(tab)
    label = f'<span class="nav-item__text">{esc(nombre)}</span>'
    activo = tab["id"] == active
    clases = "rail__link nav-item" + (" is-active" if activo else "")
    if not _puede_ver(user, tab):
        return (f'<span class="{clases} is-off" aria-disabled="true" '
                f'aria-label="{esc(nombre)}" title="Requiere el permiso '
                f'{esc(tab.get("perm"))}">'
                f'{icono_html}{label}</span>')
    aria = ' aria-current="page"' if activo else ""
    return (f'<a class="{clases}" href="{esc(tab["href"])}"{aria} '
            f'aria-label="{esc(nombre)}" title="{esc(nombre)}">'
            f'{icono_html}{label}</a>')


def _nav(active, user=None, inferior=False):
    """El `<nav>` de navegación.

    inferior=False → barra lateral (escritorio y tableta), con todos los
    destinos. inferior=True → barra inferior del teléfono: los cuatro destinos
    diarios y una hoja «Más» con el resto, hecha con `<details>` para no
    necesitar JavaScript (cinco huecos en total, el último es «Más»).
    """
    tabs = [t for t in config.TABS if t.get("enabled", True)]
    if not inferior:
        items = "".join(_nav_item(t, active, user) for t in tabs)
        return (f'<nav class="rail__nav" aria-label="Navegación principal">'
                f'{items}</nav>')

    diarios = [t for t in tabs if t["id"] in NAV_INFERIOR]
    resto = [t for t in tabs if t["id"] not in NAV_INFERIOR]
    items = "".join(_nav_item(t, active, user) for t in diarios)
    mas = "".join(_nav_item(t, active, user) for t in resto)
    abierto = ' open' if active in {t["id"] for t in resto} else ''
    salir = ('<a class="rail__link nav-item" href="/logout">'
             f'{icono("logout")}<span class="nav-item__text">Salir</span></a>')
    return (f'<nav class="rail rail--bottom" aria-label="Navegación inferior">'
            f'{items}'
            f'<details class="rail__more"{abierto}>'
            f'<summary class="rail__link nav-item" aria-label="Más destinos">'
            f'{icono("chevron")}<span class="nav-item__text">Más</span>'
            f'</summary>'
            f'<div class="sheet rail__sheet">{mas}{salir}</div>'
            f'</details>'
            f'</nav>')


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
<meta name="theme-color" content="#F5F5F7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">
<meta name="color-scheme" content="light dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
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
            'viewport-fit=cover">')

# Tipografía: la del sistema (DESIGN.md). No se carga ninguna fuente externa:
# el panel usa -apple-system / Segoe UI / Roboto, que ya están en el equipo y
# no hacen una petición de red (antes se nominaba Outfit de Google Fonts).


def _toast(message, kind="ok"):
    """Aviso dentro del flujo (no fixed): se lee donde ocurrió la acción."""
    if not message:
        return ""
    return (f'<div class="toast toast--{kind}" role="status">'
            f'{icono("check" if kind == "ok" else "alert")}'
            f'<span>{esc(message)}</span></div>')


def page(active, body, user=None, flash_ok="", flash_err="", subtitle="", refresh=0,
         conn=None, banner="", sync="idle", lugar="desconocido", ip="",
         back_href=None, back_label="← Volver al panel"):
    """Layout general: barra lateral, barra superior y contenido.

    `back_href`: a dónde vuelve la flecha del bloque del módulo. None = la home
    (es el menú, no tiene a dónde volver); las vistas que están dentro de un
    módulo lo dejan en None y se resuelve abajo contra el módulo al que
    pertenecen. Solo se pasa a mano cuando el destino es otro (p.ej. el log de
    un worker vuelve a Estado).

    `banner`, `sync`, `lugar` e `ip` se conservan en la firma porque las vistas
    ya las pasan, pero el banner del shell se retiró: el lugar y la IP ahora
    son el `title` del usuario en la barra superior, y `sync`/`banner` no se
    pintan.
    """
    refresh_js = (AUTO_REFRESH_JS.replace("__SECS__", str(int(refresh)))
                  if refresh else "")
    tab = config.tab_por_id(active)
    titulo = (tab or {}).get("modulo") or config.TITLE
    es_inicio = active == config.MODULO_RAIZ
    # El subtítulo de la vista manda; si no hay, se usa la descripción del
    # módulo (la raíz —Estado— no: ahí el subtítulo es el de la vista).
    detalle = subtitle or ("" if es_inicio or tab is None else tab.get("desc", ""))
    # esc_sub, no esc: los subtítulos traen <code> con rutas y escaparlo los
    # mostraba como texto literal.
    detalle_html = (f'<p class="topbar__sub">{esc_sub(detalle)}</p>'
                    if detalle else "")
    # Sin flecha por defecto: la navegación lateral ya dice dónde estás. Solo
    # se pinta cuando una vista la pide a propósito (p. ej. el log de un worker
    # vuelve a Estado).
    volver = ""
    if back_href:
        volver = (f'<a class="btn btn-sm btn-secondary modulo-back" '
                  f'href="{esc(back_href)}" title="Volver atrás">'
                  f'{esc(back_label)}</a>')
    # conn: "online" (verde) / "offline" (rojo) — lo pasa la vista de Workers.
    # La clase `badge-live` se conserva: es el piloto de conexión de siempre.
    # Ya no es un elemento propio de la cabecera: va como texto pequeño junto
    # al nombre del usuario (y se oculta en el teléfono con él).
    live = ""
    if conn:
        ok = conn != "offline"
        live = (f'<span class="topbar__conn badge-live '
                f'{"is-ok" if ok else "is-bad"}">'
                f'{esc("Conectado" if ok else "Sin conexión")}</span>')
    usuario = ""
    salir = ""
    if user is not None:
        nombre = user.get("nombre") or user.get("email") or ""
        donde = "" if lugar in ("", "desconocido") else f' · {esc(lugar)}'
        usuario = (f'<span class="topbar__who" title="{esc(ip or nombre)}">'
                   f'{live}<span class="topbar__user">{esc(nombre)}{donde}</span>'
                   f'</span>')
        salir = (f'<a class="btn btn-sm btn-secondary btn--quiet" href="/logout" '
                 f'title="Cerrar sesión">{icono("logout")}<span>Salir</span></a>')
    elif live:
        usuario = live
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
{VIEWPORT}
<title>{esc(config.TITLE)} · {esc(titulo)}</title>
{PWA_HEAD}
<link rel="icon" type="image/png" href="/logo.png">
<style>{CSS}</style>
</head>
<body>
<div class="app">
<aside class="rail">
  <a class="logo-link" href="/" title="Volver al inicio">
    {icono("dashboard", 22)}<span class="rail__brand-name">Workers</span>
  </a>
  {_nav(active, user)}
  <div class="rail__foot">
    <span class="rail__version">v{esc(config.APP_VERSION)}</span>
    {salir}
  </div>
</aside>
<main class="main">
  <header class="topbar">
    <div class="topbar__titles">
      {volver}
      <h1 class="topbar__title">{esc(titulo)}</h1>
      {detalle_html}
    </div>
    <div class="shell-actions topbar__actions">
      {usuario}
      {salir}
    </div>
  </header>
  <div class="content">
    {_toast(flash_ok, "ok")}
    {_toast(flash_err, "err")}
    {body}
    <footer class="foot">
      <span>WorkersAdmon · config central de workers, notificaciones y apps</span>
      <span>API JSON en <a href="/api/status">/api/status</a></span>
      <span class="foot__version">v{esc(config.APP_VERSION)}</span>
    </footer>
  </div>
</main>
{_nav(active, user, inferior=True)}
</div>
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
<body class="auth">
<main class="auth__wrap">
  <div class="auth__brand">
    {icono("dashboard", 24)}<span>Workers</span>
  </div>
  <div class="card auth__card login-card">
    <h1 class="auth__title">Entrar</h1>
    <p class="auth__sub">Con tu cuenta del HUB.</p>

    <div class="pk" id="pk" hidden>
      <button class="btn btn--primary btn-block" type="button" id="pk_btn">
        {icono("check")}<span>Entrar con passkey</span></button>
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
      <button class="btn btn--primary btn-block" type="submit"{disabled}>Entrar</button>
    </form>
    {f'<div class="toast toast--err" role="alert">{esc(error)}</div>' if error else ''}
  </div>
  <p class="auth__foot">WorkersAdmon · v{esc(config.APP_VERSION)}</p>
</main>
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
      : 'El passkey solo funciona en https://worker.ecc-sa.com.mx';
    return;
  }}
  el('pk').hidden = false;

  el('pk_btn').addEventListener('click', async function () {{
    el('pk_btn').disabled = true;
    status('Preparando passkey…');
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
      status('¡Bienvenido' + (fj.nombre ? ', ' + fj.nombre : '') + '!');
      window.location.href = fj.redirect || '/';
    }} catch (e) {{
      var n = (e && e.name) || '';
      if (n === 'NotAllowedError' || n === 'AbortError') {{
        status('Passkey cancelada. Usa tu contraseña.');
      }} else {{
        status('No se pudo entrar: ' + ((e && e.message) || e));
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
<body class="auth">
<main class="auth__wrap">
  <div class="auth__brand">
    {icono("dashboard", 24)}<span>Workers</span>
  </div>
  <div class="card auth__card">
    <div class="empty">
      {icono("alert", 28)}
      <h1 class="auth__title">Sin conexión</h1>
      <p class="auth__sub">No se pudo contactar con el panel. Revisa tu red y
        vuelve a intentar.</p>
      <button class="btn btn--primary btn-block" type="button" onclick="location.reload()">
        {icono("refresh")}<span>Reintentar</span></button>
    </div>
  </div>
</main>
{PWA_JS}
</body>
</html>"""


def forbidden_page(reason, csrf=""):
    """Página de 'sin permiso' (usuario válido pero sin acceso a la sección)."""
    body = (f'<div class="card"><div class="empty">'
            f'{icono("alert", 28)}<p>{esc(reason)}</p>'
            f'<a class="btn btn--quiet" href="/">← Volver al panel</a>'
            f'</div></div>')
    return page("forbidden", body)
