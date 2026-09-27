#!/usr/bin/env python3
"""
ECCSA-Shell · sync_shell.py — propaga el shell a las apps del ecosistema.

Cada app guarda una copia de la variante que le corresponde (para poder
construirse sin red y funcionar offline). Este script la copia y, con --check,
verifica que nadie se haya desincronizado.

Uso:
    python tools/sync_shell.py --all              # copia a las apps conocidas
    python tools/sync_shell.py --all --check      # NO escribe; sale 1 si divergen
    python tools/sync_shell.py --target /ruta --variant plain
    python tools/sync_shell.py --list

Variante por app (la elige el stack, no el gusto):
    field          → t4    (Tailwind v4)
    Admon          → t3    (Tailwind v3)
    WorkersAdmon   → plain (Python, sin build)
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(HERE, "dist")
VERSION_FILE = os.path.join(HERE, "VERSION")

# app → (variante, destino CSS relativo al repo, banner de referencia)
APPS = {
    "field":        ("t4",    "src/app.css",          "banner/SyncHeader.svelte"),
    "admon":        ("t3",    "src/styles/app.css",   "banner/SyncHeader.svelte"),
    "workersadmon": ("plain", "panel/shell.css",      "banner/banner.py.html"),
}

# Las apps Svelte (Tailwind) además reciben este módulo con las versiones, para
# que el banner muestre cuál app y qué shell están corriendo.
SVELTE_SHELL_JS = """/* ECCSA-Shell · generado por tools/sync_shell.py — NO editar a mano.
   Úsalo en el banner:  import { APP_VERSION, SHELL_VERSION } from '$lib/shell.js' */
export const SHELL_VERSION = '%(shell)s';
export const APP_ID = '%(app)s';
export const APP_VERSION = '%(appver)s';
"""

# Regla de versionado: si el DISEÑO cambió (la huella de dist/ es distinta a la
# que tienen las apps) hay que subir VERSION. Evita que 3 apps queden con
# "shell 1.0.0" y un CSS que ya no corresponde a ese 1.0.0.
def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def version_bump_pendiente(repo, nuevo, sha_nuevo):
    """(bool, mensaje): True si hay que subir VERSION antes de propagar."""
    ver = os.path.join(repo, "ECCSA_SHELL_VERSION")
    sha_f = os.path.join(repo, "ECCSA_SHELL_SHA")
    if not os.path.isfile(ver):
        return False, ""
    ver_apps = open(ver, encoding="utf-8").read().strip()
    sha_apps = (open(sha_f, encoding="utf-8").read().strip()
                if os.path.isfile(sha_f) else "")
    if sha_apps and sha_apps != sha_nuevo and ver_apps == nuevo:
        return True, (f"el diseño cambió (sha {sha_apps} → {sha_nuevo}) pero "
                      f"VERSION sigue en {nuevo}")
    return False, ""


CANDIDATES = {
    "field":        ["/tmp/opencode/field", "/workspace/field", "D:/Antigravity/field"],
    "admon":        ["/tmp/admon", "/workspace/admon", "D:/Antigravity/admon"],
    "workersadmon": ["/tmp/opencode/WorkersAdmon", "/workspace/WorkersAdmon"],
}


def find_repo(app):
    for cand in CANDIDATES.get(app, []):
        if os.path.isdir(cand):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--variant", choices=["t4", "t3", "plain"])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="propagar aunque no se haya subido VERSION")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    version = open(VERSION_FILE, encoding="utf-8").read().strip()

    if args.list:
        for app, (var, css, banner) in APPS.items():
            repo = find_repo(app)
            print(f"{app:14} {var:6} {css:22} banner={banner} repo={repo or 'NO ENCONTRADO'}")
        return 0

    jobs = []   # (repo, variante, css_rel, app_id)
    if args.target:
        variant = args.variant
        rel = None
        if variant is None:                      # deducir por contenido del repo
            for app, (var, css, _) in APPS.items():
                if args.target.endswith(css.replace("/", os.sep)) or app in args.target.lower():
                    variant, rel = var, css
                    break
        if not rel:
            ap.error("no pude deducir la variante; usa --variant t4|t3|plain")
        jobs.append((args.target, variant, rel,
                      'field' if 'field' in args.target.lower() else
                      'admon' if 'admon' in args.target.lower() else 'app'))
    elif args.all:
        for app, (var, css, _) in APPS.items():
            repo = find_repo(app)
            if repo:
                jobs.append((repo, var, css, app))
            else:
                print(f"[--  ] {app:14} repo no encontrado (usa --target)")
    else:
        ap.error("usa --all, --target o --list")

    rc = 0
    for repo, variant, rel, app_id in jobs:
        src = os.path.join(DIST, f"shell.{variant}.css")
        dst = os.path.join(repo, rel)
        ver_dst = os.path.join(repo, "ECCSA_SHELL_VERSION")
        # Módulo de versiones para apps Svelte (Tailwind)
        js_dst = os.path.join(repo, "src/lib/shell.js")
        es_svelte = rel.endswith(".css") and "src" in rel.split(os.sep)[0:1] or \
            rel.startswith("src/")
        if not os.path.isfile(src):
            print(f"[ERR ] falta {src}; corre: python tools/build_shell.py")
            rc = 1
            continue
        nuevo = open(src, encoding="utf-8").read()
        ver_actual = open(ver_dst, encoding="utf-8").read().strip() if os.path.isfile(ver_dst) else ""
        if args.check:
            actual = open(dst, encoding="utf-8").read() if os.path.isfile(dst) else ""
            probs = []
            if actual != nuevo:
                probs.append(f"{rel} desactualizado")
            if ver_actual != version:
                probs.append(f"ECCSA_SHELL_VERSION={ver_actual or '—'} (esperado {version})")
            if rel.startswith("src/") and rel.endswith(".css") and os.path.isfile(js_dst):
                if f"SHELL_VERSION = '{version}'" not in open(js_dst, encoding="utf-8").read():
                    probs.append("src/lib/shell.js desactualizado")
            pendiente, por_que = version_bump_pendiente(repo, nuevo, _sha(nuevo))
            if pendiente:
                probs.append("VERSION sin subir: " + por_que)
            if probs:
                print(f"[DIFF] {os.path.basename(repo):14} {'; '.join(probs)}")
                rc = 1
            else:
                print(f"[OK  ] {os.path.basename(repo):14} al día (shell {version})")
            continue
        sha_nuevo = _sha(nuevo)
        pendiente, por_que = version_bump_pendiente(repo, version, sha_nuevo)
        if pendiente and not args.force:
            print(f"[BLOQ] {os.path.basename(repo):14} {por_que}")
            print(f"       → sube la VERSION del shell (editá VERSION) o usa --force")
            rc = 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(nuevo)
        with open(os.path.join(repo, "ECCSA_SHELL_SHA"), "w", encoding="utf-8") as fh:
            fh.write(sha_nuevo + "\n")
        with open(ver_dst, "w", encoding="utf-8") as fh:
            fh.write(version + "\n")
        if rel.startswith("src/") and rel.endswith(".css"):
            appver = "1.0.0"
            pkg = os.path.join(repo, "package.json")
            if os.path.isfile(pkg):
                import json as _json
                try:
                    appver = _json.load(open(pkg, encoding="utf-8")).get("version", appver)
                except Exception:
                    pass
            os.makedirs(os.path.dirname(js_dst), exist_ok=True)
            with open(js_dst, "w", encoding="utf-8") as fh:
                fh.write(SVELTE_SHELL_JS % {"shell": version,
                                            "app": app_id,
                                            "appver": appver})
        print(f"[OK  ] {os.path.basename(repo):14} {rel} ← shell.{variant}.css (v{version})")

    if rc and args.check:
        print("\nPara alinear: python tools/sync_shell.py --all")
    return rc


if __name__ == "__main__":
    sys.exit(main())
