#!/usr/bin/env python3
"""
ECCSA-Shell · check_daily.py — verificación automática de desincronización.

Corre dentro del contenedor `workersadmon` (que lleva una copia verificada del
shell en /app/shell) y comprueba dos cosas:

  1. `build_shell.py --check` → que dist/ corresponde a tokens.css + src/.
  2. La copia de esta app: /app/panel/shell.css debe ser idéntica a
     dist/shell.plain.css y /app/ECCSA_SHELL_VERSION debe coincidir con VERSION.

Escribe el resultado en /data/shell_check.json (lo muestra el panel) y devuelve
código 1 si algo está mal, para que una tarea programada de Windows lo detecte.

    python3 /app/shell/tools/check_daily.py
"""
import hashlib
import json
import os
import subprocess
import sys
import time

SHELL_DIR = os.environ.get("SHELL_DIR", "/app/shell")
APP_DIR = os.environ.get("SHELL_APP_DIR", "/app")
DATA_DIR = os.environ.get("WORKERS_DATA_DIR", "/data")
OUT = os.path.join(DATA_DIR, "shell_check.json")


def sha_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def main():
    problems = []

    # 1) ¿dist/ está generado desde las fuentes?
    rc = subprocess.run([sys.executable, "tools/build_shell.py", "--check"],
                        cwd=SHELL_DIR, capture_output=True, text=True)
    if rc.returncode != 0:
        problems.append("dist/ desactualizado: " + (rc.stdout + rc.stderr).strip()[-200:])

    # 2) ¿La copia de esta app está al día?
    dist_plain = os.path.join(SHELL_DIR, "dist", "shell.plain.css")
    copia = os.path.join(APP_DIR, "panel", "shell.css")
    shell_ver = open(os.path.join(SHELL_DIR, "VERSION"), encoding="utf-8").read().strip()
    copia_ver = ""
    ver_file = os.path.join(APP_DIR, "ECCSA_SHELL_VERSION")
    if os.path.isfile(ver_file):
        copia_ver = open(ver_file, encoding="utf-8").read().strip()
    sha_dist = sha_file(dist_plain) if os.path.isfile(dist_plain) else None
    sha_copia = sha_file(copia) if os.path.isfile(copia) else None
    if sha_copia is None:
        problems.append(f"no existe {copia}")
    elif sha_copia != sha_dist:
        problems.append(f"panel/shell.css desactualizado (sha {sha_copia} ≠ {sha_dist})")
    if copia_ver != shell_ver:
        problems.append(f"versión del shell: app {copia_ver or '—'} vs repo {shell_ver}")

    resultado = {
        "ok": not problems,
        "shell_version": shell_ver,
        "app_version": copia_ver,
        "sha": {"dist": sha_dist, "app": sha_copia},
        "problemas": problems,
        "revisado": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(resultado, fh, indent=2, ensure_ascii=False)
    except OSError as e:
        print("no pude escribir el resultado:", e)

    print("SHELL CHECK:", "OK [OK]" if not problems else "DESINCRONIZADO [MAL]")
    for p in problems:
        print("  -", p)
    print(f"  shell {shell_ver} | app {copia_ver} | sha {sha_copia}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
