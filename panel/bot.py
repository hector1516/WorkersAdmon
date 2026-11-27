"""
panel/bot.py — Interruptores del bot de WhatsApp (Volt)
======================================================
Los seis valores viven en `HUB_Config` y los leen los módulos del bot
(`wa_bot`, `wa_persona`, `wa_volt`, `wa_redaccion` y `wa_asistencia`). Aquí
solo se leen y se escriben con la MISMA validación del formulario SSR
(`panel/config_form.py`), para que la tarjeta del SPA y la de `/clasico` no
puedan aceptar cosas distintas.

`wa_bot` es un pseudo-worker (`spec.PSEUDO_SPEC`): no es un programa de
supervisord. Su catálogo es la fuente única de claves, tipos y defaults.
"""
from . import db, spec
from .config_form import parse_config_form

WORKER = "wa_bot"

# Nombre corto del contrato JSON (y del control del SPA) → clave de HUB_Config.
CLAVES = {
    "sarcasmo": "wa_bot_sarcasmo",
    "roast_grupo": "wa_roast_grupo",
    "volt": "wa_volt_activo",
    "volt_cada": "wa_volt_cada",
    "ia": "wa_bot_ia",
    "asistencia": "wa_asist_activo",
}

CAMPOS = {f.get("clave") or f["id"]: f for f in spec.spec_for(WORKER)}


def _cierto(valor, default):
    """Bool tolerante: ausente deja el default; "0"/"no"/"off" apaga."""
    if valor is None or str(valor).strip() == "":
        return default
    return str(valor).strip().lower() not in ("0", "false", "no", "off")


def _normalizar_bool(valor):
    """1/0 como texto, o None si el valor no es un booleano reconocible."""
    if isinstance(valor, bool):
        return "1" if valor else "0"
    texto = str(valor).strip().lower()
    if texto in ("1", "true", "si", "sí", "on"):
        return "1"
    if texto in ("0", "false", "no", "off"):
        return "0"
    return None


def leer():
    """{sarcasmo: bool, ..., volt_cada: int} con los defaults del catálogo."""
    valores = db.get_config_values(list(CAMPOS))     # {} si la BD no responde
    salida = {}
    for nombre, clave in CLAVES.items():
        campo = CAMPOS[clave]
        crudo = valores.get(clave)
        if campo["tipo"] == "bool":
            salida[nombre] = _cierto(crudo,
                                     str(campo.get("default", "1")) == "1")
        else:
            try:
                salida[nombre] = int(str(crudo or campo["default"]).strip())
            except (KeyError, TypeError, ValueError):
                salida[nombre] = 0
    return salida


def guardar(clave, valor):
    """
    Valida y guarda UNA clave. Devuelve `(ok, error)`.

    `clave` acepta el nombre corto del contrato (`sarcasmo`) o la clave de
    `HUB_Config` (`wa_bot_sarcasmo`). Un valor que no sea 0/1 en un interruptor
    o un entero 1–100 en `wa_volt_cada` se rechaza con error legible.
    """
    clave = str(clave or "").strip()
    campo = CAMPOS.get(clave) or CAMPOS.get(CLAVES.get(clave, ""))
    if campo is None:
        return False, ("Clave no válida. Usa una de: "
                       + ", ".join(CLAVES.values()) + ".")

    if campo["tipo"] == "bool":
        valor = _normalizar_bool(valor)
        if valor is None:
            return False, f"{campo['label']}: usa 1 (sí) o 0 (no)."
    else:
        try:
            valor = str(int(str(valor).strip()))
        except (TypeError, ValueError):
            return False, f"{campo['label']}: debe ser un número entero."

    # Rango y largo: las mismas reglas del formulario SSR, sin una segunda copia.
    _env, cfg_updates, error = parse_config_form(WORKER, {campo["id"]: [valor]})
    if error:
        return False, error
    ok, fallo = db.set_config_values(cfg_updates)
    if not ok:
        return False, f"HUB_Config rechazó el valor: {fallo}"
    return True, ""
