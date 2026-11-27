"""
panel/api_config.py — API JSON de Configuración, Apps y Avisos
=============================================================
Se registra sola: `panel/api.py` importa todos los `panel/api_*.py` al final.
Las rutas son las de `panel_ui/API.md`:

  GET  /api/panel/config   → workers con sus campos editables
  POST /api/panel/config   → {valores:{clave:valor}} (+ worker opcional)
  GET  /api/panel/apps     → catálogo por app + claves sin clasificar
  POST /api/panel/apps     → {accion: save|edit|del|add|clasificar}
  GET  /api/panel/avisos   → avisos del ecosistema y su contexto

Reglas:
  * La sesión y el permiso se comprueban aquí, porque `panel/api.py` solo
    valida el CSRF de los POST.
  * Los secretos NUNCA viajan: van como `{valor: "", tiene_valor: true}`.
  * La validación y el MERGE son los mismos de la vista SSR: se reusa
    `panel/config_form.parse_config_form` y `panel/db.set_config_values`.
    No hay una segunda copia de las reglas.
"""
import datetime

from . import auth, db, envconf, spec, workers
from .api import _grupos, _sesion, registrar
from .config_form import parse_config_form
from .views import apps as apps_view
from .views import workers as workers_view

# Orden y título de las secciones de una tarjeta de Configuración.
ORIGENES = (
    ("env", "ENTORNO", "Se guarda en el conf y se reaplica en cada arranque "
                       "(volumen /data). Si el worker está corriendo se "
                       "reinicia al guardar."),
    ("hub_config", "HUB_CONFIG", "Va a la misma tabla que edita el HUB y "
                                 "aplica de inmediato, sin reiniciar."),
    ("const", "CÓDIGO", "Solo lectura: la fuente de verdad es el repo HUB."),
    ("info", "INFO", "Nota informativa."),
)

# Los tipos del spec que la API simplifica para el frontend.
_TIPO_API = {
    "hour": "number", "minute": "number",
    "const": "readonly", "info": "readonly",
}


# ── permisos ─────────────────────────────────────────────────────────────────
def _sesion_apps(handler):
    """Sesión + `AccesoAppConfig`, que es lo que pide la pestaña Apps."""
    user, error = _sesion(handler)
    if error:
        return None, error
    if not auth.has_perm(user, "AccesoAppConfig"):
        return None, (403, {"error": "Tu usuario no tiene el permiso "
                                     "AccesoAppConfig."})
    return user, None


def _log(user, accion):
    db.log_activity(user["email"], "Panel Workers", accion)


# ── Configuración ────────────────────────────────────────────────────────────
def _tipo_api(f):
    return _TIPO_API.get(f["tipo"], f["tipo"])


def _opciones(f):
    """Opciones del select si el catálogo las declara (hoy casi nunca)."""
    crudas = f.get("opciones") or []
    out = []
    for o in crudas:
        if isinstance(o, dict):
            out.append({"valor": str(o.get("valor", "")),
                        "texto": str(o.get("texto", o.get("valor", "")))})
        elif isinstance(o, (list, tuple)) and len(o) >= 2:
            out.append({"valor": str(o[0]), "texto": str(o[1])})
    return out


def _campo(f, env_vals, cfg_vals):
    """Un campo de la tarjeta: valor vigente, sin exponer secretos."""
    fid, origen = f["id"], f["origen"]
    api_tipo = _tipo_api(f)
    clave_cfg = f.get("clave") or fid

    if origen == "env":
        crudo = env_vals.get(fid)
        tiene_valor = crudo is not None
        valor = (crudo if tiene_valor
                 else ("" if f.get("default") is None else str(f["default"])))
    elif origen == "hub_config":
        crudo = cfg_vals.get(clave_cfg, "")
        tiene_valor = bool(str(crudo).strip())
        valor = crudo if tiene_valor else str(f.get("default", ""))
    else:                                   # const / info
        valor = f.get("valor", "")
        tiene_valor = bool(str(valor).strip())

    if api_tipo == "secret":
        valor = ""                          # el secreto jamás se manda

    return {
        "clave": fid,
        "label": f["label"],
        "tipo": api_tipo,
        "valor": "" if valor is None else str(valor),
        "ayuda": f.get("ayuda", ""),
        "origen": origen,
        "unidad": f.get("unidad", ""),
        "tiene_valor": bool(tiene_valor),
        "opciones": _opciones(f),
    }


def _grupos_config(fields, env_vals, cfg_vals):
    """Campos agrupados por origen, en el orden de ORIGENES."""
    grupos = []
    for origen, titulo, nota in ORIGENES:
        campos = [_campo(f, env_vals, cfg_vals)
                  for f in fields if f.get("origen") == origen]
        if campos:
            grupos.append({"titulo": titulo, "nota": nota, "campos": campos})
    return grupos


def _config_get(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    status = workers.build_status()
    programas = {p.get("name"): p for p in status.get("programs") or []}
    workers_spec = spec.all_workers()

    env_vals = {w: envconf.read_env(w) for w in workers_spec}
    claves = [c for w in workers_spec for c in spec.hub_config_keys(w)]
    cfg_vals = db.get_config_values(claves)     # {} si la BD no responde

    salida = []
    for nombre in workers_spec:
        prog = programas.get(nombre) or {}
        salida.append({
            "nombre": nombre,
            "estado": prog.get("state") or "NO_HABILITADO",
            "descripcion": workers.CATALOGO.get(nombre, {}).get("descripcion", ""),
            "grupos": _grupos_config(spec.spec_for(nombre),
                                     env_vals.get(nombre, {}), cfg_vals),
        })
    return 200, {"workers": salida}


def _workers_de(claves):
    """Workers a los que pertenece alguna de las claves enviadas."""
    pedidas = set(claves)
    duenos = []
    for nombre in spec.all_workers():
        ids = {f["id"] for f in spec.editable_fields(nombre)}
        if ids & pedidas:
            duenos.append(nombre)
    return duenos


def _config_post(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    valores = cuerpo.get("valores")
    if not isinstance(valores, dict) or not valores:
        return 400, {"error": "No hay valores que guardar."}

    nombre = str(cuerpo.get("worker") or "").strip()
    if nombre:
        if not spec.spec_for(nombre):
            return 400, {"error": f"El worker '{nombre}' no tiene "
                                  "configuración declarada."}
    else:
        duenos = _workers_de(valores)
        if len(duenos) != 1:
            return 400, {"error": "Indica el worker: los valores enviados "
                                  "no pertenecen a uno solo."}
        nombre = duenos[0]

    # Misma forma que el formulario SSR: {campo: [valor]}.
    form = {str(k): ["" if v is None else v] for k, v in valores.items()}
    env_updates, cfg_updates, fallo = parse_config_form(nombre, form)
    if fallo:
        return 400, {"error": fallo}

    detalles = []
    if env_updates:
        ok, salida = envconf.write_env(nombre, env_updates)
        if not ok:
            return 400, {"error": f"No se pudo guardar la configuración de "
                                  f"'{nombre}': {salida or 'error'}"}
        detalles.append(f"{len(env_updates)} variable(s) de entorno")
    if cfg_updates:
        ok, salida = db.set_config_values(cfg_updates)
        if not ok:
            return 400, {"error": f"HUB_Config rechazó los valores: {salida}"}
        detalles.append(f"{len(cfg_updates)} clave(s) HUB_Config")

    if not detalles:
        return 200, {"ok": True, "mensaje": "Sin cambios: los campos "
                                            "enviados están vacíos."}

    claves = sorted(list(env_updates) + list(cfg_updates))
    _log(user, f"Config '{nombre}' actualizada (API): {', '.join(claves)}")
    return 200, {"ok": True,
                 "mensaje": f"Configuración de '{nombre}' guardada: "
                            f"{' + '.join(detalles)}."}


# ── Apps ─────────────────────────────────────────────────────────────────────
def _fila_json(f):
    """Una clave del catálogo, con su valor y sin exponer secretos."""
    valor = f.get("Valor") or ""
    if f.get("Tipo") == "secret":
        valor = ""
    return {
        "id": f.get("Id"),
        "clave": f.get("Clave") or "",
        "titulo": f.get("Titulo") or f.get("Clave") or "",
        "tipo": f.get("Tipo") or "text",
        "unidad": f.get("Unidad") or "",
        "orden": f.get("Orden") or 0,
        "descripcion": f.get("Descripcion") or "",
        "valor": valor,
        "tiene_valor": bool(str(f.get("Valor") or "").strip()),
    }


def _apps_get(handler, params, cuerpo):
    user, error = _sesion_apps(handler)
    if error:
        return error

    tiene_tabla = db.config_catalog_exists()
    catalogo = db.get_config_catalog() if tiene_tabla else []
    todos = db.get_all_config_values()
    catalogadas = {f["Clave"] for f in catalogo}

    apps = []
    for app in sorted({f["App"] for f in catalogo}):
        filas = sorted([f for f in catalogo if f["App"] == app],
                       key=lambda f: (f.get("Orden") or 0, f["Clave"]))
        apps.append({
            "clave": app,
            "titulo": app,
            "tipo": "",
            "unidad": "",
            "orden": 0,
            "descripcion": "",
            "claves": [_fila_json(f) for f in filas],
        })

    sin_clasificar = [
        {"clave": clave, "titulo": clave, "tipo": "text", "unidad": "",
         "orden": 0, "descripcion": "",
         # El valor no se manda: todavía no sabemos si es secreto.
         "tiene_valor": bool(str(valor).strip())}
        for clave, valor in sorted(todos.items()) if clave not in catalogadas
    ]
    return 200, {
        "apps": apps,
        "sin_clasificar": sin_clasificar,
        "migracion": tiene_tabla,
        "aviso": "" if tiene_tabla else
                 "Falta la migración 0036: HUB_ConfigCatalogo no existe. "
                 "Las claves se listan sin clasificar y no se puede editar.",
    }


def _fila_por_id(fid):
    """Fila del catálogo por Id (None si ya no existe)."""
    try:
        fid = int(fid)
    except (TypeError, ValueError):
        return None
    for fila in db.get_config_catalog():
        if fila["Id"] == fid:
            return fila
    return None


def _apps_save(cuerpo):
    app = str(cuerpo.get("app") or "").strip()
    valores = cuerpo.get("valores")
    if not app or not isinstance(valores, dict) or not valores:
        return 400, {"error": "Indica la app y los valores a guardar."}

    filas = [f for f in db.get_config_catalog() if f["App"] == app]
    if not filas:
        return 400, {"error": f"La app '{app}' no tiene claves catalogadas."}

    # La API acepta la clave o el Id como llave de `valores`.
    por_id = {}
    for fila in filas:
        if fila["Clave"] in valores:
            por_id[str(fila["Id"])] = valores[fila["Clave"]]
        elif str(fila["Id"]) in valores:
            por_id[str(fila["Id"])] = valores[str(fila["Id"])]
    guardar, claves = apps_view.valores_del_bloque(filas, por_id)
    if not guardar:
        return 200, {"ok": True, "mensaje": "Sin cambios: no hay valores "
                                            "que guardar."}

    ok, fallo = db.set_config_values(guardar)
    if not ok:
        return 400, {"error": f"No se pudieron guardar: {fallo}"}
    return 200, {"ok": True,
                 "mensaje": f"{len(guardar)} valor(es) de {app} guardados.",
                 "claves": claves}


def _apps_edit(cuerpo):
    fila = _fila_por_id(cuerpo.get("id"))
    if not fila:
        return 400, {"error": "Esa clave ya no está en el catálogo."}

    titulo = str(cuerpo.get("titulo", fila["Titulo"]) or "").strip()
    desc = str(cuerpo.get("descripcion", fila["Descripcion"]) or "").strip()
    tipo = str(cuerpo.get("tipo", fila["Tipo"]) or fila["Tipo"]).strip()
    unidad = str(cuerpo.get("unidad", fila["Unidad"]) or "").strip()
    orden = cuerpo.get("orden", fila["Orden"])

    ok, fallo = db.update_catalog_item(fila["Id"], titulo, desc, tipo,
                                       unidad, orden)
    if not ok:
        return 400, {"error": f"No se pudo guardar: {fallo}"}

    if "valor" in cuerpo:
        incluir, valor = apps_view.valor_editable(tipo, cuerpo.get("valor"))
        if incluir:
            okv, errv = db.set_config_values({fila["Clave"]: valor})
            if not okv:
                return 400, {"error": f"Metadatos guardados; el valor no: "
                                      f"{errv}"}
    return 200, {"ok": True,
                 "mensaje": f"Clave '{fila['Clave']}' guardada."}


def _apps_del(cuerpo):
    fila = _fila_por_id(cuerpo.get("id"))
    if not fila:
        return 400, {"error": "Esa clave ya no está en el catálogo."}
    ok, fallo = db.delete_catalog_item(fila["Id"])
    if not ok:
        return 400, {"error": f"No se pudo borrar: {fallo}"}
    return 200, {"ok": True,
                 "mensaje": f"Clave '{fila['Clave']}' fuera del catálogo. "
                            "El valor en HUB_Config se conserva."}


def _apps_add(cuerpo):
    app = str(cuerpo.get("app") or "").strip()
    clave = str(cuerpo.get("clave") or "").strip()
    titulo = str(cuerpo.get("titulo") or "").strip()
    desc = str(cuerpo.get("descripcion") or "").strip()
    tipo = str(cuerpo.get("tipo") or "").strip()
    unidad = str(cuerpo.get("unidad") or "").strip()
    orden = cuerpo.get("orden", 0)
    if not tipo:
        return 400, {"error": "Elige el tipo de la clave."}

    ok, fallo = db.add_catalog_item(app, clave, titulo, desc, tipo, unidad,
                                    orden)
    if not ok:
        return 400, {"error": f"No se pudo agregar: {fallo}"}

    valor = cuerpo.get("valor")
    if valor not in (None, ""):
        okv, errv = db.set_config_values({clave: valor})
        if not okv:
            return 400, {"error": f"Catálogo creado, pero no se pudo escribir "
                                  f"el valor: {errv}"}
    return 200, {"ok": True,
                 "mensaje": f"Clave '{clave}' agregada a {app}."}


def _apps_clasificar(cuerpo):
    clave = str(cuerpo.get("clave") or "").strip()
    app = str(cuerpo.get("app") or "").strip()
    titulo = str(cuerpo.get("titulo") or "").strip() or clave
    tipo = str(cuerpo.get("tipo") or "text").strip()
    if not clave:
        return 400, {"error": "No se reconoció la clave."}
    if not app:
        return 400, {"error": "Indica la app a la que pertenece la clave."}

    ok, fallo = db.add_catalog_item(app, clave, titulo, "", tipo, "", 0)
    if not ok:
        return 400, {"error": f"No se pudo clasificar: {fallo}"}
    return 200, {"ok": True,
                 "mensaje": f"'{clave}' ahora vive en {app}."}


_ACCIONES_APPS = {
    "save": _apps_save,
    "edit": _apps_edit,
    "del": _apps_del,
    "add": _apps_add,
    "clasificar": _apps_clasificar,
}


def _apps_post(handler, params, cuerpo):
    user, error = _sesion_apps(handler)
    if error:
        return error

    accion = str(cuerpo.get("accion") or "").strip().lower()
    funcion = _ACCIONES_APPS.get(accion)
    if not funcion:
        return 400, {"error": "Acción no válida. Usa save, edit, del, add o "
                              "clasificar."}

    if not db.config_catalog_exists():
        return 400, {"error": "Falta la migración 0036: la tabla "
                              "HUB_ConfigCatalogo no existe."}

    codigo, respuesta = funcion(cuerpo)
    if codigo == 200:
        # La bitácora registra claves, nunca valores.
        _log(user, f"Apps (API): {accion} · {respuesta.get('mensaje', '')}")
    return codigo, respuesta


# ── Avisos ───────────────────────────────────────────────────────────────────
def _avisos_get(handler, params, cuerpo):
    user, error = _sesion(handler)
    if error:
        return error

    status = workers.build_status()
    avisos = workers_view.avisos_datos(status, workers.shell_check(),
                                       workers.versiones_check())
    totales = status.get("totals") or {}
    return 200, {
        "avisos": [{"titulo": a.get("titulo", ""),
                    "detalle": a.get("detalle", ""),
                    "ok": bool(a.get("ok")),
                    "nivel": a.get("nivel", "ok" if a.get("ok") else "bad")}
                   for a in avisos],
        "grupos": _grupos(status).get("grupos", []),
        "contexto": {
            "actualizado": datetime.datetime.now().isoformat(timespec="seconds"),
            "zona": status.get("timezone") or "",
            "supervisor_error": status.get("supervisor_error"),
            "totales": totales,
        },
    }


# ── rutas ────────────────────────────────────────────────────────────────────
registrar("GET", r"/api/panel/config", _config_get)
registrar("POST", r"/api/panel/config", _config_post)
registrar("GET", r"/api/panel/apps", _apps_get)
registrar("POST", r"/api/panel/apps", _apps_post)
registrar("GET", r"/api/panel/avisos", _avisos_get)
