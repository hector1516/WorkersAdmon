import json
import logging
from pywebpush import WebPushException, webpush

from .config import settings
from .db import get_conn

log = logging.getLogger("push")


def _vapid_claims():
    return {
        "sub": settings.vapid_subject,
    }


def subscribe(user_id: int, endpoint: str, p256dh: str, auth: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUBMAIL_PushSubscriptions (UserID, Endpoint, P256DH, Auth) "
            "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE P256DH=VALUES(P256DH), Auth=VALUES(Auth), UserID=VALUES(UserID)",
            (user_id, endpoint, p256dh, auth),
        )
        conn.commit()
    finally:
        conn.close()


def unsubscribe(endpoint: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM HUBMAIL_PushSubscriptions WHERE Endpoint=%s", (endpoint,))
        conn.commit()
    finally:
        conn.close()


def _get_subs_for_user(user_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT Endpoint, P256DH, Auth FROM HUBMAIL_PushSubscriptions WHERE UserID=%s",
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _notify_user(user_id: int, title: str, body: str, url: str):
    subs = _get_subs_for_user(user_id)
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False).encode("utf-8")
    for sub in subs:
        info = {
            "endpoint": sub["Endpoint"],
            "keys": {"p256dh": sub["P256DH"], "auth": sub["Auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims=_vapid_claims(),
                timeout=10,
            )
        except WebPushException as e:
            if getattr(e, "response", None) is not None and e.response.status_code in (404, 410):
                unsubscribe(sub["Endpoint"])
            else:
                log.warning("push fallido a %s: %s", sub["Endpoint"][:80], e)
        except Exception as e:
            log.warning("push error a %s: %s", sub["Endpoint"][:80], e)


def notify_new_mail(user_ids, title, body, url="/"):
    for uid in set(user_ids or []):
        try:
            _notify_user(uid, title, body, url)
        except Exception as e:
            log.warning("notify user %s error: %s", uid, e)