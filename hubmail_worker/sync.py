import email
import email.utils
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape

from .almacen import get_almacen
from .config import settings
from .crypto import decrypt_secret
from .db import get_conn
from .filters import apply_filters, extract_sender_ip, sweep_filters
from .imap_client import (
    IMAPClient,
    IMAPError,
    _decode_mime,
    _decode_payload,
    _parse_addresses,
    _parse_folder_line,
)

SYNC_INTERVAL = 300  # 5 minutos

RETENTION_MAX_KEEP = 10
RETENTION_MAX_DAYS = 7
RETENTION_FOLDER_PARTS = [
    "junk", "spam", "bulk", "trash", "deleted",
    "papelera", "basura", "no deseado", "elementos eliminados", "correo no deseado",
]

_locks_guard = threading.Lock()
_folder_locks = {}
_account_cooldown = {}
_account_busy = {}

_sync_progress = {}
_sync_progress_guard = threading.Lock()


def _set_progress(account_id, **kw):
    with _sync_progress_guard:
        p = _sync_progress.setdefault(account_id, {})
        p.update(kw)
        p["last_update"] = datetime.now()


def get_sync_progress():
    with _sync_progress_guard:
        return {aid: dict(p) for aid, p in _sync_progress.items()}


def _log_sync_error(account_id, folder, error):
    if not error:
        return
    text = str(error)[:2000]
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUBMAIL_SyncErrors (AccountID, Folder, Error) VALUES (%s,%s,%s)",
            (account_id, folder, text),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _account_in_use(account_id):
    return _account_busy.get(account_id, False)


def _cooldown_active(account_id):
    cd = _account_cooldown.get(account_id)
    return cd is not None and datetime.now() < cd


def _mark_connected(account_id):
    _account_cooldown[account_id] = datetime.now() + timedelta(seconds=SYNC_INTERVAL)


def _safe(value):
    return value


def _addr_json(items):
    return json.dumps([{"name": it.get("name", ""), "email": it.get("email", "")} for it in items], ensure_ascii=False)


def _addr_from_json(text):
    try:
        return json.loads(text or "[]")
    except Exception:
        return []


def _fmt_dt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def parse_email(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    body_html = ""
    body_text = ""
    attachments = 0
    attachment_items = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ct = part.get_content_type()
        disp = part.get_content_disposition() or ""
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        fn = part.get_filename()
        if disp == "attachment" or (fn and not disp):
            attachments += 1
            attachment_items.append({
                "name": _decode_mime(fn) or "adjunto",
                "content_type": ct,
                "cid": "",
                "size": len(payload),
                "data": payload,
            })
        elif ct.startswith("image/") and disp == "inline":
            cid = part.get("Content-ID")
            cid_clean = cid.strip("<>") if cid else ""
            attachment_items.append({
                "name": _decode_mime(fn) or cid_clean or "inline",
                "content_type": ct,
                "cid": cid_clean,
                "size": len(payload),
                "data": payload,
            })
        elif ct == "text/html" and not body_html:
            body_html = _decode_payload(payload, part.get_content_charset())
        elif ct == "text/plain" and not body_text:
            body_text = _decode_payload(payload, part.get_content_charset())
    if not body_html and body_text:
        body_html = "<pre>" + escape(body_text) + "</pre>"

    date = None
    try:
        if msg.get("Date"):
            date = _fmt_dt(parsedate_to_datetime(msg.get("Date")))
    except Exception:
        date = None

    from_list = _parse_addresses(msg.get("From"))
    return {
        "from_name": from_list[0]["name"] if from_list else "",
        "from_email": from_list[0]["email"] if from_list else "",
        "to_text": _addr_json(_parse_addresses(msg.get("To"))),
        "cc_text": _addr_json(_parse_addresses(msg.get("Cc"))),
        "subject": _decode_mime(msg.get("Subject")),
        "date": date,
        "message_id": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "body_html": body_html,
        "body_text": body_text,
        "attachments": attachments,
        "attachment_items": attachment_items,
        "size": len(raw_bytes),
    }


def _get_account_row(account_id):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("SELECT * FROM HUBMAIL_Accounts WHERE AccountID=%s", (account_id,))
        return cur.fetchone()
    finally:
        conn.close()


def canonical_account_id(account_id):
    row = _get_account_row(account_id)
    if row and row.get("CanonicalAccountID"):
        return int(row["CanonicalAccountID"])
    return account_id


def canonical_account_row(account_id):
    row = _get_account_row(account_id)
    if row and row.get("CanonicalAccountID"):
        cid = int(row["CanonicalAccountID"])
        if cid != account_id:
            crow = _get_account_row(cid)
            if crow:
                return crow
    return row


def _load_existing(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT m.UID, m.BodyHtml, m.HasAttachments, "
            "COALESCE(a.AttCount, 0) AS AttCount "
            "FROM HUBMAIL_Messages m "
            "LEFT JOIN ("
            "  SELECT AccountID, Folder, UID, COUNT(*) AS AttCount "
            "  FROM HUBMAIL_Attachments "
            "  WHERE AccountID=%s AND Folder=%s "
            "  GROUP BY AccountID, Folder, UID"
            ") a ON a.AccountID=m.AccountID AND a.Folder=m.Folder AND a.UID=m.UID "
            "WHERE m.AccountID=%s AND m.Folder=%s",
            (account_id, folder, account_id, folder),
        )
        result = {}
        for r in cur.fetchall():
            uid = int(r["UID"])
            needs_att = bool(r["HasAttachments"]) and (r["AttCount"] or 0) == 0
            result[uid] = {
                "has_body": bool(r["BodyHtml"]),
                "needs_full": needs_att,
            }
        return result
    finally:
        conn.close()


def _sync_state(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT LastSync, TotalCount FROM HUBMAIL_SyncState WHERE AccountID=%s AND Folder=%s",
            (account_id, folder),
        )
        row = cur.fetchone()
        if not row:
            return None, 0
        last = row["LastSync"]
        if last is not None and last.tzinfo is not None:
            last = last.replace(tzinfo=None)
        return last, row["TotalCount"] or 0
    finally:
        conn.close()


def _update_sync_state(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT COUNT(*) AS N FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s",
            (account_id, folder),
        )
        total = cur.fetchone()["N"]
        cur.execute(
            "SELECT COUNT(*) AS N FROM HUBMAIL_SyncState WHERE AccountID=%s AND Folder=%s",
            (account_id, folder),
        )
        if cur.fetchone()["N"]:
            cur.execute(
                "UPDATE HUBMAIL_SyncState SET LastSync=NOW(), TotalCount=%s "
                "WHERE AccountID=%s AND Folder=%s",
                (total, account_id, folder),
            )
        else:
            cur.execute(
                "INSERT INTO HUBMAIL_SyncState (AccountID, Folder, LastSync, TotalCount) VALUES (%s,%s,NOW(),%s)",
                (account_id, folder, total),
            )
        conn.commit()
    finally:
        conn.close()


def _touch_sync_state(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "UPDATE HUBMAIL_SyncState SET LastSync=NOW() "
            "WHERE AccountID=%s AND Folder=%s",
            (account_id, folder),
        )
        conn.commit()
    finally:
        conn.close()


def _folder_db_count(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT COUNT(*) AS N FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s",
            (account_id, folder),
        )
        return cur.fetchone()["N"]
    finally:
        conn.close()


def _attach_dir_abs(account_id, folder, uid):
    """Ruta ABSOLUTA donde se escribe el archivo (dentro de este contenedor)."""
    return os.path.join(settings.attachments_dir, str(account_id), folder, str(uid))


def _attach_dir_rel(account_id, folder, uid):
    """Ruta RELATIVA que se guarda en HUBMAIL_Attachments.FilePath.

    OJO — por qué relativa y no absoluta. El worker escribe los adjuntos en su
    propio volumen, pero la app (Mailbox) los lee desde el suyo: si en la base
    quedara la ruta absoluta del worker (`/data/attachments/...` dentro de
    workersadmon), la app buscaría ese mismo texto en SU filesystem y no lo
    encontraría. `os.path.isfile()` devolvería False sin error y los adjuntos
    saldrían vacíos, en silencio. Guardando la ruta relativa, cada lado la
    resuelve con SU `attachments_dir` y el volumen se puede montar donde sea.
    """
    return "/".join([str(account_id), folder, str(uid)])


def normalizar_adjunto(file_path, base_dir=None):
    """Pasa un FilePath de la BD a la ruta RELATIVA canónica, o None.

    Acepta las tres formas que hay en la base:

    * relativa ya canónica (`12/INBOX/340/0_foto.jpg`) → se queda igual;
    * absoluta legacy (`/data/attachments/12/INBOX/...`) → se le quita el
      prefijo `attachments_dir`. La escribía HUBMail antes de que las rutas
      fueran relativas, y hay miles de filas así.

    Devolver la relativa (y no una ruta local) es lo que permite cambiar el
    destino —disco o SMB— sin tocar la base: la app siempre termina pidiendo
    los bytes con `leer_adjunto()`, que no sabe (ni necesita saber) dónde están.
    """
    if not file_path:
        return None
    if not os.path.isabs(file_path):
        return file_path
    base = (base_dir or settings.attachments_dir).rstrip("/") + "/"
    if file_path.startswith(base):
        return file_path[len(base):]
    # absoluta con otro prefijo (p.ej. un attachments_dir distinto al actual):
    # se recorta hasta el primer componente numérico, que es el AccountID.
    partes = file_path.lstrip("/").split("/")
    for i, parte in enumerate(partes):
        if parte.isdigit():
            return "/".join(partes[i:])
    return None


def leer_adjunto(file_path, base_dir=None):
    """Devuelve los BYTES de un adjunto, o None si no está.

    **Esta es la función que debe usar la app**, no un `open()` sobre la ruta:
    con el destino en SMB no existe tal ruta local. Va al almacén activo, así
    que funciona igual con disco o con SMB.
    """
    rel = normalizar_adjunto(file_path, base_dir)
    if rel is None:
        return None
    return get_almacen().leer(rel)


def resolver_adjunto(file_path, base_dir=None):
    """Ruta LOCAL de un adjunto, o None.

    Solo tiene sentido con el almacén de disco. Se conserva para herramientas y
    diagnósticos; la app debe usar `leer_adjunto()`.
    """
    rel = normalizar_adjunto(file_path, base_dir)
    if rel is None:
        return None
    base = base_dir or settings.attachments_dir
    directa = os.path.join(base, rel.replace("/", os.sep))
    return directa if os.path.isfile(directa) else None


def _replace_attachments(account_id, folder, uid, items):
    if not items:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM HUBMAIL_Attachments WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (account_id, folder, uid),
        )
        rows = []
        almacen = get_almacen()
        for i, a in enumerate(items):
            fname = _safe(a["name"]) or f"att_{i}"
            # Sanitizar nombre de archivo
            safe_fname = "".join(c if c.isalnum() or c in "._-" else "_" for c in fname)
            # Lo que va a la BD es la RELATIVA (ver _attach_dir_rel). El mismo
            # texto sirve para el disco y para SMB: lo que cambia es la base
            # contra la que se resuelve, no la ruta guardada.
            file_path = f"{_attach_dir_rel(account_id, folder, uid)}/{i}_{safe_fname}"
            try:
                almacen.escribir(file_path, a["data"])
            except Exception as e:
                print(f"[SYNC] error guardando attachment {file_path}: {e}", flush=True)
                file_path = ""
            rows.append(
                (account_id, folder, uid, _safe(a["name"]), _safe(a["content_type"]),
                 _safe(a["cid"]), a["size"], file_path)
            )
        if rows:
            cur.executemany(
                "INSERT INTO HUBMAIL_Attachments (AccountID, Folder, UID, Name, ContentType, Cid, Size, FilePath) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _upsert_message(account_id, folder, uid, flags, raw):
    p = parse_email(raw)
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT COUNT(*) AS N FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (account_id, folder, uid),
        )
        exists = cur.fetchone()["N"] > 0
        base = [
            _safe(p["message_id"]), _safe(p["in_reply_to"]),
            _safe(p["from_name"]), _safe(p["from_email"]),
            _safe(p["to_text"]), _safe(p["cc_text"]),
            _safe(p["subject"]), p["date"],
            1 if "\\Seen" in flags else 0,
            1 if "\\Answered" in flags else 0,
            1 if "\\Flagged" in flags else 0,
            1 if "\\Draft" in flags else 0,
            1 if p["attachments"] else 0,
            _safe(p["body_html"]), _safe(p["body_text"]), p["size"],
        ]
        if exists:
            cur.execute(
                """UPDATE HUBMAIL_Messages SET
                       MessageIdHeader=%s, InReplyTo=%s, FromName=%s, FromEmail=%s,
                       ToText=%s, CcText=%s, Subject=%s, DateSent=%s,
                       Seen=%s, Answered=%s, Flagged=%s, Draft=%s,
                       HasAttachments=%s, BodyHtml=%s, BodyText=%s, Size=%s,
SyncedAt=NOW()
                   WHERE AccountID=%s AND Folder=%s AND UID=%s""",
                base + [account_id, folder, uid],
            )
        else:
            cur.execute(
                """INSERT INTO HUBMAIL_Messages
                       (AccountID, Folder, UID, MessageIdHeader, InReplyTo, FromName, FromEmail,
                        ToText, CcText, Subject, DateSent, Seen, Answered, Flagged, Deleted, Draft,
                        HasAttachments, BodyHtml, BodyText, Size)
                   VALUES
                       (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s)""",
                [account_id, folder, uid] + base,
            )
        conn.commit()
    finally:
        conn.close()
    _replace_attachments(account_id, folder, uid, p["attachment_items"])


def _insert_many(account_id, folder, items):
    if not items:
        return 0
    conn = get_conn()
    try:
        cur = conn.cursor()
        data = []
        att_rows = []
        for uid, meta, raw in items:
            try:
                p = parse_email(raw)
            except Exception as e:
                print(f"[SYNC] parse skip uid={uid}: {e}", flush=True)
                continue
            data.append([
                account_id, folder, uid,
                _safe(p["message_id"]), _safe(p["in_reply_to"]),
                _safe(p["from_name"]), _safe(p["from_email"]),
                _safe(p["to_text"]), _safe(p["cc_text"]),
                _safe(p["subject"]), p["date"],
                1 if "\\Seen" in meta else 0,
                1 if "\\Answered" in meta else 0,
                1 if "\\Flagged" in meta else 0,
                1 if "\\Draft" in meta else 0,
                1 if p["attachments"] else 0,
                _safe(p["body_html"]), _safe(p["body_text"]), p["size"],
                extract_sender_ip(raw),
            ])
            for a in p["attachment_items"]:
                att_rows.append(
                    (account_id, folder, uid, _safe(a["name"]), _safe(a["content_type"]),
                     _safe(a["cid"]), a["size"], a["data"])
                )
        if not data:
            return 0
        sql = """INSERT INTO HUBMAIL_Messages
               (AccountID, Folder, UID, MessageIdHeader, InReplyTo, FromName, FromEmail,
                ToText, CcText, Subject, DateSent, Seen, Answered, Flagged, Deleted, Draft,
                HasAttachments, BodyHtml, BodyText, Size, SenderIP)
               VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s)"""
        ok = 0
        try:
            cur.executemany(sql, data)
            ok = len(data)
        except Exception as e:
            print(f"[SYNC] batch insert falló ({e}), reintento por fila", flush=True)
            conn.rollback()
            cur = conn.cursor()
            for row in data:
                try:
                    cur.execute(sql, row)
                    ok += 1
                except Exception as e2:
                    print(f"[SYNC] fila rechazada uid={row[2]}: {e2}", flush=True)
        conn.commit()
        if att_rows:
            _save_att_rows_to_fs(account_id, folder, att_rows)
        return ok
    finally:
        conn.close()


def _save_att_rows_to_fs(account_id, folder, att_rows):
    """Guarda att_rows (tuplas con datos binarios) en filesystem."""
    from collections import defaultdict
    by_uid = defaultdict(list)
    for row in att_rows:
        uid = row[2]
        by_uid[uid].append(row)
    almacen = get_almacen()
    for uid, rows in by_uid.items():
        conn = get_conn()
        try:
            cur = conn.cursor()
            for i, row in enumerate(rows):
                fname = row[3] or f"att_{i}"
                safe_fname = "".join(c if c.isalnum() or c in "._-" else "_" for c in fname)
                # Relativa en la BD, igual que en _replace_attachments.
                file_path = f"{_attach_dir_rel(account_id, folder, uid)}/{i}_{safe_fname}"
                try:
                    almacen.escribir(file_path, row[7])   # Data
                except Exception as e:
                    print(f"[SYNC] error guardando attachment {file_path}: {e}", flush=True)
                    file_path = ""
                cur.execute(
                    "INSERT INTO HUBMAIL_Attachments (AccountID, Folder, UID, Name, ContentType, Cid, Size, FilePath) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (account_id, folder, uid, row[3], row[4], row[5], row[6], file_path),
                )
            conn.commit()
        finally:
            conn.close()


def _update_body_many(account_id, folder, items):
    if not items:
        return 0
    conn = get_conn()
    try:
        cur = conn.cursor()
        data = []
        att_rows = []
        for uid, meta, raw in items:
            try:
                p = parse_email(raw)
            except Exception as e:
                print(f"[SYNC] parse skip uid={uid}: {e}", flush=True)
                continue
            data.append([
                _safe(p["message_id"]), _safe(p["in_reply_to"]),
                _safe(p["from_name"]), _safe(p["from_email"]),
                _safe(p["to_text"]), _safe(p["cc_text"]),
                _safe(p["subject"]), p["date"],
                1 if "\\Seen" in meta else 0,
                1 if "\\Answered" in meta else 0,
                1 if "\\Flagged" in meta else 0,
                1 if "\\Draft" in meta else 0,
                1 if p["attachments"] else 0,
                _safe(p["body_html"]), _safe(p["body_text"]), p["size"],
                account_id, folder, uid,
            ])
            for a in p["attachment_items"]:
                att_rows.append(
                    (account_id, folder, uid, _safe(a["name"]), _safe(a["content_type"]),
                     _safe(a["cid"]), a["size"], a["data"])
                )
        if not data:
            return 0
        sql = """UPDATE HUBMAIL_Messages SET
               MessageIdHeader=%s, InReplyTo=%s, FromName=%s, FromEmail=%s,
               ToText=%s, CcText=%s, Subject=%s, DateSent=%s,
               Seen=%s, Answered=%s, Flagged=%s, Draft=%s,
               HasAttachments=%s, BodyHtml=%s, BodyText=%s, Size=%s,
               SyncedAt=NOW()
               WHERE AccountID=%s AND Folder=%s AND UID=%s"""
        ok = 0
        try:
            cur.executemany(sql, data)
            ok = len(data)
        except Exception as e:
            print(f"[SYNC] batch update falló ({e}), reintento por fila", flush=True)
            conn.rollback()
            cur = conn.cursor()
            for row in data:
                try:
                    cur.execute(sql, row)
                    ok += 1
                except Exception as e2:
                    print(f"[SYNC] fila rechazada uid={row[-1]}: {e2}", flush=True)
        conn.commit()
        uids = sorted({r[2] for r in att_rows})
        for uid in uids:
            rows = [r for r in att_rows if r[2] == uid]
            try:
                _replace_attachments(account_id, folder, uid, [
                    {"name": r[3], "content_type": r[4], "cid": r[5], "size": r[6], "data": r[7]}
                    for r in rows
                ])
            except Exception as e:
                print(f"[SYNC] adjuntos uid={uid}: {e}", flush=True)
        return ok
    finally:
        conn.close()


def _update_flags(account_id, folder, flags_map):
    if not flags_map:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = [
            (
                1 if "\\Seen" in fl else 0,
                1 if "\\Flagged" in fl else 0,
                1 if "\\Answered" in fl else 0,
                1 if "\\Draft" in fl else 0,
                account_id, folder, uid,
            )
            for uid, fl in flags_map.items()
        ]
        cur.executemany(
            "UPDATE HUBMAIL_Messages SET Seen=%s, Flagged=%s, Answered=%s, Draft=%s "
            "WHERE AccountID=%s AND Folder=%s AND UID=%s",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _sync_folder_conn(account_id, folder, imap, force=False, with_bodies=True):
    key = (account_id, folder)
    with _locks_guard:
        lock = _folder_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        print(f"[SYNC] skip {account_id}/{folder}: lock ocupado", flush=True)
        return {"new": 0, "updated": 0, "total": 0}

    try:
        last, total = _sync_state(account_id, folder)
        fresh = last is not None and (datetime.now() - last).total_seconds() < SYNC_INTERVAL \
            and _folder_db_count(account_id, folder) == total
        if not with_bodies and fresh and not force:
            try:
                _touch_sync_state(account_id, folder)
            except Exception as e:
                print(f"[SYNC] touch syncstate {account_id}/{folder}: {e}", flush=True)
            return {"new": 0, "updated": 0, "total": total}

        try:
            uids = imap.fetch_uid_list(folder)
            flags_map = imap.fetch_flags_map(folder, uids)
            print(f"[SYNC] {account_id}/{folder}: uids={len(uids)}", flush=True)
        except IMAPError as e:
            print(f"[SYNC] {account_id}/{folder}: error IMAP uid/flags: {e}", flush=True)
            _log_sync_error(account_id, folder, e)
            return {"new": 0, "updated": 0, "total": 0}

        existing = _load_existing(account_id, folder)
        pending = _pending_uids(account_id, folder)
        new_uids = [u for u in uids if u not in existing and u not in pending]
        missing_body = [u for u in uids if u in existing and (not existing[u]["has_body"] or existing[u]["needs_full"]) and u not in pending]

        new = updated = 0
        if new_uids:
            try:
                it = imap.iter_headers_many(folder, new_uids) if not with_bodies \
                    else imap.iter_full_many(folder, new_uids)
                for chunk in it:
                    new += _insert_many(account_id, folder, chunk)
            except Exception as e:
                print(f"[SYNC] error fetch new {account_id}/{folder}: {e}", flush=True)
                _log_sync_error(account_id, folder, f"fetch new: {e}")
        if missing_body and with_bodies:
            try:
                for chunk in imap.iter_full_many(folder, missing_body):
                    updated += _update_body_many(account_id, folder, chunk)
            except Exception as e:
                print(f"[SYNC] error fetch body {account_id}/{folder}: {e}", flush=True)
                _log_sync_error(account_id, folder, f"fetch body: {e}")
        if with_bodies and (new_uids or missing_body):
            try:
                apply_filters(account_id, folder, new_uids + missing_body, imap)
            except Exception as e:
                print(f"[SYNC] filtros {account_id}/{folder}: {e}", flush=True)
        try:
            flags_map = {u: fl for u, fl in flags_map.items() if u not in pending}
            _update_flags(account_id, folder, flags_map)
        except Exception as e:
            print(f"[SYNC] error flags {account_id}/{folder}: {e}", flush=True)
        try:
            _update_sync_state(account_id, folder)
        except Exception as e:
            print(f"[SYNC] error syncstate {account_id}/{folder}: {e}", flush=True)
        if new > 0 and folder == "INBOX":
            try:
                _push_new_mail(account_id, new_uids)
            except Exception as e:
                print(f"[SYNC] error push {account_id}: {e}", flush=True)
        print(f"[SYNC] {account_id}/{folder}: fin new={new} updated={updated} total={len(uids)}", flush=True)
        return {"new": new, "updated": updated, "total": len(uids)}
    finally:
        lock.release()


def _push_new_mail(account_id, new_uids):
    from .push import notify_new_mail

    if not new_uids:
        return
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT DISTINCT UserID FROM HUBMAIL_Accounts "
            "WHERE AccountID=%s OR CanonicalAccountID=%s",
            (account_id, account_id),
        )
        user_ids = [r["UserID"] for r in cur.fetchall()]
        cur.execute(
            "SELECT FromName, FromEmail, Subject FROM HUBMAIL_Messages "
            "WHERE AccountID=%s AND Folder='INBOX' AND UID IN (%s) "
            "ORDER BY DateSent DESC LIMIT 1"
            % (account_id, ",".join("%s" for _ in new_uids)),
            tuple(new_uids),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return
    sender = row["FromName"] or row["FromEmail"] or "Nuevo correo"
    subject = row["Subject"] or "(sin asunto)"
    notify_new_mail(user_ids, f"HUBMail · {sender}", subject)


def sync_folder(account_id, folder, force=False, with_bodies=True):
    account_id = canonical_account_id(account_id)
    if _account_in_use(account_id):
        total = _folder_db_count(account_id, folder)
        try:
            _touch_sync_state(account_id, folder)
        except Exception as e:
            print(f"[SYNC] touch syncstate {account_id}/{folder}: {e}", flush=True)
        return {"new": 0, "updated": 0, "total": total, "throttled": True}
    if _cooldown_active(account_id) and not force:
        total = _folder_db_count(account_id, folder)
        try:
            _touch_sync_state(account_id, folder)
        except Exception as e:
            print(f"[SYNC] touch syncstate {account_id}/{folder}: {e}", flush=True)
        return {"new": 0, "updated": 0, "total": total, "throttled": True}

    acc = _get_account_row(account_id)
    if not acc:
        return {"error": "cuenta no existe"}

    _mark_connected(account_id)
    imap = IMAPClient(acc["IMAPHost"], acc["IMAPPort"], acc["Username"], decrypt_secret(acc["PasswordEnc"]))
    try:
        imap.connect()
        return _sync_folder_conn(account_id, folder, imap, force=force, with_bodies=with_bodies)
    finally:
        imap.close()


def _is_retention_folder(folder):
    low = (folder or "").lower()
    return any(p in low for p in RETENTION_FOLDER_PARTS)


def _run_retention(account_id, imap):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("SELECT LastRun FROM HUBMAIL_Retention WHERE AccountID=%s", (account_id,))
        row = cur.fetchone()
        if row and row["LastRun"]:
            if (datetime.now() - row["LastRun"]).total_seconds() < 86400:
                return {"skipped": True}
        cur.execute("SELECT Folder FROM HUBMAIL_Folders WHERE AccountID=%s", (account_id,))
        target_folders = [r["Folder"] for r in cur.fetchall() if _is_retention_folder(r["Folder"])]
        if not target_folders:
            _upsert_retention(cur, account_id)
            conn.commit()
            return {"folders": 0, "deleted": 0}
        cutoff = datetime.now() - timedelta(days=RETENTION_MAX_DAYS)
        deleted_total = 0
        for folder in target_folders:
            cur.execute(
                "SELECT UID, DateSent FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s",
                (account_id, folder),
            )
            rows = cur.fetchall()
            if not rows:
                continue
            rows.sort(key=lambda r: (r["DateSent"] or datetime.min), reverse=True)
            keep = rows[:RETENTION_MAX_KEEP]
            keep_ids = set(int(r["UID"]) for r in keep if (r["DateSent"] or datetime.min) >= cutoff)
            to_delete = [r for r in rows if int(r["UID"]) not in keep_ids]
            if not to_delete:
                continue
            try:
                imap.delete_messages(folder, [str(int(r["UID"])) for r in to_delete])
            except Exception as e:
                print(f"[RETENTION] imap {account_id}/{folder}: {e}", flush=True)
            for r in to_delete:
                uid = int(r["UID"])
                cur.execute(
                    "DELETE FROM HUBMAIL_Attachments WHERE AccountID=%s AND Folder=%s AND UID=%s",
                    (account_id, folder, uid),
                )
                cur.execute(
                    "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
                    (account_id, folder, uid),
                )
            deleted_total += len(to_delete)
        _upsert_retention(cur, account_id)
        conn.commit()
        return {"folders": len(target_folders), "deleted": deleted_total}
    finally:
        conn.close()


def _upsert_retention(cur, account_id):
    cur.execute("SELECT 1 FROM HUBMAIL_Retention WHERE AccountID=%s", (account_id,))
    if cur.fetchone():
        cur.execute("UPDATE HUBMAIL_Retention SET LastRun=NOW() WHERE AccountID=%s", (account_id,))
    else:
        cur.execute("INSERT INTO HUBMAIL_Retention (AccountID, LastRun) VALUES (%s, NOW())", (account_id,))


def _save_folders(account_id, delimiter, folders):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM HUBMAIL_Folders WHERE AccountID=%s", (account_id,))
        seen = set()
        rows = []
        for f in folders:
            name = (f["name"] or "").rstrip()
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append((account_id, name, delimiter, ",".join(f.get("flags") or [])))
        if rows:
            cur.executemany(
                "INSERT INTO HUBMAIL_Folders (AccountID, Folder, Delimiter, Flags) VALUES (%s,%s,%s,%s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _enqueue_op(account_id, op_type, folder, uid, value=None, dest_folder=None, dest_account_id=None, msgid=None, raw_message=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO HUBMAIL_PendingOps (AccountID, OpType, Folder, UID, Value, DestFolder, DestAccountID, MsgId, RawMessage) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (account_id, op_type, folder, int(uid), value, dest_folder, dest_account_id, msgid, raw_message),
        )
        conn.commit()
    finally:
        conn.close()


def _set_op_status(op_id, status, error=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE HUBMAIL_PendingOps SET Status=%s, Error=%s WHERE OpID=%s",
            (status, error, op_id),
        )
        conn.commit()
    finally:
        conn.close()


def _db_update_flags(account_id, folder, uid, seen=None, flagged=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if seen is not None:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET Seen=%s WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (1 if seen else 0, account_id, folder, uid),
            )
        if flagged is not None:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET Flagged=%s WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (1 if flagged else 0, account_id, folder, uid),
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


def _db_get_message_id(account_id, folder, uid):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT MessageIdHeader FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (account_id, folder, uid),
        )
        row = cur.fetchone()
        return row["MessageIdHeader"] if row else None
    finally:
        conn.close()


def _db_move_message(account_id, folder, uid, dest, new_uid=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if new_uid:
            cur.execute(
                "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (account_id, dest, new_uid),
            )
            cur.execute(
                "UPDATE HUBMAIL_Messages SET Folder=%s, UID=%s "
                "WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (dest, new_uid, account_id, folder, uid),
            )
        else:
            cur.execute(
                "UPDATE HUBMAIL_Messages SET Folder=%s WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (dest, account_id, folder, uid),
            )
        conn.commit()
    finally:
        conn.close()


def _db_cross_move_message(src_account_id, src_folder, uid, dst_account_id, dst_folder, new_uid, seen):
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT MessageIdHeader, InReplyTo, FromName, FromEmail, ToText, CcText, Subject, "
            "DateSent, Answered, Flagged, HasAttachments, BodyHtml, BodyText, Size, Spam, SenderIP "
            "FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (src_account_id, src_folder, uid),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (dst_account_id, dst_folder, new_uid),
            )
            cur.execute(
                "INSERT INTO HUBMAIL_Messages (AccountID, Folder, UID, MessageIdHeader, InReplyTo, "
                "FromName, FromEmail, ToText, CcText, Subject, DateSent, Seen, Answered, Flagged, "
                "HasAttachments, BodyHtml, BodyText, Size, Spam, SenderIP) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (dst_account_id, dst_folder, new_uid, row["MessageIdHeader"], row["InReplyTo"],
                 row["FromName"], row["FromEmail"], row["ToText"], row["CcText"], row["Subject"],
                 row["DateSent"], seen, row["Answered"], row["Flagged"], row["HasAttachments"],
                 row["BodyHtml"], row["BodyText"], row["Size"], row["Spam"], row["SenderIP"]),
            )
            cur.execute(
                "DELETE FROM HUBMAIL_Attachments WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (dst_account_id, dst_folder, new_uid),
            )
            cur.execute(
                "SELECT Name, ContentType, Cid, Size, Data FROM HUBMAIL_Attachments "
                "WHERE AccountID=%s AND Folder=%s AND UID=%s",
                (src_account_id, src_folder, uid),
            )
            atts = cur.fetchall()
            if atts:
                cur.executemany(
                    "INSERT INTO HUBMAIL_Attachments (AccountID, Folder, UID, Name, ContentType, Cid, Size, Data) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (dst_account_id, dst_folder, new_uid, a["Name"], a["ContentType"], a["Cid"],
                         a["Size"], a["Data"])
                        for a in atts
                    ],
                )
        cur.execute(
            "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (src_account_id, src_folder, uid),
        )
        cur.execute(
            "DELETE FROM HUBMAIL_Attachments WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (src_account_id, src_folder, uid),
        )
        conn.commit()
    finally:
        conn.close()


def _pending_uids(account_id, folder):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT UID FROM HUBMAIL_PendingOps WHERE AccountID=%s AND Folder=%s AND Status='pending'",
            (account_id, folder),
        )
        return {int(r[0]) for r in cur.fetchall()}
    finally:
        conn.close()


def _fix_move_uid(account_id, dest_folder, msgid, new_uid):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM HUBMAIL_Messages WHERE AccountID=%s AND Folder=%s AND UID=%s",
            (account_id, dest_folder, new_uid),
        )
        cur.execute(
            "UPDATE HUBMAIL_Messages SET UID=%s WHERE AccountID=%s AND Folder=%s AND MessageIdHeader=%s",
            (new_uid, account_id, dest_folder, msgid),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_cross_move(src_account_id, op, simap):
    dst = _get_account_row(op["DestAccountID"])
    if not dst:
        raise IMAPError("Cuenta destino no existe")
    raw, flags = simap.fetch_raw_with_flags(op["Folder"], str(op["UID"]))
    dimap = IMAPClient(dst["IMAPHost"], dst["IMAPPort"], dst["Username"], decrypt_secret(dst["PasswordEnc"]))
    try:
        dimap.connect()
        new_uid = dimap.append_message(op["DestFolder"], raw, flags)
        simap.delete_message(op["Folder"], str(op["UID"]))
    finally:
        dimap.close()
    seen = 1 if "\\Seen" in flags else 0
    _db_cross_move_message(src_account_id, op["Folder"], op["UID"], op["DestAccountID"], op["DestFolder"], int(new_uid), seen)


def _sent_folder(account_id, folders):
    named = [f for f in folders if f["name"].lower() in ("sent", "sent items", "enviados", "enviado")]
    if named:
        return named[0]["name"]
    for f in folders:
        if "\\Sent" in (f.get("flags") or []):
            return f["name"]
    for f in folders:
        low = f["name"].lower()
        if "sent" in low or "enviad" in low:
            return f["name"]
    return "Sent"


def _apply_append_op(account_id, op, imap):
    raw = op.get("RawMessage")
    if not raw:
        raise IMAPError("Mensaje sin contenido para guardar en Enviados")
    lf = imap.list_folders()
    dest = op.get("DestFolder") or _sent_folder(account_id, lf["folders"])
    new_uid = imap.append_message(dest, raw, ["\\Seen"])
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT 1 FROM HUBMAIL_Folders WHERE AccountID=%s AND Folder=%s",
            (account_id, dest),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO HUBMAIL_Folders (AccountID, Folder, Delimiter, Flags) VALUES (%s,%s,%s,%s)",
                (account_id, dest, lf["delimiter"], "\\Sent"),
            )
        conn.commit()
    finally:
        conn.close()


def _apply_pending_ops(account_id, imap):
    """Drena la cola de operaciones pendientes y las ejecuta contra IMAP.

    Este es EL punto donde el worker escribe en el buzón. Con
    HUBMAIL_SYNC_DRYRUN=1 se salta entero: la cola se sigue acumulando (la app
    la sigue llenando) pero nadie la ejecuta contra IMAP. Es el modo para
    probar este worker en paralelo al de HUBMail sin que los dos escriban.
    """
    if settings.dry_run:
        return
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT * FROM HUBMAIL_PendingOps WHERE AccountID=%s AND Status='pending' ORDER BY OpID",
            (account_id,),
        )
        ops = cur.fetchall()
    finally:
        conn.close()
    for op in ops:
        try:
            if op["OpType"] in ("seen", "flag"):
                flag = "\\Seen" if op["OpType"] == "seen" else "\\Flagged"
                imap.set_flag(op["Folder"], str(op["UID"]), flag, bool(op["Value"]))
            elif op["OpType"] == "delete":
                imap.delete_message(op["Folder"], str(op["UID"]))
            elif op["OpType"] == "move":
                imap.move_message(op["Folder"], str(op["UID"]), op["DestFolder"])
                new_uid = None
                if op.get("MsgId"):
                    new_uid = imap.find_uid_by_message_id(op["DestFolder"], op["MsgId"])
                if not new_uid:
                    new_uid = imap.last_uid(op["DestFolder"])
                if op.get("MsgId"):
                    _fix_move_uid(account_id, op["DestFolder"], op["MsgId"], int(new_uid))
                else:
                    _db_move_message(account_id, op["Folder"], op["UID"], op["DestFolder"], int(new_uid))
            elif op["OpType"] == "cross_move":
                _apply_cross_move(account_id, op, imap)
            elif op["OpType"] == "append":
                _apply_append_op(account_id, op, imap)
            _set_op_status(op["OpID"], "done")
        except Exception as e:
            print(f"[SYNC] op {op['OpID']} ({op['OpType']}) falló: {e}", flush=True)
            _set_op_status(op["OpID"], "failed", str(e)[:500])


def sync_account(account_id):
    account_id = canonical_account_id(account_id)
    acc = _get_account_row(account_id)
    if not acc:
        return None

    started = datetime.now()
    _mark_connected(account_id)
    _account_busy[account_id] = True
    _set_progress(
        account_id, status="syncing", started_at=started,
        finished_at=None, duration=None, error=None,
        current_folder=None, folder_index=0, folder_count=0,
    )
    imap = IMAPClient(acc["IMAPHost"], acc["IMAPPort"], acc["Username"], decrypt_secret(acc["PasswordEnc"]))
    try:
        imap.connect()
        try:
            _apply_pending_ops(account_id, imap)
        except Exception as e:
            msg = f"ops pendientes: {e}"
            print(f"[SYNC] account {account_id}: error aplicando ops pendientes: {e}", flush=True)
            _log_sync_error(account_id, None, msg)
        lf = imap.list_folders()
        folders = lf["folders"]
        _save_folders(account_id, lf["delimiter"], folders)
    except IMAPError as e:
        print(f"[SYNC] account {account_id}: error conexión/listado: {e}", flush=True)
        _log_sync_error(account_id, None, e)
        imap.close()
        _account_busy[account_id] = False
        _set_progress(
            account_id, status="error", finished_at=datetime.now(),
            duration=round((datetime.now() - started).total_seconds(), 1), error=str(e)[:500],
        )
        return None
    except Exception as e:
        msg = f"conexión/listado: {e}"
        print(f"[SYNC] account {account_id}: error {msg}", flush=True)
        _log_sync_error(account_id, None, str(e)[:2000])
        imap.close()
        _account_busy[account_id] = False
        _set_progress(
            account_id, status="error", finished_at=datetime.now(),
            duration=round((datetime.now() - started).total_seconds(), 1), error=str(e)[:500],
        )
        return None
    result = {"new": 0, "updated": 0, "total": 0}
    total_folders = len(folders)
    _set_progress(account_id, folder_count=total_folders)
    try:
        for idx, f in enumerate(folders, start=1):
            _set_progress(account_id, folder_index=idx, current_folder=f["name"])
            try:
                r = _sync_folder_conn(account_id, f["name"], imap)
                result["new"] += r.get("new", 0)
                result["updated"] += r.get("updated", 0)
                result["total"] += r.get("total", 0)
            except Exception as e:
                print(f"[SYNC] error folder {account_id}/{f['name']}: {e}", flush=True)
                _log_sync_error(account_id, f["name"], e)
                imap.close()
                continue
        try:
            retention = _run_retention(account_id, imap)
            if retention and not retention.get("skipped"):
                print(f"[RETENTION] {account_id}: {retention}", flush=True)
        except Exception as e:
            msg = f"retención: {e}"
            print(f"[RETENTION] error {account_id}: {e}", flush=True)
            _log_sync_error(account_id, None, msg)
        try:
            swept = sweep_filters(account_id, imap)
            if swept:
                print(f"[SYNC] filtros sweep {account_id}: {swept} mensajes", flush=True)
        except Exception as e:
            print(f"[FILTER] sweep error {account_id}: {e}", flush=True)
    finally:
        imap.close()
        _account_busy[account_id] = False
    _set_progress(
        account_id, status="ok", finished_at=datetime.now(),
        duration=round((datetime.now() - started).total_seconds(), 1),
        folder_index=total_folders, current_folder=None,
    )
    return result


_SYNC_MAX_ACCOUNTS = 4


def _sync_account_safe(account_id):
    try:
        sync_account(account_id)
    except Exception as e:
        print(f"[SYNC] cuenta {account_id}: error {e}", flush=True)


def sync_all_accounts():
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("SELECT AccountID FROM HUBMAIL_Accounts WHERE CanonicalAccountID IS NULL")
        ids = [r["AccountID"] for r in cur.fetchall()]
    finally:
        conn.close()
    if not ids:
        return
    sem = threading.BoundedSemaphore(_SYNC_MAX_ACCOUNTS)
    threads = []

    def _run(aid):
        with sem:
            _sync_account_safe(aid)

    for aid in ids:
        t = threading.Thread(target=_run, args=(aid,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()