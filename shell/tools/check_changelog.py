#!/usr/bin/env python3
"""
ECCSA-Shell · check_changelog.py — el popup de novedades no puede quedar viejo.

Qué verifica: que la `version` de cada app coincida con la que muestra su
banner. Son dos números que viven en archivos distintos, y si se separan el
popup 📋 deja de salariar justo cuando hay algo que contar (típicamente después
de un release, que es cuando más lo necesitas).

  Field   → la de src/lib/shell.js, que sync_shell.py genera desde package.json
  Admon   → ídem
  Panel   → panel/config.py:APP_VERSION

Y de paso avisa si el changelog está vacío (un popup sin contenido es ruido) y
si el texto es larguísimo (se sale de la pantalla en un iPhone).

Uso:
    python tools/check_changelog.py --repo /ruta/a/field
    python tools/check_changelog.py --all           # usa CANDIDATES de sync_shell
    python tools/check_changelog.py --all --quiet   # solo imprime si hay problema

Sale 1 si algo no cuadra, para que un workflow pueda fallar con esto.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# app → (dónde está el changelog, cómo se lee la versión de la app)
CHANGELOG = {
    "field":        ("static/changelog.json", "shell_js"),
    "admon":        ("public/changelog.json", "shell_js"),
    "workersadmon": ("static/changelog.json", "config_py"),
}

# Textos más largos que esto se salen de la tarjeta en un iPhone.
MAX_CAMBIO = 160
MAX_CAMBIOS = 12


def _version_de_la_app(repo, como):
    """La versión que muestra el banner de esa app, o None si no se encuentra."""
    if como == "shell_js":
        # La genera sync_shell.py desde package.json: es la que pinta el banner.
        ruta = os.path.join(repo, "src", "lib", "shell.js")
        if not os.path.isfile(ruta):
            return None
        m = re.search(r"APP_VERSION\s*=\s*'([^']+)'",
                      open(ruta, encoding="utf-8").read())
        return m.group(1) if m else None
    # config_py: APP_VERSION = "1.1.0"
    ruta = os.path.join(repo, "panel", "config.py")
    if not os.path.isfile(ruta):
        return None
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                  open(ruta, encoding="utf-8").read())
    return m.group(1) if m else None


def revisa(app, repo, quiet):
    problemas = []
    rel_changelog, como = CHANGELOG[app]
    ruta = os.path.join(repo, rel_changelog)

    if not os.path.isfile(ruta):
        problemas.append(f"no existe {rel_changelog}")
    else:
        try:
            datos = json.load(open(ruta, encoding="utf-8"))
        except ValueError as e:
            datos = None
            problemas.append(f"{rel_changelog} no es JSON válido: {e}")

        if datos:
            ver_changelog = str(datos.get("version", "") or "")
            ver_app = _version_de_la_app(repo, como)

            if not ver_changelog:
                problemas.append(f"{rel_changelog} sin 'version'")
            elif not ver_app:
                problemas.append("no se encontró la versión de la app "
                                 "(¿falta ECCSA_SHELL_VERSION?)")
            elif ver_changelog != ver_app:
                problemas.append(
                    f"el popup dice v{ver_changelog} pero el banner muestra "
                    f"v{ver_app} — el 📋 no va a saltar hasta que se actualice")

            cambios = [c for c in (datos.get("cambios") or []) if c]
            if not cambios:
                problemas.append("sin 'cambios': el popup abriría vacío")
            if len(cambios) > MAX_CAMBIOS:
                problemas.append(f"{len(cambios)} cambios es mucho para un "
                                 f"modal de teléfono (máx {MAX_CAMBIOS})")
            largos = [c for c in cambios if len(c) > MAX_CAMBIO]
            if largos:
                problemas.append(f"{len(largos)} cambio(s) de más de "
                                 f"{MAX_CAMBIO} caracteres")

    if problemas or not quiet:
        marca = "OK  " if not problemas else "DIFF"
        print(f"[{marca}] {app:14} {repo}")
        for p in problemas:
            print(f"         - {p}")
    return problemas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="repo de una sola app (usa --app)")
    ap.add_argument("--app", choices=sorted(CHANGELOG))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.repo and args.app:
        return 1 if revisa(args.app, args.repo, args.quiet) else 0

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sync_shell", os.path.join(HERE, "tools", "sync_shell.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)

    rc = 0
    for app in CHANGELOG:
        repo = ss.find_repo(app, silencioso=True)
        if not repo:
            # NUNCA "OK" por no haber revisado: un check que pasa sin mirar es
            # peor que no tener check, porque da falsa confianza.
            print(f"[DIFF] {app:14} repo no encontrado — NO se pudo verificar")
            print(f"         - fijalo con SHELL_APP_{app.upper()}=/ruta/a/la/app")
            rc = 1
            continue
        if revisa(app, repo, args.quiet):
            rc = 1
    if rc == 0 and not args.quiet:
        print("\nLos 3 changelog coinciden con la versión que muestra su banner.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
