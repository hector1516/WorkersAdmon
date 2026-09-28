#!/usr/bin/env python3
"""
ECCSA-Shell · sync_shell.py — propaga el shell a las apps del ecosistema.

Cada app guarda una copia de lo que le toca (para poder construirse sin red y
funcionar offline). Este script la copia y, con --check, verifica que nadie se
haya desincronizado.

Se propaga en dos families:

  1. El CSS del shell (tokens + componentes), que sale de dist/ y va a un solo
     archivo por app. Al propagar también estampa ECCSA_SHELL_VERSION y
     ECCSA_SHELL_SHA, y regenera src/lib/shell.js con las versiones.
  2. Los ARTEFACTOS del banner: el componente de referencia y la regla de
     ubicación por IP. Se elige el destino por VARIANTE (que es lo que define el
     stack de la app), no por el nombre de la carpeta.

Uso:
    python tools/sync_shell.py --all              # copia a las apps conocidas
    python tools/sync_shell.py --all --check      # NO escribe; sale 1 si divergen
    python tools/sync_shell.py --target /ruta --variant plain
    python tools/sync_shell.py --list

Variante por app (la elige el stack, no el gusto):
    field          → t4    (Tailwind v4, SvelteKit)
    Dashboard      → t4    (Tailwind v4, SvelteKit; pantalla de kiosco sin sesión)
    Admon          → t3    (Tailwind v3, Svelte)
    WorkersAdmon   → plain (Python, sin build)
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(HERE, "dist")
BANNER = os.path.join(HERE, "banner")
VERSION_FILE = os.path.join(HERE, "VERSION")

# app → (variante, destino del CSS relativo al repo, banner de referencia)
APPS = {
    "field":        ("t4",    "src/app.css",        "banner/SyncHeader.svelte"),
    "dashboard":    ("t4",    "src/app.css",        "banner/SyncHeader.svelte"),
    "admon":        ("t3",    "src/styles/app.css", "banner/SyncHeader.svelte"),
    "workersadmon": ("plain", "panel/shell.css",    "banner/banner.py.html"),
}

# Qué artefactos del banner se propagan, por VARIANTE. La variante es lo único
# que distingue al stack (Tailwind v4 vs v3 vs sin build), así que colgar los
# destinos de acá evita depender de acertar el nombre de la carpeta.
ARTEFACTOS = {
    "t4": {
        "SyncHeader.svelte": "src/lib/components/SyncHeader.svelte",
        "ActionsBar.svelte":  "src/lib/components/ActionsBar.svelte",
        "Changelog.svelte":   "src/lib/components/Changelog.svelte",
        "changelog.js":       "src/lib/changelog.js",
        "lugar.py":           "api/lugar.py",
    },
    "t3": {
        "SyncHeader.svelte": "src/components/SyncHeader.svelte",
        "ActionsBar.svelte":  "src/components/ActionsBar.svelte",
        "Changelog.svelte":   "src/components/Changelog.svelte",
        "changelog.js":       "src/lib/changelog.js",
        "lugar.py":           "api/lugar.py",
    },
    "plain": {
        "lugar.py":         "panel/lugar.py",
        "banner.py.html":   "panel/banner.py.html",
        "actions.py.html":  "panel/actions.py.html",
        "changelog.py.html": "panel/changelog.py.html",
    },
}

# Las apps Svelte (Tailwind) además reciben este módulo con las versiones, para
# que el banner muestre cuál app y qué shell están corriendo.
SVELTE_SHELL_JS = """/* ECCSA-Shell · generado por tools/sync_shell.py — NO editar a mano.
   Úsalo en el banner:  import { APP_VERSION, SHELL_VERSION } from '$lib/shell.js' */
export const SHELL_VERSION = '%(shell)s';
export const APP_ID = '%(app)s';
export const APP_VERSION = '%(appver)s';
"""

# app_id de cada variante, para estamparlo en src/lib/shell.js.
# OJO: la variante NO identifica a la app. `t4` la usan Field y Dashboard, así
# que el id sale del NOMBRE de la carpeta destino (única señal no ambigua que
# hay en el modo --target) y esta tabla es solo el respaldo cuando el nombre no
# coincide con ninguna app conocida.
APP_ID_DE_VARIANTE = {"t4": "field", "t3": "admon"}


def _app_id_de_target(target, variant):
    """app_id a estampar en src/lib/shell.js para un `--target` puntual.

    Con más de una app por variante no se puede deducir de la variante: se usa
    el nombre de la carpeta si coincide con una app conocida, y si no, el
    respaldo de la variante.
    """
    nombre = os.path.basename(os.path.normpath(target)).lower()
    if nombre in APPS:
        return nombre
    return APP_ID_DE_VARIANTE.get(variant, "app")


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


# Rutas locales donde puede estar el clon de cada app, en orden de preferencia.
# La vía que manda es la variable de entorno (SHELL_APP_FIELD,
# SHELL_APP_ADMON, SHELL_APP_WORKERSADMON): es lo que usa CI, donde los clones
# están en un directorio temporal. Estas de acá son para trabajar en la máquina.
#
# OJO con el prefijo: NO usar ECCSA_* para esto. En algunos entornos (sandboxes
# de agentes) las variables ECC*Unknown se descartan al lanzar los procesos
# hijos, y la ruta se pierde en silencio.
#
# Si aparece más de un clon, se elige el primero y se DICE, para que nadie edite
# en un clon y sincronice contra otro.
CANDIDATES = {
    "field":        ["field", "../field", "D:/Antigravity/field"],
    "dashboard":    ["dashboard", "../dashboard", "D:/Antigravity/dashboard"],
    "admon":        ["admon", "../admon", "D:/Antigravity/admon"],
    "workersadmon": ["WorkersAdmon", "../WorkersAdmon"],
}


def candidatos(app):
    """Rutas candidatas: primero la variable de entorno, luego las locales."""
    de_entorno = os.environ.get("SHELL_APP_" + app.upper(), "").strip()
    return ([de_entorno] if de_entorno else []) + CANDIDATES.get(app, [])


def find_repo(app, silencioso=False):
    """Primer clon que exista, o None. Avisa si había más de uno."""
    existentes = [c for c in candidatos(app)
                  if os.path.isdir(os.path.join(c, ".git"))
                  or os.path.isdir(c)]
    if not existentes:
        return None
    elegido = existentes[0]
    if len(existentes) > 1 and not silencioso:
        print(f"    :ojo: {app} está en {len(existentes)} sitios; uso "
              f"{elegido} (los otros: {', '.join(existentes[1:])}).")
        print(f"    :oka: fijalo con SHELL_APP_{app.upper()}={elegido} "
              f"para no adivinar.")
    return elegido


def leer(path):
    return open(path, encoding="utf-8").read() if os.path.isfile(path) else ""


def _artefactos_pendientes(repo, variant):
    """[(etiqueta, ruta_destino_rel, motivo)] de lo que difiere en los artefactos."""
    probs = []
    for origen, destino in ARTEFACTOS.get(variant, {}).items():
        nuevo = leer(os.path.join(BANNER, origen))
        if not nuevo:
            probs.append(f"falta banner/{origen} en el repo del shell")
            continue
        if leer(os.path.join(repo, destino)) != nuevo:
            probs.append(f"{destino} desactualizado")
    return probs


def _propagar_artefactos(repo, variant):
    """Copia los artefactos del banner. Devuelve la lista de los que cambiaron."""
    cambiados = []
    for origen, destino in ARTEFACTOS.get(variant, {}).items():
        nuevo = leer(os.path.join(BANNER, origen))
        if not nuevo:
            continue
        dst = os.path.join(repo, destino)
        if leer(dst) == nuevo:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(nuevo)
        cambiados.append(destino)
    return cambiados


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
            arts = ", ".join(ARTEFACTOS.get(var, {}).values()) or "—"
            print(f"{app:14} {var:6} {css:22} repo={repo or 'NO ENCONTRADO'}")
            print(f"{'':14} banner={banner}")
            print(f"{'':14} artefactos={arts}")
        return 0

    jobs = []   # (repo, variante, css_rel, app_id)
    if args.target:
        variant = args.variant
        rel = None
        if variant is None:
            # Deducir por contenido del repo. OJO: el nombre de la carpeta NO
            # sirve para esto — "admon" es subcadena de "WorkersAdmon" y esa
            # comparación mandaba al panel a la variante de Admon (y a escribir
            # src/styles/app.css en el repo equivocado). Se decide por el
            # archivo que el repo ya tiene, que es lo único no ambiguo.
            for app, (var, css, _) in APPS.items():
                if os.path.isfile(os.path.join(args.target, *css.split("/"))):
                    variant, rel = var, css
                    break
        else:
            # Variante dada: la ruta del CSS sale de APPS. Con varias apps por
            # variante se elige la de nombre de carpeta conocido, y si el repo
            # es nuevo se toma la primera app de esa variante.
            nombre = os.path.basename(os.path.normpath(args.target)).lower()
            candidatos = [(a, c) for a, (v, c, _) in APPS.items() if v == variant]
            if nombre in APPS and APPS[nombre][0] == variant:
                rel = APPS[nombre][1]
            elif candidatos:
                rel = candidatos[0][1]
        if not rel or not variant:
            ap.error("no pude deducir la variante; usa --variant t4|t3|plain "
                     "(o corré --list para ver qué archivo usa cada app)")
        jobs.append((args.target, variant, rel,
                      _app_id_de_target(args.target, variant)))
    elif args.all:
        for app, (var, css, _) in APPS.items():
            repo = find_repo(app)
            if repo:
                jobs.append((repo, var, css, app))
            else:
                print(f"[--  ] {app:14} repo no encontrado "
                      f"(probé {', '.join(candidatos(app))}; "
                      f"fijalo con ECCSA_{app.upper()}=...)")
    else:
        ap.error("usa --all, --target o --list")

    rc = 0
    for repo, variant, rel, app_id in jobs:
        nombre = os.path.basename(repo)
        src = os.path.join(DIST, f"shell.{variant}.css")
        dst = os.path.join(repo, rel)
        ver_dst = os.path.join(repo, "ECCSA_SHELL_VERSION")
        # Módulo de versiones para apps Svelte (Tailwind)
        js_dst = os.path.join(repo, "src/lib/shell.js")
        if not os.path.isfile(src):
            print(f"[ERR ] falta {src}; corre: python tools/build_shell.py")
            rc = 1
            continue
        nuevo = open(src, encoding="utf-8").read()
        ver_actual = leer(ver_dst).strip()
        if args.check:
            probs = []
            if leer(dst) != nuevo:
                probs.append(f"{rel} desactualizado")
            if ver_actual != version:
                probs.append(f"ECCSA_SHELL_VERSION={ver_actual or '—'} (esperado {version})")
            if rel.startswith("src/") and rel.endswith(".css") and os.path.isfile(js_dst):
                if f"SHELL_VERSION = '{version}'" not in leer(js_dst):
                    probs.append("src/lib/shell.js desactualizado")
            probs += _artefactos_pendientes(repo, variant)
            pendiente, por_que = version_bump_pendiente(repo, nuevo, _sha(nuevo))
            if pendiente:
                probs.append("VERSION sin subir: " + por_que)
            if probs:
                print(f"[DIFF] {nombre:14} {'; '.join(probs)}")
                rc = 1
            else:
                print(f"[OK  ] {nombre:14} al día (shell {version})")
            continue
        sha_nuevo = _sha(nuevo)
        pendiente, por_que = version_bump_pendiente(repo, version, sha_nuevo)
        if pendiente and not args.force:
            print(f"[BLOQ] {nombre:14} {por_que}")
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
                try:
                    appver = json.load(open(pkg, encoding="utf-8")).get("version", appver)
                except Exception:
                    pass
            os.makedirs(os.path.dirname(js_dst), exist_ok=True)
            with open(js_dst, "w", encoding="utf-8") as fh:
                fh.write(SVELTE_SHELL_JS % {"shell": version,
                                            "app": app_id,
                                            "appver": appver})
        arts = _propagar_artefactos(repo, variant)
        detalle = f" (artefactos: {', '.join(arts)})" if arts else ""
        print(f"[OK  ] {nombre:14} {rel} ← shell.{variant}.css (v{version}){detalle}")

    if rc and args.check:
        print("\nPara alinear: python tools/sync_shell.py --all")
    return rc


if __name__ == "__main__":
    sys.exit(main())
