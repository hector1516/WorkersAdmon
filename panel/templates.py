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

# ─────────────────────────────────────────────────────────────────────────────
# CSS del panel — patrón "ECCSA Shell" (el mismo de Field y Admon; ver DESIGN.md)
# ═════════════════════════════════════════════════════════════════════════════
# Tokens: los mismos valores que `src/app.css` de Field/Admon (@theme):
#   bg #0F172A · surface #1E293B · surface-2 #334155 · primary #FF6B00 …
# Shell: 100dvh + fondo con engrane al 5% + tab bar inferior fija con
# safe-area + header sticky. Móvil: objetivos táctiles ≥44px, inputs a 16px
# (si no, iOS hace zoom al enfocar) y cero tap-highlight.
CSS = """
:root{--bg:#0F172A;--panel:#1E293B;--panel2:#334155;--line:rgba(255,255,255,.08);
--txt:#F8FAFC;--muted:#94A3B8;--orange:#FF6B00;--yellow:#FFAE00;
--green:#22C55E;--red:#EF4444;--gray:#64748B;--blue:#38BDF8;
--radius:16px;--radius-sm:12px;--nav-h:64px}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--txt);
 font-family:Outfit,'Segoe UI',system-ui,-apple-system,sans-serif;font-size:16px;
 min-height:100dvh;-webkit-tap-highlight-color:transparent;
 -webkit-font-smoothing:antialiased;overscroll-behavior-y:none}
/* Textura de fondo (engrane) — igual que Field y Admon */
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;
 background:url('/engrane.png') center center / 140px 140px repeat;opacity:.05}
.wrap{position:relative;z-index:1;max-width:1180px;margin:0 auto;
 padding:0 20px calc(var(--nav-h) + 22px + env(safe-area-inset-bottom))}
/* Header fijo (como la status-bar de Field) con desenfoque al hacer scroll */
header{position:sticky;top:0;z-index:90;display:flex;align-items:center;
 justify-content:space-between;gap:16px;flex-wrap:wrap;padding:10px 0 12px;
 background:rgba(15,23,42,.82);-webkit-backdrop-filter:blur(12px);
 backdrop-filter:blur(12px);border-bottom:1px solid var(--line);
 margin:0 -20px 0;padding-left:20px;padding-right:20px}
.hd{display:flex;gap:14px;align-items:flex-start;min-width:0}
.logo{width:56px;height:56px;object-fit:contain;flex:0 0 auto;padding:5px;
 background:#0B1220;border:1px solid var(--line);border-radius:var(--radius)}
.logo-big{display:block;width:104px;height:auto;margin:0 auto 12px;
 padding:8px;background:#0B1220;border:1px solid var(--line);border-radius:18px}
h1{font-size:1.35rem;margin:0;letter-spacing:.3px;line-height:1.2}
h1 span{color:var(--orange)}
.sub{color:var(--muted);font-size:.82rem;margin-top:4px;line-height:1.45}
.badge-live{background:rgba(34,197,94,.12);color:var(--green);border:1px solid
 rgba(34,197,94,.4);padding:6px 12px;border-radius:999px;font-size:.78rem;
 font-weight:600;white-space:nowrap}
.badge-live.off{background:rgba(239,68,68,.12);color:var(--red);
 border-color:rgba(239,68,68,.4)}
/* ── Tab bar inferior fija (patrón Field/Admon) ──────────────────────────── */
.bottom-nav{position:fixed;bottom:0;left:0;right:0;z-index:100;
 background:var(--panel);border-top:1px solid var(--line);
 display:flex;justify-content:space-around;align-items:stretch;
 padding:4px 0;padding-bottom:max(4px,env(safe-area-inset-bottom));
 box-shadow:0 -6px 24px rgba(0,0,0,.35)}
.nav-item{display:flex;flex-direction:column;align-items:center;
 justify-content:center;gap:3px;min-height:56px;min-width:64px;padding:6px 10px;
 border-radius:var(--radius-sm);text-decoration:none;font-size:.68rem;
 font-weight:600;color:var(--muted);border:none;background:none;
 font-family:inherit;cursor:pointer;transition:all .15s ease}
.nav-item .nav-icon{font-size:1.25rem;line-height:1}
.nav-item:hover{color:var(--txt)}
.nav-item.active{color:var(--orange);background:rgba(255,107,0,.1)}
.nav-item:active{transform:scale(.96)}
.nav-item.off{opacity:.45;cursor:not-allowed}
@media(min-width:900px){
 .bottom-nav{left:50%;right:auto;transform:translateX(-50%);
  width:min(720px,100%);border:1px solid var(--line);border-bottom:none;
  border-radius:var(--radius) var(--radius) 0 0}
}
.tnav{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 12px}
.tnav a{padding:9px 13px;border:1px solid var(--line);border-radius:10px;
 background:rgba(255,255,255,.02);color:var(--txt);text-decoration:none;
 font-size:.86rem;font-weight:600;min-height:38px;display:inline-flex;
 align-items:center}
.tnav a:hover{border-color:var(--orange);color:var(--yellow)}
.tnav a.on{background:var(--orange);border-color:var(--orange);color:#0F172A}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;
 font-size:.78rem;font-weight:600}
.pill.ok{background:rgba(34,197,94,.14);color:#4ade80}
.pill.err{background:rgba(239,68,68,.14);color:#f87171}
.pill.wait{background:rgba(245,158,11,.14);color:#fbbf24}
.desc{color:var(--muted);font-size:.9rem;line-height:1.6;margin:4px 0 10px}
ol.desc{margin:6px 0 4px 20px}
p.desc{margin:0}
.chiprow{margin:-4px 0 10px}
.chip{display:inline-block;padding:3px 10px;margin:2px 4px 2px 0;
 border-radius:999px;font-size:.78rem;background:rgba(255,107,0,.13);
 color:var(--yellow);border:1px solid rgba(255,174,0,.35)}
.chip em{color:var(--muted);font-style:normal;font-size:.74rem}
.chip.off{background:rgba(100,116,139,.16);color:var(--muted);
 border-color:var(--line)}
.chip.warn{background:rgba(239,68,68,.12);color:#f87171;
 border-color:rgba(239,68,68,.45)}
.chklist{max-height:330px;overflow:auto;-webkit-overflow-scrolling:touch;
 border:1px solid var(--line);border-radius:10px;background:#0B1220;padding:5px}
.chk{display:flex;gap:10px;align-items:center;min-height:44px;
 padding:8px 10px;border-radius:8px;cursor:pointer;
 border-left:3px solid transparent}
.chk:hover{background:rgba(255,255,255,.04)}
.chk input{width:20px;height:20px;accent-color:var(--orange);flex:0 0 auto}
.chk span{font-size:.92rem}
.chk em{color:var(--muted);font-style:normal;font-size:.8rem;margin-left:6px}
.chk input:checked ~ span{color:var(--yellow);font-weight:600}
.chk:has(input:checked){background:rgba(255,107,0,.12);
 border-left-color:var(--orange)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
 gap:12px;margin:18px 0}
.card{background:var(--panel);border:1px solid var(--line);
 border-radius:var(--radius);padding:14px 16px}
.card .n{font-size:1.75rem;font-weight:700;line-height:1.1}
.card .l{color:var(--muted);font-size:.74rem;margin-top:6px;
 text-transform:uppercase;letter-spacing:.6px;line-height:1.3}
.card.green .n{color:var(--green)}.card.orange .n{color:var(--orange)}
.card.gray .n{color:var(--muted)}.card.blue .n{color:var(--blue)}
.card.red .n{color:var(--red)}
.panel{background:var(--panel);border:1px solid var(--line);
 border-radius:var(--radius);overflow:hidden;margin-top:8px}
/* Tablas: en pantallas angostas se desplazan horizontalmente (en vez de
   comprimir columnas hasta hacerlas ilegibles). */
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
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
 font-weight:700;letter-spacing:.4px;white-space:nowrap}
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
/* Botones: objetivo táctil ≥44px y "squash" al presionar (como Field) */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.35rem;
 min-height:44px;padding:10px 15px;border-radius:var(--radius-sm);
 font-size:.85rem;font-weight:700;border:1px solid transparent;cursor:pointer;
 text-decoration:none;color:#0F172A;background:var(--gray);
 touch-action:manipulation;transition:transform .12s ease,filter .15s ease}
.btn:active{transform:scale(.97)}
.btn.on{background:var(--green);border-color:var(--green);color:#0F172A}
.btn.off{background:var(--yellow);border-color:var(--yellow)}
.btn.re{background:var(--blue);border-color:var(--blue)}
.btn.ghost{background:transparent;color:var(--muted);border-color:var(--line)}
.btn.log{background:var(--panel2);color:var(--txt);border-color:var(--line)}
.btn.sm{min-height:38px;padding:8px 12px;font-size:.8rem}
.btn:disabled{opacity:.4;cursor:not-allowed}
form.inline{display:inline}
button.linklike{background:none;border:none;color:var(--yellow);font:inherit;
 cursor:pointer;padding:0;text-decoration:underline;min-height:24px}
.actions-cell{white-space:nowrap}
.actions{display:flex;gap:8px;flex-wrap:wrap}
pre.log{margin:0;padding:16px 18px;background:#0B1220;color:#CBD5E1;
 font-family:ui-monospace,Consolas,monospace;font-size:.76rem;line-height:1.5;
 white-space:pre-wrap;word-break:break-word;max-height:64vh;overflow:auto;
 -webkit-overflow-scrolling:touch;overscroll-behavior:contain}
footer{margin-top:26px;color:var(--muted);font-size:.8rem;display:flex;
 justify-content:space-between;gap:12px;flex-wrap:wrap}
a{color:var(--yellow);text-decoration:none}
code{background:var(--panel2);padding:2px 6px;border-radius:6px;font-size:.82em}
/* ── Tarjetas de programa (pestaña Workers, sin tabla para móvil) ────────── */
.wgroup{margin:22px 0 10px;font-size:.76rem;font-weight:700;letter-spacing:.7px;
 text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:8px}
.wgroup::after{content:'';flex:1;height:1px;background:var(--line)}
.wgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.wcard{background:var(--panel);border:1px solid var(--line);
 border-radius:var(--radius);padding:14px 16px;display:flex;flex-direction:column;
 gap:10px}
.wcard.bad{border-color:rgba(239,68,68,.5)}
.wcard.off{opacity:.82}
.wcard-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.wcard-name{font-weight:700;font-size:1rem;word-break:break-word}
.wcard-meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.wcard-facts{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;
 font-size:.8rem;color:var(--muted)}
.wcard-facts b{display:block;color:var(--txt);font-weight:600;font-size:.84rem}
.wcard .actions{margin-top:auto}
@media(max-width:420px){
 .wcard-facts{grid-template-columns:1fr}
 .wcard .actions form.inline{flex:1 1 46%}
 .wcard .actions .btn{width:100%}
}
/* ── Login ───────────────────────────────────────────────────────────────── */
.box{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
 padding:26px 24px;max-width:430px;margin:36px auto}
.box h1{font-size:1.3rem;margin-bottom:6px}
label{display:block;font-size:.8rem;color:var(--muted);margin:14px 0 6px;
 text-transform:uppercase;letter-spacing:.6px}
input[type=email],input[type=password],input[type=text],
input[type=number]{width:100%;padding:12px 13px;
 border-radius:var(--radius-sm);border:1px solid var(--line);background:#0B1220;
 color:var(--txt);font-size:1rem;font-family:inherit}
input:focus{outline:none;border-color:var(--orange)}
.submit{width:100%;margin-top:20px;padding:13px;border:none;
 border-radius:var(--radius-sm);background:var(--orange);color:#0F172A;
 font-weight:700;font-size:1rem;cursor:pointer;min-height:48px}
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
select{width:100%;padding:12px 13px;border-radius:var(--radius-sm);
 border:1px solid var(--line);background:#0B1220;color:var(--txt);
 font-size:1rem;font-family:inherit}
select:focus{outline:none;border-color:var(--orange)}
textarea{width:100%;padding:12px 13px;border-radius:var(--radius-sm);
 border:1px solid var(--line);background:#0B1220;color:var(--txt);
 font-size:1rem;font-family:inherit;resize:vertical;line-height:1.45}
textarea:focus{outline:none;border-color:var(--orange)}
.cfg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));
 gap:16px;padding:18px}
.cfg-field label{margin-top:0}
.cfg-field .help{color:var(--muted);font-size:.78rem;margin-top:5px;line-height:1.45}
.ro{background:#0B1220;border:1px solid var(--line);border-radius:10px;
 padding:11px 13px;color:#CBD5E1;word-break:break-word;font-size:.9rem;
 min-height:44px}
.cfg-save{padding:0 18px 18px;display:flex;gap:8px;align-items:center}
.cfg-note{padding:12px 18px;border-top:1px solid var(--line);color:var(--muted);
 font-size:.78rem;background:var(--panel2)}
.card-desc{padding:14px 18px 2px;color:var(--muted);font-size:.86rem;line-height:1.55}
.wdesc{font-size:.78rem}
.wdesc summary{cursor:pointer;color:var(--muted);font-weight:600;list-style:none;
 min-height:28px;display:flex;align-items:center}
.wdesc summary::-webkit-details-marker{display:none}
.wdesc summary:hover{color:var(--yellow)}
.wdesc[open] summary{color:var(--orange)}
.wdesc div{padding:10px 12px;background:#0B1220;
 border:1px solid var(--line);border-left:2px solid var(--orange);
 border-radius:8px;line-height:1.55;color:#CBD5E1}
/* ── Teléfonos / tabletas ──────────────────────────────────────────────
   Reglas de móvil: objetivos táctiles ≥44px, inputs a 16px (si no, iOS
   hace zoom al enfocar), safe-areas del notch y tablas con scroll propio. */
@media(max-width:820px){
 .wrap{padding-left:max(12px,env(safe-area-inset-left));
  padding-right:max(12px,env(safe-area-inset-right))}
 .hide-sm{display:none}
 header{gap:10px;padding:8px 0 10px;margin-left:-12px;margin-right:-12px;
  padding-left:12px;padding-right:12px}
 .hd{gap:10px}
 .logo{width:46px;height:46px;border-radius:14px;padding:4px}
 h1{font-size:1.15rem}
 h1 span{display:none}
 .sub{font-size:.78rem}
 .badge-live{padding:5px 10px;font-size:.7rem}
 .tnav{gap:6px;margin:12px 0 10px}
 .tnav a{padding:10px 12px;font-size:.84rem}
 .chklist{max-height:none}
 .cards{grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:10px;margin:16px 0}
 .card{padding:12px 13px}
 .card .n{font-size:1.55rem}
 .card .l{font-size:.7rem}
 .wgrid{grid-template-columns:1fr;gap:10px}
 .panel h2{padding:14px}
 table{font-size:.82rem}
 .tscroll table{min-width:640px}
 th{padding:10px}
 td{padding:11px 10px}
 .actions-cell{white-space:normal}
 .cfg-grid{padding:14px;gap:14px}
 .cfg-save{padding:0 14px 14px;flex-wrap:wrap}
 .cfg-note{padding:10px 14px}
 .card-desc{padding:12px 14px 2px}
 .box{margin:20px auto;padding:22px 18px}
 pre.log{padding:13px 14px;font-size:.72rem}
 footer{flex-direction:column;gap:6px}
}
@media(max-width:430px){
 .wrap{padding-left:10px;padding-right:10px}
 header{margin-left:-10px;margin-right:-10px;padding-left:10px;
  padding-right:10px}
 .logo{width:42px;height:42px}
 h1{font-size:1.05rem}
 .card-desc,.sub{font-size:.76rem}
 .cards{grid-template-columns:repeat(2,1fr)}
 .card .n{font-size:1.4rem}
 .tscroll table{min-width:560px}
 .cfg-grid{grid-template-columns:1fr;padding:12px}
 .box{margin:14px auto;padding:20px 14px}
 .btn{font-size:.82rem;padding:10px 12px}
 .nav-item{font-size:.62rem;min-width:56px;padding:6px 4px}
}
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
         conn=None):
    """Layout general: header fijo + barra de contenido + tab bar inferior."""
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
