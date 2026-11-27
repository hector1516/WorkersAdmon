"""
panel/api_bot.py — Interruptores del bot de WhatsApp (Volt)
===========================================================
GET  /api/panel/bot   → {"sarcasmo": true, "roast_grupo": true,
                         "volt": true, "volt_cada": 12, "ia": false,
                         "asistencia": true}
POST /api/panel/bot   ← {"clave": "sarcasmo|wa_bot_sarcasmo|...", "valor": 0|1}

La sesión se comprueba aquí; el CSRF de todo POST ya lo valida `panel/api.py`.
La validación y el guardado son los de `panel/bot.py`, que comparte con la
tarjeta SSR de `/clasico/configuracion`: no hay una segunda copia de las reglas.
"""
from . import bot, db
from .api import _sesion, registrar


def _get(handler, params, cuerpo):
    _user, error = _sesion(handler)
    if error:
        return error
    return 200, bot.leer()


def _post(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    cuerpo = cuerpo or {}
    clave = str(cuerpo.get("clave") or "").strip()
    if not clave:
        return 400, {"error": "Falta la clave del control."}
    if "valor" not in cuerpo:
        return 400, {"error": "Falta el valor del control."}

    ok, fallo = bot.guardar(clave, cuerpo.get("valor"))
    if not ok:
        return 400, {"error": fallo}

    # La bitácora registra la clave, nunca el valor.
    db.log_activity(user.get("email"), "Panel Workers",
                    f"Bot de WhatsApp: {clave} actualizado")
    return 200, {"ok": True}


registrar("GET", r"/api/panel/bot", _get)
registrar("POST", r"/api/panel/bot", _post)
