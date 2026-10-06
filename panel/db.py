"""
panel/db.py — Capa de datos del panel (SQL Server vía pymssql)
================================================================
Reutiliza la MISMA base de datos y las MISMAS tablas que el HUB:

  HUB_Users      → login y permisos
  HUB_Sessions   → tokens de sesión (cookie ecsa_token)
  HUB_Config     → claves de configuración (fase C: pestaña Apps)
  HUB_Telegram*  → notificaciones (fase B)

Las funciones son portes fieles de `eccsa_db.py` (create_session_token /
validate_session_token / authenticate_hub_user) para que un token creado
por el HUB sea válido en el panel y viceversa.
"""
import datetime
import importlib.util
import os
import re

import pymssql

SESSION_DAYS = 30  # vigencia del token, idéntica a eccsa_db.SESSION_DAYS


# ─── Conexión ────────────────────────────────────────────────────────────────
def _resolve_config():
    """
    Credenciales: variables de entorno > secretos_local.py > defaults.
    (Misma precedencia que config_db.py del HUB; el entrypoint del contenedor
    genera secretos_local.py con exactamente los mismos valores que las env vars.)
    """
    env = {k: os.environ.get(k) for k in
           ("HUB_DB_SERVER", "HUB_DB_USER", "HUB_DB_PASSWORD", "HUB_DB_DATABASE")}
    if env["HUB_DB_SERVER"]:
        return {
            "server": env["HUB_DB_SERVER"],
            "user": env["HUB_DB_USER"] or "",
            "password": env["HUB_DB_PASSWORD"] or "",
            "database": env["HUB_DB_DATABASE"] or "ECCSA_Admon",
        }
    for path in (os.path.join(os.getcwd(), "secretos_local.py"),
                 "/app/secretos_local.py"):
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("secretos_local", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cfg = getattr(mod, "DB_CONFIG_LOCAL", None)
            if cfg:
                return dict(cfg)
    return {"server": "10.188.141.15", "user": "sa",
            "password": "", "database": "ECCSA_Admon"}


def get_connection():
    """Conexión a SQL Server (pymssql). El caller cierra con `with`/finally."""
    cfg = _resolve_config()
    return pymssql.connect(
        server=cfg["server"], user=cfg["user"], password=cfg["password"],
        database=cfg["database"], login_timeout=8, timeout=30,
        charset="UTF-8", as_dict=False,
    )


def _rows(query, params=()):
    """Ejecuta un SELECT y devuelve la lista de filas como dicts (o [])."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(query, params)
            return list(cur.fetchall() or [])
    finally:
        conn.close()


def _execute(query, params=()):
    """Ejecuta INSERT/UPDATE/DELETE con commit. Devuelve rows afectados."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = int(cur.rowcount or 0)   # leer antes de cerrar el cursor
        conn.commit()
        return rows
    finally:
        conn.close()


# ─── Usuarios y permisos ─────────────────────────────────────────────────────
# Columnas de permisos que el panel necesita (lectura granular por pestaña).
# AccesoDeteccionRed es la de la pestaña Asistencia: es la misma columna que
# usa el HUB (comparten HUB_Users), y sin ella `auth.has_perm` no la encontraría
# porque aquí solo se leen las columnas de esta lista.
_PERM_COLS = (
    "AccesoConfiguracion, AccesoAppConfig, AccesoTelegram, AccesoConfigurarCorreo, "
    "AccesoConfigAI, AccesoUsuarios, AccesoVM, AccesoEdicionBD, AccesoDeteccionRed"
)


def authenticate(email, password):
    """
    Valida correo/contraseña contra HUB_Users.
    Las contraseñas en esta base están en texto plano (mismo comportamiento
    que eccsa_db.authenticate_hub_user): se compara literalmente.
    Devuelve el dict del usuario o None.
    """
    rows = _rows(
        f"SELECT Id, Email, Nombre, Nickname, Activo, Password, {_PERM_COLS} "
        "FROM HUB_Users WHERE LTRIM(RTRIM(Email)) = %s",
        (str(email or "").strip().lower(),))
    if not rows:
        return None
    u = rows[0]
    if not u.get("Activo"):
        return None
    if (u.get("Password") or "") != (password or ""):
        return None
    return _public_user(u)


def _public_user(row):
    """Normaliza una fila de HUB_Users a dict con los permisos como bool."""
    return {
        "id": row.get("Id"),
        "email": (row.get("Email") or "").strip(),
        "nombre": row.get("Nombre") or "",
        "nickname": (row.get("Nickname") or "").strip(),
        "perms": {c: bool(row.get(c)) for c in _PERM_COLS.split(", ")},
    }


def get_user_by_id(user_id):
    """Usuario activo por Id (mismo dict que authenticate/validate_session_token)."""
    if not user_id:
        return None
    try:
        rows = _rows(
            f"SELECT Id, Email, Nombre, Nickname, {_PERM_COLS} "
            "FROM HUB_Users WHERE Id = %s AND Activo = 1", (int(user_id),))
        return _public_user(rows[0]) if rows else None
    except Exception:
        return None


# ─── Passkeys (solo VERIFICACIÓN: el registro lo hace HUB/Field) ─────────────
def get_passkey_by_credential(cred_id):
    """Fila de HUB_Passkeys por CredentialId (string base64url) o None."""
    if not cred_id or len(str(cred_id)) > 500:
        return None
    try:
        rows = _rows(
            "SELECT Id, IdUsuario, CredentialId, PublicKey, SignCount, RpId "
            "FROM HUB_Passkeys WHERE CredentialId = %s", (str(cred_id),))
        return rows[0] if rows else None
    except Exception:
        return None


def update_passkey_sign_count(passkey_id, sign_count):
    """Actualiza el contador de firma y el último uso de la passkey."""
    try:
        _execute(
            "UPDATE HUB_Passkeys SET SignCount = %s, UltimoUso = GETDATE() "
            "WHERE Id = %s", (int(sign_count or 0), int(passkey_id)))
        return True
    except Exception:
        return False


def validate_session_token(token):
    """
    Devuelve el usuario activo dueño del token, o None si no existe/expiró.
    Mismo mecanismo que eccsa_db.validate_session_token (HUB_Sessions).
    """
    if not token or len(token) > 100:
        return None
    try:
        rows = _rows(
            "SELECT s.UserEmail FROM HUB_Sessions s "
            "WHERE s.Token = %s AND s.ExpiresAt > GETDATE()", (token,))
        if not rows:
            return None
        email = rows[0]["UserEmail"]
        users = _rows(
            f"SELECT Id, Email, Nombre, Nickname, Activo, {_PERM_COLS} "
            "FROM HUB_Users WHERE Email = %s AND Activo = 1", (email,))
        return _public_user(users[0]) if users else None
    except Exception:
        return None


def create_session_token(email):
    """Crea un token de sesión nuevo (lo mismo que hace el HUB al loguearse)."""
    token = _token_urlsafe()
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Limpia los tokens vencidos y los previos del mismo usuario
            cur.execute(
                "DELETE FROM HUB_Sessions WHERE UserEmail = %s OR ExpiresAt < GETDATE()",
                (email,))
            cur.execute(
                "INSERT INTO HUB_Sessions (Token, UserEmail, CreatedAt, ExpiresAt) "
                "VALUES (%s, %s, GETDATE(), %s)", (token, email, expires))
        conn.commit()
        return token
    finally:
        conn.close()


def delete_session_token(token):
    """Cierra la sesión (logout)."""
    if not token:
        return 0
    try:
        return _execute("DELETE FROM HUB_Sessions WHERE Token = %s", (token,))
    except Exception:
        return 0


def _token_urlsafe():
    import secrets
    return secrets.token_urlsafe(32)


def db_ping():
    """Prueba rápida de conectividad (usado por el healthcheck del panel)."""
    try:
        rows = _rows("SELECT GETDATE() AS ahora")
        return {"ok": bool(rows), "now": str(rows[0].get("ahora")) if rows else None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def log_activity(usuario, modulo, accion):
    """
    Bitácora compartida con el HUB (HUB_ActivityLog): queda registrada en
    "Bitácora de Actividades Recientes" del menú del HUB.
    Best-effort: si falla no debe tumbar la acción que originó el registro.
    """
    try:
        _execute("INSERT INTO HUB_ActivityLog (Usuario, Modulo, Accion) "
                 "VALUES (%s, %s, %s)",
                 (str(usuario or "Sistema").strip()[:100],
                  str(modulo or "Panel").strip()[:100],
                  str(accion or "").strip()[:500]))
        return True
    except Exception:
        return False


# ─── Configuración (HUB_Config) ──────────────────────────────────────────────
# Misma tabla que usa el HUB para parámetros compartidos (tipo_cambio_usd,
# govale_user, telegram_bot_token, net_*...). Los workers la leen en cada
# ciclo, por lo que un cambio aplicado desde el panel surte efecto sin
# reiniciar; los valores NUNCA se registran en la bitácora.
MAX_CONFIG_VALUE = 500      # HUB_Config.Valor es NVARCHAR(500)


def get_config_values(claves):
    """{clave: valor} de HUB_Config para la lista de claves pedida."""
    claves = [c for c in dict.fromkeys(claves) if c]
    if not claves:
        return {}
    ph = ", ".join(["%s"] * len(claves))
    try:
        rows = _rows(f"SELECT Clave, Valor FROM HUB_Config WHERE Clave IN ({ph})",
                     tuple(claves))
        return {r["Clave"]: (r["Valor"] or "") for r in rows}
    except Exception:
        return {}


def set_config_values(valores):
    """
    Upsert en HUB_Config (patrón idéntico a save_network_config del HUB).
    `valores` = {clave: valor}. Devuelve (ok, mensaje_de_error).
    """
    if not valores:
        return True, ""
    for clave, valor in valores.items():
        if not clave or len(str(clave)) > 50:
            return False, f"clave inválida: {clave!r}"
        if valor is None:
            valor = ""
        if len(str(valor)) > MAX_CONFIG_VALUE:
            return False, (f"'{clave}': valor demasiado largo "
                           f"({len(str(valor))} > {MAX_CONFIG_VALUE} caracteres)")
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for clave, valor in valores.items():
                    cur.execute("""
                        MERGE HUB_Config AS target
                        USING (SELECT %s AS Clave) AS source
                        ON target.Clave = source.Clave
                        WHEN MATCHED THEN
                            UPDATE SET Valor = %s, Actualizado = GETDATE()
                        WHEN NOT MATCHED THEN
                            INSERT (Clave, Valor) VALUES (%s, %s);
                    """, (clave, str(valor), clave, str(valor)))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


# ─── Notificaciones (Fase C) ─────────────────────────────────────────────────
# Telegram → HUB_Telegram* · Push → HUB_PushConfig/HUB_PushSubscriptions ·
# Correo → HUB_EmailConfig · IA → HUB_AIConfig. Mismas tablas y mismos
# criterios de actualización que eccsa_db.py del HUB.

# ── Telegram ─────────────────────────────────────────────────────────────────
def get_telegram_eventos():
    """Eventos de alerta con su plantilla (mismo SELECT que el HUB)."""
    try:
        return _rows("SELECT IdEvento, Nombre, PlantillaMensaje, AdjuntarArchivo, "
                     "Activo FROM HUB_TelegramEventos ORDER BY Nombre ASC")
    except Exception:
        return []


def update_telegram_evento(id_evento, plantilla, adjuntar_archivo, activo):
    """Actualiza plantilla/adjunto/estado de un evento. Devuelve (ok, err)."""
    try:
        n = _execute(
            "UPDATE HUB_TelegramEventos SET PlantillaMensaje = %s, "
            "AdjuntarArchivo = %s, Activo = %s WHERE IdEvento = %s",
            (str(plantilla or "").strip()[:1000], int(bool(adjuntar_archivo)),
             int(bool(activo)), str(id_evento or "").strip()))
        if n == 0:
            return False, f"evento '{id_evento}' no existe"
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── OpenWA / WhatsApp ───────────────────────────────────────────────────────
# Espejo de lo de arriba para los avisos por WhatsApp. La API key vive en
# HUB_Config igual que el token del bot, pero NUNCA se devuelve a la pantalla:
# las vistas preguntan solo si hay algo guardado y lo enmascaran con
# openwa_client.enmascarar.

def get_openwa_config():
    """{api_key, base_url, session_id} de HUB_Config."""
    valores = get_config_values(["openwa_api_key", "openwa_base_url",
                                 "openwa_session_id"]) or {}
    return {
        "api_key": (valores.get("openwa_api_key") or "").strip(),
        "base_url": (valores.get("openwa_base_url") or "").strip(),
        "session_id": (valores.get("openwa_session_id") or "").strip(),
    }


def get_openwa_eventos():
    """Avisos de WhatsApp con su plantilla, sus telefonos y su estado."""
    try:
        return _rows("SELECT IdEvento, Nombre, Descripcion, PlantillaMensaje, "
                     "AdjuntarArchivo, Telefonos, Activo FROM HUB_WhatsappEventos "
                     "ORDER BY Nombre ASC")
    except Exception:
        return []


def update_openwa_evento(id_evento, plantilla, telefonos, adjuntar_archivo, activo):
    """
    Guarda un aviso. `telefonos` es el texto con los numeros separados por comas;
    se guarda tal cual (sin normalizar) para que quien administra vea lo que
    escribio y pueda corregirlo. La normalizacion a `<numero>@c.us` se hace al
    encolar, en openwa_client.
    """
    try:
        n = _execute(
            "UPDATE HUB_WhatsappEventos SET PlantillaMensaje = %s, Telefonos = %s, "
            "AdjuntarArchivo = %s, Activo = %s WHERE IdEvento = %s",
            (str(plantilla or "").strip()[:2000], str(telefonos or "").strip()[:600],
             int(bool(adjuntar_archivo)), int(bool(activo)),
             str(id_evento or "").strip()))
        if n == 0:
            return False, f"evento '{id_evento}' no existe"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_openwa_historial(limite=100, estado=None):
    """Ultimos avisos entregados o fallidos, para la sub-pestana Historial."""
    try:
        if estado:
            return _rows("SELECT TOP (%s) Id, IdEvento, ChatId, Estado, Intentos, "
                         "Error, Creado, Enviado FROM HUB_WhatsappQueue "
                         "WHERE Estado = %s ORDER BY Creado DESC",
                         (int(limite), estado))
        return _rows("SELECT TOP (%s) Id, IdEvento, ChatId, Estado, Intentos, "
                     "Error, Creado, Enviado FROM HUB_WhatsappQueue "
                     "ORDER BY Creado DESC", (int(limite),))
    except Exception:
        return []


def get_openwa_destinatarios_resueltos(id_evento):
    """
    Que chatIds salen de un evento, y que entradas del texto NO son validas.

    Devuelve (chats, rechazados) para poder mostrarlos: si alguien pego
    "8123211516, hector" hay que decirle que la segunda entrada no es un numero,
    en vez de mandar medio aviso y dejarle pensar que funciono.
    """
    import importlib
    try:
        # `openwa_client` vive en /app junto a los workers; se importa por
        # nombre (como _hub() con eccsa_db) porque el panel corre desde otro
        # directorio de trabajo.
        normalizar = importlib.import_module("openwa_client").normalizar_chat_ids
    except Exception:
        return [], []
    for ev in get_openwa_eventos():
        if ev.get("IdEvento") == id_evento:
            return normalizar(ev.get("Telefonos") or "")
    return [], []


def get_telegram_destinatarios(id_evento):
    """Ids de usuario que reciben un evento."""
    try:
        return [r["IdUsuario"] for r in _rows(
            "SELECT IdUsuario FROM HUB_TelegramDestinatarios WHERE IdEvento = %s",
            (str(id_evento or "").strip(),))]
    except Exception:
        return []


def set_telegram_destinatarios(id_evento, lista_ids):
    """Reemplaza la lista completa de destinatarios de un evento."""
    id_evento = str(id_evento or "").strip()
    ids = [int(i) for i in dict.fromkeys(lista_ids or [])]
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_TelegramDestinatarios "
                            "WHERE IdEvento = %s", (id_evento,))
                for uid in ids:
                    cur.execute("INSERT INTO HUB_TelegramDestinatarios "
                                "(IdEvento, IdUsuario) VALUES (%s, %s)",
                                (id_evento, uid))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


def get_active_users():
    """Usuarios activos (para los select multiple de destinatarios)."""
    try:
        return _rows("SELECT Id, Nombre, Email FROM HUB_Users WHERE Activo = 1 "
                     "ORDER BY Nombre ASC")
    except Exception:
        return []


def telegram_metrics():
    """Contadores en una sola consulta (tarjeta de resumen)."""
    try:
        rows = _rows(
            "SELECT "
            "(SELECT COUNT(*) FROM HUB_TelegramUsuarios WHERE Activo=1) AS vinculados, "
            "(SELECT COUNT(*) FROM HUB_TelegramEventos) AS eventos, "
            "(SELECT COUNT(*) FROM HUB_TelegramDestinatarios) AS destinatarios, "
            "(SELECT COUNT(*) FROM HUB_TelegramQueue WHERE Estado='PENDIENTE') AS pendientes, "
            "(SELECT COUNT(*) FROM HUB_TelegramQueue WHERE Estado='FALLADO') AS fallados, "
            "(SELECT COUNT(*) FROM HUB_TelegramQueue WHERE Estado='ENVIADO') AS enviados")
        return rows[0] if rows else {}
    except Exception:
        return {}


def get_telegram_vinculados():
    """Usuarios vinculados al bot (HUB_TelegramUsuarios + HUB_Users)."""
    try:
        return _rows(
            "SELECT t.IdUsuario, t.ChatId, t.NombreTelegram, t.TelefonoMAC, "
            "t.Activo, t.FechaVinculado, u.Nombre, u.Email "
            "FROM HUB_TelegramUsuarios t JOIN HUB_Users u ON t.IdUsuario = u.Id "
            "ORDER BY u.Nombre ASC")
    except Exception:
        return []


def add_telegram_usuario(id_usuario, chat_id, nombre_telegram, telefono_mac=""):
    """Vincula (o actualiza) un usuario con su chat de Telegram. Devuelve (ok, err)."""
    try:
        _execute(
            "MERGE HUB_TelegramUsuarios AS t "
            "USING (SELECT %s AS IdUsuario) AS s ON t.IdUsuario = s.IdUsuario "
            "WHEN MATCHED THEN UPDATE SET ChatId = %s, NombreTelegram = %s, "
            "TelefonoMAC = %s, Activo = 1, FechaVinculado = GETDATE() "
            "WHEN NOT MATCHED THEN INSERT (IdUsuario, ChatId, NombreTelegram, "
            "TelefonoMAC) VALUES (%s, %s, %s, %s)",
            (int(id_usuario), int(chat_id), nombre_telegram, telefono_mac,
             int(id_usuario), int(chat_id), nombre_telegram, telefono_mac))
        return True, ""
    except Exception as exc:
        return False, str(exc)


def unlink_telegram_usuario(id_usuario):
    """Quita la vinculación y sus destinatarios. Devuelve (ok, err)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HUB_TelegramDestinatarios "
                            "WHERE IdUsuario = %s", (int(id_usuario),))
                cur.execute("DELETE FROM HUB_TelegramUsuarios "
                            "WHERE IdUsuario = %s", (int(id_usuario),))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


def get_telegram_historial(limite=100):
    """Últimos envíos de la cola (HUB_TelegramQueue), más recientes primero."""
    try:
        return _rows(
            "SELECT TOP (%s) Id, IdEvento, ChatId, LEFT(Texto, 120) AS Texto, "
            "Estado, Intentos, Creado FROM HUB_TelegramQueue "
            "ORDER BY Creado DESC", (int(limite),))
    except Exception:
        return []


def limpiar_telegram_historial(dias=30):
    """Borra envíos terminados con más de N días. Devuelve (ok, detalle)."""
    try:
        n = _execute(
            "DELETE FROM HUB_TelegramQueue WHERE Estado IN ('ENVIADO', 'FALLADO') "
            "AND Creado < DATEADD(day, -%s, GETDATE())", (int(dias),))
        return True, str(n)
    except Exception as exc:
        return False, str(exc)


# ── Push (Web Push / VAPID) ──────────────────────────────────────────────────
def get_push_config():
    """Claves VAPID guardadas (nunca se devuelven completas a la UI)."""
    try:
        rows = _rows("SELECT TOP 1 Id, VapidPublicKey, VapidPrivateKey, "
                     "VapidEmail, UpdatedAt FROM HUB_PushConfig ORDER BY Id ASC")
        if not rows:
            return {"public": "", "private": "", "email": "", "updated": None}
        r = rows[0]
        return {"public": (r.get("VapidPublicKey") or "").strip(),
                "private": (r.get("VapidPrivateKey") or "").strip(),
                "email": (r.get("VapidEmail") or "").strip(),
                "updated": r.get("UpdatedAt")}
    except Exception:
        return {"public": "", "private": "", "email": "", "updated": None}


def save_push_config(public, private, email):
    """Upsert de las claves VAPID (Id=1). Devuelve (ok, err)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "IF EXISTS (SELECT 1 FROM HUB_PushConfig WHERE Id = 1) "
                    "UPDATE HUB_PushConfig SET VapidPublicKey = %s, "
                    "VapidPrivateKey = %s, VapidEmail = %s, UpdatedAt = GETDATE() "
                    "WHERE Id = 1 "
                    "ELSE INSERT HUB_PushConfig (Id, VapidPublicKey, VapidPrivateKey, "
                    "VapidEmail, UpdatedAt) VALUES (1, %s, %s, %s, GETDATE())",
                    (str(public or "").strip()[:512], str(private or "").strip()[:512],
                     str(email or "").strip()[:512],
                     str(public or "").strip()[:512], str(private or "").strip()[:512],
                     str(email or "").strip()[:512]))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


def get_push_subscriptions():
    """Suscripciones activas (endpoint/p256dh/auth) para enviar una prueba."""
    try:
        return _rows("SELECT Endpoint, P256dhKey, AuthKey, UserEmail "
                     "FROM HUB_PushSubscriptions")
    except Exception:
        return []


def remove_push_subscription(endpoint):
    """Borra una suscripción caducada (410/404) como hace el HUB."""
    try:
        return _execute("DELETE FROM HUB_PushSubscriptions WHERE Endpoint = %s",
                        (str(endpoint or "")[:2000],))
    except Exception:
        return 0


# ── Avisos push por app (módulo "📣 Avisos") ─────────────────────────────────
# Estas cinco leen HUB_PushSuscripciones / HUB_AvisosCola, que son las tablas
# con columna `App` creadas por la migración 0056. Las de arriba
# (HUB_PushSubscriptions) son la tabla VIEJA, sin columna de app: ahí una
# suscripción de Admon es indistinguible de una de Field y por eso el módulo
# nuevo no la usa.


def get_suscripciones_por_app():
    """{app: número de suscripciones activas}. Sirve para el conteo del panel."""
    try:
        return {r["App"]: int(r["Total"] or 0) for r in _rows(
            "SELECT App, COUNT(*) AS Total FROM HUB_PushSuscripciones "
            "WHERE Activo = 1 GROUP BY App")}
    except Exception:
        return {}


def get_plataformas_suscripciones(app):
    """[(plataforma, total)] para decir '3 iPhone, 1 PC' y no un número pelado."""
    try:
        return [(r["P"], int(r["Total"] or 0)) for r in _rows(
            "SELECT ISNULL(Plataforma, '?') AS P, COUNT(*) AS Total "
            "FROM HUB_PushSuscripciones WHERE Activo = 1 AND App = %s "
            "GROUP BY ISNULL(Plataforma, '?') ORDER BY Total DESC", (app,))]
    except Exception:
        return []


def get_avisos_en_cola(app):
    """Avisos pendientes de esta app. No es historial: es lo que aún no salió."""
    try:
        filas = _rows("SELECT COUNT(*) AS Total FROM HUB_AvisosCola "
                      "WHERE App = %s AND Estado = 'PENDIENTE'", (app,))
        return int(filas[0]["Total"]) if filas else 0
    except Exception:
        return 0


def get_ultimo_resumen_avisos():
    """Fecha del último resumen entregado (HUB_Config). La usa la vista para no
    prometer un resumen que ya se mandó."""
    try:
        filas = _rows("SELECT Valor FROM HUB_Config WHERE Clave = %s",
                      ("avisos_ultimo_resumen",))
        return (filas[0]["Valor"] or "") if filas else ""
    except Exception:
        return ""


def get_usuarios_con_permiso_sin_suscribir(permiso, app):
    """
    Personas activas con el permiso que NO tienen ningún dispositivo suscrito
    para esta app.

    Es el número que explica por qué un aviso encendido "no le llega a nadie":
    sin él, la pantalla del módulo se vería bien y el usuario no recibiría nada.
    El nombre de la columna viene de notif_dispatch.TIPOS (código, no entrada del
    usuario), pero se revalida igual por si algún día la tabla de tipos se llena
    desde la base.
    """
    import re
    if not re.match(r"^Acceso[A-Za-z]{3,40}$", permiso or ""):
        return 0
    try:
        filas = _rows(
            f"SELECT COUNT(*) AS Total FROM HUB_Users u "
            f"WHERE u.Activo = 1 AND u.{permiso} = 1 AND NOT EXISTS ("
            f"  SELECT 1 FROM HUB_PushSuscripciones s"
            f"  WHERE s.IdUsuario = u.Id AND s.App = %s AND s.Activo = 1)",
            (app,))
        return int(filas[0]["Total"]) if filas else 0
    except Exception:
        return 0


# ── Correo SMTP ──────────────────────────────────────────────────────────────
def get_email_config():
    """Config SMTP (misma lectura que eccsa_db.get_email_config)."""
    try:
        rows = _rows("SELECT TOP 1 SmtpServer, Port, Username, Password, UseSSL, "
                     "UseTLS, RequireAuth FROM HUB_EmailConfig ORDER BY Id ASC")
        if not rows:
            return {"smtp_server": "", "port": 465, "username": "", "password": "",
                    "use_ssl": True, "use_tls": False, "require_auth": True}
        r = rows[0]
        return {"smtp_server": (r.get("SmtpServer") or "").strip(),
                "port": int(r.get("Port") or 465),
                "username": (r.get("Username") or "").strip(),
                "password": (r.get("Password") or "").strip(),
                "use_ssl": bool(r.get("UseSSL")),
                "use_tls": bool(r.get("UseTLS")),
                "require_auth": bool(r.get("RequireAuth"))}
    except Exception:
        return {"smtp_server": "", "port": 465, "username": "", "password": "",
                "use_ssl": True, "use_tls": False, "require_auth": True}


def save_email_config(smtp_server, port, username, password, use_ssl, use_tls,
                      require_auth):
    """UPDATE con INSERT si no existe la fila (Id=1). Devuelve (ok, err)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE HUB_EmailConfig SET SmtpServer = %s, Port = %s, "
                    "Username = %s, Password = %s, UseSSL = %s, UseTLS = %s, "
                    "RequireAuth = %s",
                    (str(smtp_server or "").strip()[:255], int(port),
                     str(username or "").strip()[:255], str(password or "").strip()[:255],
                     int(bool(use_ssl)), int(bool(use_tls)), int(bool(require_auth))))
                if int(cur.rowcount or 0) == 0:
                    cur.execute(
                        "IF NOT EXISTS (SELECT 1 FROM HUB_EmailConfig) "
                        "INSERT HUB_EmailConfig (SmtpServer, Port, Username, Password, "
                        "UseSSL, UseTLS, RequireAuth) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (str(smtp_server or "").strip()[:255], int(port),
                         str(username or "").strip()[:255], str(password or "").strip()[:255],
                         int(bool(use_ssl)), int(bool(use_tls)), int(bool(require_auth))))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


# ── IA (Gemini) ──────────────────────────────────────────────────────────────
def get_ai_config():
    """Provider/ApiKey/Model (mismos defaults que eccsa_db.get_ai_config)."""
    try:
        rows = _rows("SELECT TOP 1 Provider, ApiKey, Model FROM HUB_AIConfig "
                     "ORDER BY Id ASC")
        if not rows:
            return {"provider": "google_gemini", "api_key": "",
                    "model": "gemini-2.0-flash"}
        r = rows[0]
        return {"provider": (r.get("Provider") or "google_gemini").strip(),
                "api_key": (r.get("ApiKey") or "").strip(),
                "model": (r.get("Model") or "gemini-2.0-flash").strip()}
    except Exception:
        return {"provider": "google_gemini", "api_key": "",
                "model": "gemini-2.0-flash"}


def save_ai_config(provider, api_key, model):
    """Upsert Id=1 (mismo patrón que eccsa_db.update_ai_config)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "IF EXISTS (SELECT 1 FROM HUB_AIConfig WHERE Id = 1) "
                    "UPDATE HUB_AIConfig SET Provider = %s, ApiKey = %s, "
                    "Model = %s, UpdatedAt = GETDATE() WHERE Id = 1 "
                    "ELSE INSERT HUB_AIConfig (Id, Provider, ApiKey, Model, UpdatedAt) "
                    "VALUES (1, %s, %s, %s, GETDATE())",
                    (str(provider or "google_gemini").strip()[:100],
                     str(api_key or "").strip()[:500],
                     str(model or "").strip()[:200],
                     str(provider or "google_gemini").strip()[:100],
                     str(api_key or "").strip()[:500],
                     str(model or "").strip()[:200]))
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


# ─── Catálogo de configuración por app (Fase D) ──────────────────────────────
# HUB_ConfigCatalogo → METADATOS (app, título, tipo, orden) de cada clave.
# HUB_Config          → el VALOR (no se mueve de ahí: es donde lo leen HUB,
#                       Field, admon y los workers).
# Migración: 0036_config_catalogo.sql (repo HUB → migrations/).

TIPOS_APP = ("text", "secret", "number", "bool", "readonly")
_APP_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_CLAVE_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,50}$")


def config_catalog_exists():
    """True si la migración 0036 ya creó HUB_ConfigCatalogo en esta BD."""
    try:
        return bool(_rows(
            "SELECT 1 AS x FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'HUB_ConfigCatalogo'"))
    except Exception:
        return False


def get_config_catalog():
    """Filas del catálogo con su valor vigente (LEFT JOIN contra HUB_Config)."""
    try:
        return _rows("""
            SELECT c.Id, c.App, c.Clave, c.Titulo,
                   ISNULL(c.Descripcion, '') AS Descripcion,
                   c.Tipo, ISNULL(c.Unidad, '') AS Unidad, c.Orden, c.Actualizado,
                   ISNULL(v.Valor, '') AS Valor
            FROM HUB_ConfigCatalogo c
            LEFT JOIN HUB_Config v ON v.Clave = c.Clave
            ORDER BY c.App, c.Orden, c.Clave""")
    except Exception:
        return []


def get_all_config_values():
    """{clave: valor} de TODA HUB_Config (alimenta el grupo 'sin clasificar')."""
    try:
        return {r["Clave"]: (r["Valor"] or "")
                for r in _rows("SELECT Clave, Valor FROM HUB_Config ORDER BY Clave")}
    except Exception:
        return {}


def _validar_item(app, clave, titulo, tipo, descripcion="", unidad="", orden=0):
    """Valida un registro del catálogo. Devuelve '' si está bien, si no el error."""
    if not _APP_RE.match(str(app or "").strip()):
        return ("la app solo puede llevar letras, números, guion bajo o guion "
                "(máx. 40)")
    if not _CLAVE_RE.match(str(clave or "").strip()):
        return "la clave solo puede llevar letras, números, '_', '.' o '-' (máx. 50)"
    if not str(titulo or "").strip():
        return "falta el título"
    if len(str(titulo)) > 120:
        return "el título supera 120 caracteres"
    if len(str(descripcion or "")) > 400:
        return "la descripción supera 400 caracteres"
    if len(str(unidad or "")) > 20:
        return "la unidad supera 20 caracteres"
    if tipo not in TIPOS_APP:
        return f"tipo no válido: {tipo!r}"
    try:
        orden = int(orden)
        if not 0 <= orden <= 9999:
            raise ValueError
    except (TypeError, ValueError):
        return "el orden debe estar entre 0 y 9999"
    return ""


def add_catalog_item(app, clave, titulo, descripcion="", tipo="text",
                     unidad="", orden=0):
    """Alta en el catálogo. Devuelve (ok, mensaje_de_error)."""
    app = str(app or "").strip()
    clave = str(clave or "").strip()
    error = _validar_item(app, clave, titulo, tipo, descripcion, unidad, orden)
    if error:
        return False, error
    # HUB_Config.Clave es PRIMARY KEY global: una clave pertenece a UNA sola app
    try:
        previa = _rows("SELECT App FROM HUB_ConfigCatalogo WHERE Clave = %s", (clave,))
        if previa:
            return False, f"la clave '{clave}' ya está catalogada en {previa[0]['App']}"
        _execute(
            "INSERT INTO HUB_ConfigCatalogo (App, Clave, Titulo, Descripcion, "
            "Tipo, Unidad, Orden, Actualizado) VALUES (%s, %s, %s, %s, %s, %s, %s, GETDATE())",
            (app, clave, str(titulo).strip(), str(descripcion or "").strip(),
             tipo, str(unidad or "").strip(), int(orden)))
        return True, ""
    except Exception as exc:
        return False, str(exc)


def update_catalog_item(item_id, titulo, descripcion="", tipo="text",
                        unidad="", orden=0):
    """Actualiza metadatos de una fila del catálogo (App y Clave no cambian)."""
    error = _validar_item("x", "x", titulo, tipo, descripcion, unidad, orden)
    if error:
        return False, error
    try:
        rows = _execute(
            "UPDATE HUB_ConfigCatalogo SET Titulo = %s, Descripcion = %s, "
            "Tipo = %s, Unidad = %s, Orden = %s, Actualizado = GETDATE() "
            "WHERE Id = %s",
            (str(titulo).strip(), str(descripcion or "").strip(), tipo,
             str(unidad or "").strip(), int(orden), int(item_id)))
        return (rows > 0, "" if rows else "la clave ya no existe en el catálogo")
    except Exception as exc:
        return False, str(exc)


def delete_catalog_item(item_id):
    """Quita la fila del catálogo. NO toca HUB_Config (el valor se conserva)."""
    try:
        rows = _execute("DELETE FROM HUB_ConfigCatalogo WHERE Id = %s",
                        (int(item_id),))
        return (rows > 0, "" if rows else "la clave ya no existe en el catálogo")
    except Exception as exc:
        return False, str(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# ASISTENCIA
# ═══════════════════════════════════════════════════════════════════════════════
# Estas NO están en panel/db.py: la lógica vive en `eccsa_db.py` (la capa de
# datos del ecosistema, la misma que usa el worker del escáner) porque la
# escriben y la leen los dos. Aquí solo se delegan, para que las vistas del
# panel sigan hablando únicamente con `panel.db` — que es lo que permite
# falsearlas en las pruebas.
#
# El import es perezoso a propósito: `eccsa_db` resuelve sus propias credenciales
# y no hace falta para arrancar el panel (login, config, notificaciones).


def _hub():
    """Importa `eccsa_db` bajo demanda. Lanza si no está (imagen mal construida)."""
    import importlib
    return importlib.import_module("eccsa_db")


def get_ultimo_escaneo():
    """
    Ultimo escaneo REAL de la red: cuando fue y cuantos equipos traia.

    Esto es distinto del latido del worker, que solo dice que el proceso sigue
    vivo. Durante cinco dias el worker estuvo "sano" sin que nadie escaneara, y
    el panel mostraba su ultimo ciclo como si todo bien: el estado en sitio /
    fuera no era confiable y no habia forma de verlo desde la pantalla.
    """
    try:
        filas = _rows("SELECT MAX(FechaScan) AS Ultimo, COUNT(DISTINCT MACAddress) AS Equipos "
                      "FROM HUB_NetworkScanResults WHERE FechaScan >= DATEADD(hour, -1, GETDATE())")
        r = filas[0] if filas else {}
        ultimo = r.get("Ultimo")
        equipos = r.get("Equipos") or 0
        if not ultimo:
            # Sin escaneos en la ultima hora: se va mas atras para poder
            # decir "hace cuanto esta parado" en vez de solo "no hay datos".
            filas = _rows("SELECT MAX(FechaScan) AS Ultimo FROM HUB_NetworkScanResults")
            ultimo = filas[0].get("Ultimo") if filas else None
        return {"ultimo": ultimo, "equipos": equipos}
    except Exception:
        return {"ultimo": None, "equipos": 0}


def get_asistencia_fecha(fecha, user_id=None):
    """Asistencias ya calculadas de una fecha (de `eccsa_db`)."""
    return _hub().get_asistencia_fecha(fecha, user_id)


def get_all_usuario_turnos():
    """Asignaciones de turno activas, con el nombre del usuario y del turno."""
    return _hub().get_all_usuario_turnos()


def calcular_y_guardar_asistencias_fecha(fecha=None):
    """
    Recalcula y guarda las asistencias de una fecha. Devuelve el desglose por
    estado (`guardados`, `por_estado`, `sin_turno`, `afirmables`), que es lo
    que la vista pinta: el número solo no dice si los días fueron afirmables o
    días en los que el escáner no escaneó.
    """
    return _hub().calcular_y_guardar_asistencias_fecha(fecha)
