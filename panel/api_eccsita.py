"""
panel/api_eccsita.py — API JSON del modulo ECCSita
==================================================
Expone la memoria semanal del grupo de WhatsApp ECCSita. La captura la hace
`panel/wa_webhook.py`; aqui solo se lee, se resume (sin IA en esta fase) y se
exporta.

Rutas (todas bajo la sesion del panel y CSRF en los POST):

  GET  /api/panel/eccsita/semanas
  GET  /api/panel/eccsita/semanas/{anio}/{semana}
  POST /api/panel/eccsita/semanas/{anio}/{semana}/generar
  POST /api/panel/eccsita/semanas/{anio}/{semana}/revisar
  GET  /api/panel/eccsita/semanas/{anio}/{semana}/export?formato=md|json
  GET  /api/panel/eccsita/grupo
  POST /api/panel/eccsita/grupo
  GET  /api/panel/eccsita/grupos
  GET  /api/panel/eccsita/personas
  GET  /api/panel/eccsita/personas/{idUsuario}
  POST /api/panel/eccsita/perfiles/{id}

La cronica de esta fase es determinista (`GeneradoPor='BASICO'`): cuenta quien
hablo cada dia y guarda las citas mas largas. `Temas` se guarda vacio a
proposito: sin IA, inventar temas seria inventar conversacion.

El export responde `None` para avisar a `panel/api.py` de que la respuesta ya
salió con su propio `Content-Disposition` (ver `_responder`).

El roast (`/personas` y `/perfiles`) lo lee y modera `eccsita_perfiles`, que
vive en la raíz junto a los workers: se importa por demanda, igual que
`openwa_client`, para que las pruebas lo sustituyan sin abrir SQL Server.
"""
import json
import logging
import urllib.parse

from . import api as _api
from . import db, eccsita

log = logging.getLogger("panel.api.eccsita")

FORMATOS = ("md", "json")
MAX_ANIO = 2100
MIN_ANIO = 2000


# ─── utilidades ──────────────────────────────────────────────────────────────
def _query(handler):
    q = urllib.parse.urlsplit(getattr(handler, "path", "") or "").query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items() if v}


def _log(user, accion):
    try:
        db.log_activity((user or {}).get("email"), "Panel Workers", accion)
    except Exception:
        log.warning("no se pudo registrar en la bitacora: %s", accion)


def _semana(params):
    """(anio, semana) validos o (None, error_http)."""
    try:
        anio = int(params.get("anio") or 0)
        semana = int(params.get("semana") or 0)
    except (TypeError, ValueError):
        return None, (400, {"error": "Año o semana no válidos."})
    if not (MIN_ANIO <= anio <= MAX_ANIO) or not (1 <= semana <= 53):
        return None, (400, {"error": "Año o semana fuera de rango."})
    return (anio, semana), None


def _grupo():
    return eccsita.config().get("grupo") or ""


def _sin_grupo():
    return 400, {"error": "Primero elige el grupo de ECCSita."}


# ─── semanas ─────────────────────────────────────────────────────────────────
def _semanas(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error

    grupo = _grupo()
    limite = _query(handler).get("limite") or eccsita.MAX_LIMITE
    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = eccsita.MAX_LIMITE
    items = eccsita.listar_semanas(grupo, limite) if grupo else []
    return 200, {"items": items, "grupo": grupo, "configurado": bool(grupo)}


def _detalle(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    grupo = _grupo()
    if not grupo:
        return _sin_grupo()

    valores, malo = _semana(params)
    if malo:
        return malo
    anio, semana = valores
    datos = eccsita.detalle_semana(grupo, anio, semana)
    if not datos:
        return 404, {"error": "Esa semana no existe en ECCSita."}
    return 200, datos


def _generar(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    grupo = _grupo()
    if not grupo:
        return _sin_grupo()

    valores, malo = _semana(params)
    if malo:
        return malo
    anio, semana = valores
    datos = eccsita.generar_basico(grupo, anio, semana)
    if not datos:
        return 404, {"error": "Esa semana no tiene mensajes que resumir."}
    _log(user, f"ECCSita: crónica básica de la semana {semana} de {anio}")
    return 200, datos


def _revisar(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    grupo = _grupo()
    if not grupo:
        return _sin_grupo()

    valores, malo = _semana(params)
    if malo:
        return malo
    anio, semana = valores
    datos = eccsita.marcar_revisada(grupo, anio, semana)
    if not datos:
        return 404, {"error": "Esa semana no tiene crónica que revisar."}
    _log(user, f"ECCSita: semana {semana} de {anio} marcada como revisada")
    return 200, datos


# ─── export ──────────────────────────────────────────────────────────────────
def _markdown(semana):
    """Markdown de una semana: cabecera, cronica, personajes y momentos."""
    lineas = [
        f"# ECCSita — Semana {semana['semana']} de {semana['anio']}",
        "",
        f"- Inicio: {semana.get('inicio') or ''}",
        f"- Fin: {semana.get('fin') or ''}",
        f"- Mensajes: {semana.get('total', 0)}",
        f"- Participantes: {semana.get('participantes', 0)}",
        f"- Estado: {semana.get('estado') or ''}",
        "",
    ]
    resumen = (semana.get("resumen") or "").strip()
    if resumen:
        lineas += ["## Crónica", "", resumen, ""]

    personas = semana.get("personas") or []
    if personas:
        lineas += ["## Personas", ""]
        lineas += [f"- {p.get('nombre')}: {p.get('mensajes')} mensajes"
                   for p in personas]
        lineas.append("")

    momentos = semana.get("momentos") or []
    if momentos:
        lineas += ["## Momentos", ""]
        for m in momentos:
            lineas.append(f"- «{m.get('texto') or ''}» — "
                          f"{m.get('autor') or ''} ({m.get('fecha') or ''})")
        lineas.append("")

    temas = semana.get("temas") or []
    if temas:
        lineas += ["## Temas", ""]
        lineas += [f"- {t}" for t in temas]
        lineas.append("")

    return "\n".join(lineas).strip() + "\n"


def _export(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    grupo = _grupo()
    if not grupo:
        return _sin_grupo()

    valores, malo = _semana(params)
    if malo:
        return malo
    anio, semana = valores
    formato = (_query(handler).get("formato") or "md").strip().lower()
    if formato not in FORMATOS:
        return 400, {"error": "Formato no válido: usa md o json."}

    datos = eccsita.detalle_semana(grupo, anio, semana)
    if not datos:
        return 404, {"error": "Esa semana no existe en ECCSita."}

    base = f"eccsita-{anio}-s{semana:02d}"
    if formato == "json":
        contenido = json.dumps(datos, ensure_ascii=False, indent=2)
        ctype, nombre = "application/json", base + ".json"
    else:
        contenido = _markdown(datos)
        ctype, nombre = "text/markdown", base + ".md"

    handler._send(200, contenido, ctype, [
        ("Content-Disposition", f'attachment; filename="{nombre}"')])
    return None


# ─── grupo del que se captura ────────────────────────────────────────────────
def _grupo_get(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error
    grupo = _grupo()
    return 200, {"chatId": grupo, "configurado": bool(grupo)}


def _grupo_post(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error

    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    chat = str(cuerpo.get("chatId") or "").strip()
    if not chat:
        return 400, {"error": "Falta el chatId del grupo."}
    if not chat.endswith("@g.us"):
        return 400, {"error": "El chatId debe terminar en @g.us."}
    if len(chat) > 80:
        return 400, {"error": "El chatId del grupo es demasiado largo."}

    ok, err = eccsita.set_grupo(chat)
    if not ok:
        return 400, {"error": f"No se pudo guardar el grupo: {err}"}
    _log(user, "ECCSita: grupo configurado")
    return 200, {"ok": True, "chatId": chat, "mensaje": "Grupo guardado."}


def _grupos(handler, params, cuerpo):
    user, error = _api._sesion(handler)
    if error:
        return error

    # El cliente vive en /app, no en panel/: se importa por nombre para que las
    # pruebas lo puedan sustituir sin tocar la red.
    import importlib
    try:
        cliente = importlib.import_module("openwa_client")
        ok, datos = cliente.listar_grupos()
    except Exception as exc:
        log.warning("no se pudieron leer los grupos de OpenWA: %s", exc)
        return 200, {"items": [], "error": f"{type(exc).__name__}: {exc}"}
    if not ok:
        return 200, {"items": [], "error": str(datos or "OpenWA no respondió.")}
    items = [{"id": g.get("id") or "", "nombre": g.get("nombre") or "",
              "participantes": g.get("participantes") or 0}
             for g in (datos or []) if g.get("id")]
    return 200, {"items": items}


# ─── perfiles del grupo (roast) ──────────────────────────────────────────────
ACCIONES_PERFIL = ("aprobar", "ocultar", "borrar")


def _modulo_perfiles():
    """`eccsita_perfiles` por demanda (vive en /app, no en panel/)."""
    import importlib
    return importlib.import_module("eccsita_perfiles")


def _conexion():
    """Conexión del panel; las pruebas la sustituyen."""
    return db.get_connection()


def _con_perfiles(funcion):
    """Abre y cierra una conexión alrededor de una lectura o escritura."""
    conn = _conexion()
    try:
        return funcion(conn)
    finally:
        conn.close()


def _personas(handler, params, cuerpo):
    """Resumen del roast: una fila por usuario con menciones guardadas."""
    user, error = _api._sesion(handler)
    if error:
        return error

    modulo = _modulo_perfiles()
    items = _con_perfiles(lambda conn: modulo.perfiles(conn)) or []
    return 200, {"items": items, "total": len(items)}


def _persona(handler, params, cuerpo):
    """Las menciones de un usuario, con su nombre para el encabezado."""
    user, error = _api._sesion(handler)
    if error:
        return error

    try:
        uid = int(params.get("idUsuario") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return 400, {"error": "Usuario no válido."}

    modulo = _modulo_perfiles()
    items = _con_perfiles(lambda conn: modulo.perfiles(conn, uid)) or []
    usuario = db.get_user_by_id(uid)
    if not items and not usuario:
        return 404, {"error": "Ese usuario no tiene perfiles en ECCSita."}
    return 200, {
        "usuario": {"id": uid, "nombre": (usuario or {}).get("nombre") or ""},
        "items": items,
    }


def _perfil_post(handler, params, cuerpo):
    """Aprueba, oculta o borra UNA mención (`accion` en el cuerpo)."""
    user, error = _api._sesion(handler)
    if error:
        return error

    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    accion = str(cuerpo.get("accion") or "").strip().lower()
    if accion not in ACCIONES_PERFIL:
        return 400, {"error": "Acción no válida: usa aprobar, ocultar o "
                              "borrar."}
    try:
        pid = int(params.get("id") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return 400, {"error": "Mención no válida."}

    modulo = _modulo_perfiles()
    if accion == "borrar":
        ok = _con_perfiles(lambda conn: modulo.borrar(conn, pid))
        detalle = "Mención borrada." if ok else "Esa mención ya no existe."
    else:
        valor = 1 if accion == "aprobar" else 0
        ok = _con_perfiles(lambda conn: modulo.aprobar(conn, pid, valor))
        detalle = ("Mención aprobada." if accion == "aprobar"
                   else "Mención ocultada.")
        if not ok:
            detalle = "Esa mención ya no existe."

    if not ok:
        return 404, {"error": detalle}
    _log(user, f"ECCSita: mención {pid} ({accion})")
    return 200, {"ok": True, "accion": accion, "mensaje": detalle}


# ─── registro ────────────────────────────────────────────────────────────────
_SEMANA = r"/api/panel/eccsita/semanas/(?P<anio>\d{4})/(?P<semana>\d{1,2})"

_api.registrar("GET", r"/api/panel/eccsita/semanas", _semanas)
_api.registrar("GET", _SEMANA, _detalle)
_api.registrar("POST", _SEMANA + r"/generar", _generar)
_api.registrar("POST", _SEMANA + r"/revisar", _revisar)
_api.registrar("GET", _SEMANA + r"/export", _export)
_api.registrar("GET", r"/api/panel/eccsita/grupo", _grupo_get)
_api.registrar("POST", r"/api/panel/eccsita/grupo", _grupo_post)
_api.registrar("GET", r"/api/panel/eccsita/grupos", _grupos)
_api.registrar("GET", r"/api/panel/eccsita/personas", _personas)
_api.registrar("GET", r"/api/panel/eccsita/personas/(?P<idUsuario>\d+)",
               _persona)
_api.registrar("POST", r"/api/panel/eccsita/perfiles/(?P<id>\d+)",
               _perfil_post)
