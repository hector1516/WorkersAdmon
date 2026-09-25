"""
panel/envconf.py — Variables de entorno de supervisord por worker
=================================================================
Los workers del HUB leen sus intervalos/horarios de variables de entorno
(`CRON_*`) que viven en la línea `environment=` de su `.conf`. El panel las
edita sin tocar el código:

  1. Se parchea el conf en `docker/conf.d.available/<worker>.conf`
  2. Se copia al conf ACTIVO (`/etc/supervisor/conf.d/`) si el worker está
     habilitado, y se ejecuta `supervisorctl reread + update` (supervisord
     reinicia el proceso si cambió su sección)
  3. Se persiste la copia de valores en `/data/worker_env.json` — el volumen
     — y el entrypoint la REAPLICA en cada arranque, así el ajuste sobrevive
     a una reconstrucción de la imagen.

Formato del JSON: {"<worker>": {"CLAVE": "valor", ...}}.
Solo se guardan valores distintos al default del código (los defaults quedan
en `panel/spec.py` y en el propio worker).
"""
import json
import os
import re
import shutil

from . import config, workers

ENV_FILE = os.path.join(config.DATA_DIR, "worker_env.json")

_PAIR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"')
_ENV_RE = re.compile(r"^environment=.*$", re.M)


# ─── rutas ────────────────────────────────────────────────────────────────────
def conf_path(name):
    """Conf fuente (el que se copia al habilitar el worker)."""
    return os.path.join(config.AVAILABLE_DIR, f"{name}.conf")


def active_conf_path(name):
    """Conf que supervisord está usando ahora mismo (si el worker está activo)."""
    return os.path.join(getattr(config, "ACTIVE_CONF_DIR", "/etc/supervisor/conf.d"),
                        f"{name}.conf")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path, text):
    """Escritura atómica (tmp + replace) para no dejar el conf a medio escribir."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ─── lectura/escritura del conf ───────────────────────────────────────────────
def _parse_env_line(line):
    return dict(_PAIR_RE.findall(line.split("=", 1)[1]))


def read_env(name):
    """{CLAVE: valor} actuales del conf disponible ({} si no tiene environment=)."""
    text = _read(conf_path(name))
    m = _ENV_RE.search(text)
    return _parse_env_line(m.group(0)) if m else {}


def _render_env(pairs):
    return "environment=" + ",".join(f'{k}="{v}"' for k, v in pairs.items())


def _apply_to_text(text, updates):
    """
    Devuelve el texto del conf con `updates` aplicadas sobre la línea
    `environment=` (vacío = eliminar la variable). Si la línea no existe y
    hay valores nuevos, se agrega al final del archivo.
    """
    current = {}
    m = _ENV_RE.search(text)
    if m:
        current = _parse_env_line(m.group(0))
    for key, value in updates.items():
        if value is None or str(value).strip() == "":
            current.pop(key, None)
        else:
            current[key] = str(value).strip()
    if not current:
        return (_ENV_RE.sub("", text, count=1) if m else text), current
    line = _render_env(current)
    if m:
        text = _ENV_RE.sub(lambda _m: line, text, count=1)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    return text, current


def _valid_value(value):
    """Sin comillas ni saltos de línea: romperían la sintaxis del conf."""
    return '"' not in value and "\n" not in value and "\r" not in value


# ─── persistencia en el volumen ───────────────────────────────────────────────
def load_overrides():
    """JSON persistido en /data/worker_env.json ({} si no existe)."""
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_overrides(data):
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, ENV_FILE)


def override_for(name, key):
    """Valor persistido para una variable (None si no se ha sobrescrito)."""
    return load_overrides().get(name, {}).get(key)


# ─── acciones ─────────────────────────────────────────────────────────────────
def write_env(name, updates):
    """
    Aplica `updates` {CLAVE: valor} a un worker.
    Devuelve (ok, mensaje). Reinicia el worker si estaba corriendo.
    """
    if not workers._valid_name(name):
        return False, f"nombre inválido: {name!r}"
    path = conf_path(name)
    if not os.path.exists(path):
        return False, f"no existe conf para '{name}'"

    for key, value in updates.items():
        if not _valid_value(str(value or "")):
            return False, f"valor no válido para {key} (no usa comillas ni saltos)"

    rows_before, _err = workers.supervisor_status()
    before = {n: s for n, s, _p, _u in (rows_before or [])}

    text = _read(path)
    new_text, _current = _apply_to_text(text, updates)
    _write(path, new_text)

    # Copia al conf activo para que supervisord vea el cambio
    active = active_conf_path(name)
    active_touched = False
    if os.path.exists(active):
        shutil.copyfile(path, active)
        active_touched = True

    # Persiste el overlay en el volumen (sobrevive a rebuild de la imagen)
    data = load_overrides()
    merged = dict(data.get(name, {}))
    for key, value in updates.items():
        if value is None or str(value).strip() == "":
            merged.pop(key, None)
        else:
            merged[key] = str(value).strip()
    if merged:
        data[name] = merged
    else:
        data.pop(name, None)
    _save_overrides(data)

    if not active_touched:
        return True, "guardado (se aplicará al habilitar/reiniciar el worker)"

    was_running = before.get(name) in ("RUNNING", "STARTING")
    workers._run([config.SUPERVISORCTL, "-c", config.SUPERVISOR_CONF, "reread"])
    ok, out = workers._run([config.SUPERVISORCTL, "-c", config.SUPERVISOR_CONF,
                            "update"])
    if ok and was_running:
        rows, _err = workers.supervisor_status()
        states = {n: s for n, s, _p, _u in (rows or [])}
        if states.get(name) not in ("RUNNING", "STARTING", "RESTARTING"):
            workers._run([config.SUPERVISORCTL, "-c", config.SUPERVISOR_CONF,
                          "start", name])
    return ok, out or ("guardado y recargado" if ok else "error en supervisorctl")


def apply_all():
    """
    Reaplica TODO el overlay /data/worker_env.json sobre los confs.
    Lo invoca el entrypoint en cada arranque (sobrevive a rebuild) y el CLI
    `python3 -m panel.envconf --apply`.
    """
    data = load_overrides()
    applied, errors = 0, []
    for name, updates in data.items():
        path = conf_path(name)
        if not os.path.exists(path):
            errors.append(f"{name}: conf no encontrada")
            continue
        try:
            text = _read(path)
            new_text, _cur = _apply_to_text(text, updates)
            if new_text != text:
                _write(path, new_text)
            active = active_conf_path(name)
            if os.path.exists(active):
                shutil.copyfile(path, active)
            applied += 1
        except Exception as exc:            # noqa: BLE001 — un worker no tumba al resto
            errors.append(f"{name}: {exc}")
    return applied, errors


if __name__ == "__main__":                  # pragma: no cover — CLI del entrypoint
    import sys
    if "--apply" in sys.argv:
        n, errs = apply_all()
        print(f"[envconf] {n} worker(s) reaplicados")
        for e in errs:
            print(f"[envconf] aviso: {e}")
        sys.exit(0)
    print("uso: python3 -m panel.envconf --apply")
