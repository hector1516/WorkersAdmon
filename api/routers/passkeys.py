import base64
import os
import time
from urllib.parse import urlparse

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from auth import create_token, get_user_by_id, require_user
from config import load_jwt_config
from db import get_connection

router = APIRouter()

# ── Passkeys DUALES (transición a *.ecc-sa.com.mx) ──────────────────────────
# rp_viejo = passkeys creadas antes de la migración: solo funcionan en field.
# rp_nuevo = rp raíz: funciona en CUALQUIER subdominio de ecc-sa.com.mx.
# El registro SIEMPRE usa el rp nuevo. El login acepta ambos (según RpId en BD).
# Limpieza final (manual, con backup): DELETE WHERE RpId = 'field.ecc-sa.com.mx'
RP_ID = "ecc-sa.com.mx"  # rp para REGISTRAR passkeys nuevas
RP_ID_OLD = "field.ecc-sa.com.mx"  # rp de passkeys legacy (solo login)
RP_NAME = "ECCSA"
LEGACY_ORIGIN = "https://field.ecc-sa.com.mx"
CHALLENGE_TTL = 5 * 60  # 5 minutos

# Candidatos a rp_id que el cliente puede pedir en login/options
ALLOWED_RP_IDS = {RP_ID, RP_ID_OLD}


def _is_allowed_origin(origin: str) -> bool:
    """Allowlist: https://ecc-sa.com.mx o cualquier subdominio ( *.ecc-sa.com.mx ).
    Devuelve True también para localhost (desarrollo)."""
    if not origin:
        return False
    p = urlparse(origin)
    if p.hostname in ("localhost", "127.0.0.1"):
        return True
    if p.scheme != "https":
        return False
    host = p.hostname or ""
    return host == "ecc-sa.com.mx" or host.endswith(".ecc-sa.com.mx")


def _origin_for_request(request: Request, rp_id: str) -> str:
    """Origin esperado según la passkey:
    - legacy: siempre https://field.ecc-sa.com.mx (así se crearon)
    - nueva: el Origin real de la petición, validado contra allowlist
    """
    if rp_id == RP_ID_OLD:
        return LEGACY_ORIGIN
    origin = request.headers.get("origin", "")
    if not _is_allowed_origin(origin):
        raise HTTPException(status_code=400, detail="Origin no permitido")
    return origin


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _issue_challenge(purpose: str) -> tuple[str, bytes]:
    """Genera challenge aleatorio y lo envuelve en JWT stateless (multi-worker safe)."""
    cfg = load_jwt_config()
    raw = os.urandom(32)
    now = int(time.time())
    token = pyjwt.encode(
        {"purpose": purpose, "ch": _b64url_encode(raw), "iat": now, "exp": now + CHALLENGE_TTL},
        cfg["secret"],
        algorithm=cfg["algorithm"],
    )
    return token, raw


def _read_challenge(state: str, purpose: str) -> bytes:
    cfg = load_jwt_config()
    try:
        data = pyjwt.decode(state, cfg["secret"], algorithms=[cfg["algorithm"]])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Desafío expirado, intenta de nuevo")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Desafío inválido")
    if data.get("purpose") != purpose or not data.get("ch"):
        raise HTTPException(status_code=400, detail="Desafío inválido")
    return base64url_to_bytes(data["ch"])


def _user_credentials(user_id: int) -> list[dict]:
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute(
            "SELECT Id, CredentialId, PublicKey, SignCount FROM HUB_Passkeys WHERE IdUsuario = %s",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _session_response(user: dict) -> dict:
    token = create_token(user)
    expires_at = int(time.time()) + (90 * 24 * 3600)
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
            },
        },
    }


class RegisterOptionsRequest(BaseModel):
    label: str = ""


class RegisterVerifyRequest(BaseModel):
    credential: dict
    state: str
    label: str = ""


class LoginVerifyRequest(BaseModel):
    credential: dict
    state: str


class LoginOptionsRequest(BaseModel):
    # "ecc-sa.com.mx" (nueva) | "field.ecc-sa.com.mx" (legacy). Default: legacy,
    # porque durante la transición la mayoría solo tiene passkeys viejas.
    rp: str = "field.ecc-sa.com.mx"


@router.post("/register/options")
def register_options(req: RegisterOptionsRequest, user=Depends(require_user), request: Request = None):
    full = get_user_by_id(user["id"])
    if not full:
        raise HTTPException(status_code=401, detail="Usuario no válido")
    # Validar origin ANTES de emitir options (registro siempre en rp nuevo)
    origin = _origin_for_request(request, RP_ID) if request else LEGACY_ORIGIN
    existing = _user_credentials(user["id"])
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["CredentialId"]))
        for c in existing
    ]
    state, challenge = _issue_challenge("wk_reg")
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=str(user["id"]).encode("utf-8"),
        user_name=full["Email"],
        user_display_name=full["Nombre"],
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude or None,
    )
    return {"options": options_to_json(options), "state": state}


@router.post("/register/verify")
def register_verify(req: RegisterVerifyRequest, user=Depends(require_user), request: Request = None):
    challenge = _read_challenge(req.state, "wk_reg")
    origin = _origin_for_request(request, RP_ID) if request else LEGACY_ORIGIN
    try:
        v = verify_registration_response(
            credential=req.credential,
            expected_challenge=challenge,
            expected_rp_id=RP_ID,
            expected_origin=origin,
        )
    except Exception as e:
        print(f"[passkey] register FAIL uid={user['id']}: {e}", flush=True)
        raise HTTPException(status_code=400, detail="No se pudo registrar la passkey")
    cred_id = _b64url_encode(v.credential_id)
    pubkey = _b64url_encode(v.credential_public_key)
    transports = ",".join(req.credential.get("transports", []) or [])
    label = (req.label or "Mi equipo")[:100]
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT Id FROM HUB_Passkeys WHERE CredentialId = %s", (cred_id,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Esta passkey ya está registrada")
        cur.execute(
            "INSERT INTO HUB_Passkeys (IdUsuario, CredentialId, PublicKey, SignCount, Transports, Etiqueta, RpId) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user["id"], cred_id, pubkey, v.sign_count, transports, label, RP_ID),
        )
        # Nickname de Legends asociado al USUARIO: inicializar solo si está vacío
        # (no sobrescribir el apodo existente — ver migración 0034)
        cur.execute(
            "SELECT Nickname FROM HUB_Users WHERE Id = %s", (user["id"],)
        )
        u = cur.fetchone()
        nick = (u.get("Nickname") or "").strip() if u else ""
        if not nick:
            cur.execute(
                "UPDATE HUB_Users SET Nickname = %s WHERE Id = %s",
                (label, user["id"]),
            )
        conn.commit()
    print(f"[passkey] register OK uid={user['id']} label={label} rp={RP_ID}", flush=True)
    return {"ok": True}


@router.post("/login/options")
def login_options(req: LoginOptionsRequest = None):
    # Whitelist de rp_id: el navegador SOLO ofrece passkeys de ese rp.
    # Por eso el frontend reintenta con el otro rp si este no encuentra credenciales.
    rp = (req.rp if req else RP_ID_OLD)
    if rp not in ALLOWED_RP_IDS:
        rp = RP_ID_OLD
    state, challenge = _issue_challenge("wk_login")
    options = generate_authentication_options(rp_id=rp, challenge=challenge)
    return {"options": options_to_json(options), "state": state, "rp": rp}


@router.post("/login/verify")
def login_verify(req: LoginVerifyRequest, request: Request = None):
    challenge = _read_challenge(req.state, "wk_login")
    cred_id = req.credential.get("id", "")
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT * FROM HUB_Passkeys WHERE CredentialId = %s", (cred_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Passkey no reconocida")
    # El userHandle debe corresponder al dueño de la credencial
    try:
        raw_handle = req.credential.get("response", {}).get("userHandle")
        handle_uid = int(base64url_to_bytes(raw_handle).decode("utf-8")) if raw_handle else row["IdUsuario"]
    except Exception:
        handle_uid = row["IdUsuario"]
    if handle_uid != row["IdUsuario"]:
        raise HTTPException(status_code=401, detail="Passkey no válida para este usuario")
    # Validar con el rp/origin con el que SE CREÓ la credencial (dual):
    # filas viejas sin RpId (pre-0035) se tratan como legacy.
    cred_rp = (row.get("RpId") or RP_ID_OLD).strip() or RP_ID_OLD
    if cred_rp not in ALLOWED_RP_IDS:
        cred_rp = RP_ID_OLD
    expected_origin = _origin_for_request(request, cred_rp) if request else LEGACY_ORIGIN
    try:
        v = verify_authentication_response(
            credential=req.credential,
            expected_challenge=challenge,
            expected_rp_id=cred_rp,
            expected_origin=expected_origin,
            credential_public_key=base64.urlsafe_b64decode(row["PublicKey"] + "=="),
            credential_current_sign_count=row["SignCount"],
            require_user_verification=False,
        )
    except Exception as e:
        print(f"[passkey] login FAIL cred={cred_id[:12]} rp={cred_rp}: {e}", flush=True)
        raise HTTPException(status_code=401, detail="No se pudo verificar la passkey")
    conn2 = get_connection()
    with conn2.cursor(as_dict=True) as cur:
        cur.execute(
            "UPDATE HUB_Passkeys SET SignCount = %s, UltimoUso = GETDATE() WHERE Id = %s",
            (v.new_sign_count, row["Id"]),
        )
        conn2.commit()
    user = get_user_by_id(row["IdUsuario"])
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no válido o inactivo")
    print(f"[passkey] login OK uid={user['Id']}", flush=True)
    return _session_response(user)


@router.get("/mine")
def my_passkeys(user=Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute(
            "SELECT Id, Etiqueta, FechaCreacion, UltimoUso, RpId FROM HUB_Passkeys "
            "WHERE IdUsuario = %s ORDER BY FechaCreacion DESC",
            (user["id"],),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        for k in ("FechaCreacion", "UltimoUso"):
            if r.get(k) is not None:
                r[k] = str(r[k])
        # Badge de UI: passkey nueva (raíz) vs legacy (field.)
        rp = (r.get("RpId") or RP_ID_OLD).strip() or RP_ID_OLD
        r["EsNueva"] = rp == RP_ID
    return {"passkeys": rows}


@router.delete("/{pid}")
def delete_passkey(pid: int, user=Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("DELETE FROM HUB_Passkeys WHERE Id = %s AND IdUsuario = %s", (pid, user["id"]))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Passkey no encontrada")
    return {"ok": True}
