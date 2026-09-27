import jwt
import datetime
from fastapi import Header, HTTPException
from config import load_jwt_config
from db import get_connection

JWT_CONFIG = load_jwt_config()

def create_token(user_data: dict) -> str:
    payload = {
        "sub": user_data["Id"],
        "nombre": user_data["Nombre"],
        "email": user_data["Email"],
        "permisos": {
            "kilometros": bool(user_data.get("AccesoRegistroKilometros", 0)),
            "reportes": bool(user_data.get("AccesoReportes", 0)),
            "ticket_oxxogas": bool(user_data.get("AccesoRegistroTicketOxxoGas", 0)),
            "vales_qr": bool(user_data.get("AccesoSolicitarVales", 0)),
            "vm": bool(user_data.get("AccesoVM", 0)),
            "ia": True,
            "admin_usuarios": bool(user_data.get("AccesoUsuarios", 0)),
            # Remisiones: acceso duro hardcodeado (control real = IdUsuarioAsignado en BD)
            "remisiones": True,
        },
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=JWT_CONFIG["expires_days"]),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_CONFIG["secret"], algorithm=JWT_CONFIG["algorithm"])

def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_CONFIG["secret"], algorithms=[JWT_CONFIG["algorithm"]])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def authenticate_user(email: str, password: str) -> dict | None:
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute(
            "SELECT Id, Nombre, Email, Password, Nickname, AccesoUsuarios, "
            "AccesoRegistroKilometros, AccesoReportes, "
            "AccesoRegistroTicketOxxoGas, AccesoSolicitarVales, AccesoVM "
            "FROM HUB_Users WHERE LTRIM(RTRIM(Email)) = %s",
            (email.strip().lower(),)
        )
        row = cur.fetchone()
        if row and row.get("Password", "").strip() == password.strip():
            return dict(row)
        return None

def get_user_by_id(user_id: int) -> dict | None:
    """Fetch active user row (DB-shaped) by Id, or None."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT Id, Nombre, Email, Nickname, AccesoUsuarios, "
                "AccesoRegistroKilometros, AccesoReportes, "
                "AccesoRegistroTicketOxxoGas, AccesoSolicitarVales, AccesoVM "
                "FROM HUB_Users WHERE Id = %s AND Activo = 1",
                (user_id,)
            )
            return cur.fetchone()
    except Exception:
        return None

def require_user(authorization: str = Header(None)):
    """Extract user from JWT token. Returns user dict with id, nombre, email."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    try:
        payload = jwt.decode(authorization.split(" ")[1], JWT_CONFIG["secret"], algorithms=[JWT_CONFIG["algorithm"]])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    user_id = payload.get("sub")
    nombre = payload.get("nombre")
    email = payload.get("email")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    permisos = payload.get("permisos", {})
    return {
        "id": user_id,
        "nombre": nombre,
        "email": email,
        "is_admin": permisos.get("admin_usuarios", False)
    }

def refresh_token(old_token: str) -> str | None:
    """Refresh an expired JWT if the payload is still valid (within 90 days of expiry)."""
    try:
        # Decode without expiration check
        payload = jwt.decode(old_token, JWT_CONFIG["secret"], algorithms=[JWT_CONFIG["algorithm"]], options={"verify_exp": False})
        # Check it's not TOO old (max 90 days past expiry)
        exp = payload.get("exp", 0)
        import time
        if time.time() - exp > 90 * 24 * 3600:
            return None
        # Re-create with fresh expiry + fresh permissions
        user_id = payload.get("sub")
        if not user_id:
            return None
        user_data = get_user_by_id(user_id)
        if not user_data:
            return None
        return create_token(user_data)
    except Exception:
        return None
