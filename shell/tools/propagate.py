#!/usr/bin/env python3
"""
ECCSA-Shell · propagate.py — empuja el shell a los repos de las apps.

Por qué existe: `sync_shell.py` copia a clones LOCALES, que es lo correcto para
trabajar en la máquina. Pero en GitHub Actions no hay clones: el job solo tiene
este repo. Así que este script hace lo mismo speaking la API de GitHub — crea o
actualiza cada archivo de cada app — para que un push al shell termine en las
3 apps sin que nadie tenga que correr nada a mano.

Cada app tiene su propio workflow de deploy, así que a partir de acá el cambio
se publica solo: este workflow solo deja los archivos en el repo.

El mapa de "qué archivo va a dónde" NO se reescribe acá: se importa de
sync_shell.py, que es la fuente canónica. Si mañana se agrega un artefacto al
shell, esta lista se actualiza sola.

Uso (lo que corre el workflow):
    SHELL_DEPLOY_TOKEN=<pat>  python tools/propagate.py
    SHELL_DEPLOY_TOKEN=<pat>  python tools/propagate.py --dry-run

    --only field|admon|workersadmon   propaga a una sola app
    --app-version 1.2.3               estampa la versión de la app (lo normal es
                                      que la suba el commit de la app, no esto)
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"

# app_id → (repo "dueño/nombre", rama principal). El orden es el de APPS.
REPOS = {
    "field":        ("hector1516/field",        "main"),
    "admon":        ("hector1516/AdmonApp",     "master"),
    "workersadmon": ("hector1516/WorkersAdmon", "master"),
}

COMMIT_MESSAGE = "chore(shell): propaga ECCSA-Shell {version}"


def _carga_sync_shell():
    """Importa tools/sync_shell.py para no duplicar el mapa de destinos."""
    import importlib.util
    ruta = os.path.join(HERE, "tools", "sync_shell.py")
    spec = importlib.util.spec_from_file_location("sync_shell", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def archivos_de_la_app(mod, app):
    """{ruta_en_la_app: ruta_en_el_shell} para una app."""
    variante, css_rel, _ = mod.APPS[app]
    archivos = {css_rel: os.path.join("dist", f"shell.{variante}.css")}
    for origen, destino in mod.ARTEFACTOS.get(variante, {}).items():
        archivos[destino] = os.path.join("banner", origen)
    return archivos


def _api(token, path, method="GET", body=None):
    peticion = urllib.request.Request(f"{API}{path}", method=method)
    peticion.add_header("Authorization", f"Bearer {token}")
    peticion.add_header("Accept", "application/vnd.github+json")
    peticion.add_header("X-GitHub-Api-Version", "2022-11-28")
    datos = None
    if body is not None:
        datos = json.dumps(body).encode("utf-8")
        peticion.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(peticion, data=datos, timeout=60) as r:
        crudo = r.read()
    return json.loads(crudo) if crudo else {}


def _contenido_en_github(token, repo, ruta, rama):
    """(sha, texto) del archivo, o (None, None) si no existe todavía."""
    try:
        info = _api(token, f"/repos/{repo}/contents/{ruta}?ref={rama}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    return info.get("sha"), base64.b64decode(info["content"]).decode("utf-8")


def _sube(token, repo, ruta, rama, texto, sha, mensaje):
    cuerpo = {
        "message": mensaje,
        "content": base64.b64encode(texto.encode("utf-8")).decode("ascii"),
        "branch": rama,
    }
    if sha:
        cuerpo["sha"] = sha
    _api(token, f"/repos/{repo}/contents/{ruta}", method="PUT", body=cuerpo)


def propaga(app, mod, token, dry_run, mensaje):
    repo, rama = REPOS[app]
    print(f"▸ {app} → {repo} ({rama})")
    cambios = 0
    for destino, origen in sorted(archivos_de_la_app(mod, app).items()):
        ruta_shell = os.path.join(HERE, origen)
        if not os.path.isfile(ruta_shell):
            print(f"    ! falta {origen} en el shell: se omite")
            continue
        texto = open(ruta_shell, encoding="utf-8").read()

        sha, actual = _contenido_en_github(token, repo, destino, rama)
        if actual == texto:
            print(f"    = {destino:42} sin cambios")
            continue
        if dry_run:
            verb = "actualizar" if sha else "crear"
            print(f"    ~ {destino:42} {verb} ({len(texto)} bytes)")
        else:
            _sube(token, repo, destino, rama, texto, sha, mensaje)
            verb = "actualizado" if sha else "creado"
            print(f"    ↑ {destino:42} {verb} ({len(texto)} bytes)")
        cambios += 1
    if not cambios:
        print("    (nada que propagar)")
    return cambios


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=sorted(REPOS))
    args = ap.parse_args()

    token = os.environ.get("SHELL_DEPLOY_TOKEN", "").strip()
    if not token:
        print("::warning::SHELL_DEPLOY_TOKEN no está configurado: no se propaga.")
        print("::warning::hay que correr 'python tools/sync_shell.py --all' a mano.")
        return 0

    mod = _carga_sync_shell()
    version = open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    mensaje = COMMIT_MESSAGE.format(version=version)

    # Antes de tocar nada, verificar que el diseño esté generado: si dist/ está
    # viejo, se subiría un CSS que no corresponde a VERSION y la guarda de
    # versionado de las apps lo va a marcar.
    if not args.dry_run:
        import subprocess
        rc = subprocess.run([sys.executable,
                             os.path.join(HERE, "tools", "build_shell.py"),
                             "--check"], capture_output=True, text=True)
        if rc.returncode != 0:
            print("::error::dist/ está desactualizado; corre build_shell.py primero")
            print(rc.stdout + rc.stderr)
            return 1

    print(f"ECCSA-Shell {version} → propagando"
          f"{' (dry-run)' if args.dry_run else ''}")
    total = 0
    for app in (args.only,) if args.only else REPOS:
        try:
            total += propaga(app, mod, token, args.dry_run, mensaje)
        except urllib.error.HTTPError as e:
            print(f"    ERROR {app}: la API devolvió {e.code} {e.reason}")
            return 1
        except urllib.error.URLError as e:
            print(f"    ERROR {app}: no se pudo hablar con la API ({e.reason})")
            return 1
    verb = "se propagarían" if args.dry_run else "se propagaron"
    print(f"Listo: {total} archivos {verb} en "
          f"{1 if args.only else len(REPOS)} app(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
