#!/usr/bin/env python3
"""
ECCSA-Shell · check_versiones.py — ¿las apps corren con las versiones del mandato?

Compara las versiones REALMENTE instaladas en los contenedores del ServerVM
contra `versiones/requisitos-canonicos.txt` (que es la lista de Field, el
mandato). Corre en el ServerVM (docker exec a cada contenedor) y escribe el
resultado en el volumen de workersadmon para que el panel lo muestre.

    python tools/check_versiones.py            # en el ServerVM
    python tools/check_versiones.py --json     # solo imprime JSON

Salida: 0 si las apps cumplen el mandato en lo que tienen en común, 1 si no.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(HERE, "versiones", "requisitos-canonicos.txt")
OUT = os.environ.get("VERSIONES_OUT", "/data/versiones.json")

# app → (contenedor, ruta dentro del contenedor del requirements, módulos que
# la app NO tiene y por lo tanto no se comparan)
APPS = {
    "field":        ("field",        None, set()),
    # El kiosco NO tiene nginx (lo sirve el contenedor) ni push/passkeys: solo
    # se le compara lo que comparte con el mandato.
    "dashboard":    ("dashboard",    "/app/api/requirements.txt",
                     {"uvicorn", "python-multipart", "pyjwt", "webauthn",
                      "pywebpush", "py-vapid", "reportlab", "google-generativeai",
                      "pytz", "pysmb"}),
    "admon":        ("admon",        "/app/requirements.txt", set()),
    "workersadmon": ("workersadmon", "/app/requirements.txt",
                     {"uvicorn", "python-multipart"}),   # el panel usa HTTP stdlib
}

SNIPPET = r"""
from importlib.metadata import version, PackageNotFoundError
import json
mods = %(mods)s
out = {}
for p in mods:
    try:
        out[p] = version(p)
    except PackageNotFoundError:
        out[p] = None
print(json.dumps(out))
"""


def parse_canon():
    """Lee los pins del archivo canónico → {paquete: versión}."""
    out = {}
    with open(CANON, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9_.-]+(?:\[[^\]]+\])?)==([0-9][0-9A-Za-z.]*)", line)
            if m:
                out[m.group(1).split("[")[0].lower()] = m.group(2)
    return out


def dist_info(container, mod):
    """Versión instalada de un paquete dentro del contenedor."""
    code = ("from importlib.metadata import version,PackageNotFoundError\n"
            "try: print(version(%r))\n"
            "except PackageNotFoundError: print('__AUSENTE__')\n" % mod)
    try:
        p = subprocess.run(["docker", "exec", container, "python3", "-c", code],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        # Sin docker en la máquina: no se puede verificar nada. Decirlo claro
        # es mejor que un traceback que parece un error del script.
        raise SystemExit(
            "docker no está en el PATH.\n"
            "Este chequeo compara lo INSTALADO en los contenedores, así que solo "
            "corre en el ServerVM (o en cualquier máquina con acceso al Docker de "
            "producción).\n"
            "Para ver la lista canónica sin tocar contenedores, usá:\n"
            "    python tools/check_versiones.py --solo-listado")
    except subprocess.TimeoutExpired:
        return None
    return p.stdout.strip() or None


def contenedor_existe(container):
    """True si el contenedor está corriendo. Una app sin contenedor no se
    marca como desincronizada: sencillamente no hay nada que comparar."""
    try:
        p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                           capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and p.stdout.strip() == "true"


def main():
    # Modo lista: qué se espera en cada app, sin tocar contenedores. Sirve para
    # CI y para consultar el mandato desde cualquier máquina.
    if "--solo-listado" in sys.argv:
        canon = parse_canon()
        print(f"Mandato (Field) — {len(canon)} paquetes:\n")
        for app, (_cont, req, no_tiene) in APPS.items():
            print(f"  {app}:")
            for m in sorted(set(canon) - {x.lower() for x in no_tiene}):
                print(f"      {m:28} {canon[m]}")
            print()
        return 0

    canon = parse_canon()
    resultado = {"revisado": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "mandato": canon, "apps": {}, "problemas": []}
    for app, (cont, _req, no_tiene) in APPS.items():
        if not contenedor_existe(cont):
            resultado["sin_contenedor"].append(app)
            continue
        mods = sorted(set(canon) - {m.lower() for m in no_tiene})
        filas = {}
        for m in mods:
            instalada = dist_info(cont, m)
            filas[m] = {"esperada": canon[m], "instalada": instalada,
                        "ok": instalada == canon[m]}
            if instalada != canon[m]:
                resultado["problemas"].append(
                    f"{app}: {m} tiene {instalada or 'no instalado'}, "
                    f"el mandato dice {canon[m]}")
        resultado["apps"][app] = filas

    # El mandato manda sobre Field: si Field se desvía, el aviso es distinto
    desv_field = [f"{m}={f['instalada']}" for m, f in resultado["apps"]["field"].items()
                  if not f["ok"]]
    resultado["field_es_el_mandato"] = not desv_field
    if desv_field:
        resultado["problemas"].insert(
            0, "Field (el mandato) se desvía: " + ", ".join(desv(field)))

    resultado["ok"] = not resultado["problemas"]
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(resultado, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass  # puede no haber volumen (/data) si se corre fuera del contenedor

    if "--json" in sys.argv:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
    else:
        print("VERSIIONES:", "AL DIA [OK]" if resultado["ok"] else "DESINCRONIZADAS [MAL]")
        for app in resultado.get("sin_contenedor", []):
            print(f"  {app:14} — sin contenedor corriendo (no se compara)")
        for app, filas in resultado["apps"].items():
            malos = [f"{m}={f['instalada']}" for m, f in filas.items() if not f["ok"]]
            print(f"  {app:14} {len(filas)-len(malos)}/{len(filas)} ok"
                  + (f"  ← {', '.join(malos)}" if malos else ""))
        for p in resultado["problemas"]:
            print("  -", p)
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
