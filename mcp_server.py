import json
import subprocess
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import urllib.parse
import time
import secrets
import base64 as _b64
from urllib.parse import urlparse as _urlparse

# ─── WebAuthn / Passkeys (copiado de Field — dual RP *.ecc-sa.com.mx) ───────
# rp_raíz = passkeys nuevas: funcionan en CUALQUIER subdominio de ecc-sa.com.mx
#           (HUB, Field, apps futuras).
# rp_legacy = passkeys creadas antes de la migración: solo en su subdominio.
# El registro SIEMPRE usa el rp raíz. El login acepta ambos (según RpId en BD).
RP_ID = "ecc-sa.com.mx"                 # rp para REGISTRAR passkeys nuevas
RP_ID_OLD_HUB = "hub.ecc-sa.com.mx"     # rp legacy HUB (solo login)
RP_ID_OLD_FIELD = "field.ecc-sa.com.mx" # rp legacy Field (solo login, misma BD)
RP_NAME = "ECCSA"
CHALLENGE_TTL = 5 * 60  # 5 minutos

# RPs que el cliente puede pedir en login/options
ALLOWED_RP_IDS = {RP_ID, RP_ID_OLD_HUB, RP_ID_OLD_FIELD}

# JWT stateless para challenges (multi-worker safe, sin estado en memoria).
# Se puede sobreescribir con HUB_JWT_SECRET (recommendado >= 32 bytes para HS256).
JWT_SECRET = os.environ.get("HUB_JWT_SECRET", "eccsa-hub-passkey-challenge-secret-2026-v1")
JWT_ALG = "HS256"


def _is_allowed_origin(origin):
    """Allowlist: https://ecc-sa.com.mx o cualquier subdominio (*.ecc-sa.com.mx).
    Devuelve True también para localhost (desarrollo)."""
    if not origin:
        return False
    p = _urlparse(origin)
    if p.hostname in ("localhost", "127.0.0.1"):
        return True
    if p.scheme != "https":
        return False
    host = p.hostname or ""
    return host == "ecc-sa.com.mx" or host.endswith(".ecc-sa.com.mx")


def _origin_for_rp(origin_header, rp_id):
    """Origin esperado según la passkey:
    - legacy (hub./field.): siempre https://<rp> (así se crearon)
    - nueva (raíz): el Origin real de la petición, validado contra allowlist
    """
    if rp_id in (RP_ID_OLD_HUB, RP_ID_OLD_FIELD):
        return f"https://{rp_id}"
    if not _is_allowed_origin(origin_header):
        raise ValueError("Origin no permitido")
    return origin_header


def _b64url_encode(raw):
    return _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _issue_challenge(purpose):
    """Genera challenge aleatorio y lo envuelve en JWT stateless (multi-worker safe)."""
    raw = os.urandom(32)
    now = int(time.time())
    token = __import__("jwt").encode(
        {"purpose": purpose, "ch": _b64url_encode(raw), "iat": now, "exp": now + CHALLENGE_TTL},
        JWT_SECRET, algorithm=JWT_ALG,
    )
    return token, raw


def _read_challenge(state, purpose):
    """Decodifica el JWT state y devuelve los bytes del challenge."""
    import jwt as _pyjwt
    try:
        data = _pyjwt.decode(state, JWT_SECRET, algorithms=[JWT_ALG])
    except _pyjwt.ExpiredSignatureError:
        raise ValueError("Desafío expirado, intenta de nuevo")
    except Exception:
        raise ValueError("Desafío inválido")
    if data.get("purpose") != purpose or not data.get("ch"):
        raise ValueError("Desafío inválido")
    # base64url sin padding → recalcular '=' (stdlib, sin depender de webauthn aquí)
    ch = data["ch"]
    return _b64.urlsafe_b64decode(ch + "=" * (-len(ch) % 4))


def _webauthn_begin_register(payload):
    """Genera opciones de registro (navigator.credentials.create).
    Usa JWT stateless para el challenge y rp raíz para la passkey nueva."""
    try:
        from webauthn import generate_registration_options, options_to_json, base64url_to_bytes
        from webauthn.helpers.structs import (
            AuthenticatorAttachment, AuthenticatorSelectionCriteria,
            PublicKeyCredentialDescriptor, PublicKeyCredentialType,
            ResidentKeyRequirement, UserVerificationRequirement,
        )
        import eccsa_db as db
    except Exception as e:
        return 500, {"ok": False, "msg": f"interior err: {e}"}
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return 400, {"ok": False, "msg": "email obligatorio"}
    # Validar origin ANTES de emitir options (registro siempre en rp raíz)
    origin = payload.get("origin") or ""
    try:
        _origin_for_rp(origin, RP_ID)
    except ValueError as e:
        return 400, {"ok": False, "msg": str(e)}
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Id, Nombre, Email FROM HUB_Users WHERE LTRIM(RTRIM(Email)) = %s AND Activo = 1",
                    (email,),
                )
                u = cur.fetchone()
        if not u:
            return 404, {"ok": False, "msg": "usuario no encontrado"}
        user_id = u["Id"]
        # Credenciales existentes (para excluir duplicados)
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT CredentialId FROM HUB_Passkeys WHERE IdUsuario = %s", (user_id,))
                existing = [dict(r) for r in cur.fetchall()]
        exclude = None
        if existing:
            exclude = [
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(c["CredentialId"]), type=PublicKeyCredentialType.PUBLIC_KEY
                )
                for c in existing
            ]
        state, challenge = _issue_challenge("wk_reg")
        options = generate_registration_options(
            rp_id=RP_ID,  # SIEMPRE raíz: funciona en cualquier subdominio
            rp_name=RP_NAME,
            user_id=str(user_id).encode("utf-8"),
            user_name=u["Email"],
            user_display_name=u["Nombre"] or u["Email"],
            challenge=challenge,
            timeout=120000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=exclude,
        )
        options_dict = json.loads(options_to_json(options))
        return 200, {"ok": True, "options": options_dict, "state": state}
    except Exception as exc:
        return 500, {"ok": False, "msg": f"begin_register error: {exc}"}


def _webauthn_finish_register(payload):
    """Valida la respuesta al challenge de registro, guarda la passkey e
    inicializa el Nickname del usuario si está vacío (mismo flujo que Field)."""
    try:
        from webauthn import verify_registration_response
        import eccsa_db as db
    except Exception as e:
        return 500, {"ok": False, "msg": f"interior err: {e}"}
    email = (payload.get("email") or "").strip().lower()
    state = (payload.get("state") or "").strip()
    if not email or not state:
        return 400, {"ok": False, "msg": "email y state obligatorios"}
    try:
        challenge = _read_challenge(state, "wk_reg")
    except ValueError as e:
        return 400, {"ok": False, "msg": str(e)}
    origin = payload.get("origin") or ""
    try:
        expected_origin = _origin_for_rp(origin, RP_ID)
    except ValueError as e:
        return 400, {"ok": False, "msg": str(e)}
    try:
        verification = verify_registration_response(
            credential=payload.get("credential", {}),
            expected_challenge=challenge,
            expected_rp_id=RP_ID,
            expected_origin=expected_origin,
        )
        cred_id = _b64url_encode(verification.credential_id)
        pubkey = _b64url_encode(verification.credential_public_key)
        sign_count = verification.sign_count
        transports = ",".join(payload.get("credential", {}).get("transports", []) or [])
        label = (payload.get("label") or "Mi equipo")[:100]
        # Guardar passkey + inicializar Nickname si está vacío
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                # Verificar duplicado
                cur.execute("SELECT Id FROM HUB_Passkeys WHERE CredentialId = %s", (cred_id,))
                if cur.fetchone():
                    return 400, {"ok": False, "msg": "Esta passkey ya está registrada"}
                # Obtener user_id
                cur.execute(
                    "SELECT Id FROM HUB_Users WHERE LTRIM(RTRIM(Email)) = %s AND Activo = 1", (email,)
                )
                u = cur.fetchone()
                if not u:
                    return 404, {"ok": False, "msg": "usuario no encontrado"}
                user_id = u["Id"]
                # Insertar passkey con rp raíz
                cur.execute(
                    "INSERT INTO HUB_Passkeys (IdUsuario, CredentialId, PublicKey, SignCount, Transports, Etiqueta, RpId) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (user_id, cred_id, pubkey, sign_count, transports, label, RP_ID),
                )
                # Nickname de Legends: inicializar solo si está vacío (no sobrescribir)
                cur.execute("SELECT Nickname FROM HUB_Users WHERE Id = %s", (user_id,))
                uu = cur.fetchone()
                nick = (uu.get("Nickname") or "").strip() if uu else ""
                if not nick:
                    cur.execute("UPDATE HUB_Users SET Nickname = %s WHERE Id = %s", (label, user_id))
                conn.commit()
        print(f"[passkey] register OK email={email} label={label} rp={RP_ID}", flush=True)
        return 200, {"ok": True, "msg": "passkey registrada"}
    except Exception as exc:
        return 400, {"ok": False, "msg": f"verificación fallida: {exc}"}


def _webauthn_begin_login(payload):
    """Genera opciones de autenticación para un rp específico.
    Acepta rp='ecc-sa.com.mx' (nueva) o rp legacy. El frontend reintenta con
    el otro rp si este no encuentra credenciales (mismo flujo que Field)."""
    try:
        from webauthn import generate_authentication_options, options_to_json, base64url_to_bytes
        from webauthn.helpers.structs import (
            PublicKeyCredentialDescriptor, PublicKeyCredentialType,
            UserVerificationRequirement,
        )
        import eccsa_db as db
    except Exception as e:
        return 500, {"ok": False, "msg": f"interior err: {e}"}
    email = (payload.get("email") or "").strip().lower()
    # Whitelist de rp_id: el navegador SOLO ofrece passkeys de ese rp
    rp = (payload.get("rp") or RP_ID_OLD_FIELD).strip()
    if rp not in ALLOWED_RP_IDS:
        rp = RP_ID_OLD_FIELD
    try:
        allow = None
        if email:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute(
                        "SELECT Id FROM HUB_Users WHERE LTRIM(RTRIM(Email)) = %s AND Activo = 1",
                        (email,),
                    )
                    u = cur.fetchone()
            if not u:
                return 404, {"ok": False, "msg": "usuario no encontrado"}
            user_id = u["Id"]
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute(
                        "SELECT CredentialId FROM HUB_Passkeys WHERE IdUsuario = %s", (user_id,)
                    )
                    existing = [dict(r) for r in cur.fetchall()]
            if not existing:
                return 404, {"ok": False, "msg": "no tiene passkey registrada"}
            allow = [
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(c["CredentialId"]),
                    type=PublicKeyCredentialType.PUBLIC_KEY,
                )
                for c in existing
            ]
        state, challenge = _issue_challenge("wk_login")
        options = generate_authentication_options(
            rp_id=rp,
            challenge=challenge,
            timeout=120000,
            allow_credentials=allow,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        options_dict = json.loads(options_to_json(options))
        return 200, {"ok": True, "options": options_dict, "state": state, "rp": rp}
    except Exception as exc:
        return 500, {"ok": False, "msg": f"begin_login error: {exc}"}


def _webauthn_finish_login(payload):
    """Valida la respuesta de autenticación, crea sesión y devuelve token.
    Valida con el rp/origin con el que SE CREÓ la credencial (dual, como Field)."""
    try:
        from webauthn import base64url_to_bytes, verify_authentication_response
        import eccsa_db as db
    except Exception as e:
        return 500, {"ok": False, "msg": f"interior err: {e}"}
    state = (payload.get("state") or "").strip()
    if not state:
        return 400, {"ok": False, "msg": "state obligatorio"}
    try:
        challenge = _read_challenge(state, "wk_login")
    except ValueError as e:
        return 400, {"ok": False, "msg": str(e)}
    credential = payload.get("credential", {})
    cred_id_raw = credential.get("id", "")
    if not cred_id_raw:
        return 400, {"ok": False, "msg": "falta id de credencial"}
    origin = payload.get("origin") or ""
    try:
        # Buscar credencial por CredentialId (string base64url)
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT * FROM HUB_Passkeys WHERE CredentialId = %s", (cred_id_raw,))
                row = cur.fetchone()
        if not row:
            return 404, {"ok": False, "msg": "passkey no reconocida"}
        # El userHandle debe corresponder al dueño de la credencial
        try:
            raw_handle = credential.get("response", {}).get("userHandle")
            handle_uid = int(base64url_to_bytes(raw_handle).decode("utf-8")) if raw_handle else row["IdUsuario"]
        except Exception:
            handle_uid = row["IdUsuario"]
        if handle_uid != row["IdUsuario"]:
            return 401, {"ok": False, "msg": "passkey no válida para este usuario"}
        # Validar con el rp/origin con el que SE CREÓ la credencial (dual)
        cred_rp = (row.get("RpId") or RP_ID_OLD_FIELD).strip() or RP_ID_OLD_FIELD
        if cred_rp not in ALLOWED_RP_IDS:
            cred_rp = RP_ID_OLD_FIELD
        try:
            expected_origin = _origin_for_rp(origin, cred_rp)
        except ValueError as e:
            return 400, {"ok": False, "msg": str(e)}
        # PublicKey viene como base64url string SIN padding → recalcular '='
        # (Python exige len%4==0; atob del navegador es tolerante, aquí no)
        pk_str = row["PublicKey"] or ""
        pk_bytes = _b64.urlsafe_b64decode(pk_str + "=" * (-len(pk_str) % 4))
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=cred_rp,
            expected_origin=expected_origin,
            credential_public_key=pk_bytes,
            credential_current_sign_count=row["SignCount"],
            require_user_verification=False,
        )
        # Actualizar sign count y último uso
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE HUB_Passkeys SET SignCount = %s, UltimoUso = GETDATE() WHERE Id = %s",
                    (verification.new_sign_count, row["Id"]),
                )
                conn.commit()
        # Obtener usuario completo y crear sesión
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Id, Nombre, Email, Activo FROM HUB_Users WHERE Id = %s", (row["IdUsuario"],))
                user = cur.fetchone()
        if not user or not user.get("Activo"):
            return 401, {"ok": False, "msg": "usuario no válido o inactivo"}
        token = db.create_session_token(user["Email"])
        if not token:
            return 500, {"ok": False, "msg": "fallo al crear sesión"}
        print(f"[passkey] login OK uid={user['Id']} rp={cred_rp}", flush=True)
        return 200, {"ok": True, "token": token, "email": user["Email"], "nombre": user["Nombre"]}
    except Exception as exc:
        return 400, {"ok": False, "msg": f"verificación fallida: {exc}"}

# Exporta el directorio del repo para poder importar eccsa_db y guardar/leer
# las suscripciones push (HUB_PushSubscriptions) desde este servidor.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


def _save_push_subscription(payload):
    """Guarda una suscripción push recibida del navegador en la BD."""
    try:
        import eccsa_db
    except Exception as e:
        return False, f"no eccsa_db: {e}"
    email = (payload.get("email") or "").strip()
    endpoint = (payload.get("endpoint") or "").strip()
    p256dh = (payload.get("p256dh") or "").strip()
    auth = (payload.get("auth") or "").strip()
    if not email or not endpoint:
        return False, "email y endpoint son obligatorios"
    try:
        ok = eccsa_db.save_push_subscription(email, endpoint, p256dh, auth)
        return ok, ("guardada" if ok else "fallo al guardar")
    except Exception as e:
        return False, f"error: {e}"


def _remove_push_subscription(payload):
    """Elimina una suscripción push por endpoint."""
    try:
        import eccsa_db
    except Exception as e:
        return False, f"no eccsa_db: {e}"
    endpoint = (payload.get("endpoint") or "").strip()
    if not endpoint:
        return False, "endpoint obligatorio"
    try:
        ok = eccsa_db.remove_push_subscription(endpoint)
        return ok, ("eliminada" if ok else "no encontrada")
    except Exception as e:
        return False, f"error: {e}"


def _send_cors_headers():
    self_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return self_headers


def _write_json(handler, status, obj):
    """Escribe una respuesta JSON con cabeceras CORS."""
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.end_headers()
        handler.wfile.write(json.dumps(obj).encode("utf-8"))
    except Exception as e:
        print(f"Error escribiendo JSON: {e}")

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class MCPServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress logging to keep output clean
        pass

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path.startswith("/__webauthn/"):
            # Endpoints webauthn solo admiten POST; GET responde ok de sondeo
            _write_json(self, 200, {"ok": True, "method": "GET", "ready": True})
            return
        if parsed_url.path in ("/__push_subscribe__", "/__push_unsubscribe__"):
            # Endpoints push solo admiten POST; GET responde ok de sondeo
            _write_json(self, 200, {"ok": True, "method": "GET", "ready": True})
            return
        if parsed_url.path == "/sse":
            # Respond with standard Server-Sent Events headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            # Send the endpoint event indicating client should POST to /message
            try:
                self.wfile.write(b"event: endpoint\ndata: /message\n\n")
                self.wfile.flush()
                # Keep connection open for SSE (thread-safe sleep loop)
                while True:
                    # Send a keep-alive ping event every 15 seconds to prevent client timeout
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(15)
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))

        def _read_payload():
            """Lee y parsea el body JSON de la petición, inyectando Host y Origin reales."""
            if content_length <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(data, dict):
                    data = {}
                data.setdefault("host", (self.headers.get("Host") or "").strip())
                data.setdefault("origin", (self.headers.get("Origin") or "").strip())
                return data
            except Exception:
                return {}

        # ─── Endpoints WebAuthn / Passkeys ───────────────────────────────────
        if parsed_url.path == "/__webauthn/register_begin":
            code, obj = _webauthn_begin_register(_read_payload())
            _write_json(self, code, obj)
            return
        if parsed_url.path == "/__webauthn/register_finish":
            code, obj = _webauthn_finish_register(_read_payload())
            _write_json(self, code, obj)
            return
        if parsed_url.path == "/__webauthn/login_begin":
            code, obj = _webauthn_begin_login(_read_payload())
            _write_json(self, code, obj)
            return
        if parsed_url.path == "/__webauthn/login_finish":
            code, obj = _webauthn_finish_login(_read_payload())
            _write_json(self, code, obj)
            return

        # ─── Endpoints de suscripción push ──────────────────────────────────
        if parsed_url.path == "/__push_subscribe__":
            try:
                post_data = self.rfile.read(content_length)
                payload = json.loads(post_data.decode("utf-8"))
                ok, msg = _save_push_subscription(payload)
                _write_json(self, 200, {"ok": ok, "msg": msg})
            except Exception as e:
                _write_json(self, 500, {"ok": False, "msg": str(e)})
            return

        if parsed_url.path == "/__push_unsubscribe__":
            if content_length > 0:
                try:
                    post_data = self.rfile.read(content_length)
                    payload = json.loads(post_data.decode("utf-8"))
                    ok, msg = _remove_push_subscription(payload)
                    _write_json(self, 200, {"ok": ok, "msg": msg})
                except Exception as e:
                    _write_json(self, 500, {"ok": False, "msg": str(e)})
            else:
                _write_json(self, 400, {"ok": False, "msg": "sin datos"})
            return

        # ─── Endpoints MCP JSON-RPC ──────────────────────────────────────────
        if parsed_url.path == "/message":
            post_data = self.rfile.read(content_length)
            
            try:
                request = json.loads(post_data.decode("utf-8"))
                response = self.handle_jsonrpc(request)
                
                # Send the response back
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        # Support CORS preflight requests (también para los endpoints push)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def handle_jsonrpc(self, req):
        method = req.get("method")
        req_id = req.get("id")
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "container-mcp-server",
                        "version": "1.0.0"
                    }
                },
                "id": req_id
            }
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {
                            "name": "run_command",
                            "description": "Execute a shell command inside the container.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "command": {
                                        "type": "string",
                                        "description": "The command to run."
                                    }
                                },
                                "required": ["command"]
                            }
                        },
                        {
                            "name": "write_file",
                            "description": "Write content to a file inside the container.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "The target file path."
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "The content to write."
                                    }
                                },
                                "required": ["path", "content"]
                            }
                        },
                        {
                            "name": "read_file",
                            "description": "Read content of a file inside the container.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "The target file path."
                                    }
                                },
                                "required": ["path"]
                            }
                        }
                    ]
                },
                "id": req_id
            }
        
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            result = self.execute_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id
            }
        
        elif method == "notifications/initialized":
            return {}
            
        else:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method {method} not found"
                },
                "id": req_id
            }

    def execute_tool(self, name, args):
        if name == "run_command":
            command = args.get("command")
            try:
                res = subprocess.run(
                    command, 
                    shell=True, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    timeout=30
                )
                output = f"Stdout:\n{res.stdout}\nStderr:\n{res.stderr}"
                return {"content": [{"type": "text", "text": output}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error running command: {str(e)}"}]}
                
        elif name == "write_file":
            path = args.get("path")
            content = args.get("content")
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return {"content": [{"type": "text", "text": f"Successfully wrote to {path}"}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error writing file: {str(e)}"}]}
                
        elif name == "read_file":
            path = args.get("path")
            try:
                if not os.path.exists(path):
                    return {"content": [{"type": "text", "text": f"File {path} does not exist"}]}
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                return {"content": [{"type": "text", "text": content}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error reading file: {str(e)}"}]}
        else:
            return {"content": [{"type": "text", "text": f"Tool {name} not supported"}]}

def run_server():
    server_address = ("", 8000)
    httpd = ThreadingHTTPServer(server_address, MCPServerHandler)
    print("Multi-threaded MCP server running on port 8000 inside container...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()

if __name__ == "__main__":
    run_server()
