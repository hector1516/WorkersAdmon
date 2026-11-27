"""
panel/config_form.py — Validación del formulario de Configuración por worker
===========================================================================
La comparten la vista SSR (`panel/server.py`) y la API JSON
(`panel/api_config.py`). Es pura: no escribe nada, solo convierte el formulario
en los valores que hay que persistir y devuelve el primer error de validación.

Reglas (las mismas de siempre):
  * `secret` en blanco = no cambiar.
  * `number`/`hour`/`minute` enteros, con `min`/`max` del catálogo.
  * `bool` solo acepta `0` o `1`.
  * nada supera `db.MAX_CONFIG_VALUE` (HUB_Config.Valor es NVARCHAR(500)).
"""
from . import db, spec


def parse_config_form(name, form):
    """
    Valida el formulario de un worker.

    `form` tiene la forma de `urllib.parse.parse_qs`: {campo: [valor]}.
    Devuelve `(env_updates, hub_config_updates, error)`.
    """
    env_updates, cfg_updates, error = {}, {}, None
    for f in spec.editable_fields(name):
        raw = form.get(f["id"])
        if raw is None:                     # el campo no vino en el POST
            continue
        value = str(raw[0] or "").strip()
        tipo, origen = f["tipo"], f["origen"]
        key = f.get("clave") if origen == "hub_config" else f["id"]

        if tipo == "secret" and value == "":
            continue                        # no cambiar el secreto guardado

        if tipo in ("number", "hour", "minute"):
            if value == "":
                value = str(f.get("default", ""))
            try:
                number = int(value)
            except ValueError:
                error = f"{f['label']}: debe ser un número entero."
                break
            if f.get("min") is not None and number < f["min"]:
                error = f"{f['label']}: mínimo {f['min']}."
                break
            if f.get("max") is not None and number > f["max"]:
                error = f"{f['label']}: máximo {f['max']}."
                break
            value = str(number)

        if tipo == "bool" and value not in ("0", "1"):
            error = f"{f['label']}: valor no válido."
            break
        if len(value) > db.MAX_CONFIG_VALUE:
            error = (f"{f['label']}: demasiado largo "
                     f"({len(value)} > {db.MAX_CONFIG_VALUE}).")
            break

        if origen == "env":
            env_updates[key] = value
        else:
            cfg_updates[key] = value
    return env_updates, cfg_updates, error
