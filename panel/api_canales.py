"""
panel/api_canales.py — Interruptores de los canales de salida
=============================================================
GET  /api/panel/canales   -> {"telegram": bool, "whatsapp": bool, "push": bool}
POST /api/panel/canales   <- {"canal": "telegram|whatsapp|push", "activo": bool}

Los valores viven en `HUB_Config` y los lee/escribe `canales.py`; aquí solo se
expone el contrato JSON del SPA. Requiere sesión de panel; el CSRF ya lo valida
`panel/api.py` en todo POST.
"""
import canales

from . import api

log = api.log


def _estado(handler, params, cuerpo):
    _user, error = api._sesion(handler)
    if error:
        return error
    return 200, canales.estado()


def _guardar(handler, params, cuerpo):
    user, error = api._sesion(handler)
    if error:
        return error

    cuerpo = cuerpo or {}
    canal = str(cuerpo.get("canal") or "").strip().lower()
    if canal not in canales.CANALES:
        return 400, {"error": "Canal no válido. Usa telegram, whatsapp o push."}
    if "activo" not in cuerpo:
        return 400, {"error": "Falta el campo 'activo'."}

    valor = cuerpo.get("activo")
    if not isinstance(valor, bool):
        if valor in (0, 1):
            valor = bool(valor)
        else:
            return 400, {"error": "'activo' debe ser true o false."}

    try:
        canales.guardar(canal, valor)
    except Exception:
        log.exception("no se pudo guardar el canal %s", canal)
        return 500, {"error": "No se pudo guardar el canal."}

    api.db.log_activity(user.get("email"), "Panel Workers",
                        f"Canal {canal}: {'activo' if valor else 'apagado'}")
    return 200, {"ok": True, "canal": canal,
                 "activo": canales.activo(canal)}


api.registrar("GET", r"/api/panel/canales", _estado)
api.registrar("POST", r"/api/panel/canales", _guardar)
