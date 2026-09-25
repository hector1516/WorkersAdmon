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
_PERM_COLS = (
    "AccesoConfiguracion, AccesoAppConfig, AccesoTelegram, AccesoConfigurarCorreo, "
    "AccesoConfigAI, AccesoUsuarios, AccesoVM, AccesoEdicionBD"
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
