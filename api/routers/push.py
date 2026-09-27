import os
import json
import base64
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth import require_user
from db import get_connection

router = APIRouter()

# VAPID keys - generated once, stored in env or DB
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS = {"sub": "mailto:admin@ecc-sa.com.mx"}

def _cfg_get(clave: str) -> str:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = %s", (clave,))
            row = cur.fetchone()
            return (row[0] or "") if row else ""
    except Exception:
        return ""

def _cfg_set(clave: str, valor: str):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM HUB_Config WHERE Clave = %s", (clave,))
        if cur.fetchone()[0] > 0:
            cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = %s", (valor, clave))
        else:
            cur.execute("INSERT INTO HUB_Config (Clave, Valor, Actualizado) VALUES (%s, %s, GETDATE())", (clave, valor))
        conn.commit()

def _ensure_vapid_keys():
    """Claves VAPID persistentes en HUB_Config (sobreviven reinicios)."""
    global VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY
    if VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY:
        return
    if os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY"):
        VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
        VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"]
        return
    priv = _cfg_get("vapid_private_key")
    pub = _cfg_get("vapid_public_key")
    if priv and pub:
        VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY = priv, pub
        return
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        # Generate EC P-256 key pair
        private_key = ec.generate_private_key(ec.SECP256R1())
        # Private key: PEM string for pywebpush
        priv = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()
        # Public key: raw uncompressed EC bytes (65 bytes) -> base64url for the client
        pub_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        pub = base64.urlsafe_b64encode(pub_raw).decode()
        # Guardar ambas en una transaccion para no mezclar pares entre workers
        conn = get_connection()
        with conn.cursor() as cur:
            for clave, valor in (("vapid_private_key", priv), ("vapid_public_key", pub)):
                cur.execute("SELECT COUNT(*) FROM HUB_Config WHERE Clave = %s", (clave,))
                if cur.fetchone()[0] > 0:
                    cur.execute("UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = %s", (valor, clave))
                else:
                    cur.execute("INSERT INTO HUB_Config (Clave, Valor, Actualizado) VALUES (%s, %s, GETDATE())", (clave, valor))
            conn.commit()
        VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY = priv, pub
        print(f"[push] VAPID keys generated and saved to DB")
    except Exception as e:
        print(f"[push] ERROR: VAPID key generation failed: {e}")

@router.get("/vapid-public-key")
def get_vapid_public_key():
    _ensure_vapid_keys()
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(503, "Web Push not configured")
    return {"publicKey": VAPID_PUBLIC_KEY}

class PushSubscription(BaseModel):
    endpoint: str
    keys: dict[str, str]

@router.post("/subscribe")
def subscribe_push(sub: PushSubscription, user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        # Upsert subscription
        cur.execute("""
            MERGE HUB_PushSubscriptions AS target
            USING (SELECT %s AS endpoint, %s AS userId) AS source
            ON target.endpoint = source.endpoint AND target.userId = source.userId
            WHEN MATCHED THEN
                UPDATE SET p256dh = %s, auth = %s, updatedAt = GETDATE()
            WHEN NOT MATCHED THEN
                INSERT (endpoint, userId, p256dh, auth, createdAt, updatedAt)
                VALUES (%s, %s, %s, %s, GETDATE(), GETDATE());
        """, (
            sub.endpoint, user["id"],
            sub.keys.get("p256dh", ""), sub.keys.get("auth", ""),
            sub.endpoint, user["id"], sub.keys.get("p256dh", ""), sub.keys.get("auth", "")
        ))
        conn.commit()
    return {"ok": True}

@router.delete("/subscribe")
def unsubscribe_push(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM HUB_PushSubscriptions WHERE userId = %s", (user["id"],))
        conn.commit()
    return {"ok": True}

def send_push_notification(user_id: int, title: str, body: str, url: str = "/"):
    """Send push notification to all subscriptions of a user."""
    try:
        _ensure_vapid_keys()
        from pywebpush import webpush
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT endpoint, p256dh, auth FROM HUB_PushSubscriptions WHERE userId = %s",
                (user_id,)
            )
            subs = cur.fetchall()

        if not subs or not VAPID_PRIVATE_KEY:
            return

        payload = json.dumps({
            "title": title,
            "body": body,
            "url": url,
            "icon": "/icons/icon-192x192.png",
            "badge": "/icons/icon-192x192.png"
        })

        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {
                            "p256dh": sub["p256dh"],
                            "auth": sub["auth"]
                        }
                    },
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except Exception as e:
                # Only remove subscription on 404/410 (expired/invalid)
                # Keep it on transient errors (429, 500, network timeout)
                status_code = getattr(e, 'code', None) or getattr(e, 'response', None)
                if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    status_code = e.response.status_code
                if status_code in (404, 410):
                    with conn.cursor() as cur2:
                        cur2.execute(
                            "DELETE FROM HUB_PushSubscriptions WHERE endpoint = %s",
                            (sub["endpoint"],)
                        )
                    conn.commit()
                else:
                    print(f"[push] Transient error for {sub['endpoint'][:50]}: {e}")
    except Exception as e:
        print(f"Push notification error: {e}")

def notify_all_users(title: str, body: str, exclude_user_id: int = None):
    """Send notification to all active users (for new data alerts)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        if exclude_user_id:
            cur.execute("SELECT Id FROM HUB_Users WHERE Activo = 1 AND Id != %s", (exclude_user_id,))
        else:
            cur.execute("SELECT Id FROM HUB_Users WHERE Activo = 1")
        users = cur.fetchall()

    for u in users:
        send_push_notification(u["Id"], title, body)


def _get_user_name(user_id: int) -> str:
    """Obtiene el Nombre de un usuario desde HUB_Users."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (user_id,))
            row = cur.fetchone()
            return row["Nombre"] if row else "Alguien"
    except Exception:
        return "Alguien"


def _get_nickname(user_id: int) -> str:
    """Obtiene el nickname del usuario (HUB_Users.Nickname, migración 0034)."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT Nickname FROM HUB_Users WHERE Id = %s",
                (user_id,)
            )
            row = cur.fetchone()
            nick = (row.get("Nickname") or "").strip() if row else ""
            return nick if nick else _get_user_name(user_id)
    except Exception:
        return _get_user_name(user_id)


# ── Legends notifications (to all users) ──

NIVEL_EMOJI = {"Bronce": "🥉", "Plata": "🥈", "Oro": "🥇", "Diamante": "💎"}

def notify_level_up(user_id: int, new_level: str):
    """Notifica a TODOS cuando alguien sube de nivel."""
    name = _get_nickname(user_id)
    emoji = NIVEL_EMOJI.get(new_level, "🎯")
    notify_all_users(
        "🎉 ¡Sube de nivel!",
        f"{name} alcanzó nivel {new_level} {emoji}"
    )

def notify_ranking_pass(user_id: int, passed_user_id: int, new_position: int):
    """Notifica a TODOS cuando alguien pasa a otro en el ranking."""
    passer = _get_nickname(user_id)
    passed = _get_nickname(passed_user_id)
    notify_all_users(
        "🏆 ¡Movimiento en el ranking!",
        f"{passer} pasó a {passed} — ahora en posición #{new_position}"
    )

def notify_new_user(user_id: int, nickname: str):
    """Notifica a TODOS cuando un nuevo usuario se une a Legends."""
    notify_all_users(
        "👋 ¡Nuevo en Legends!",
        f"{nickname} se unió al equipo"
    )

def notify_weekly_winner(user_id: int, points: int):
    """Notifica a TODOS cuando se elige al ganador semanal."""
    name = _get_nickname(user_id)
    notify_all_users(
        "🏆 ¡Ganador de la semana!",
        f"{name} ganó la semana con {points} puntos"
    )


class PushSendRequest(BaseModel):
    title: str
    body: str
    user_ids: list[int] | None = None
    all: bool = False


@router.post("/send")
def send_manual(req: PushSendRequest, user: dict = Depends(require_user)):
    """Envio manual de push (solo admins)."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Solo administradores")
    title = (req.title or "").strip()[:120]
    body = (req.body or "").strip()[:500]
    if not title or not body:
        raise HTTPException(status_code=400, detail="Título y mensaje requeridos")
    _ensure_vapid_keys()
    count = 0
    if req.all:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Id FROM HUB_Users WHERE Activo = 1")
            ids = [r["Id"] for r in cur.fetchall()]
        for uid in ids:
            send_push_notification(uid, title, body)
            count += 1
    else:
        for uid in req.user_ids or []:
            try:
                send_push_notification(int(uid), title, body)
                count += 1
            except Exception:
                pass
    return {"ok": True, "sent": count}
