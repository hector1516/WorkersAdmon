import json
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import settings
from .db import get_conn

_SPAM_RE = re.compile(r"\[(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\]")
_RECEIVED_RE = re.compile(r"^Received:", re.M)


def get_admin_user_id():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT UserID FROM HUBMAIL_Admins ORDER BY UserID LIMIT 1")
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def is_admin(user):
    aid = get_admin_user_id()
    return aid is not None and user["id"] == aid


def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "")


def extract_sender_ip(raw):
    if not raw:
        return ""
    text = raw.decode("utf-8", "ignore")
    for m in _RECEIVED_RE.finditer(text):
        start = m.end()
        end = text.find("\n", start)
        line = text[start:end if end != -1 else None]
        ip = _SPAM_RE.search(line or "")
        if ip:
            return ip.group(1)
    return ""


def _load_enabled_zones():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Zone FROM HUBMAIL_SpamLists WHERE Enabled=1 AND Type='DNSBL' ORDER BY Priority"
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def check_dnsbl(sender_ip):
    if not sender_ip:
        return False
    try:
        parts = sender_ip.split(".")
        if len(parts) != 4:
            return False
    except Exception:
        return False
    zones = _load_enabled_zones()
    if not zones:
        return False
    rev = ".".join(reversed(parts))

    def lookup(zone):
        try:
            socket.gethostbyname(f"{rev}.{zone}")
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=min(len(zones), 8)) as ex:
        futures = [ex.submit(lookup, z) for z in zones]
        for f in futures:
            try:
                if f.result(timeout=3):
                    return True
            except Exception:
                continue
    return False


def _cond_text(field, row):
    if field == "from":
        return "{} {}".format(row.get("FromName") or "", row.get("FromEmail") or "")
    if field == "to":
        return row.get("ToText") or ""
    if field == "subject":
        return row.get("Subject") or ""
    if field == "body":
        return "{} {}".format(row.get("BodyText") or "", _strip_html(row.get("BodyHtml") or ""))
    if field == "domain":
        return row.get("FromEmail") or ""
    return ""


def _eval_conditions(cond_list, row):
    for c in cond_list or []:
        field = (c.get("field") or "").lower()
        op = (c.get("op") or "contains").lower()
        val = (c.get("value") or "").strip().lower()
        if not field or not val:
            continue
        text = _cond_text(field, row).lower()
        if op == "equals":
            if text.strip() != val:
                return False
        else:
            if val not in text:
                return False
    return True


def _load_filters(user_id, scope, account_id=None):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        if scope == "ACCOUNT":
            cur.execute(
                "SELECT * FROM HUBMAIL_Filters WHERE UserID=%s AND Scope='ACCOUNT' "
                "AND (AccountID=%s OR AccountID IS NULL) AND Enabled=1 ORDER BY OrderNo, FilterID",
                (user_id, account_id),
            )
        else:
            cur.execute(
                "SELECT * FROM HUBMAIL_Filters WHERE UserID=%s AND Scope='GLOBAL' "
                "AND Enabled=1 ORDER BY OrderNo, FilterID",
                (user_id,),
            )
        rows = cur.fetchall()
        for r in rows:
            try:
                r["_conds"] = json.loads(r["Conditions"] or "[]")
            except Exception:
                r["_conds"] = []
        return rows
    finally:
        conn.close()


def _linked_user_ids(account_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT UserID FROM HUBMAIL_Accounts "
            "WHERE AccountID=%s OR CanonicalAccountID=%s",
            (account_id, account_id),
        )
        return [int(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def _load_messages(account_id, folder, uids):
    if not uids:
        return []
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        uids_sql = ",".join(str(int(u)) for u in uids)
        cur.execute(
            "SELECT UID, FromName, FromEmail, ToText, Subject, BodyText, BodyHtml, SenderIP "
            "FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID IN (" + uids_sql + ")",
            (account_id, folder),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _db_set_flags(account_id, folder, uid, seen=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if seen is not None:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET Seen=%s WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (1 if seen else 0, account_id, folder, uid),
            )
        conn.commit()
    finally:
        conn.close()


def _db_set_spam(account_id, uid, spam=True):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE HUBMAIL_Messages SET Spam=%s WHERE AccountID=%s AND UID=%s",
            (1 if spam else 0, account_id, uid),
        )
        conn.commit()
    finally:
        conn.close()


def _db_mark_filtered_many(account_id, folder, uids):
    if not uids:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        for uid in uids:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET FilteredAt=NOW() "
                "WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (account_id, folder, int(uid)),
            )
        conn.commit()
    finally:
        conn.close()


def reset_filtered(scope, account_id=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if scope == "GLOBAL":
            cur.execute("UPDATE HUBMAIL_Messages SET FilteredAt=NULL")
        else:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET FilteredAt=NULL "
                "WHERE AccountID=%s OR AccountID IN "
                "(SELECT AccountID FROM HUBMAIL_Accounts WHERE CanonicalAccountID=%s)",
                (account_id, account_id),
            )
        conn.commit()
    finally:
        conn.close()


def _db_delete_messages(account_id, folder, uids):
    if not uids:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        for uid in uids:
            cur.execute(
                "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (account_id, folder, int(uid)),
            )
        conn.commit()
    finally:
        conn.close()


def _db_move_message(account_id, folder, uid, dest):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE HUBMAIL_Messages SET Folder=%s WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (dest, account_id, folder, uid),
        )
        conn.commit()
    finally:
        conn.close()


def _spam_folder(account_id):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT Folder FROM HUBMAIL_Folders WHERE AccountID=%s ORDER BY Folder",
            (account_id,),
        )
        folders = [r["Folder"] for r in cur.fetchall()]
    finally:
        conn.close()
    for f in folders:
        low = f.lower()
        if low in ("junk", "spam", "junk email", "spam folder", "correo no deseado"):
            return f
    for f in folders:
        low = f.lower()
        if "junk" in low or "spam" in low or "no deseado" in low:
            return f
    return None


def _apply_spam(account_id, folder, row, imap):
    uid = str(row["UID"])
    _db_set_spam(account_id, row["UID"], True)
    dest = _spam_folder(account_id)
    if dest and dest != folder:
        try:
            imap.move_message(folder, uid, dest)
            _db_move_message(account_id, folder, row["UID"], dest)
        except Exception as e:
            print(f"[FILTER] spam move error {e}", flush=True)


def _apply_action(f, account_id, folder, row, imap):
    """Ejecuta la acción del filtro sobre el buzón.

    Con HUBMAIL_SYNC_DRYRUN=1 se registra el match pero NO se toca IMAP (los
    `imap.*` de abajo son los únicos lugares donde los filtros escriben en el
    buzón). La caché local tampoco se marca como filtrada, para que el barrido
    del siguiente ciclo vuelva a evaluar el mismo mensaje y el modo ensayo no
    deje huecos que luego haya que rellenar.
    """
    if settings.dry_run:
        uid = str(row["UID"])
        print(f"[FILTER] (ensayo) {f.get('Action') or 'spam'} sobre "
              f"{account_id}/{folder}/{uid}", flush=True)
        return
    uid = str(row["UID"])
    action = f.get("Action") or "spam"
    if action == "mark_read":
        try:
            imap.set_flag(folder, uid, "\\Seen", True)
            _db_set_flags(account_id, folder, row["UID"], seen=True)
        except Exception as e:
            print(f"[FILTER] mark_read error {e}", flush=True)
    elif action == "spam":
        _apply_spam(account_id, folder, row, imap)
    elif action == "delete":
        try:
            imap.delete_message(folder, uid)
            _db_delete_messages(account_id, folder, [uid])
        except Exception as e:
            print(f"[FILTER] delete error {e}", flush=True)
    elif action == "move":
        dest = f.get("ActionFolder") or ""
        if dest and dest != folder:
            try:
                imap.move_message(folder, uid, dest)
                _db_move_message(account_id, folder, row["UID"], dest)
            except Exception as e:
                print(f"[FILTER] move error {e}", flush=True)


def apply_filters(account_id, folder, uids, imap):
    if not uids:
        return
    admin_uid = get_admin_user_id()
    global_filters = _load_filters(admin_uid, "GLOBAL") if admin_uid else []
    account_filters = []
    for uid_user in _linked_user_ids(account_id):
        account_filters += _load_filters(uid_user, "ACCOUNT", account_id)
    rows = _load_messages(account_id, folder, uids)
    dns_checked = 0
    processed = []
    for row in rows:
        matched = None
        for f in global_filters:
            if _eval_conditions(f.get("_conds"), row):
                matched = f
                break
        if matched is None:
            for f in account_filters:
                if _eval_conditions(f.get("_conds"), row):
                    matched = f
                    break
        if matched is not None:
            _apply_action(matched, account_id, folder, row, imap)
        if folder == "INBOX" and dns_checked < 20:
            ip = row.get("SenderIP")
            if ip:
                dns_checked += 1
                if check_dnsbl(ip):
                    _apply_spam(account_id, folder, row, imap)
        if row.get("BodyHtml") or row.get("BodyText"):
            processed.append(row["UID"])
    if processed:
        _db_mark_filtered_many(account_id, folder, processed)


def sweep_filters(account_id, imap, limit=500):
    from collections import OrderedDict

    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT Folder, UID FROM HUBMAIL_Messages "
            "WHERE AccountID=%s AND FilteredAt IS NULL "
            "AND (BodyHtml IS NOT NULL OR BodyText IS NOT NULL) "
            "ORDER BY CASE WHEN Folder='INBOX' THEN 0 ELSE 1 END, SyncedAt "
            "LIMIT %s",
            (account_id, limit),
        )
        groups = OrderedDict()
        for r in cur.fetchall():
            groups.setdefault(r["Folder"], []).append(int(r["UID"]))
    finally:
        conn.close()
    total = 0
    for folder, uids in groups.items():
        try:
            apply_filters(account_id, folder, uids, imap)
            total += len(uids)
        except Exception as e:
            print(f"[FILTER] sweep {account_id}/{folder}: {e}", flush=True)
    return total