"""
panel/api_whatsapp.py — API JSON del módulo WhatsApp
=====================================================
Las cinco sub-pestañas de `panel/views/whatsapp.py` como JSON para el SPA:

  conexion        → OpenWA (API key como secreto, nunca el valor)
  avisos          → HUB_WhatsappEventos con sus destinatarios resueltos
  grupos          → grupos de la sesión (consulta a OpenWA)
  historial       → últimos envíos de HUB_WhatsappQueue
  conversaciones  → bandeja: lista, hilo y acciones

Los grupos y el historial no tumban la pantalla: si OpenWA no responde, el
bloque contesta 200 con `error` y la lista vacía, igual que la vista dice «no se
pudieron leer» en vez de pintar una lista vacía que parezca «no hay grupos».

Reglas del POST (las mismas de `server._wa_action`):
  * permiso `AccesoWhatsapp` (`auth.has_perm_whatsapp`);
  * CSRF lo valida `panel/api.py` antes de llegar aquí;
  * `openwa_client.enviar_texto` se llama SOLO en la acción `enviar`;
  * si OpenWA rechaza el envío no se guarda la fila OUT: el historial no puede
    decir que salió algo que no salió.
"""
import importlib
import logging
import urllib.parse

from . import api as _api
from . import auth, db
from .views import notifications as _notif_view
from .views import wa_conversaciones as _conv_view

log = logging.getLogger("panel.api.whatsapp")

BLOQUES = ("conexion", "avisos", "grupos", "historial", "conversaciones")
ACCIONES = ("enviar", "tomar", "devolver", "cerrar")
LIMITE_LISTA = 100
LIMITE_HILO = 200


# ── utilidades ───────────────────────────────────────────────────────────────
def _query(handler):
    q = urllib.parse.urlsplit(getattr(handler, "path", "") or "").query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items() if v}


def _iso(valor):
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat(timespec="seconds")
    return str(valor)


def _secreto(valor):
    return {"valor": "", "tiene_valor": bool((valor or "").strip())}


def _texto(valores, clave, defecto=""):
    valor = valores.get(clave, defecto)
    if isinstance(valor, bool):
        return "1" if valor else "0"
    return str(valor if valor is not None else defecto).strip()


def _si(valores, clave, defecto=False):
    if clave not in valores:
        return defecto
    valor = valores.get(clave)
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on")
    return bool(valor)


def _log(user, accion):
    try:
        db.log_activity((user or {}).get("email"), "Panel Workers", accion)
    except Exception:
        log.warning("no se pudo registrar en la bitácora: %s", accion)


def _permiso_ok(user):
    return bool(auth.has_perm_whatsapp(user))


def _sin_permiso():
    return 403, {"error": "Requiere el permiso AccesoWhatsapp en el HUB."}


def _openwa():
    """El cliente OpenWA se importa por nombre: vive en /app, no en panel/."""
    return importlib.import_module("openwa_client")


# ── lectura: conexión ────────────────────────────────────────────────────────
def _conexion(user):
    cfg = db.get_openwa_config() or {}
    sesion = (cfg.get("session_id") or "").strip()
    return {
        "bloque": "conexion", "puede": True,
        "base_url": cfg.get("base_url") or "",
        "session_id": sesion,
        "api_key": _secreto(cfg.get("api_key")),
        "configurado": bool((cfg.get("api_key") or "").strip()),
    }


# ── lectura: avisos ──────────────────────────────────────────────────────────
def _avisos(user):
    eventos = db.get_openwa_eventos() or []
    ultimos = db.get_openwa_ultimos_envios() or {}
    items = []
    for ev in eventos:
        eid = ev.get("IdEvento") or ""
        try:
            validos, rechazados = db.get_openwa_destinatarios_resueltos(eid)
        except Exception:
            validos, rechazados = [], []
        ultimo = ultimos.get(eid) or {}
        items.append({
            "id": eid,
            "nombre": ev.get("Nombre") or eid,
            "descripcion": ev.get("Descripcion") or "",
            "plantilla": ev.get("PlantillaMensaje") or "",
            "telefonos": ev.get("Telefonos") or "",
            "adjuntar": bool(ev.get("AdjuntarArchivo")),
            "activo": bool(ev.get("Activo")),
            "destinatarios": list(validos or []),
            "rechazados": list(rechazados or []),
            "ultimo": ({"estado": ultimo.get("Estado"),
                        "creado": _iso(ultimo.get("Creado"))}
                       if ultimo else None),
        })
    return {"bloque": "avisos", "puede": True, "items": items}


# ── lectura: grupos ──────────────────────────────────────────────────────────
def _grupos(user):
    # Se reusa el cache de la vista (90 s): la sub-pestaña Grupos y Avisos ya
    # llaman a OpenWA y no tiene caso pedirlo dos veces seguidas.
    ok, datos = _notif_view._grupos_de_la_sesion()
    if not ok:
        return 200, {"bloque": "grupos", "puede": True, "items": [],
                     "error": str(datos or "OpenWA no respondió.")}
    items = [{"id": g.get("id") or "", "nombre": g.get("nombre") or "",
              "participantes": g.get("participantes") or 0}
             for g in (datos or []) if g.get("id")]
    eventos = [{"id": ev.get("IdEvento") or "",
                "nombre": ev.get("Nombre") or ev.get("IdEvento") or ""}
               for ev in (db.get_openwa_eventos() or [])]
    return 200, {"bloque": "grupos", "puede": True, "items": items,
                 "avisos": eventos}


# ── lectura: historial ───────────────────────────────────────────────────────
def _historial(user):
    items = []
    for h in db.get_openwa_historial(limite=LIMITE_LISTA) or []:
        items.append({
            "id": h.get("Id"),
            "evento": h.get("IdEvento") or "",
            "chat": h.get("ChatId") or "",
            "estado": h.get("Estado") or "",
            "intentos": h.get("Intentos"),
            "error": h.get("Error") or "",
            "creado": _iso(h.get("Creado")),
            "enviado": _iso(h.get("Enviado")),
        })
    return {"bloque": "historial", "puede": True, "items": items}


_LEER = {"conexion": _conexion, "avisos": _avisos,
         "grupos": _grupos, "historial": _historial}


def _get(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    if not _permiso_ok(user):
        return _sin_permiso()

    bloque = (params.get("bloque") or _query(handler).get("bloque")
              or "conexion").strip().lower()
    if bloque == "conversaciones":
        return _conv_lista(handler, params, cuerpo)
    if bloque not in _LEER:
        return 400, {"error": "Bloque no válido."}
    resultado = _LEER[bloque](user)
    return resultado if isinstance(resultado, tuple) else (200, resultado)


# ── guardado de la conexión y de los avisos ──────────────────────────────────
def _guardar_conexion(user, valores):
    cfg = db.get_openwa_config() or {}
    api_key = _texto(valores, "openwa_api_key") or _texto(valores, "api_key")
    base_url = _texto(valores, "openwa_base_url") or _texto(valores, "base_url")
    session_id = (_texto(valores, "openwa_session_id")
                  or _texto(valores, "session_id"))

    nuevos = {}
    if api_key:
        # Mismo contrato que la vista: vacío = no tocar, BORRAR = borrar.
        nuevos["openwa_api_key"] = ("" if api_key.strip().upper() == "BORRAR"
                                    else api_key)
    if base_url:
        nuevos["openwa_base_url"] = base_url
    if session_id:
        nuevos["openwa_session_id"] = session_id
    if not nuevos:
        return 400, {"error": "Sin cambios: no se envió ningún valor."}

    ok, err = db.set_config_values(nuevos)
    if not ok:
        return 400, {"error": f"No se pudo guardar la conexión: {err}"}
    _log(user, "Conexión de OpenWA (WhatsApp) actualizada")
    return 200, {"ok": True, "mensaje": "Conexión de OpenWA guardada.",
                 "api_key": _secreto(nuevos.get("openwa_api_key",
                                                cfg.get("api_key")))}


def _guardar_aviso(user, valores):
    eid = _texto(valores, "id_evento") or _texto(valores, "IdEvento")
    if not eid:
        return 400, {"error": "Falta el aviso."}
    if db.get_openwa_evento(eid) is None:
        return 404, {"error": f"El aviso '{eid}' no existe."}

    plantilla = _texto(valores, "plantilla") or _texto(valores, "PlantillaMensaje")
    telefonos = _texto(valores, "telefonos") or _texto(valores, "Telefonos")
    adjunto = _si(valores, "adjuntar", _si(valores, "AdjuntarArchivo"))
    activo = _si(valores, "activo", _si(valores, "Activo"))

    # Un número mal escrito se avisa al guardar, no a medias del envío.
    try:
        validos, rechazados = _openwa().normalizar_chat_ids(telefonos)
    except Exception:
        validos, rechazados = [], []
    if rechazados:
        return 400, {"error": "No se guardó: no son números → "
                              f"{', '.join(rechazados)}. Escríbelos con clave "
                              "de país (ej. 5218123211516)."}
    ok, err = db.update_openwa_evento(eid, plantilla, telefonos, adjunto, activo)
    if not ok:
        return 400, {"error": f"No se pudo guardar el aviso: {err}"}
    _log(user, f"Aviso de WhatsApp {eid} actualizado")
    a_donde = (f" a {len(validos)} número(s)" if validos
               else " (sin números: no se enviará nada)")
    return 200, {"ok": True, "mensaje": f"Aviso {eid} guardado{a_donde}.",
                 "destinatarios": list(validos or [])}


def _probar_conexion(user, valores):
    destino = (_texto(valores, "numero") or _texto(valores, "openwa_prueba_numero"))
    from . import workers
    ok, msg = workers.test_openwa(destino)
    _log(user, f"Prueba de OpenWA: {'OK' if ok else 'ERROR'}")
    return 200, {"ok": bool(ok), "mensaje": f"OpenWA: {msg}"}


def _probar_aviso(user, valores):
    eid = _texto(valores, "id_evento") or _texto(valores, "IdEvento")
    if not eid:
        return 400, {"error": "Falta el aviso."}
    try:
        alertas = importlib.import_module("openwa_alerts")
        ok, msg = alertas.reenviar_ultimo(eid)
    except Exception as exc:
        _log(user, f"Reenvío del aviso {eid}: ERROR")
        return 400, {"error": f"No se pudo reenviar: {exc}"}
    _log(user, f"Reenvío del aviso {eid}: {'OK' if ok else 'sin envío'}")
    return 200, {"ok": bool(ok), "mensaje": msg}


_GUARDAR = {"conexion": _guardar_conexion, "avisos": _guardar_aviso}
_PROBAR = {"conexion": _probar_conexion, "avisos": _probar_aviso}


def _post(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    if not _permiso_ok(user):
        return _sin_permiso()

    bloque = (params.get("bloque") or "").strip().lower()
    if bloque not in _GUARDAR:
        return 400, {"error": "Bloque sin acciones de guardado."}

    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    accion = str(cuerpo.get("accion") or "").strip().lower()
    valores = cuerpo.get("valores") or {}
    if not isinstance(valores, dict):
        return 400, {"error": "Los valores deben ser un objeto."}
    if accion == "save":
        return _GUARDAR[bloque](user, valores)
    if accion == "probar":
        return _PROBAR[bloque](user, valores)
    return 400, {"error": "Acción no válida."}


# ── bandeja de conversaciones ────────────────────────────────────────────────
def _nombre_contacto(fila, chat=""):
    nombre = (fila.get("Nombre") or "").strip()
    if nombre:
        return nombre
    tel = ((fila.get("Telefono") or "").strip()
           or _conv_view.telefono_de(chat or fila.get("ChatId")))
    return tel or "(sin nombre)"


def _conv_lista(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    if not _permiso_ok(user):
        return _sin_permiso()

    items = []
    for c in db.get_wa_conversaciones(limite=LIMITE_LISTA) or []:
        chat = c.get("ChatId") or ""
        items.append({
            "chat": chat,
            "nombre": _nombre_contacto(c, chat),
            "telefono": ((c.get("Telefono") or "").strip()
                         or _conv_view.telefono_de(chat)),
            "estado": c.get("Estado") or "",
            "asignado": (c.get("Asignado") or "").strip() or None,
            "ultima": _iso(c.get("Ultima")),
            "ultimo_texto": c.get("Texto") or "",
        })
    return 200, {"items": items}


def _conv_detalle(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    if not _permiso_ok(user):
        return _sin_permiso()

    chat = (params.get("chat") or "").strip()
    conv = db.get_wa_conversacion(chat) if chat else None
    if not conv:
        return 404, {"error": "Ese chat no existe en la bandeja."}

    mensajes, nombre = [], ""
    for m in db.get_wa_mensajes(chat, limite=LIMITE_HILO) or []:
        nombre = (m.get("Nombre") or "").strip() or nombre
        mensajes.append({
            "dir": "OUT" if str(m.get("Direccion") or "").upper() == "OUT"
                   else "IN",
            "texto": m.get("Texto") or "",
            "fecha": _iso(m.get("Fecha")),
        })
    return 200, {
        "chat": chat,
        "nombre": nombre or _conv_view.telefono_de(chat) or "(sin nombre)",
        "telefono": _conv_view.telefono_de(chat),
        "estado": conv.get("Estado") or "",
        "asignado": (conv.get("Asignado") or "").strip() or None,
        "mensajes": mensajes,
    }


def _conv_accion(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    if not _permiso_ok(user):
        return _sin_permiso()

    chat = (params.get("chat") or "").strip()
    accion = (params.get("accion") or "").strip().lower()
    if accion not in ACCIONES:
        return 400, {"error": "Acción no válida."}
    if not chat or not db.get_wa_conversacion(chat):
        return 404, {"error": "Ese chat no existe en la bandeja."}

    if accion == "tomar":
        db.set_wa_estado(chat, "HUMANO", (user or {}).get("id"))
        _log(user, f"Conversación de WhatsApp {chat}: la tomó "
                   f"{(user or {}).get('email')}")
        return 200, {"ok": True, "estado": "HUMANO",
                     "mensaje": "Tomaste la conversación: el bot ya no "
                                "contesta."}
    if accion == "devolver":
        db.set_wa_estado(chat, "BOT")
        _log(user, f"Conversación de WhatsApp {chat}: devuelta al bot")
        return 200, {"ok": True, "estado": "BOT",
                     "mensaje": "La conversación volvió al bot."}
    if accion == "cerrar":
        db.set_wa_estado(chat, "CERRADA")
        _log(user, f"Conversación de WhatsApp {chat}: cerrada")
        return 200, {"ok": True, "estado": "CERRADA",
                     "mensaje": "Conversación cerrada. El bot no contestará."}

    # enviar
    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    texto = str(cuerpo.get("texto") or "").strip()
    if not texto:
        return 400, {"error": "Escribe el texto antes de enviar."}
    try:
        ok, msg = _openwa().enviar_texto(chat, texto)
    except Exception as exc:
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        # Sin fila OUT: el historial no puede decir que salió algo que no salió.
        return 400, {"error": f"No se pudo enviar: {msg}"}

    from tz_mx import now_mx
    guardado = db.guardar_wa_salida(
        chat, texto, telefono=_conv_view.telefono_de(chat),
        nombre=(user or {}).get("nombre"), fecha=now_mx())
    _log(user, f"Conversación de WhatsApp {chat}: respuesta enviada")
    if not guardado:
        return 200, {"ok": True, "guardado": False,
                     "mensaje": "Respuesta enviada, pero no se pudo guardar en "
                                "el historial (¿falta la migración 0066?)."}
    return 200, {"ok": True, "guardado": True, "mensaje": "Respuesta enviada."}


# ── registro ─────────────────────────────────────────────────────────────────
_api.registrar("GET", r"/api/panel/whatsapp", _get)
_api.registrar("GET", r"/api/panel/whatsapp/(?P<bloque>[a-z]+)", _get)
_api.registrar("POST", r"/api/panel/whatsapp/(?P<bloque>[a-z]+)", _post)
_api.registrar("GET", r"/api/panel/wa/conversaciones", _conv_lista)
_api.registrar("GET", r"/api/panel/wa/conversaciones/(?P<chat>[^/]+)",
               _conv_detalle)
_api.registrar("POST",
               r"/api/panel/wa/conversaciones/(?P<chat>[^/]+)/(?P<accion>[a-z]+)",
               _conv_accion)
