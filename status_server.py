"""
status_server.py — Página web de estado de WorkersAdmon
=======================================================
Servidor HTTP mínimo (stdlib, sin dependencias) que muestra qué workers corren
en este contenedor, cuándo se activaron y cuándo se ejecutaron por última vez.

Endpoints
---------
GET /            → página HTML (tema oscuro ECCSA, auto-refresh 15 s)
GET /api/status  → JSON con el mismo estado (para monitoreo/programas)
GET /healthz     → "ok" (sondeo)

Fuentes de datos
----------------
1. `supervisorctl status`                → estado real (RUNNING/STOPPED/...) + uptime
2. /data/workers_enabled.txt             → lista persistente de workers habilitados
3. /app/docker/conf.d.available/*.conf   → workers disponibles (aún no habilitados)
4. /data/heartbeats/<worker>.json        → última ejecución reportada por el worker
"""
import datetime
import html
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATUS_PORT = int(os.environ.get("STATUS_PORT", "8080"))
SUPERVISOR_CONF = os.environ.get("SUPERVISOR_CONF", "/etc/supervisor/supervisord.conf")
AVAILABLE_DIR = os.environ.get("WORKERS_AVAILABLE_DIR",
                               "/app/docker/conf.d.available")
TITLE = os.environ.get("STATUS_TITLE", "Workers Admon")

# Colores del tema HUB (tema oscuro)
CSS = """
:root{--bg:#0F172A;--panel:#1E293B;--panel2:#273449;--line:#334155;
--txt:#E2E8F0;--muted:#94A3B8;--orange:#FF6B00;--yellow:#FFAE00;
--green:#22C55E;--red:#EF4444;--gray:#64748B;--blue:#38BDF8;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font-family:Outfit,'Segoe UI',system-ui,-apple-system,sans-serif;font-size:15px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;
gap:16px;flex-wrap:wrap;padding-bottom:18px;border-bottom:1px solid var(--line)}
h1{font-size:1.5rem;margin:0;letter-spacing:.3px}
h1 span{color:var(--orange)}
.sub{color:var(--muted);font-size:.85rem;margin-top:4px}
.badge-live{background:rgba(34,197,94,.12);color:var(--green);border:1px solid
rgba(34,197,94,.4);padding:6px 12px;border-radius:999px;font-size:.8rem;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
gap:12px;margin:22px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:16px 18px}
.card .n{font-size:1.9rem;font-weight:700;line-height:1.1}
.card .l{color:var(--muted);font-size:.8rem;margin-top:6px;text-transform:uppercase;
letter-spacing:.6px}
.card.green .n{color:var(--green)}.card.orange .n{color:var(--orange)}
.card.gray .n{color:var(--muted)}.card.blue .n{color:var(--blue)}
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
footer{margin-top:26px;color:var(--muted);font-size:.8rem;display:flex;
justify-content:space-between;gap:12px;flex-wrap:wrap}
a{color:var(--yellow);text-decoration:none}
code{background:var(--panel2);padding:2px 6px;border-radius:6px;font-size:.82em}
@media(max-width:820px){.hide-sm{display:none}table{font-size:.82rem}}
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


# ─── utilidades ──────────────────────────────────────────────────────────────
def get_data_dir():
    """Directorio de datos persistente (/data en Docker, ./workers_data en local)."""
    for candidate in (os.environ.get("WORKERS_DATA_DIR", "/data"),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers_data")):
        try:
            os.makedirs(candidate, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    return os.environ.get("WORKERS_DATA_DIR", "/tmp")


DATA_DIR = get_data_dir()
ENABLED_FILE = os.path.join(DATA_DIR, "workers_enabled.txt")
HEARTBEAT_DIR = os.path.join(DATA_DIR, "heartbeats")


def now():
    return datetime.datetime.now()


def parse_uptime(text):
    """Convierte el uptime de supervisorctl ('1 day, 2:03:04', '0:05:33') a segundos."""
    if not text:
        return None
    text = text.strip()
    days = 0
    m = re.match(r"^(\d+)\s+days?,\s*(.+)$", text)
    if m:
        days = int(m.group(1))
        text = m.group(2).strip()
    parts = text.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 3:
        h, mi, s = parts
    elif len(parts) == 2:
        h, mi, s = 0, parts[0], parts[1]
    else:
        return None
    return days * 86400 + h * 3600 + mi * 60 + s


def get_supervisor_status():
    """[(nombre, estado, pid, uptime_texto)] desde supervisord. None si falla."""
    try:
        proc = subprocess.run(
            ["supervisorctl", "-c", SUPERVISOR_CONF, "status"],
            capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return None, str(exc)
    if proc.returncode != 0 and not proc.stdout.strip():
        return None, (proc.stderr or "").strip() or f"rc={proc.returncode}"
    rows = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s*(.*)$", line.strip())
        if not m:
            continue
        name, state, rest = m.group(1), m.group(2).upper(), m.group(3)
        pid = None
        mp = re.search(r"\bpid\s+(\d+)", rest)
        if mp:
            pid = int(mp.group(1))
        uptime_txt = None
        mu = re.search(r"\buptime\s+(.+)$", rest)
        if mu:
            uptime_txt = mu.group(1).strip()
        rows.append((name, state, pid, uptime_txt))
    return rows, None


def read_enabled():
    """Nombres de la lista persistente de workers habilitados."""
    names = set()
    try:
        with open(ENABLED_FILE, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line:
                    names.add(line)
    except FileNotFoundError:
        pass
    return names


def read_available():
    """Nombres con conf en conf.d.available (workers candidatos)."""
    names = set()
    try:
        for fn in os.listdir(AVAILABLE_DIR):
            if fn.endswith(".conf"):
                names.add(fn[:-5])
    except FileNotFoundError:
        pass
    return names


def read_heartbeats():
    """{worker: dict} con la última ejecución reportada."""
    out = {}
    try:
        for fn in os.listdir(HEARTBEAT_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(HEARTBEAT_DIR, fn), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    out[data.get("worker") or fn[:-5]] = data
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out


def rel_time(when):
    """Texto relativo ('hace 12 min') para una fecha datetime o None."""
    if when is None:
        return "—"
    secs = int((now() - when).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"hace {secs} s"
    if secs < 3600:
        return f"hace {secs // 60} min"
    if secs < 86400:
        return f"hace {secs // 3600} h"
    return f"hace {secs // 86400} d"


def parse_dt(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(str(text)[:19], fmt)
        except ValueError:
            continue
    return None


# ─── estado consolidado ──────────────────────────────────────────────────────
def build_status():
    sup_rows, sup_err = get_supervisor_status()
    enabled = read_enabled()
    available = read_available()
    beats = read_heartbeats()

    progs = {}

    # base: todo lo disponible en conf.d.available
    for name in sorted(available):
        progs[name] = {"name": name, "state": "NO_HABILITADO", "pid": None,
                       "uptime_seconds": None, "enabled": False, "available": True}

    # estado real de supervisord (incluye status_web, que no está en available)
    for name, state, pid, uptime_txt in (sup_rows or []):
        entry = progs.setdefault(name, {"name": name, "available": False})
        entry.update({
            "state": state,
            "pid": pid,
            "uptime_seconds": parse_uptime(uptime_txt),
            "uptime_text": uptime_txt,
            "enabled": entry.get("enabled") or state in ("RUNNING", "STARTING"),
        })

    # habilitados en la lista persistente aunque supervisord aún no los tenga
    for name in enabled:
        entry = progs.setdefault(name, {"name": name, "available": name in available})
        entry["enabled"] = True
        if entry.get("state") in (None, "NO_HABILITADO") and not entry.get("pid"):
            entry["state"] = "UNKNOWN" if name in available else "UNKNOWN"

    # status_web siempre está habilitado
    if "status_web" in progs:
        progs["status_web"]["enabled"] = True

    # heartbeats → última ejecución
    for name, entry in progs.items():
        hb = beats.get(name)
        if hb:
            last = parse_dt(hb.get("last_run"))
            entry["last_run"] = hb.get("last_run")
            entry["last_run_dt"] = last
            entry["detail"] = hb.get("detail") or ""
            entry["count"] = hb.get("count")
        else:
            entry.update({"last_run": None, "last_run_dt": None,
                          "detail": "", "count": None})
        # fecha de activación = cuando arrancó el proceso
        up = entry.get("uptime_seconds")
        entry["started_at"] = (now() - datetime.timedelta(seconds=up)).strftime(
            "%Y-%m-%d %H:%M:%S") if up is not None else None
        entry["running"] = entry.get("state") in ("RUNNING", "STARTING")

    ordered = sorted(progs.values(), key=lambda e: (not e["running"], e["name"]))
    totals = {
        "running": sum(1 for e in ordered if e["running"]),
        "stopped": sum(1 for e in ordered
                       if e["state"] in ("STOPPED", "EXITED", "NO_HABILITADO", "UNKNOWN")),
        "errors": sum(1 for e in ordered if e["state"] in ("FATAL", "BACKOFF")),
        "enabled": sum(1 for e in ordered if e["enabled"]),
        "available": len(available),
        "total": len(ordered),
    }
    return {
        "title": TITLE,
        "now": now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": os.environ.get("TZ", "local"),
        "data_dir": DATA_DIR,
        "supervisor_error": sup_err,
        "totals": totals,
        "programs": [
            {k: v for k, v in e.items() if k != "last_run_dt"} for e in ordered
        ],
    }


# ─── HTML ────────────────────────────────────────────────────────────────────
def render_html(status):
    t = status["totals"]
    rows = []
    for e in status["programs"]:
        cls, label = ST_TAG.get(e["state"], ("t-off", e["state"]))
        en = ('<span class="tag t-yes">SÍ</span>' if e["enabled"]
              else '<span class="tag t-off">NO</span>')
        started = (f'<span class="mono">{html.escape(str(e["started_at"]))}</span>'
                   if e.get("started_at") else '<span class="muted">—</span>')
        started_rel = rel_time(parse_dt(e.get("started_at")))
        if e.get("last_run"):
            last = (f'<span class="mono">{html.escape(str(e["last_run"]))}</span><br>'
                    f'<span class="muted">{rel_time(parse_dt(e["last_run"]))}</span>')
        else:
            last = '<span class="muted">sin registros</span>'
        detail = html.escape(str(e.get("detail") or ""))
        if e.get("count") is not None:
            detail = (f'{detail}<br><span class="muted">registros: '
                      f'{e["count"]}</span>' if detail else
                      f'<span class="muted">registros: {e["count"]}</span>')
        rows.append(f"""<tr>
  <td class="name">{html.escape(e['name'])}</td>
  <td><span class="tag {cls}">{label}</span></td>
  <td>{en}</td>
  <td>{started}<br><span class="muted">{started_rel}</span></td>
  <td>{last}</td>
  <td class="hide-sm">{detail or '<span class="muted">—</span>'}</td>
</tr>""")

    body_rows = "\n".join(rows) if rows else ""
    err = (f'<div class="empty" style="color:var(--red)">supervisord no responde: '
           f'{html.escape(str(status["supervisor_error"]))}</div>'
           if status.get("supervisor_error") else "")

    # Aviso destacado cuando aún no se ha activado ningún worker
    otros = [e for e in status["programs"] if e["name"] != "status_web"]
    if not otros:
        hint = ('<div class="empty">No hay programas en '
                '<code>docker/conf.d.available/</code>.</div>')
    elif not [e for e in otros if e.get("enabled")]:
        hint = (f'<div class="empty" style="color:var(--yellow)">'
                f'🔸 Ningún worker activo todavía — {len(otros)} en standby. '
                f'Activa el primero con <code>docker exec workersadmon '
                f'enable_worker &lt;nombre&gt;</code></div>')
    else:
        hint = ""

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>{html.escape(status['title'])} — Estado de workers</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>⚙️ {html.escape(status['title'])} <span>· estado de workers</span></h1>
      <div class="sub">Contenedor <code>workersadmon</code> ·
        zona horaria {html.escape(status['timezone'])} ·
        datos en <code>{html.escape(status['data_dir'])}</code></div>
    </div>
    <div class="badge-live">● ACTUALIZADO {html.escape(status['now'])}</div>
  </header>

  <div class="cards">
    <div class="card green"><div class="n">{t['running']}</div>
      <div class="l">En ejecución</div></div>
    <div class="card gray"><div class="n">{t['stopped']}</div>
      <div class="l">Detenidos / no habilitados</div></div>
    <div class="card orange"><div class="n">{t['enabled']}</div>
      <div class="l">Habilitados</div></div>
    <div class="card blue"><div class="n">{t['available']}</div>
      <div class="l">Disponibles en el repo</div></div>
  </div>

  {err}
  <div class="panel">
    <h2>Programas del contenedor</h2>
    <table>
      <thead><tr>
        <th>Programa</th><th>Estado</th><th>Habilitado</th>
        <th>Activo desde</th><th>Última ejecución</th><th class="hide-sm">Detalle</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
    {hint}
  </div>

  <footer>
    <span>Auto-refresh cada 15 s · API JSON en <a href="/api/status">/api/status</a></span>
    <span>Activar: <code>enable_worker &lt;nombre&gt;</code> ·
      ver lista: <code>workers_list</code></span>
  </footer>
</div>
</body>
</html>"""


# ─── HTTP ────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silencioso: los logs viven en /var/log/supervisor/status_web.log

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        try:
            status = build_status()
        except Exception as exc:
            return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if path == "/api/status":
            return self._send(200, json.dumps(status, ensure_ascii=False, indent=2),
                              "application/json")
        if path == "/":
            return self._send(200, render_html(status), "text/html")
        return self._send(404, "No encontrado", "text/plain")


def run_server():
    server = ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), Handler)
    print(f"[status_web] Página de estado en http://0.0.0.0:{STATUS_PORT}/  "
          f"(datos: {DATA_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
