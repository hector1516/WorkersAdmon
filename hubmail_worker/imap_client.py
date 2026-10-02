import codecs
import email
import email.utils
import imaplib
import base64
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import escape


class IMAPError(Exception):
    pass


_UID_RE = re.compile(r"UID\s+(\d+)")
_FLAGS_RE = re.compile(r"FLAGS\s*\((.*?)\)")


def _extract_uid(meta):
    m = _UID_RE.search(meta)
    return int(m.group(1)) if m else None


def _utf7_encode(name: str) -> str:
    try:
        return codecs.encode(name, "imap4-utf-7")
    except Exception:
        return name


def _q(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _utf7_decode(name: str) -> str:
    try:
        return codecs.decode(name, "imap4-utf-7")
    except Exception:
        return name


def _decode_mime(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_addresses(value):
    if not value:
        return []
    result = []
    for name, addr in email.utils.getaddresses([value]):
        if addr:
            result.append({"name": _decode_mime(name), "email": addr})
    return result


def _fmt_date(value):
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=-6))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def _decode_payload(payload, charset):
    for enc in (charset, "utf-8", "latin-1"):
        if enc:
            try:
                return payload.decode(enc)
            except Exception:
                continue
    return payload.decode("utf-8", "replace")


def _parse_folder_line(line):
    flags = []
    rest = line
    if rest.startswith("("):
        end = rest.find(")")
        flags = rest[1:end].split()
        rest = rest[end + 1:].strip()
    parts = rest.split(" ", 1)
    delimiter = ""
    name = parts[0]
    if len(parts) > 1:
        delim_raw = parts[0].strip('"')
        delimiter = delim_raw if delim_raw and delim_raw != "NIL" else ""
        name = parts[1]
    name = name.strip().strip('"').replace('\\"', '"')
    return flags, delimiter, name


class IMAPClient:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self._conn = None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        try:
            conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
        except Exception as e:
            raise IMAPError(f"No se pudo conectar a IMAP ({self.host}): {e}")
        try:
            conn.login(self.username, self.password)
        except Exception:
            raise IMAPError("Credenciales IMAP incorrectas o cuenta bloqueada")
        self._conn = conn
        return conn

    def connect(self):
        return self._connect()

    def close(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def list_folders(self):
        conn = self._connect()
        typ, data = conn.list()
        if typ != "OK":
            raise IMAPError("Error al listar carpetas")
        folders = []
        seen = set()
        delimiter = ""
        for line in data:
            try:
                s = line.decode("utf-8", "replace")
            except Exception:
                s = line.decode("latin-1", "replace")
            flags, sep, name = _parse_folder_line(s)
            if not delimiter and sep:
                delimiter = sep
            name = _utf7_decode(name).rstrip()
            if not name or name in seen:
                continue
            seen.add(name)
            folders.append({"name": name, "flags": flags})
        return {"delimiter": delimiter, "folders": folders}

    def list_messages(self, folder="INBOX", criteria="ALL", page=1, page_size=25, unread_only=False):
        conn = self._connect()
        try:
            typ, _ = conn.select(_q(_utf7_encode(folder)), readonly=True)
            if typ != "OK":
                raise IMAPError(f"No se pudo abrir la carpeta {folder}")
            search_criteria = "UNSEEN" if unread_only else criteria
            typ, data = conn.uid("search", None, search_criteria)
            if typ != "OK":
                raise IMAPError("Error en la búsqueda")
            ids = data[0].split()
            total = len(ids)
            ids = ids[::-1]
            start = (page - 1) * page_size
            chunk = ids[start:start + page_size]
            messages = []
            for mid in chunk:
                typ, d = conn.uid("fetch", mid, "(UID FLAGS RFC822.HEADER)")
                if typ != "OK" or not d or not isinstance(d[0], tuple):
                    continue
                meta, raw = d[0][0].decode("utf-8", "replace"), d[0][1]
                messages.append(self._parse_header(raw, meta))
            return {"total": total, "page": page, "page_size": page_size, "messages": messages}
        finally:
            self.close()

    def _parse_header(self, raw, meta):
        msg = email.message_from_bytes(raw)
        uid = _extract_uid(meta) or meta
        flags = _FLAGS_RE.search(meta)
        flags = flags.group(1).split() if flags else []
        return {
            "id": str(uid),
            "subject": _decode_mime(msg.get("Subject")) or "(sin asunto)",
            "from": _parse_addresses(msg.get("From")),
            "to": _parse_addresses(msg.get("To")),
            "date": _fmt_date(msg.get("Date")),
            "unread": "\\Seen" not in flags,
            "flagged": "\\Flagged" in flags,
            "has_attachments": "multipart/mixed" in (msg.get_content_type() or ""),
        }

    def get_message(self, folder, msgid):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, d = conn.uid("fetch", msgid, "(UID FLAGS RFC822)")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                raise IMAPError("Mensaje no encontrado")
            meta, raw = d[0][0].decode("utf-8", "replace"), d[0][1]
            msg = email.message_from_bytes(raw)
            return self._full_message(msg, meta)
        finally:
            self.close()

    def _full_message(self, msg, meta):
        body_html = ""
        body_text = ""
        attachments = []
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
                attachments.append({
                    "name": _decode_mime(fn) or "adjunto",
                    "content_type": ct,
                    "cid": "",
                    "size": len(payload),
                    "data": base64.b64encode(payload).decode(),
                })
            elif ct == "text/html" and not body_html:
                body_html = _decode_payload(payload, part.get_content_charset())
            elif ct == "text/plain" and not body_text:
                body_text = _decode_payload(payload, part.get_content_charset())
            elif ct.startswith("image/") and disp == "inline":
                cid = part.get("Content-ID")
                cid_clean = cid.strip("<>") if cid else ""
                attachments.append({
                    "name": _decode_mime(fn) or cid_clean or "inline",
                    "content_type": ct,
                    "cid": cid_clean,
                    "size": len(payload),
                    "data": base64.b64encode(payload).decode(),
                })
        if not body_html and body_text:
            body_html = "<pre>" + escape(body_text) + "</pre>"
        uid = _extract_uid(meta) or meta
        flags = _FLAGS_RE.search(meta)
        flags = flags.group(1).split() if flags else []
        return {
            "id": str(uid),
            "subject": _decode_mime(msg.get("Subject")) or "(sin asunto)",
            "from": _parse_addresses(msg.get("From")),
            "to": _parse_addresses(msg.get("To")),
            "cc": _parse_addresses(msg.get("Cc")),
            "date": _fmt_date(msg.get("Date")),
            "unread": "\\Seen" not in flags,
            "flagged": "\\Flagged" in flags,
            "body_html": body_html,
            "body_text": body_text,
            "attachments": attachments,
            "message_id": msg.get("Message-ID"),
            "in_reply_to": msg.get("In-Reply-To"),
        }

    # ─────────────────────────────────────────────────────────────────────
    # `UID STORE` — por qué NO se usa conn.uid("store", ...)
    # ─────────────────────────────────────────────────────────────────────
    # El código anterior era `conn.uid("store", uid, "+FLAGS", flag)`. En
    # Python 3.11 `imaplib.uid()` pasa los argumentos a `_command()` tal cual
    # (ver Lib/imaplib.py: `_command` solo concatena `b' ' + arg`, sin comillas
    # ni paréntesis), así que al socket le llegaba:
    #
    #     UID STORE 1474 +FLAGS \Seen          <-- flag SUELTO
    #
    # El RFC 3501 exige que el flag-list vaya entre paréntesis
    # (`flag-list = "(" [flag *(SP flag)] ")"`), y `+FLAGS` sin lista detrás
    # deja al servidor con un parámetro de menos. GoDaddy lo tolera; el
    # servidor de la cuenta 34 no, y devolvía:
    #
    #     BAD [UID STORE extra parameters supplied "\Seen"]
    #
    # Son 3 ops `failed` en HUBMAIL_PendingOps (2026-09-01 y 2026-09-09) que
    # nadie vio porque la UI refleja el cambio al instante en la caché: el
    # correo se veía marcado, y el STORE contra IMAP nunca ocurría.
    #
    # La forma explícita de abajo no depende de la versión de Python: se arma
    # `_simple_command`, que en 3.11 también concatena verbatim, y el texto
    # sale exactamente como se escribe. `.SILENT` además evita que el servidor
    # conteste con un `* n FETCH` por cada mensaje del lote.
    #
    # También por eso los errores se ven: `set_flag` no se tragaba el fallo.
    @staticmethod
    def _uid_store(conn, uids, flag, valor):
        prefijo = "+" if valor else "-"
        return conn._simple_command("UID", "STORE", str(uids),
                                    f"{prefijo}FLAGS.SILENT", f"({flag})")

    def set_flag(self, folder, msgid, flag, value):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)))
            typ, _ = self._uid_store(conn, msgid, flag, value)
            if typ != "OK":
                raise IMAPError(
                    f"El servidor no acepto marcar el mensaje "
                    f"({flag}) en {folder}: {typ}"
                )
        finally:
            self.close()

    def set_flags(self, folder, ids, flag, value):
        if not ids:
            return
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)))
            typ, _ = self._uid_store(conn, ",".join(str(i) for i in ids), flag, value)
            if typ != "OK":
                raise IMAPError(
                    f"El servidor no acepto marcar {len(ids)} mensajes "
                    f"({flag}) en {folder}: {typ}"
                )
        finally:
            self.close()

    def move_message(self, folder, msgid, dest):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)))
            typ, _ = conn.uid("copy", msgid, _q(_utf7_encode(dest)))
            if typ != "OK":
                raise IMAPError("No se pudo mover a la carpeta destino")
            # ⚠️ AQUÍ NO se levanta IMAPError aunque el STORE falle, y es a
            # propósito. El COPY ya salió bien: el mensaje YA está en el
            # destino. Si se levantara, la cola marcaría la op como `failed` y
            # el próximo ciclo reintentaría el COPY entero, dejando una copia
            # nueva en el destino cada 5 minutos, sin límite.
            #
            # Lo que se deja es el estado clásico de un IMAP-move a medias: el
            # original sigue en el origen (no se marcó \Deleted) y el
            # sincronizador lo vuelve a bajar, con lo que se reconcilea solo.
            # Un aviso en el log es suficiente; el error se propaga arriba en
            # los otros métodos, que sí son idempotentes.
            typ, dat = self._uid_store(conn, msgid, "\\Deleted", True)
            if typ != "OK":
                print(f"[imap] move {folder}->{dest} uid={msgid}: el mensaje se "
                      f"copió pero no se pudo marcar \\Deleted en el origen "
                      f"({typ}). Queda una copia en ambos lados hasta el "
                      f"próximo sync.", flush=True)
            conn.expunge()
        finally:
            self.close()

    def fetch_raw_with_flags(self, folder, msgid):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, d = conn.uid("fetch", msgid, "(FLAGS RFC822)")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                raise IMAPError("Mensaje no encontrado")
            meta = d[0][0].decode("utf-8", "replace")
            raw = d[0][1]
            flags = _FLAGS_RE.search(meta)
            flags = set(flags.group(1).split()) if flags else set()
            return raw, flags
        finally:
            self.close()

    def append_message(self, folder, raw, flags=None):
        conn = self._connect()
        try:
            electedos = [f for f in (flags or []) if f in ("\\Seen", "\\Flagged")]
            # Los paréntesis también aquí: el RFC 3501 pide flag-list entre
            # paréntesis en APPEND, y `imaplib.append()` los pasa tal cual
            # (mismo motivo que en _uid_store). Con ellos, el servidor estricto
            # deja de rechazar el APPEND de los correos enviados.
            flags_str = f"({' '.join(electedos)})" if electedos else ""
            typ, _ = conn.append(_utf7_encode(folder), flags_str, None, raw)
            if typ != "OK":
                raise IMAPError("No se pudo copiar a la carpeta destino")
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                raise IMAPError("No se pudo obtener el UID del mensaje copiado")
            return data[0].split()[-1].decode()
        finally:
            self.close()

    def find_uid_by_message_id(self, folder, message_id):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, data = conn.uid("search", None, "HEADER", "Message-ID", _q(message_id))
            if typ == "OK" and data and data[0]:
                return data[0].split()[0].decode()
        finally:
            self.close()
        return None

    def last_uid(self, folder):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, data = conn.uid("search", None, "ALL")
            if typ == "OK" and data and data[0]:
                return data[0].split()[-1].decode()
        finally:
            self.close()
        return None

    def delete_message(self, folder, msgid):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)))
            typ, _ = self._uid_store(conn, msgid, "\\Deleted", True)
            if typ != "OK":
                raise IMAPError(f"No se pudo marcar para borrar en {folder}: {typ}")
            conn.expunge()
        finally:
            self.close()

    def delete_messages(self, folder, ids):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)))
            if ids:
                typ, _ = self._uid_store(conn, ",".join(str(i) for i in ids),
                                         "\\Deleted", True)
                if typ != "OK":
                    raise IMAPError(
                        f"No se pudo marcar {len(ids)} mensajes para borrar "
                        f"en {folder}: {typ}")
            conn.expunge()
        finally:
            self.close()

    def unread_count(self, folder="INBOX"):
        conn = self._connect()
        try:
            conn.select(_q(_utf7_encode(folder)), readonly=True)
            typ, data = conn.uid("search", None, "UNSEEN")
            if typ != "OK":
                return 0
            return len(data[0].split()) if data and data[0] else 0
        finally:
            self.close()

    # ---------------------------------------------------------- sync helpers
    def select_folder(self, folder, readonly=True):
        conn = self._connect()
        typ, _ = conn.select(_q(_utf7_encode(folder)), readonly=readonly)
        if typ != "OK":
            raise IMAPError(f"No se pudo abrir la carpeta {folder}")
        return conn

    def fetch_uid_list(self, folder):
        conn = self.select_folder(folder, readonly=True)
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise IMAPError("Error en la búsqueda UID")
        return [int(x) for x in data[0].split()] if data and data[0] else []

    def fetch_flags_map(self, folder, uids=None):
        conn = self.select_folder(folder, readonly=True)
        result = {}
        if uids:
            ranges = [uids[i:i + 200] for i in range(0, len(uids), 200)]
        else:
            ranges = [None]
        for part in ranges:
            if part:
                cmd = ",".join(str(u) for u in part)
            else:
                cmd = "1:*"
            try:
                typ, data = conn.uid("fetch", cmd, "(UID FLAGS)")
            except Exception:
                continue
            if typ != "OK":
                continue
            for item in data:
                if not isinstance(item, tuple):
                    continue
                text = item[0].decode("utf-8", "replace")
                uid = _extract_uid(text)
                if uid is None:
                    continue
                fm = _FLAGS_RE.search(text)
                result[uid] = fm.group(1).split() if fm else []
        return result

    def fetch_full_many(self, folder, uids, chunk=50):
        conn = self.select_folder(folder, readonly=True)
        results = []
        for i in range(0, len(uids), chunk):
            part = ",".join(str(u) for u in uids[i:i + chunk])
            typ, data = conn.uid("fetch", part, "(UID FLAGS BODY.PEEK[])")
            if typ != "OK":
                continue
            for item in data:
                if not isinstance(item, tuple):
                    continue
                meta = item[0].decode("utf-8", "replace")
                uid = _extract_uid(meta)
                if uid is None:
                    continue
                results.append((uid, meta, item[1]))
        return results

    def iter_full_many(self, folder, uids, chunk=25):
        conn = self.select_folder(folder, readonly=True)
        for i in range(0, len(uids), chunk):
            part = ",".join(str(u) for u in uids[i:i + chunk])
            try:
                typ, data = conn.uid("fetch", part, "(UID FLAGS BODY.PEEK[])")
            except Exception:
                continue
            if typ != "OK":
                continue
            out = []
            for item in data:
                if not isinstance(item, tuple):
                    continue
                meta = item[0].decode("utf-8", "replace")
                uid = _extract_uid(meta)
                if uid is None:
                    continue
                out.append((uid, meta, item[1]))
            if out:
                yield out

    def iter_headers_many(self, folder, uids, chunk=100):
        conn = self.select_folder(folder, readonly=True)
        for i in range(0, len(uids), chunk):
            part = ",".join(str(u) for u in uids[i:i + chunk])
            try:
                typ, data = conn.uid(
                    "fetch", part,
                    "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO)])",
                )
            except Exception:
                continue
            if typ != "OK":
                continue
            out = []
            for item in data:
                if not isinstance(item, tuple):
                    continue
                meta = item[0].decode("utf-8", "replace")
                uid = _extract_uid(meta)
                if uid is None:
                    continue
                out.append((uid, meta, item[1]))
            if out:
                yield out