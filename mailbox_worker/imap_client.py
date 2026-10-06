"""
mailbox_worker/imap_client.py — cliente IMAP de Mailbox.

Es una adaptación de `hubmail_worker/imap_client.py` (565 líneas ya probadas) con
lo que el modelo de STREAMING necesita y ese cliente NO tenía. Se conserva toda
la lógica de dominio porque es lo caro de re-derivar: el paréntesis de
`UID STORE`, las carpetas UTF-7, la semántica parcial de `move_message`.

─── Lo que se AGREGÓ (y es lo que hace posible el modelo) ────────────────────

1. `fetch_parte(carpeta, uid, parte)`  →  `BODY.PEEK[<seccion>]`
   Pide UNA parte MIME (ej. `2.1`) en vez del mensaje entero. Sin esto, abrir un
   adjunto de 40 MB bajaría también los otros 39 MB.

2. `fetch_parte_trozo(carpeta, uid, parte, desde, bytes)`  →  `BODY.PEEK[...]<n.m>`
   La MISMA parte, pero solo un rango de bytes. Con esto un archivo de 300 MB
   pasa por el proceso en 256 KB por vez. Es lo que hace viable el streaming.

3. Autenticación XOAUTH2
   Para OAuth2. `LOGIN` plano no alcanza: el token de acceso expira en una hora
   y hay que refrescarlo.

Lo que NO se copió del cliente viejo: los métodos de LECTURA para la app
(`get_message`, `_full_message`, `list_messages`) ya no los necesita nadie. La app
no habla IMAP, y el worker lee del índice. Borrarlos fue deliberado: código que
nadie llama es código que alguien va a "arreglar" sin saber para qué.

SSL: solo `IMAP4_SSL` (TLS implícito, 993). No hay STARTTLS ni IMAP4 plano: las
cuentas que se dan de alta en Mailbox son de servidores que lo soportan, y
aceptar conexión en claro sería una puerta abierta que nadie necesita.
"""

# `typing` se importa por `Optional` y parece innecesario: en Python 3.14 las
# anotaciones se difieren (PEP 649) y `_parse_fecha(valor) -> Optional[datetime]`
# no necesita el nombre resuelto. En 3.11 —que es la versión del contenedor— SÍ se
# evalúan, y el worker moría al arrancar con
# `NameError: name 'Optional' is not defined`.
#
# Se descubrió al arrancar en el servidor, después de que 425 tests passersan en
# local: la máquina de desarrollo corre 3.14 y el contenedor 3.11.
import base64
import codecs
import email
import email.policy
import email.utils
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from html import escape as html_escape
from typing import Optional


class IMAPError(Exception):
    pass


_UID_RE = re.compile(r"UID\s+(\d+)")
_FLAGS_RE = re.compile(r"FLAGS\s*\((.*?)\)")


def _extract_uid(meta: bytes):
    m = _UID_RE.search(meta.decode("ascii", "replace") if isinstance(meta, bytes) else meta)
    return int(m.group(1)) if m else None


def _utf7_encode(name: str) -> str:
    """IMAP guarda los nombres de carpeta en UTF-7 modificado, no en UTF-8."""
    try:
        return codecs.encode(name, "imap4-utf-7")
    except Exception:
        return name


def _utf7_decode(name: str) -> str:
    try:
        return str(codecs.decode(name.encode("ascii", "replace"), "imap4-utf-7"))
    except Exception:
        return name


def _q(s: str) -> str:
    """Cita un nombre de carpeta para IMAP: escapa \\ y "."""
    return '"' + str(s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decode_mime(raw) -> str:
    """
    Decodifica un valor MIME codificado (=?utf-8?B?...?=) a texto.

    Se hace a mano y no con `str(make_header(decode_header(x)))` porque:

      · `decode_header` en Python 3.13+ exige `str`; darle bytes lanza
        TypeError con un mensaje que no dice nada del contexto.
      · `str(make_header(...))` mete "=?unknown-8bit?...?=" en vez del texto
        cuando el charset no se reconoce, que es justo lo que pasa con los
        nombres de archivo de Outlook.

    Un header corrupto devuelve algo legible, nunca revienta: esto corre
    dentro del sync y una excepción aquí tumbaría la cuenta entera.
    """
    if not raw:
        return ""
    texto = raw if isinstance(raw, str) else raw.decode("latin-1", errors="replace")
    if "=?" not in texto:
        return texto.strip()
    try:
        partes = decode_header(texto)
    except Exception:
        return texto.strip()
    salida = []
    for parte, charset in partes:
        if not isinstance(parte, bytes):
            salida.append(parte)
            continue
        for cod in (charset, "utf-8", "latin-1"):
            if not cod:
                continue
            try:
                salida.append(parte.decode(cod, errors="replace"))
                break
            except LookupError:
                # Un charset que no existe ("charset-inexistente") NO puede
                # tumbar el sync. Se cae a utf-8 y sigue.
                continue
        else:
            salida.append(parte.decode("utf-8", errors="replace"))
    return "".join(salida).strip()


def _separar_remitente(crudo: str):
    """
    `"Juan Pérez" <juan@ecc-sa.com.mx>` → `("Juan Pérez", "juan@ecc-sa.com.mx")`.

    Convierte UN campo From en las dos columnas que la lista usa (remitente
    visible y remitente real). El nombre visible es lo que el usuario reconoce;
    la dirección es lo que se usa para agrupar y para decide auto-respuestas.

    Casos raros que aparecen de verdad:
      · `juan@x.com (Juan)` → la forma inversa; se saca el email con regex.
      · `Grupo <grupo@x.com>` sin nombre → nombre = el email, que es lo que se
        vería igual en cualquier cliente.
      · Sin email → se devuelve lo que haya, sin inventar nada.
    """
    texto = (crudo or "").strip()
    if not texto:
        return "", ""

    # 1) La forma `user@x.com (Nombre)`, que es la que manda Outlook/Exchange.
    #    Sin este caso el nombre visible se pierde y la lista muestra el email
    #    crudo en todas las filas de Exchange.
    m = re.search(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\s*\(+(.+?)\)+", texto)
    if m:
        return m.group(2).strip().strip('"').strip(), m.group(1).strip()

    # 2) La forma normal: `"Nombre" <email>`.
    nombre, sep, resto = texto.rpartition("<")
    if sep and ">" in resto:
        email_ = resto.split(">")[0].strip()
        nombre = nombre.strip().strip('"').strip()
        return nombre, email_

    # 3) Solo un email, o un nombre suelto.
    m = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", texto)
    if m:
        return m.group(0), m.group(0)
    return texto, ""


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y %H:%M:%S +0000")


def _parse_fecha(valor) -> Optional[datetime]:
    if not valor:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(str(valor))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _parse_folder_line(line: str):
    """`(LIST) "/" "INBOX"` → ('INBOX', '/')"""
    try:
        txt = line.decode("ascii", "replace") if isinstance(line, bytes) else line
    except Exception:
        return None
    m = re.match(r'\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|\"[^\"]*\"|\S+)\s+(?P<name>.+)$', txt.strip())
    if not m:
        return None
    delim = m.group("delim")
    delim = "" if delim == "NIL" else delim.strip('"')
    nombre = m.group("name").strip()
    if nombre.startswith('"') and nombre.endswith('"'):
        nombre = nombre[1:-1]
    return _utf7_decode(nombre), delim, m.group("flags")


class IMAPClient:
    """Una sesión IMAP. Un hilo = una sesión."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: int = 30, oauth2: bool = False):
        self.host = host
        self.port = int(port or 993)
        self.username = username
        self.password = password
        self.timeout = int(timeout or 30)
        self.oauth2 = bool(oauth2)
        self._conn = None
        self._carpeta_actual = None

    # ── Conexión ────────────────────────────────────────────────────────────
    def connect(self):
        if self._conn is not None:
            return self._conn
        try:
            self._conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        except Exception as exc:
            raise IMAPError(f"no pude abrir IMAP en {self.host}:{self.port}: {exc}")

        try:
            if self.oauth2:
                self._login_xoauth2()
            else:
                # Google agrupa la App Password en bloques de 4 SIN espacios. Si
                # un admin la pega con espacios, IMAP responde "Invalid
                # credentials" y no dice por qué. Se quitan.
                clave = self.password
                if " " in clave and not self.oauth2:
                    clave = clave.replace(" ", "")
                self._conn.login(self.username, clave)
        except IMAPError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise IMAPError(f"autenticación fallida en {self.username}: {exc}")
        return self._conn

    def _login_xoauth2(self):
        """XOAUTH2 para OAuth2.

        El token de acceso de Google dura una hora. Este cliente recibe el token
        YA refrescado por `sync.py` (que es quien tiene el refresh token y puede
        pedir uno nuevo); si llega vencido, el error dice que hay que refrescar
        en vez de "login falló".
        """
        if not self.password:
            raise IMAPError("OAuth2 sin token de acceso")
        token = self.password.strip()
        if token.lower().startswith("oauth2 "):
            token = token[7:]
        auth = "user=" + self.username + "\x01auth=Bearer " + token + "\x01\x01"
        try:
            self._conn.authenticate("XOAUTH2", lambda _x: auth.encode("utf-8"))
        except imaplib.IMAP4.error as exc:
            raise IMAPError(
                f"XOAUTH2 rechazado ({exc}). Suele ser un token de acceso vencido: "
                f"refrescá el refresh token."
            )

    def close(self):
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
        self._carpeta_actual = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ── Carpetas ────────────────────────────────────────────────────────────
    def list_folders(self):
        """[(nombre, flags)] con los nombres ya en UTF-8 real."""
        c = self.connect()
        typ, data = c.list()
        if typ != "OK":
            raise IMAPError("LIST falló")
        out = []
        vistos = set()
        for raw in data or []:
            parseado = _parse_folder_line(raw)
            if not parseado:
                continue
            nombre, _delim, flags = parseado
            if nombre in vistos:
                continue
            vistos.add(nombre)
            out.append((nombre, flags))
        return out

    def status_folder(self, carpeta: str) -> dict:
        """
        `STATUS` de una carpeta: cuántos mensajes hay y cuántos sin leer.

        NO descarga el contenido. Es un comando de una línea por carpeta que
        responde con dos números, y es lo que permite pintar las pestañas con su
        contador real sin sincronizar una sola línea de correo.

        Un `STATUS` por carpeta: 20 carpetas son 20 round trips. Se mide aparte de
        `LIST` porque son cosas distintas y conviene poder apagar una sin la otra.
        """
        c = self.connect()
        codificada = _q(_utf7_encode(carpeta))
        typ, datos = c.status(codificada, "(MESSAGES UNSEEN)")
        if typ != "OK" or not datos or not datos[0]:
            return {}
        linea = datos[0]
        if isinstance(linea, bytes):
            linea = linea.decode(errors="replace")
        m_total = re.search(r"MESSAGES\s+(\d+)", linea, re.I)
        m_unseen = re.search(r"UNSEEN\s+(\d+)", linea, re.I)
        if m_total:
            total = int(m_total.group(1))
        if m_unseen:
            no_leidos = int(m_unseen.group(1))
        return {"total": total, "no_leidos": no_leidos}

    def select_folder(self, carpeta: str):
        """Selecciona la carpeta. Deja la conexión ABIERTA a propósito: el sync
        hace un solo SELECT por ciclo y después todos los FETCH sobre la misma
        sesión."""
        c = self.connect()
        typ, _ = c.select(_q(_utf7_encode(carpeta)), readonly=False)
        if typ != "OK":
            raise IMAPError(f"no pude abrir la carpeta {carpeta}")
        self._carpeta_actual = carpeta
        return carpeta

    def unread_count(self, carpeta: str = "INBOX") -> int:
        c = self.select_folder(carpeta)
        typ, data = c.uid("search", None, "UNSEEN")
        if typ != "OK":
            return 0
        ids = (data[0] or b"").split()
        return len(ids)

    # ── Búsquedas ────────────────────────────────────────────────────────────
    def fetch_uid_list(self, criteria=None) -> list:
        """UIDs de la carpeta actual."""
        c = self.connect()
        args = ["ALL"] if not criteria else list(criteria)
        typ, data = c.uid("search", None, *args)
        if typ != "OK":
            return []
        out = []
        for token in (data[0] or b"").split():
            try:
                out.append(int(token))
            except ValueError:
                pass
        return out

    def find_uid_by_message_id(self, message_id: str):
        """El UID de un mensaje por su header Message-ID.

        Necesario al mover: `UID COPY` devuelve el UID en el destino, pero
        algunos servidores lo devuelven vacío o el último, no el del mensaje que
        se movió. Buscar por Message-ID es lo único fiable.
        """
        if not message_id:
            return None
        c = self.connect()
        typ, data = c.uid("search", None, "HEADER", "Message-ID", f'"{message_id}"')
        if typ != "OK":
            return None
        ids = [int(t) for t in (data[0] or b"").split() if t.isdigit()]
        return ids[-1] if ids else None

    def last_uid(self):
        c = self.connect()
        typ, data = c.uid("search", None, "ALL")
        if typ != "OK":
            return None
        ids = [int(t) for t in (data[0] or b"").split() if t.isdigit()]
        return ids[-1] if ids else None

    def set_flag(self, uid: int, flag: str, valor: bool):
        """`UID STORE ±FLAGS.SILENT`.

        El paréntesis es OBLIGATORIO y es un bug que ya se cometió: `UID STORE
        <uid> +FLAGS.SILENT` sin paréntesis lo rechazan varios servidores.
        Se arma a mano con `_simple_command` en vez de usar `uid("store", ...)`
        porque el camino de atajo de imaplib varía entre versiones de Python.
        """
        c = self.connect()
        prefijo = "+" if valor else "-"
        typ, _ = c._simple_command("UID", "STORE", str(uid), f"{prefijo}FLAGS.SILENT", f"({flag})")
        if typ != "OK":
            raise IMAPError(f"STORE {flag} falló en el UID {uid}")

    def create_folder(self, nombre: str) -> str:
        """
        Crea la carpeta en IMAP y devuelve el nombre tal como lo usa IMAP.

        El nombre se traduce a modified UTF-7 (`&` + base64) porque es lo que
        exige IMAP para acentos: una carpeta que se llame «Facturación 2026» se
        guarda como `Facturaci&APg-n 2026`. Si se mandara el texto tal cual,
        Gmail la crea pero después no se puede volver a abrir con ese nombre.

        Se devuelve el nombre EXACTO que reporta el servidor (`c.create()`
        responde con la ruta creada) y no el que pidió el usuario, porque son
        distintos cuando hay acentos, y la app indexa por lo que dice el
        servidor. Indexar por el nombre pedido dejaría las pestañas apuntando a
        una carpeta que no existe.

        NO hace falta detectar si existe: IMAP responde con el error del
        servidor y eso llega al usuario como el error real, que es más útil que
        un "ya existe" inventado que puede ser falso (la carpeta puede existir
        en el servidor y no en nuestro índice).
        """
        c = self.connect()
        codificada = _q(_utf7_encode(nombre))
        typ, respuesta = c.create(codificada)
        if typ != "OK":
            raise IMAPError(f"IMAP CREATE falló: {respuesta}")

        # `c.create()` devuelve bytes con la ruta entre comillas: b'INBOX.Facturaci&APg-n'
        texto = respuesta.decode(errors="replace") if isinstance(respuesta, bytes) else str(respuesta)
        creado = texto.strip().strip('"').strip("'")
        # IMAP separa con '.'; el nombre que se usa en la app es el último nivel.
        return creado.split(".")[-1] if creado else nombre

    def move_message(self, uid: int, destino: str) -> Optional[int]:
        """Mueve a otra carpeta con UID COPY + borrar el original.

        Devuelve el UID del mensaje en el destino, o None si no se pudo
        determinar.

        IMPORTANTE: es una copia, no un "move" de verdad. Se hace COPY y
        después se marca el original como \\Deleted. Si el COPY falla NO se borra
        el original: perder un correo por un fallo de red en medio del COPY es
        el peor resultado posible, y por eso el borrado va después del COPY
        exitoso y nunca se propaga la excepción hacia atrás.
        """
        c = self.connect()
        destino_q = _q(_utf7_encode(destino))
        typ, _ = c.uid("copy", str(uid), destino_q)
        if typ != "OK":
            # No se borra nada: el original sigue donde estaba.
            raise IMAPError(f"UID COPY a {destino} falló")
        try:
            self.set_flag(uid, "\\Deleted", True)
        except IMAPError:
            pass          # el COPY ya se hizo; un fallo al borrar es cosmético
        return self.last_uid()

    def delete_message(self, uid: int):
        self.set_flag(uid, "\\Deleted", True)
        self._conn.expunge()

    def delete_messages(self, uids):
        if not uids:
            return
        self.set_flag(",".join(str(u) for u in uids), "\\Deleted", True)
        self._conn.expunge()

    def append_message(self, destino: str, crudo: bytes, visto: bool = True) -> Optional[int]:
        """APPEND al buzón. Lo usa el envío y el "copiar a Enviados".

        OJO: el UID que devuelve es el de `UID SEARCH ALL` — el ÚLTIMO. Con
        otro append concurrente puede ser el mensaje equivocado. Por eso quien
        llama tiene que usar el Message-ID para confirmarlo.
        """
        c = self.connect()
        flags = "(\\Seen)" if visto else ""
        try:
            typ, _ = c.append(_q(_utf7_encode(destino)), flags, None, crudo)
        except Exception as exc:
            raise IMAPError(f"APPEND a {destino} falló: {exc}")
        if typ != "OK":
            raise IMAPError(f"APPEND a {destino} devolvió {typ}")
        return self.last_uid()

    # ══════════════════════════════════════════════════════════════════════
    # LAS TRES AGREGADAS PARA EL MODELO DE STREAMING
    # ══════════════════════════════════════════════════════════════════════

    def fetch_parte(self, uid: int, parte: str) -> bytes:
        """UNA parte MIME, completa. `parte` es el part number ('2.1', '1', ...).

        Es lo que hace posible que abrir un adjunto no baje el mensaje entero.
        `BODY.PEEK` y no `BODY`: PEEK no pone el flag \\Seen, y el worker no debe
        marcar como leído un mensaje que el usuario solo está descargando.
        """
        c = self.connect()
        typ, data = c.uid("fetch", str(uid), f"(BODY.PEEK[{parte}])")
        if typ != "OK":
            raise IMAPError(f"BODY.PEEK[{parte}] del UID {uid} devolvió {typ}")
        return _primer_literal(data)

    def fetch_parte_trozo(self, uid: int, parte: str, desde: int, cantidad: int) -> bytes:
        """
        Un RANGO de bytes de una parte: `BODY.PEEK[2.1]<0.262144>`.

        La diferencia con `fetch_parte` es lo que separa el modelo de streaming
        de un modelo que "casi funciona": con esto un adjunto de 300 MB pasa por
        el proceso en 256 KB por vez, así que la memoria del worker no depende
        del tamaño del archivo. Sin esto, descargar un adjunto grande mataría el
        proceso — y mataría TODAS las cuentas, porque viven en el mismo worker.

        Se devuelve un trozo MAYOR que el pedido cuando el servidor lo manda
        entero (algunos ignoran el `<inicio.longitud>`); por eso el llamador
        avanza con `len(datos)`, no con `cantidad`.
        """
        c = self.connect()
        seccion = f"{parte}<{int(desde)}.{int(cantidad)}>"
        typ, data = c.uid("fetch", str(uid), f"(BODY.PEEK[{seccion}])")
        if typ != "OK":
            return b""
        return _primer_literal(data)

    def cuerpo_texto(self, uid: int, charset: str = "utf-8") -> str:
        """El cuerpo SIN adjuntos, como texto.

        `RFC822.TEXT` es lo que hace posible el diseño de dos fases: trae el
        texto plano del mensaje sin los adjuntos, en un solo round trip. Se
        guarda ese texto en el índice (para la búsqueda) y el HTML se pide
        aparte cuando el usuario abre el mensaje.

        Antes, bajar `BODY[]` traía los adjuntos piggyback aunque no se
        necesitaran: una bandeja de entrada con adjuntos de 8 MB se descargaba
        entera cada vez que se sincronizaba.
        """
        c = self.connect()
        try:
            # `BODY.PEEK[TEXT]`, NO `RFC822.TEXT`.
            #
            # `RFC822.TEXT` pone el flag \Seen en el buzón REAL. Sincronizar el
            # buzón de 12 personas marcaría sus correos como leídos en Gmail /
            # Hostinger sin que nadie los haya abierto: es un efecto secundario
            # en el correo de otra persona, no en nuestra copia.
            #
            # Pasó: las 419 primeras_SYNC dejaron los 419 mensajes con `Visto = 1`,
            # y en el buzón de `hector.pena@ecc-sa.com.mx` y `robot@…` esos 407
            # correos dejaron de estar sin leer. `PEEK` es la palabra que existe
            # justo para esto, y ya se usaba en `fetch_parte`.
            typ, data = c.uid("fetch", str(uid), "(BODY.PEEK[TEXT])")
        except Exception:
            return ""
        if typ != "OK":
            return ""
        crudo = _primer_literal(data)
        if not crudo:
            return ""
        # El texto viene en el charset declarado en el Content-Type.
        for cod in (charset, "utf-8", "latin-1"):
            try:
                return crudo.decode(cod, errors="replace")
            except (LookupError, UnicodeDecodeError):
                continue
        return crudo.decode("utf-8", errors="replace")

    def estructura(self, uid: int) -> dict:
        """
        La ESTRUCTURA MIME sin los bytes: `BODYSTRUCTURE`.

        Devuelve, para cada parte adjunta, {nombre, tipo, tamaño, cid, parte}.

        Esta es la pieza que hace barato el sync: se lee el manifiesto de
        adjuntos de TODOS los mensajes nuevos con un round trip chico, sin bajar
        un solo byte de archivo. Los bytes solo se piden cuando el usuario abre
        el adjunto.

        Estructura de BODYSTRUCTURE (RFC 3501):
          mensaje → (partes, subpartes, Extended) donde `partes` es la LISTA de
          (atributos, [subpartes]) de los hijos de nivel superior.
        """
        c = self.connect()
        typ, data = c.uid("fetch", str(uid), "(BODYSTRUCTURE)")
        if typ != "OK":
            return {"adjuntos": []}
        crudo = _primer_literal(data)
        if not crudo:
            return {"adjuntos": []}
        try:
            texto = crudo.decode("ascii", "replace")
        except Exception:
            return {"adjuntos": []}

        adjuntos = []
        # La base es "" y NO "1": en IMAP los hijos de nivel superior de un
        # multipart se numeran 1, 2, 3… (BODY[1], BODY[2]), el multipart no
        # consume un número. Pasar "1" hacía que todo quedara 1.1, 1.2 y el
        # FETCH pedía partes inexistentes.
        _a_adjuntos(parse_bodystructure(texto), "", adjuntos)
        return {"adjuntos": adjuntos}

    def fetch_encabezados(self, uid: int) -> dict:
        """
        Las cabeceras del mensaje, parseadas: remitente, asunto, fecha, flags.

        Es lo que necesita la LISTA para pintar una fila, y lo pide UN round
        trip por mensaje. Traer el mensaje entero para leer el `Subject`
        significaba descargar cada adjunto del buzón solo para mostrar la lista:
        con una bandeja normal eso son cientos de MB por ciclo que nadie pidió.

        `Seen` viene de los FLAGS del mismo fetch, así que no hace falta un
        `fetch_undisclosed` aparte.
        """
        vacio = {"subject": "", "from_name": "", "from_email": "", "to": "", "cc": "",
                 "bcc": "", "reply_to": "", "message_id": "", "date": None,
                 "seen": False, "flagged": False, "answered": False}
        c = self.connect()
        try:
            typ, data = c.uid(
                "fetch", str(uid),
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC BCC REPLY-TO "
                "DATE MESSAGE-ID)] FLAGS)")
        except Exception:
            return dict(vacio)
        if typ != "OK" or not data:
            return dict(vacio)

        crudo = b""
        flags = ""
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta, payload = item[0], item[1]
            if isinstance(payload, (bytes, bytearray)):
                crudo += bytes(payload)
            if isinstance(meta, (bytes, bytearray)):
                m = _FLAGS_RE.search(meta.decode("ascii", "replace"))
                if m:
                    flags = m.group(1).upper()
        if not crudo:
            return dict(vacio)

        # `email.message_from_bytes` con policy=default decodifica los headers
        # MIME (=?utf-8?B?...) a texto plano, que es justo lo que se quiere.
        msg = email.message_from_bytes(crudo, policy=email.policy.default)

        remitente = msg.get("From") or ""
        nombre, correo = _separar_remitente(remitente)
        fecha = _parse_fecha(msg.get("Date"))

        return {
            "subject": _decode_mime(msg.get("Subject")),
            "from_name": nombre,
            "from_email": correo,
            "to": _decode_mime(msg.get("To")),
            "cc": _decode_mime(msg.get("Cc")),
            "bcc": _decode_mime(msg.get("Bcc")),
            "reply_to": _decode_mime(msg.get("Reply-To")),
            "message_id": (msg.get("Message-ID") or "").strip(),
            # Si el Date es inválido se usa la hora de ingesta, no epoch 1970:
            # un mensaje sin fecha usable se ordenaría como el más viejo del
            # mundo y se purgaría en el primer ciclo de retención.
            "date": fecha or datetime.now(),
            "seen": "\\SEEN" in flags,
            "flagged": "\\FLAGGED" in flags,
            "answered": "\\ANSWERED" in flags,
        }

    def cuerpo_html(self, uid: int) -> str:
        """
        El cuerpo HTML COMPLETO, para cuando el usuario abre el mensaje.

        Se pide aquí y NO en el sync por una razón de bytes: un correo de
        newsletter pesa 3-5 veces su texto plano, y en un ciclo de sincronización
        se pedirían todos esos bytes para que la mayoría nunca se abra.

        Dos casos:
          · HTML existe → `BODY.PEEK[1]` o la parte que lo declare.
          · Solo texto plano → se devuelve el texto envuelto en un HTML
            mínimo, preescapado. Es preferible a mostrar un iframe vacío: el
            usuario igual tiene algo que leer.

        El texto se ESCAPA (`html.escape`) porque va dentro de un HTML. Un correo
        en texto plano que contenga `<script>` no debe poder ejecutarse en el
        iframe del visor, ni siquiera sin sanitizar, porque el sandbox sin
        `allow-scripts` es la última barrera y no la única.
        """
        c = self.connect()

        # Primero se busca si hay una parte HTML, leyendo el Content-Type de
        # cada parte de nivel superior: un round trip chico.
        html = ""
        try:
            typ, data = c.uid(
                "fetch", str(uid),
                "(BODY.PEEK[HEADER.FIELDS (CONTENT-TYPE)])")
            if typ == "OK":
                crudo = _primer_literal(data) or b""
                texto = crudo.decode("utf-8", "replace")
                # El Content-Type de nivel superior; si es multipart, el HTML
                # está en una de las partes y se busca abajo.
                if "text/html" in texto.lower() and "multipart" not in texto.lower():
                    html = self._html_de_parte(c, uid, "1")
        except Exception:
            html = ""

        if html:
            return html

        texto = self.cuerpo_texto(uid)
        if not texto.strip():
            return ""
        return (
            '<html><head><meta charset="utf-8"></head><body>'
            '<pre style="white-space:pre-wrap;font-family:sans-serif">'
            f"{html_escape(texto)}</pre></body></html>"
        )

    def _html_de_parte(self, c, uid: int, parte: str) -> str:
        """
        Baja UNA parte y devuelve su HTML si es `text/html`.

        `_primer_literal` porque la respuesta de FETCH trae el literal partido
        en varias líneas de 1024 bytes.
        """
        try:
            typ, data = c.uid("fetch", str(uid), f'(BODY.PEEK[{parte}])')
        except Exception:
            return ""
        if typ != "OK":
            return ""
        crudo = _primer_literal(data)
        if not crudo:
            return ""
        texto = crudo.decode("utf-8", "replace")
        # Un "cuerpo" que en realidad es otra cosa (un adjunto disfrazado de
        # HTML) no se muestra: se descarta y el visor seguirá al texto plano.
        if "<html" not in texto.lower() and "<body" not in texto.lower():
            if "<" in texto and "</" not in texto:
                return ""
        return texto


def _tokenizar(texto: str):
    """
    Parte una respuesta BODYSTRUCTURE en tokens.

    Tipos que devuelve: ("str", valor) para lo que venía entre comillas (con los
    escapes \\" y \\ ya resueltos), ("num", entero) para lo que venía entre
    llaves {12}, ("(", None) y (")", None) para los paréntesis, y
    ("atom", palabra) para el resto (NIL, mixed, base64…).

    Los paréntesis van como tokens sueltos a propósito: son tokens, no
    estructura. La estructura la arma `_a_expresion`, que ya no tiene que
    adivinar qué paréntesis abre qué.
    """
    out = []
    i = 0
    n = len(texto)
    while i < n:
        ch = texto[i]
        if ch.isspace():
            i += 1
        elif ch == '"':
            i += 1
            buf = []
            while i < n:
                if texto[i] == "\\" and i + 1 < n:
                    buf.append(texto[i + 1])
                    i += 2
                elif texto[i] == '"':
                    i += 1
                    break
                else:
                    buf.append(texto[i])
                    i += 1
            out.append(("str", "".join(buf)))
        elif ch in "()[]":
            out.append((ch, None))
            i += 1
        elif ch == "{":
            j = texto.find("}", i)
            if j == -1:
                break
            try:
                out.append(("num", int(texto[i + 1:j] or 0)))
            except ValueError:
                out.append(("atom", texto[i + 1:j]))
            i = j + 1
        elif ch.isdigit() or (ch == "-" and i + 1 < n and texto[i + 1].isdigit()):
            # En BODYSTRUCTURE los números van DESNUDOS (1152), no entre llaves.
            # Las llaves {…} son de los literales de un FETCH, no de aquí.
            j = i + 1
            while j < n and (texto[j].isdigit() or texto[j] == "."):
                j += 1
            try:
                numero = float(texto[i:j])
                out.append(("num", int(numero) if numero == int(numero) else numero))
            except ValueError:
                out.append(("atom", texto[i:j]))
            i = j
        else:
            j = i
            while j < n and texto[j] not in ' \t"()[]{}':
                j += 1
            if j == i:
                j = i + 1
            out.append(("atom", texto[i:j]))
            i = j
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# BODYSTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════
# Por qué un parser propio
# ----------------------
# imaplib tiene un helper para esto, pero es privado (`_body_structure_q`) y
# devuelve los nombres SIN decodificar: un archivo llamado "Factura Ñeñe.pdf"
# llega como "=?utf-8?B?...?=" y se muestra tal cual en la app.
#
# Y por qué NO se parsea "a ojo" con regex o contando paréntesis
# ---------------------------------------------------------------
# El problema real del primer intento: un `(` puede ser el inicio de los
# subpartes o el de los PARÁMETROS, y en el texto son idénticos. Cualquier
# heurística se equivoca con un adjunto anidado, y `Parte` es la columna con la
# que después se PIDE el adjunto: un índice mal calculado significa pedir una
# parte inexistente y que no descargue nunca nada.
#
# El truco que lo hace simple: BODYSTRUCTURE es una lista anidada que ya
#.parsea "casi" como Python. `(`→`[`, `)`→`]`, `NIL`→`None`. Entonces se
# convierte a una expresión de listas y se evalúa. No hay interpretación de
# semántica en el parseo, solo estructura — y la semántica se aplica después,
# sobre una lista de Python, donde los índices ya no son ambiguos.

_ATOM_NIL = "NIL"

# Los únicos tipos de primer nivel que define IMAP (RFC 3501). Una hoja fuera de
# esta lista no es una hoja: es basura, y se ignora en vez de inventarse un
# adjunto.
_TIPOS_IMAP = {
    "TEXT", "MULTIPART", "MESSAGE",
    "APPLICATION", "IMAGE", "AUDIO", "VIDEO",
}


def _a_expresion(tokens, i: int):
    """
    Convierte tokens de BODYSTRUCTURE a una expresión de listas de Python.

    RECURSA en cada `(`: un paréntesis abre una lista anidada, no es un valor.
    Sin esa recursión, un cuerpo con un solo nivel salía como
    `['None','None','text','plain',…]` — con DOS nulos al principio, porque los
    paréntesis abiertos se estaban convirtiendo en la cadena "None". Con eso,
    `nodo[0]` ya no era el tipo y todas las partes quedaban mal numeradas.

    Devuelve (texto_de_la_lista, posición_después).
    """
    partes = []
    while i < len(tokens):
        tipo, valor = tokens[i]
        if tipo == ")":
            return "[" + ",".join(partes) + "]", i + 1
        if tipo == "(":
            sub, i = _a_expresion(tokens, i + 1)
            partes.append(sub)
            continue
        if tipo in ("[", "]"):
            # Los corchetes son de las listas multilínea de IMAP (BODY[TEXT]<0.100>).
            # No son estructura del BODYSTRUCTURE: se ignoran.
            i += 1
            continue
        if tipo == "num":
            partes.append(str(valor))
        elif tipo == "atom":
            # NIL es el null de IMAP; cualquier otro atom es el subtipo
            # ("mixed"), el encoding ("base64") o similar.
            partes.append("None" if str(valor).upper() == _ATOM_NIL else repr(str(valor)))
        else:
            partes.append(repr(str(valor)))
        i += 1
    return "[" + ",".join(partes) + "]", i


def parse_bodystructure(texto: str):
    """BODYSTRUCTURE crudo → estructura anidada de Python. [] si no se puede."""
    if not texto:
        return []
    try:
        tokens = _tokenizar(texto)
        # Se entra en i=1, saltando el "(" externo: el BODYSTRUCTURE de un
        # multipart ES la lista de sus partes, no una lista que la contiene.
        # Envolverla agregaba un nivel y las partes salían "1.1.2.1" en vez de
        # "1.2.1" — y con `Parte` mal, el FETCH pedía una parte inexistente y el
        # adjunto no se descargaba nunca, sin error visible.
        expr, _ = _a_expresion(tokens, 1 if tokens and tokens[0][0] == "(" else 0)
        # La expresión la arma el tokenizador sobre lo que devolvió el servidor;
        # no hay entrada de usuario acá. Aun así se evalúa con el literal vacío
        # de builtins, por si un servidor raro devolviera algo inesperado.
        valor = eval(expr, {"__builtins__": {}}, {})      # noqa: S307
        # Un "NIL" suelto (o un cuerpo vacío) es `[None]`, no un mensaje. Sin
        # este filtro, el sync vería "un adjunto sin nombre y de 0 bytes" en
        # cada mensaje que no tuviera estructura.
        return valor if isinstance(valor, list) and any(v is not None for v in valor) else []
    except Exception:
        return []


# ── Índices de una hoja (RFC 3501, body-fld-lines) ──────────────────────────
_I_TIPO = 0
_I_SUBTIPO = 1
_I_PARAMS = 2
_I_ID = 3          # el Content-ID, con <>
_I_ENC = 5         # body-fld-dsp: "base64", "7bit", "quoted-printable"
_I_TAMANO = 6
_I_EXT0 = 8        # después vienen las extensiones; el nombre del archivo
_I_DISPOSICION = 9 # vive entre las extensiones


def _param_de(params, etiqueta: str):
    """Busca un parámetro con nombre. `params` es ["charset","utf-8",…]."""
    if not isinstance(params, list):
        return None
    for i in range(0, len(params) - 1, 2):
        if str(params[i]).lower() == etiqueta.lower():
            return params[i + 1]
    return None


def _nombre_de_disposicion(estructura) -> str:
    """
    El nombre del archivo cuando no viene en `params` sino en la disposición.

    Gmail y otros mandan `("attachment" ("filename" "Factura.pdf"))` en la
    extensión, y no en el `name` de los parámetros. Sin esto, esos adjuntos se
    guardan como "adjunto-1-2", que es un nombre inútil en la lista.
    """
    for ext in estructura[_I_EXT0:]:
        if isinstance(ext, list) and len(ext) >= 2 and isinstance(ext[1], list):
            nombre = _param_de(ext[1], "filename")
            if nombre:
                return _decode_mime(nombre)
    return ""


def _a_adjuntos(nodo, parte: str, salida: list, prof: int = 1):
    """
    Aplana la estructura a la lista de adjuntos.

    La detección de multiparte es trivial sobre una lista de Python: si el
    PRIMER elemento es una lista, son los hijos; si es un string, es una hoja.
    """
    if prof > 8 or not isinstance(nodo, list) or not nodo:
        return

    if isinstance(nodo[0], list):
        # Multiparte: los hijos son las listas del principio, hasta el primer
        # string (el subtipo).
        hijos = []
        for item in nodo:
            if isinstance(item, list):
                hijos.append(item)
            else:
                break
        for idx, hijo in enumerate(hijos, start=1):
            _a_adjuntos(hijo, f"{parte}.{idx}" if parte else str(idx), salida, prof + 1)
        return

    tipo = str(nodo[_I_TIPO] or "").upper()
    subtipo = str(nodo[_I_SUBTIPO] or "").upper() if len(nodo) > _I_SUBTIPO else ""

    # ── Validación de forma ────────────────────────────────────────────────
    # Una hoja real tiene por lo menos 7 campos (hasta el tamaño) y un tipo de
    # la lista cerrada de RFC 3501. Sin esta comprobación, CUALQUIER texto
    # suelto que devolviera el servidor ("NIL", un mensaje de error, una línea
    # vacía) se guardaba a la base como un adjunto de 0 bytes llamado "adjunto-",
    # que es un fantasma que ni el usuario ni el admin pueden explicar.
    if len(nodo) < _I_TAMANO + 1 or tipo not in _TIPOS_IMAP:
        return

    # `multipart` nunca llega como hoja, y `message/rfc822` es un sobre
    # encapsulado: bajar sus bytes es bajar un adjunto DENTRO de otro, y lo
    # dejamos para el cliente de correo.
    if tipo in ("MULTIPART", "MESSAGE"):
        return

    params = nodo[_I_PARAMS] if len(nodo) > _I_PARAMS else None
    nombre = _param_de(params, "name") or ""
    if not nombre:
        nombre = _nombre_de_disposicion(nodo)
    nombre = _decode_mime(nombre) if nombre else ""

    # `text/*` SIN nombre es el cuerpo del correo, no un adjunto. Por eso un
    # correo con texto plano no produce ninguna fila. PERO un `text/csv` o un
    # `text/plain` con filename sí es un adjunto de verdad, y es lo más común al
    # mandar reportes: la condición es el NOMBRE, no el tipo de contenido.
    if tipo == "TEXT" and not nombre:
        return

    cid = ""
    if len(nodo) > _I_ID and isinstance(nodo[_I_ID], str):
        cid = nodo[_I_ID].strip("<> ")
    # El Content-ID de los headers viene SIN <> (ej. "logo@eccsa"), el de la
    # estructura viene CON. Se guardan igual para que la comparación en el
    # visor funcione con las dos formas.
    cid_limpio = cid.strip("<>")

    tamano = 0
    if len(nodo) > _I_TAMANO and isinstance(nodo[_I_TAMANO], int):
        tamano = nodo[_I_TAMANO]

    if not nombre:
        nombre = f"adjunto-{parte.replace('.', '-')}"

    salida.append({
        "parte": parte,
        "nombre": nombre[:255],
        "tipo": tipo.lower(),
        "subtipo": subtipo.lower(),
        "cid": cid_limpio,
        "size": tamano,
    })


def _primer_literal(data) -> bytes:
    """Saca el literal de una respuesta FETCH de imaplib.

    imaplib devuelve una lista mezclando bytes "sueltos" (tuples que abren la
    respuesta) y literales (tuples `(bytes, bytes)`). El primer literal es el
    cuerpo; lo que se busca es el último `(x, y)` con y siendo el literal.
    """
    if not data:
        return b""
    for item in reversed(data):
        if isinstance(item, tuple) and len(item) >= 2:
            # (meta, literal[, flags])
            return item[1] or b""
    # Algunos servidores devuelven el literal sin tupla cuando cabe en una línea.
    for item in data:
        if isinstance(item, bytes) and item:
            return item
    return b""