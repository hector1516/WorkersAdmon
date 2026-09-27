from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from auth import authenticate_user, create_token, decode_token, refresh_token, get_user_by_id, require_user
from db import get_connection

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

class ChangePasswordRequest(BaseModel):
    actual: str
    nueva: str

@router.post("/login")
def login(req: LoginRequest):
    user = authenticate_user(req.email, req.password)
    if not user:
        print(f"[auth] login FAIL email={req.email}", flush=True)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = create_token(user)
    import time
    expires_at = int(time.time()) + (90 * 24 * 3600)
    print(f"[auth] login OK id={user['Id']} email={user['Email']}", flush=True)
    return {
        "token": token,
        "expiresAt": expires_at,
        "user": {
            "id": user["Id"],
            "nombre": user["Nombre"],
            "email": user["Email"],
            "nickname": (user.get("Nickname") or "").strip(),
            "permisos": {
                "kilometros": bool(user.get("AccesoRegistroKilometros", 0)),
                "reportes": bool(user.get("AccesoReportes", 0)),
                "ticket_oxxogas": bool(user.get("AccesoRegistroTicketOxxoGas", 0)),
                "vales_qr": bool(user.get("AccesoSolicitarVales", 0)),
                "vm": bool(user.get("AccesoVM", 0)),
                "ia": True,
                "remisiones": True,
            }
        }
    }

@router.post("/refresh")
def refresh(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token required")
    old_token = authorization.split(" ")[1]
    # Try normal decode first (token not expired yet) + fresh user row
    payload = decode_token(old_token)
    if payload:
        user = get_user_by_id(payload.get("sub"))
        if not user:
            print(f"[auth] refresh FAIL user-not-found sub={payload.get('sub')}", flush=True)
            raise HTTPException(status_code=401, detail="User not found")
        print(f"[auth] refresh OK (valid token) sub={payload.get('sub')}", flush=True)
        return {"token": create_token(user)}
    # Try refresh (token expired but within 30-day grace)
    new_token = refresh_token(old_token)
    if not new_token:
        print(f"[auth] refresh FAIL too-old-or-bad", flush=True)
        raise HTTPException(status_code=401, detail="Token too old, re-login required")
    print(f"[auth] refresh OK (expired token rescued)", flush=True)
    return {"token": new_token}

@router.post("/validate")
def validate(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return {"valid": False}
    payload = decode_token(authorization.split(" ")[1])
    if not payload:
        return {"valid": False}
    return {"valid": True, "user": payload}

@router.post("/change-password")
def change_password(req: ChangePasswordRequest, user=Depends(require_user)):
    nueva = (req.nueva or "").strip()
    if len(nueva) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres")
    ok = authenticate_user(user["email"], req.actual or "")
    if not ok:
        raise HTTPException(status_code=401, detail="La contraseña actual no es correcta")
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("UPDATE HUB_Users SET Password = %s WHERE Id = %s", (nueva, user["id"]))
        conn.commit()
    print(f"[auth] change-password OK id={user['id']}", flush=True)
    return {"ok": True}
