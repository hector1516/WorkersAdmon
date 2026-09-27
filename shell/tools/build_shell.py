#!/usr/bin/env python3
"""
ECCSA-Shell · build_shell.py — genera las 3 variantes del shell.

Una sola fuente (src/body.css + tokens.css) → tres archivos listos para cada app:

  dist/shell.t4.css     Tailwind v4  → Field (y apps nuevas con v4)
  dist/shell.t3.css     Tailwind v3  → Admon
  dist/shell.plain.css  sin build    → panel de WorkersAdmon (Python)

Uso:  python tools/build_shell.py [--check]
      --check solo regenera en memoria y avisa (para CI) si dist/ está viejo.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "src")
DIST = os.path.join(HERE, "dist")
VERSION_FILE = os.path.join(HERE, "VERSION")

def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read().strip() + "\n"


def root_block(tokens_path):
    """El bloque :root plano de tokens.css (los valores, sin @theme)."""
    txt = open(tokens_path, encoding="utf-8").read()
    i = txt.find(":root {")
    return txt[i:].strip() + "\n"


def theme_block(tokens_path):
    """El bloque @theme de tokens.css (Tailwind v4)."""
    txt = open(tokens_path, encoding="utf-8").read()
    i = txt.find("@theme {")
    j = txt.find("\n}", i)
    return txt[i:j + 2].strip() + "\n"


def build(tokens_path):
    body = read("body.css")
    utils = read("utilities.css")
    root = root_block(tokens_path)
    theme = theme_block(tokens_path)
    head = ("/* ECCSA-Shell · NO EDITAR: generado por tools/build_shell.py desde\n"
            "   src/body.css + tokens.css. Para cambiar el diseño, edita esos y\n"
            "   corre: python tools/build_shell.py && python tools/sync_shell.py --all */\n\n")
    # OJO con el orden en t4: Tailwind v4 convierte el @theme en su propio
    # :root, y el bloque :root de aquí trae además los tokens ESTRUCTURALES
    # (--color-line, --z-*, --radius-*, --nav-h, --tap), que no están en @theme
    # porque no son colores ni medidas de Tailwind. Si el :root de aquí fuera
    # DESPUÉS, pisaría lo que Tailwind genera. Va antes, a propósito.
    return {
        "shell.t4.css": head + "@import 'tailwindcss';\n\n" + root + "\n"
                         + theme + "\n" + body + "\n" + utils,
        "shell.t3.css": head + ("@tailwind base;\n@tailwind components;\n"
                                 "@tailwind utilities;\n\n") + root + "\n" + body + "\n" + utils,
        "shell.plain.css": head + root + "\n" + body,
    }


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def main():
    check = "--check" in sys.argv
    os.makedirs(DIST, exist_ok=True)
    tokens_path = os.path.join(HERE, "tokens.css")
    variants = build(tokens_path)
    version = open(VERSION_FILE, encoding="utf-8").read().strip()
    rc = 0
    # Manifiesto: qué se generó y con qué huella (lo usa sync_shell para
    # exigir que al cambiar el diseño se suba la versión del shell).
    manifest_path = os.path.join(DIST, "BUILD.json")
    manifest = {"version": version,
                "sha": {k: sha(v) for k, v in variants.items()}}
    if check:
        old = {}
        if os.path.isfile(manifest_path):
            try:
                old = json.load(open(manifest_path, encoding="utf-8"))
            except Exception:
                old = {}
        if old and old.get("sha") != manifest["sha"]:
            print("[DIFF] dist/ no corresponde a tokens.css + src/ "
                  "(falta correr build_shell.py)")
            rc = 1
        if old and old.get("version") != version:
            print(f"[AVISO] VERSION cambió {old.get('version')} → {version} "
                  f"y el diseño también; recuerda propagar: sync_shell.py --all")
        return rc
    for name, content in variants.items():
        path = os.path.join(DIST, name)
        old = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
        if old == content:
            print(f"[OK ]  dist/{name} al día ({len(content.splitlines())} líneas)")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"[OK ]  dist/{name} generado ({len(content.splitlines())} líneas)")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
