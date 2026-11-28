"""
panel/api_ops.py — API JSON de Asistencia, Correo y Triaje
==========================================================
Complemento de `panel/api.py` para las tres pantallas de operación. Se registra
solo: `api._cargar_extensiones()` importa cualquier `panel/api_*.py`, y aquí se
llama a `api.registrar()` al final.

Rutas:

  GET  /api/panel/asistencia?dia=YYYY-MM-DD  → kpis, filas y el día
  POST /api/panel/asistencia                 → {accion:"calcular", dia}
  GET  /api/panel/correo                     → cuentas, config SMTP y buzones
  POST /api/panel/correo                     → {accion:"guardar"|"probar"}
  GET  /api/panel/triaje                     → pendientes y resumen
  POST /api/panel/triaje                     → aprobar/rechazar/bloquear...

Reglas (las mismas de `api.py`):

  * Sesión por cookie `ecsa_token` + permiso de panel, y el permiso propio de
    cada pantalla: `AccesoDeteccionRed` para Asistencia, `AccesoUsuarios` para
    Correo y Triaje (el mismo que exige la vista SSR).
  * Nada de secretos en el JSON. La contraseña SMTP viaja como
    `{valor:"", tiene_valor:bool}` (ver `panel_ui/API.md`).
  * Los datos son los que ya consultan las vistas: no se inventa nada. Las
    etiquetas de estado salen de `panel.views.asistencia` para que la API y el
    SSR digan exactamente lo mismo.
  * Errores: `{"error": "texto para la persona"}`, con su código HTTP.
"""
import logging
import urllib.parse

from . import auth, config, db, db_correo, probes
from .api import registrar
from .views import asistencia as asis_view

log = logging.getLogger("panel.api.ops")

ASIS_PERM = "AccesoDeteccionRed"
CORREO_PERM = "AccesoUsuarios"
TRIAJE_PERM = "AccesoUsuarios"

# `.badge--*` de la vista → tono del componente Badge del SPA.
TONOS = {
    "badge--ok": "ok",
    "badge--warn": "warn",
    "badge--bad": "bad",
    "badge--info": "info",
    "": "",
}


# ── comunes ──────────────────────────────────────────────────────────────────
def _guardia(handler, permiso, motivo):
    """(user, None) con sesión y permiso; (None, (codigo, error)) si no."""
    user = auth.current_user(handler)[0]
    if not user:
        return None, (401, {"error": "Sin sesión. Inicia sesión de nuevo."})
    if not auth.has_perm(user, config.PANEL_PERMISSION):
        return None, (403, {"error": "Tu usuario no tiene el permiso "
                                     "AccesoConfiguracion para usar el panel."})
    if not auth.has_perm(user, permiso):
        return None, (403, {"error": motivo})
    return user, None


def _query(handler):
    """La query de la URL como dict de listas (parse_qs)."""
    return urllib.parse.parse_qs(
        urllib.parse.urlsplit(getattr(handler, "path", "") or "").query)


def _entero(valor, defecto=0):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


def _booleano(valor, defecto=False):
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return defecto
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def _fecha_iso(valor):
    """datetime/date → ISO, sin reventar el JSON; cadena tal cual."""
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        try:
            return valor.isoformat(timespec="seconds")
        except TypeError:
            return valor.isoformat()
    return str(valor)


# ── Asistencia ───────────────────────────────────────────────────────────────
def _kpis(asistencias):
    """El mismo resumen de la vista: presentes, entradas, salidas, sin registro."""
    total = len(asistencias)
    entradas = sum(1 for a in asistencias if asis_view._hay_entrada(a))
    salidas = sum(1 for a in asistencias if asis_view._hay_salida(a))
    sin_registro = sum(1 for a in asistencias
                       if asis_view._estado_de(a) == "SIN_DATOS")
    presentes = sum(
        1 for a in asistencias
        if asis_view._hay_entrada(a)
        and asis_view._estado_de(a) not in ("AUSENTE", "NO_APLICA", "SIN_DATOS"))
    return [
        {"label": "Presentes", "value": presentes,
         "hint": f"de {total} personas", "tone": "ok" if presentes else "",
         "icon": "users"},
        {"label": "Entradas", "value": entradas,
         "hint": "con entrada registrada", "tone": "", "icon": "login"},
        {"label": "Salidas", "value": salidas,
         "hint": "con salida registrada", "tone": "", "icon": "logout"},
        {"label": "Sin registro", "value": sin_registro,
         "hint": "el escáner no cubrió", "tone": "warn" if sin_registro else "",
         "icon": "alert"},
    ]


def _fila_json(a):
    """Una persona: hora de entrada/salida, estado y el porqué en una línea."""
    estado = asis_view._estado_de(a)
    etiqueta = asis_view.ESTADOS.get(estado, ("", estado))[1]
    tono = TONOS.get(asis_view.BADGE_ESTADO.get(estado, ""), "")

    detalle = [
        a.get("TurnoNombre") or "—",
        "Entrada esp. " + asis_view._fmt_hora(a.get("HoraEntradaEsperada")),
        "Salida esp. " + asis_view._fmt_hora(a.get("HoraSalidaEsperada")),
    ]
    tarde = a.get("MinutosTarde")
    antes = a.get("MinutosAntes")
    if tarde:
        detalle.append(f"{tarde} min tarde")
    if antes:
        detalle.append(f"{antes} min antes")

    return {
        "persona": a.get("UsuarioNombre") or "",
        "entrada": asis_view._fmt_ventana(a.get("EntradaPiso"),
                                          a.get("EntradaTecho"),
                                          a.get("HoraEntradaReal")),
        "salida": asis_view._fmt_ventana(a.get("SalidaPiso"),
                                         a.get("SalidaTecho"),
                                         a.get("HoraSalidaReal")),
        "estado": etiqueta,
        "tone": tono,
        "detalle": " · ".join(detalle),
    }


def _asistencia_get(handler, params, cuerpo):
    _user, error = _guardia(
        handler, ASIS_PERM,
        "La pestaña Asistencia requiere el permiso AccesoDeteccionRed.")
    if error:
        return error

    q = _query(handler)
    dia = asis_view._fecha_de_query(
        (q.get("dia") or q.get("fecha") or [""])[0])
    uid = _entero((q.get("usuario") or [""])[0], None)

    asistencias = db.get_asistencia_fecha(dia, uid)
    return 200, {
        "dia": dia.isoformat(),
        "kpis": _kpis(asistencias),
        "filas": [_fila_json(a) for a in asistencias],
    }


def _asistencia_post(handler, params, cuerpo):
    user, error = _guardia(
        handler, ASIS_PERM,
        "La pestaña Asistencia requiere el permiso AccesoDeteccionRed.")
    if error:
        return error

    accion = str(cuerpo.get("accion") or "calcular").strip().lower()
    if accion != "calcular":
        return 400, {"error": "Acción no válida."}

    dia = asis_view._fecha_de_query(
        str(cuerpo.get("dia") or cuerpo.get("fecha") or ""))
    resumen = db.calcular_y_guardar_asistencias_fecha(dia)
    por_estado = resumen.get("por_estado") or {}

    db.log_activity(user.get("email"), "Asistencia",
                    f"cálculo de asistencia del {dia.isoformat()}")
    return 200, {
        "ok": True,
        "dia": dia.isoformat(),
        "guardados": resumen.get("guardados", 0),
        "por_estado": por_estado,
        "sin_datos": por_estado.get("SIN_DATOS", 0),
    }


# ── Correo ───────────────────────────────────────────────────────────────────
def _config_json(cfg):
    """Config SMTP sin la contraseña: solo si hay una guardada."""
    return {
        "smtp_server": cfg.get("smtp_server") or "",
        "port": _entero(cfg.get("port"), 465),
        "username": cfg.get("username") or "",
        "use_ssl": bool(cfg.get("use_ssl")),
        "use_tls": bool(cfg.get("use_tls")),
        "require_auth": bool(cfg.get("require_auth")),
        "password": {"valor": "", "tiene_valor": bool(cfg.get("password"))},
    }


def _cuentas_json(cfg):
    """Tarjeta de estado del correo saliente (SMTP del ecosistema)."""
    servidor = cfg.get("smtp_server") or ""
    usuario = cfg.get("username") or ""
    detalle = f"{servidor}:{cfg.get('port')}" if servidor else "Sin servidor SMTP"
    if servidor and usuario:
        detalle += f" · {usuario}"
    return [{
        "nombre": "Correo SMTP",
        "estado": "Configurado" if servidor else "Falta el servidor",
        "tone": "ok" if servidor else "warn",
        "detalle": detalle,
    }]


def _buzon_json(c):
    """Un buzón de Mailbox, con su estado y el error del worker si lo hay."""
    estado = c.get("Estado") or "PENDIENTE"
    return {
        "id": c.get("Id"),
        "nombre": c.get("Alias") or c.get("Email") or "",
        "email": c.get("Email") or "",
        "estado": estado,
        "usuarios": _entero(c.get("CuantosUsan"), 0),
        "sin_leer": _entero(c.get("NoLeidos"), 0),
        "error": c.get("UltimoError") or "",
        "ultimo_sync": _fecha_iso(c.get("UltimoSync")),
        "detalle": (f"IMAP {c.get('ServidorIMAP')}:{c.get('PuertoIMAP')} · "
                    f"{_entero(c.get('CuantosUsan'), 0)} usuario(s) · "
                    f"{_entero(c.get('NoLeidos'), 0)} sin leer"),
    }


def _correo_get(handler, params, cuerpo):
    _user, error = _guardia(
        handler, CORREO_PERM,
        "La pestaña Correo requiere el permiso AccesoUsuarios.")
    if error:
        return error

    cfg = db.get_email_config()
    return 200, {
        "cuentas": _cuentas_json(cfg),
        "config": _config_json(cfg),
        "buzones": [_buzon_json(c) for c in db_correo.listar_cuentas()],
    }


def _correo_post(handler, params, cuerpo):
    user, error = _guardia(
        handler, CORREO_PERM,
        "La pestaña Correo requiere el permiso AccesoUsuarios.")
    if error:
        return error

    accion = str(cuerpo.get("accion") or "").strip().lower()

    if accion == "guardar":
        puerto = _entero(cuerpo.get("port") or cuerpo.get("puerto"), 0)
        if not 1 <= puerto <= 65535:
            return 400, {"error": "El puerto debe estar entre 1 y 65535."}

        cfg = db.get_email_config()
        # En blanco = no cambiar (la contraseña guardada nunca sale al cliente).
        password = (str(cuerpo.get("password") or "").strip()
                    or cfg.get("password", ""))
        ok, err = db.save_email_config(
            str(cuerpo.get("smtp_server") or "").strip(),
            puerto,
            str(cuerpo.get("username") or "").strip(),
            password,
            _booleano(cuerpo.get("use_ssl"), False),
            _booleano(cuerpo.get("use_tls"), False),
            _booleano(cuerpo.get("require_auth"), False))
        if not ok:
            return 500, {"error": "No se pudo guardar la configuración SMTP: "
                                  f"{err or 'error'}"}

        db.log_activity(user.get("email"), "Correo",
                        "Configuración SMTP actualizada")
        return 200, {"ok": True,
                     "mensaje": "Configuración SMTP guardada."}

    if accion == "probar":
        destino = str(cuerpo.get("destino") or "").strip()
        ok, mensaje = probes.probe_smtp(db.get_email_config(), destino)
        db.log_activity(user.get("email"), "Correo",
                        f"Prueba de correo a '{destino}': "
                        f"{'OK' if ok else 'ERROR'}")
        return 200, {"ok": bool(ok), "mensaje": mensaje}

    return 400, {"error": "Acción no válida."}


# ── Triaje ───────────────────────────────────────────────────────────────────
def _triaje_mod():
    """Import perezoso: el worker de correo no se carga si no se usa Triaje."""
    from mailbox_worker import triaje as triaje_mod
    return triaje_mod


def _numero_json(valor, defecto=0):
    """pymssql devuelve Decimal en confianza y resumen; json no lo serializa."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def _pendiente_json(c):
    """Un correo por revisar, con lo necesario para aprobar o bloquear."""
    return {
        "id": int(c.get("Id") or 0),
        "de": c.get("RemitenteEmail") or "",
        "asunto": c.get("Asunto") or "(sin asunto)",
        "fecha": _fecha_iso(c.get("Creado")) or "",
        "estado": (c.get("Estado") or "NUEVA"),
        "categoria": (c.get("Categoria") or "").lower(),
        "confianza": _numero_json(c.get("Confianza")),
        "cuenta": c.get("CuentaEmail") or "",
        "destinatario": c.get("Destinatario") or "",
        "destino": c.get("DestinoSugerido") or "",
        "motivo": c.get("Motivo") or "",
        "borrador": c.get("BorradorTexto") or "",
    }


def _triaje_get(handler, params, cuerpo):
    _user, error = _guardia(
        handler, TRIAJE_PERM,
        "La pestaña Triaje requiere el permiso AccesoUsuarios: envía correo "
        "en nombre de la empresa.")
    if error:
        return error

    triaje_mod = _triaje_mod()
    pendientes = [_pendiente_json(c) for c in triaje_mod.pendientes(100)]
    resumen = {clave: _numero_json(valor)
               for clave, valor in (triaje_mod.resumen() or {}).items()}
    return 200, {"pendientes": pendientes, "resumen": resumen}


def _triaje_post(handler, params, cuerpo):
    user, error = _guardia(
        handler, TRIAJE_PERM,
        "La pestaña Triaje requiere el permiso AccesoUsuarios.")
    if error:
        return error

    accion = str(cuerpo.get("accion") or "").strip().lower()
    idc = _entero(cuerpo.get("id"), 0)
    quien = user.get("email") or ""
    triaje_mod = _triaje_mod()

    requiere_id = ("aprobar", "rechazar", "bloquear_remitente",
                   "bloquear_dominio", "desbloquear")
    if accion in requiere_id and idc <= 0:
        return 400, {"error": "Falta el identificador del correo."}

    try:
        if accion == "aprobar":
            triaje_mod.aprobar(idc, user.get("id") or 1, quien)
            mensaje = ("Respuesta encolada. El worker la envía en su próximo "
                       "ciclo.")
        elif accion == "rechazar":
            triaje_mod.rechazar(idc, quien)
            mensaje = "Descartado."
        elif accion in ("bloquear_remitente", "bloquear_dominio"):
            tipo = "dominio" if accion == "bloquear_dominio" else "email"
            _, tipo_real, valor = triaje_mod.bloquear_desde_clasificacion(
                idc, tipo, quien)
            mensaje = (f"Bloqueado {tipo_real}: {valor} "
                       "(aplica a todas las cuentas).")
        elif accion == "bloquear":
            valor = str(cuerpo.get("valor") or "").strip()
            if not valor:
                return 400, {"error": "Escribe un correo o un dominio."}
            tipo = str(cuerpo.get("tipo") or "").strip() or None
            _, tipo_real, valor_real = triaje_mod.bloquear(
                valor, tipo=tipo, quien=quien)
            mensaje = (f"Bloqueado {tipo_real}: {valor_real} "
                       "(aplica a todas las cuentas).")
        elif accion == "importar":
            lista = str(cuerpo.get("lista") or "")
            n, malas = triaje_mod.importar_bloqueos(lista, quien)
            mensaje = (f"{n} entradas importadas, {malas} ignoradas por no ser "
                       "correo ni dominio.")
        elif accion == "desbloquear":
            triaje_mod.eliminar_bloqueo(idc)
            mensaje = "Bloqueo quitado; el remitente vuelve a entrar."
        else:
            return 400, {"error": "Acción no válida."}
    except Exception as exc:
        log.warning("triaje %s falló: %s", accion, exc)
        return 400, {"error": str(exc) or "No se pudo completar la acción."}

    db.log_activity(quien, "Triaje", f"{accion} #{idc}")
    return 200, {"ok": True, "mensaje": mensaje}


# ── registro ─────────────────────────────────────────────────────────────────
registrar("GET", r"/api/panel/asistencia", _asistencia_get)
registrar("POST", r"/api/panel/asistencia", _asistencia_post)
registrar("GET", r"/api/panel/correo", _correo_get)
registrar("POST", r"/api/panel/correo", _correo_post)
registrar("GET", r"/api/panel/triaje", _triaje_get)
registrar("POST", r"/api/panel/triaje", _triaje_post)
