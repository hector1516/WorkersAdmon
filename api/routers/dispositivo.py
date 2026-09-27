from fastapi import APIRouter, Depends, Request

from auth import require_user

router = APIRouter()


@router.get("/ip")
def mi_ip(request: Request, user=Depends(require_user)):
    """IP publica del cliente tal como la ve el servidor."""
    ip = request.headers.get("CF-Connecting-IP", "")
    if not ip:
        ip = request.headers.get("X-Real-IP", "")
    if not ip:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            ip = xff.split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return {"ip": ip or "?"}
