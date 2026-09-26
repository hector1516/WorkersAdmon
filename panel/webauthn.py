"""
panel.webauthn — Login con passkey (WebAuthn) del panel
========================================================
El panel NO registra passkeys (eso lo hacen HUB y Field): aquí solo
**verificamos** aserciones contra las mismas `HUB_Passkeys` de la BD, con la
misma librería (`webauthn`) y las mismas reglas que `mcp_server.py`.

RP (Relying Party)
------------------
- `ecc-sa.com.mx` (raíz): passkeys creadas con este rp funcionan en **cualquier
  subdominio**, así que sirven para `worker.ecc-sa.com.mx` (Fase E).
- `field.` / `hub.` (legacy): **no** se pueden usar desde workers, porque el
  navegador exige que `rpId` sea dominio registrable del origin. Se rechazan
  con un mensaje claro en vez de fallar en la niebla.

Estado del challenge
--------------------
Token firmado con HMAC-SHA256 (stdlib) y expiración de 5 minutos: sin estado
en memoria, sobrevive reinicios del panel y no necesita `pyjwt`.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from . import db

RP_ID = "ecc-sa.com.mx"                       # rp raíz (passkeys del ecosistema)
RP_LEGACY = {"field.ecc-sa.com.mx", "hub.ecc-sa.com.mx"}
CHALLENGE_TTL = 300                           # 5 minutos
TIMEOUT_MS = 120000                           # lo que espera el navegador

# Mismo secreto por defecto que mcp_server/HUB (HUB_JWT_SECRET lo sobreescribe)
_SECRET = os.environ.get(
    "HUB_JWT_SECRET", "eccsa-hub-passkey-challenge-secret-2026-v1").encode("utf-8")


# ─── Codificación base64url (sin padding, como exige WebAuthn) ───────────────
def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _from_b64url(text):
    """base64url SIN padding → bytes. (atob del navegador es tolerante; Python no.)"""
    s = str(text or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ─── Estado firmado del challenge ─────────────────────────────────────────────
def _issue_state(purpose):
    """(state, challenge): state = payload HMAC firmado, challenge en bytes."""
    challenge = os.urandom(32)
    now = int(time.time())
    payload = {"p": purpose, "ch": _b64url(challenge),
               "iat": now, "exp": now + CHALLENGE_TTL}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(_SECRET, body.encode("ascii"),
                           hashlib.sha256).digest())
    return f"{body}.{sig}", challenge


def _read_state(state, purpose):
    """Valida la firma y la expiración, y devuelve el challenge en bytes."""
    body, _, sig = str(state or "").partition(".")
    if not body or not sig:
        raise ValueError("Desafío ausente o mal formado")
    esperado = _b64url(hmac.new(_SECRET, body.encode("ascii"),
                                hashlib.sha256).digest())
    if not hmac.compare_digest(esperado, sig):
        raise ValueError("Desafío inválido (firma incorrecta)")
    try:
        payload = json.loads(_from_b64url(body).decode("utf-8"))
    except Exception:
        raise ValueError("Desafío inválido")
    if payload.get("p") != purpose:
        raise ValueError("Desafío inválido (propósito incorrecto)")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("Desafío expirado, intenta de nuevo")
    return _from_b64url(payload.get("ch") or "")


# ─── Origin ───────────────────────────────────────────────────────────────────
def is_allowed_origin(origin):
    """Allowlist: https://ecc-sa.com.mx o cualquier subdominio (+ localhost)."""
    if not origin or "://" not in origin:
        return False
    try:
        from urllib.parse import urlsplit
        p = urlsplit(origin)
    except Exception:
        return False
    if p.hostname in ("localhost", "127.0.0.1"):
        return True
    if p.scheme != "https":
        return False
    host = p.hostname or ""
    return host == "ecc-sa.com.mx" or host.endswith(".ecc-sa.com.mx")


# ─── Flujo ────────────────────────────────────────────────────────────────────
def begin_login():
    """
    Opciones de autenticación (discoverable: sin allowCredentials, el navegador
    muestra las passkeys del usuario disponibles para `ecc-sa.com.mx`).
    """
    state, challenge = _issue_state("wk_login")
    options = {
        "challenge": _b64url(challenge),
        "rpId": RP_ID,
        "timeout": TIMEOUT_MS,
        "userVerification": "preferred",
    }
    return {"options": options, "state": state, "rp": RP_ID}


def _verify_assertion(credential, challenge, origin, public_key, sign_count):
    """Chequeo criptográfico (única parte que necesita la librería `webauthn`)."""
    from webauthn import verify_authentication_response
    return verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=origin,
        credential_public_key=_from_b64url(public_key),
        credential_current_sign_count=int(sign_count or 0),
        require_user_verification=False,
    )


def verify_login(state, credential, origin):
    """
    Verifica la aserción del navegador.
    Devuelve (True, usuario) o (False, mensaje de error apto para UI).
    """
    if not is_allowed_origin(origin):
        return False, ("Origen no permitido. Entra por "
                       "https://worker.ecc-sa.com.mx (o el dominio del panel).")
    try:
        challenge = _read_state(state, "wk_login")
    except ValueError as exc:
        return False, str(exc)

    credential = credential or {}
    cred_id = str(credential.get("id") or "")
    if not cred_id:
        return False, "Falta el id de la credencial"

    row = db.get_passkey_by_credential(cred_id)
    if not row:
        return False, "Passkey no reconocida por este servidor."

    # ¿Es de un rp que SIRVE desde aquí? (legacy field/hub → no, con mensaje claro)
    rp = str(row.get("RpId") or "").strip()
    if rp in RP_LEGACY:
        return False, (f"Esta passkey pertenece a «{rp}» y solo funciona en ese "
                       f"subdominio. Crea una passkey nueva desde el HUB/Field "
                       f"(rp {RP_ID}) o entra con tu contraseña.")

    # El userHandle debe corresponder al dueño de la credencial
    try:
        raw_handle = credential.get("response", {}).get("userHandle")
        handle = int(_from_b64url(raw_handle).decode("utf-8")) if raw_handle else row["IdUsuario"]
    except Exception:
        handle = row["IdUsuario"]
    if handle != row["IdUsuario"]:
        return False, "Passkey no válida para este usuario."

    try:
        resultado = _verify_assertion(credential, challenge, origin,
                                      row["PublicKey"], row.get("SignCount"))
    except Exception as exc:
        return False, f"Verificación fallida: {exc}"

    db.update_passkey_sign_count(row["Id"],
                                 int(getattr(resultado, "new_sign_count", 0) or 0))
    user = db.get_user_by_id(row["IdUsuario"])
    if not user:
        return False, "Usuario no válido o inactivo."
    return True, user
