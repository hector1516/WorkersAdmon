import ipaddress
import os
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["online"])

# Almacen en memoria: {alias: {"last_seen": timestamp, "first_seen": timestamp}}
ONLINE_STORAGE: dict = {}

# Duracion que considera que un usuario sigue "en linea" sin ping (segundos)
ONLINE_TIMEOUT = int(os.environ.get("ONLINE_TIMEOUT", "120"))

# Alias permitidos: nombre passkey (letras/guiones) o usr_XXXX
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _gen_alias() -> str:
    return f"usr_{uuid.uuid4().hex[:6]}"


@router.post("/ping")
async def online_ping(request: Request):
    """Marca al usuario como en linea con un alias anonimo."""
    try:
        body = await request.json()
    except Exception:
        body = None
    alias = (body or {}).get("alias") if isinstance(body, dict) else None
    if not alias or not isinstance(alias, str) or not _ALIAS_RE.match(alias):
        alias = _gen_alias()

    now = datetime.now(timezone.utc)
    if alias not in ONLINE_STORAGE:
        ONLINE_STORAGE[alias] = {"last_seen": now, "first_seen": now}
    else:
        ONLINE_STORAGE[alias]["last_seen"] = now
    return {"alias": alias, "online": True}


@router.post("/logout")
async def online_logout(request: Request):
    """Remueve al usuario de la lista en linea."""
    try:
        body = await request.json()
    except Exception:
        body = None
    alias = (body or {}).get("alias") if isinstance(body, dict) else None
    if alias and alias in ONLINE_STORAGE:
        del ONLINE_STORAGE[alias]
    return {"ok": True}


@router.get("/ubicacion")
def ubicacion(request: Request):
    """Detecta si el cliente esta en la red ECCSA (IP privada)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_ip = (
        request.headers.get("X-Real-IP")
        or (forwarded.split(",")[0].strip() if forwarded else "")
        or (request.client.host if request.client else "")
    )
    try:
        ip = ipaddress.ip_address(client_ip)
        on_network = ip.is_private or ip.is_loopback
    except ValueError:
        on_network = False
    return {"on_network": on_network, "ip": client_ip}


@router.get("/usuarios")
def online_usuarios():
    """Devuelve lista de usuarios en linea (solo alias + tiempo desde ultimo ping)."""
    now = datetime.now(timezone.utc)
    resultado = []
    for alias, data in list(ONLINE_STORAGE.items()):
        delta = (now - data["last_seen"]).total_seconds()
        if delta > ONLINE_TIMEOUT:
            del ONLINE_STORAGE[alias]
            continue
        if delta < 60:
            tiempo = f"hace {int(delta)} seg"
        else:
            tiempo = f"hace {int(delta // 60)} min"
        resultado.append({"alias": alias, "tiempo": tiempo})
    resultado.sort(key=lambda x: x["tiempo"])
    return {"usuarios": resultado, "total": len(resultado)}
