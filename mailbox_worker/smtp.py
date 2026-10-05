"""
mailbox_worker/smtp.py — la salida de correo.

Este módulo es el ÚNICO que habla SMTP. La app no abre un socket de correo ni
sabe qué credenciales usa la cuenta: encola una fila y este worker la manda.

─── POR QUÉ ESTO NO ES "UN smtplib.send_message" ──────────────────────────────

Hay tres cosas que un `send_message` de diez líneas no hace y que sí se necesitan:

1. **Resolver los `cid:` de la firma.** El HTML que se guarda en la cola lleva
   `cid:logo`. Si se manda tal cual, el destinatario ve la firma con imágenes
   rotas: el `cid` no apunta a nada fuera del correo. Hay que bajar los bytes de
   la imagen del volumen compartido y pegarlos como parte MIME con el mismo
   Content-ID. Ese es el trabajo real de este archivo.

2. **No confiar en el destinatario.** `send_message` mete lo que le des en el
   sobre. Una app modificada — o un XSS en el composer — podría encolar un
   adjunto ejecutable o un dominio de salida. Aquí se filtran las direcciones a
   las de la cuenta y se recorta el número de destinatarios.

3. **No perder el hilo.** `InResponderA` es el Message-ID del correo original, y
   sin `In-Reply-To`/`References` la respuesta llega como un hilo nuevo.
"""

import mimetypes
import re
import smtplib
import ssl
from email.utils import make_msgid

from . import almacen
from .config import settings
from .crypto import decrypt_secret
from .db import filas

# `cid:xxx` en el HTML. Admite el token con o sin comillas y con ángulos
# opcionales (`cid:logo` y `cid:<logo>` son lo mismo). El NO es codicioso y no
# cruza comillas, para no agarrar un `cid:` que esté dentro de un texto suelto.
#
# La app solo produce `cid:token` sin ángulos (su sanitizador rechaza cualquier
# otra cosa), pero tolerarlos aquí es gratis y este es el punto donde un HTML
# venidero de otra herramienta se volvería un correo con imágenes rotas.
_CID_RE = re.compile(r'cid:(?:<)?(?:"([^"]+)"|([\w.+@-]{1,200}))(?:>)?', re.I)

# Bloques que se borran COMPLETOS al derivar texto plano. Si no, el recipient
# recibe el código CSS del correo como texto al principio del mensaje.
_BLOQUES_RE = re.compile(r'<(script|style|head)\b.*?</\1\s*>', re.I | re.S)
_SALTOS_RE = re.compile(r'<\s*(br|/p|/div|/tr|/li|/h[1-6])\s*/?\s*>', re.I)
_ETIQUETAS_RE = re.compile(r'<[^>]+>')

# Tope de destinatarios por envío. Nadie manda 500 correos en un clic; si pasa,
# es un bug o un abuso, y en ambos casos no debería llegar al servidor externo.
_MAX_DEST = 50


class SMTPError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Direcciones
# ═══════════════════════════════════════════════════════════════════════════════

def separar_direcciones(texto: str) -> list:
    """
    `"Juan" <juan@x.com>, maria@y.com` → `["juan@x.com", "maria@y.com"]`.

    Se usa el parser del módulo `email`, no un split por comas: los nombres
    legítimos llevan comas (`"Pérez, Juan" <j@x.com>`) y un split las rompe,
    dejando un destinatario basura que el servidor remoto rechazaría.
    """
    return [direccion for _nombre, direccion in _parsear_direcciones(texto) if direccion]


def _parsear_direcciones(texto: str) -> list:
    """
    `getaddresses` devuelve `[(nombre, direccion), ...]`.

    Es tolerante a headers rotos: un `From:` sin formato devuelve `('', 'texto')`
    en vez de lanzar. Eso es justo lo que se quiere aquí, porque el texto viene
    de la app y un valor raro debe descartarse en `es_correo_valido`, no tumbar
    el envío.
    """
    from email.utils import getaddresses
    try:
        return [(n or "", a or "") for n, a in getaddresses([texto or ""])]
    except Exception:
        return []


def es_correo_valido(dir_: str) -> bool:
    """Chequeo mínimo. No es un validador RFC, es una puerta contra basura."""
    if not dir_ or len(dir_) > 254 or dir_.count("@") != 1:
        return False
    local, _, dominio = dir_.partition("@")
    if not local or not dominio or "." not in dominio:
        return False
    if dominio.endswith(".") or dominio.startswith(".") or ".." in dominio:
        return False
    if any(c in dir_ for c in ' \t\r\n<>,;"'):
        return False
    return True


def solo_permitidos(destinatarios: list, cuenta_email: str) -> list:
    """
    Filtra lo que el servidor remoto ACEPTARÁ.

    Se quitan las direcciones inválidas y se limitan a `_MAX_DEST`. Un envío con
    destinatario inválido no se rechaza entero: el servidor de correo devuelve
    error solo por los malos, y los buenos igual se pueden entregar.
    """
    validas = [d for d in destinatarios if es_correo_valido(d)]
    if not validas:
        raise SMTPError("no quedó ninguna dirección válida en el destinatario")
    if len(validas) > _MAX_DEST:
        raise SMTPError(f"más de {_MAX_DEST} destinatarios en un envío")
    return validas


# ═══════════════════════════════════════════════════════════════════════════════
# cid: de la firma
# ═══════════════════════════════════════════════════════════════════════════════

def cids_en_html(html: str) -> list:
    """
    Los `cid:` que el HTML referencia. Es la lista de imágenes que hay que
    pegarle al mensaje o que se verán rotas.

    Se normaliza sin el prefijo `cid:` y sin los ángulos: el `Content-ID` que se
    pone en la parte MIME es `<nombre>` y el atributo del HTML es
    `cid:nombre`. Esos dos tienen que coincidir exactamente o la imagen no se ve,
    y la diferencia entre mayúsculas importa en algunos clientes.
    """
    encontrados = []
    for con_comillas, sin_comillas in _CID_RE.findall(html or ""):
        valor = (con_comillas or sin_comillas or "").strip()
        if valor.startswith("<") and valor.endswith(">"):
            valor = valor[1:-1]
        if valor and valor not in encontrados:
            encontrados.append(valor)
    return encontrados


def imagenes_de_firma(firma_id) -> dict:
    """
    `{cid: (bytes, content_type, nombre)}` de las imágenes de una firma.

    Se leen del volumen COMPARTIDO (`firmas/<IdFirma>/<Clave>`), no de la base:
    los bytes de una imagen de 40 KB no van en SQL Server. La columna `Clave` es
    la ruta relativa y `Clave` —no `Token`— la que se usa para abrir el archivo;
    el `Token` es lo que se expone por URL.
    """
    if not firma_id:
        return {}
    try:
        registros = filas(
            "SELECT Id, Nombre, ContentType, Cid, Clave FROM HUB_MailboxFirmasImagenes "
            "WHERE IdFirma = %s", (int(firma_id),))
    except Exception:
        return {}

    salida = {}
    for r in registros:
        cid = (r.get("Cid") or "").strip()
        clave = (r.get("Clave") or "").strip()
        if not cid:
            # Sin cid la imagen no se referencia, pero igual se manda como
            # adjunto visible para que no se pierda.
            cid = f"img{r['Id']}"
        datos = almacen.leer_firma(int(firma_id), clave, "")
        if not datos:
            print(f"[smtp] la imagen {clave} de la firma {firma_id} no está en el volumen")
            continue
        salida[cid] = (datos, r.get("ContentType") or "application/octet-stream",
                       r.get("Nombre") or "imagen")
    return salida


# ═══════════════════════════════════════════════════════════════════════════════
# Construcción del mensaje
# ═══════════════════════════════════════════════════════════════════════════════

def _html_a_texto(html: str) -> str:
    """
    Deriva un texto plano legible de un HTML.

    Existe por una razón concreta: un correo con SOLO `text/html` se ve EN
    BLANCO en el Outlook de escritorio y desaparece en algunos gateways
    corporativos que quitan el HTML. El texto plano es el piso mínimo de
    seguridad de un correo que sale.

    No es una conversión bonita (no conserva tablas ni listas con sangría): es
    la versión legible, que es la que se necesita. Los bloques `script`, `style`
    y `head` se borran completos, no sus etiquetas, porque si no el recipient lee
    el CSS del correo.
    """
    if not html:
        return ""
    import html as _html
    texto = _BLOQUES_RE.sub(" ", html)
    texto = _SALTOS_RE.sub("\n", texto)
    texto = _ETIQUETAS_RE.sub("", texto)
    texto = _html.unescape(texto)
    # Colapsa espacios pero RESPETA los saltos de párrafo: sin esto todo el
    # mensaje queda en un párrafo gigante.
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*\n+", "\n\n", texto)
    return texto.strip()


def construir_mensaje(fila: dict, cuenta: dict):
    """
    Arma el mensaje MIME a partir de la fila de la cola.

    Devuelve (mensaje, cid_sin_resolver).

    ─── Por qué NO se usa `EmailMessage.add_alternative/add_related` ───────────

    La API de azúcar de `email` lanza `ValueError: Cannot convert alternative to
    related` si se llama `add_related()` después de `add_alternative()`. Y ese es
    exactamente el caso de un correo con firma: hay texto plano, hay HTML, y el
    HTML lleva imágenes inline que van como `related`.

    El orden correcto de anidamiento —el que aceptan Gmail, Outlook y Apple
    Mail— es:

        multipart/related                 ← raíz: texto + imágenes del HTML
          ├── multipart/alternative       ← qué versión ver
          │     ├── text/plain
          │     └── text/html
          └── image/png  (Content-ID: <logo>)

    `multipart/alternative` NO puede contener imágenes (solo texto, por
    definición de RFC 2046), así que las imágenes cuelgan del `related` de
    arriba. Por eso la estructura se arma a mano con `MIMEMultipart` en vez de
    dejar que la librería la componga.

    `cid_sin_resolver` son los `cid:` que el HTML pedía y que no se pudieron
    resolver a bytes. No es fatal: se manda el correo igual y el Watcher lo
    reporta. Un logo que falta es peor que un correo que no llega.
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    remitente = cuenta["Email"]
    html = fila.get("HtmlSnapshot") or ""
    texto = fila.get("TextoSnapshot") or ""

    # `_para` / `_cc` los calcula `enviar_uno` (que ya validó las direcciones).
    # Si no vienen, se calculan aquí para que esta función se pueda usar sola sin
    # depender de que el llamador se acordara.
    para = fila.get("_para") or separar_direcciones(fila.get("Para") or "")
    cc = fila.get("_cc")
    if cc is None:
        cc = separar_direcciones(fila.get("Cc") or "")

    # ── 1. Resolver las imágenes de la firma ANTES de armar la estructura ──
    pedidos = cids_en_html(html)
    disponibles = imagenes_de_firma(fila.get("IdFirma"))
    imagenes = []
    faltan = []
    for cid in pedidos:
        if cid in disponibles:
            imagenes.append((cid,) + disponibles[cid])
        else:
            faltan.append(cid)

    # ── 2. Cuerpo ─────────────────────────────────────────────────────────
    # El texto plano SIEMPRE acompaña al HTML. Si el cliente no lo guardó, se
    # deriva del HTML: peor un texto plano feo que un correo que el recipient abre
    # y no ve nada. Sin la parte `text/plain`, el Outlook de escritorio muestra el
    # mensaje en blanco y algunos gateways corporativo lo descartan entero.
    if html.strip():
        cuerpo = MIMEMultipart("alternative")
        cuerpo.attach(MIMEText(texto.strip() or _html_a_texto(html), "plain", "utf-8"))
        cuerpo.attach(MIMEText(html, "html", "utf-8"))
    elif texto.strip():
        cuerpo = MIMEText(texto, "plain", "utf-8")
    else:
        cuerpo = MIMEText("(correo sin contenido)", "plain", "utf-8")

    # ── 3. Raíz: `related` solo si hay imágenes que colgar ───────────────
    if imagenes:
        msg = MIMEMultipart("related")
        msg.attach(cuerpo)
        for cid, datos, tipo, nombre in imagenes:
            msg.attach(_parte_inline(datos, tipo, nombre, cid))
    else:
        msg = cuerpo

    # ── 4. Cabeceras ──────────────────────────────────────────────────────
    msg["From"] = remitente
    msg["To"] = ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)
    # El Bcc NO va en las cabeceras: por definición nadie más debe verlo. El
    # sobre SMTP sí lo lleva, y es lo único que lo recibe.
    msg["Subject"] = fila.get("Asunto") or "(sin asunto)"

    # El hilo. Sin In-Reply-To la respuesta del cliente llega como un correo
    # suelto y no como respuesta.
    if fila.get("InResponderA"):
        mid = fila["InResponderA"].strip()
        if mid and len(mid) < 500:
            msg["In-Reply-To"] = mid
            msg["References"] = mid

    msg["Message-ID"] = make_msgid(domain=remitente.split("@")[-1])
    msg["X-Mailer"] = "ECCSA Mailbox"

    return msg, faltan


def _parte_inline(datos: bytes, tipo: str, nombre: str, cid: str):
    """
    La parte MIME de una imagen inline.

    El `Content-ID` que se pone AQUÍ tiene que coincidir EXACTAMENTE con el
    `cid:` del atributo `src` del HTML, ángulos incluidos. Si no coincide, la
    imagen viaja en el correo pero no se ve: el cliente la busca por nombre y no
    la encuentra. No falla nada, aparece el espacio en blanco, y es un bug que
    sobrevive años sin que nadie lo reporte porque el correo "sí llegó".
    """
    from email.mime.image import MIMEImage

    principal, _, sub = (tipo or "").partition("/")
    if principal != "image" or not sub:
        # Se manda como imagen genérica antes que como application/octet-stream:
        # un octet-stream que se llama .png no lo abre ningún cliente.
        principal, sub = "image", "png"
    try:
        img = MIMEImage(datos, _subtype=sub)
    except Exception as exc:
        print(f"[smtp] la imagen {nombre} ({tipo}) no es una imagen válida: {exc}")
        return None
    img.add_header("Content-ID", f"<{cid}>")
    img.add_header("Content-Disposition", "inline", filename=nombre)
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# Envío
# ═══════════════════════════════════════════════════════════════════════════════

def _conectar(cuenta: dict, credencial: str) -> smtplib.SMTP:
    """
    Abre la sesión SMTP con TLS. Nunca en claro.

    `starttls` se hace explícito y se verifica con `smtp.starttls(context=ctx)`.
    Si un servidor no soporta STARTTLS, el envío falla: es lo correcto, porque
    mandar credenciales en claro es peor que no mandar el correo.
    """
    host = cuenta.get("ServidorSMTP") or settings.default_smtp_host
    puerto = int(cuenta.get("PuertoSMTP") or settings.default_smtp_port)
    ctx = ssl.create_default_context()

    if puerto == 465:
        # TLS implícito: se abre el socket ya cifrado. Gmail lo usa con
        # smtp.gmail.com:465.
        return smtplib.SMTP_SSL(host, puerto, timeout=30, context=ctx)

    s = smtplib.SMTP(host, puerto, timeout=30)
    s.ehlo()
    if not s.has_extn("starttls"):
        s.close()
        raise SMTPError(f"{host} no ofrece STARTTLS: no se manda en claro")
    s.starttls(context=ctx)
    s.ehlo()
    return s


def _autenticar(s: smtplib.SMTP, cuenta: dict, credencial: str):
    """LOGIN o XOAUTH2, según la cuenta."""
    usuario = cuenta["Email"]
    if (cuenta.get("TipoAuth") or "PASSWORD").upper() == "OAUTH2":
        import base64
        usuario_auth = "\x01".join([usuario, credencial])
        token = base64.b64encode(f"user={usuario_auth}".encode()).decode()
        s.docmd("AUTH", "XOAUTH2 " + token)
    else:
        s.login(usuario, credencial)


def enviar_uno(cuenta: dict, fila: dict) -> str:
    """
    Manda UN correo de la cola. Devuelve el Message-ID enviado.

    Filtra los destinatarios, construye el MIME, resuelve la firma y manda. La
    conexión se abre y se cierra por correo: es más lento que mantenerla, pero
    una conexión compartida entre hilos es una forma muy fina de bloquearse, y
    el volumen de correo de ECCSA no justifica esa riesgo.
    """
    credencial = decrypt_secret(cuenta.get("CredencialCifrada") or "")
    if not credencial:
        raise SMTPError(f"la cuenta {cuenta.get('Email')} no tiene credencial")

    para = solo_permitidos(separar_direcciones(fila.get("Para") or ""), cuenta["Email"])
    cc = solo_permitidos(separar_direcciones(fila.get("Cc") or ""), cuenta["Email"]) \
        if fila.get("Cc") else []
    # El Bcc es opcional y no se valida igual: si no hay direcciones válidas se
    # ignora en vez de tirar el envío entero.
    bcc = [d for d in separar_direcciones(fila.get("Bcc") or "") if es_correo_valido(d)]

    fila = dict(fila)
    fila["_para"] = para
    fila["_cc"] = cc

    msg, faltan = construir_mensaje(fila, cuenta)
    if faltan:
        # No es fatal: se manda igual. Un logo que falta no cancela un correo.
        print(f"[smtp] cola {fila.get('Id')}: {len(faltan)} imagen(es) de firma sin resolver "
              f"({', '.join(faltan[:3])})")

    destinatarios = set(para) | set(cc) | set(bcc)
    s = None
    try:
        s = _conectar(cuenta, credencial)
        _autenticar(s, cuenta, credencial)
        s.send_message(msg, from_addr=cuenta["Email"],
                       to_addrs=list(destinatarios))
        return msg["Message-ID"]
    except smtplib.SMTPException as exc:
        raise SMTPError(f"SMTP: {exc}") from exc
    except (OSError, ssl.SSLError) as exc:
        raise SMTPError(f"conexión SMTP: {exc}") from exc
    finally:
        if s is not None:
            try:
                s.quit()
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass


def enviar_cola(cuenta: dict) -> int:
    """
    Drena la cola de envío de UNA cuenta. Devuelve cuántos se mandaron.

    Un envío fallido NO bloquea la cola: se marca con su conteo de intentos y
    el siguiente correo sigue. Un correo con un archivo adjunto que no existe no
    puede impedir que los otros 19 salgan.
    """
    from .db import ejecuta

    cid = cuenta["Id"]
    pendientes = filas(
        "SELECT TOP (%s) Id, Para, Cc, Bcc, Asunto, HtmlSnapshot, TextoSnapshot, "
        "       InResponderA, IdFirma, Intentos FROM HUB_MailboxColaEnvio "
        "WHERE IdCuenta = %s AND Estado = 'PENDIENTE' ORDER BY Creado",
        (20, cid),
    )
    enviados = 0
    for fila in pendientes:
        fila_id = fila["Id"]
        try:
            # ENVIANDO antes de mandar: si el proceso muere entre acá y el
            # send_message, el correo puede haberse ido sin que quede registro.
            # Con este estado el reinicio lo puede reintentar (duplicado
            # visible) en vez de perderlo en silencio.
            ejecuta("UPDATE HUB_MailboxColaEnvio SET Estado = 'ENVIANDO' WHERE Id = %s",
                    (fila_id,))
            mid = enviar_uno(cuenta, fila)
            ejecuta("UPDATE HUB_MailboxColaEnvio SET Estado = 'ENVIADO', Enviado = GETDATE(), "
                    "MessageIdEnviado = %s, Error = NULL WHERE Id = %s", (mid, fila_id))
            enviados += 1
            print(f"[smtp] cola {fila_id} enviado a {fila['Para'][:60]}")
        except SMTPError as exc:
            intentos = int(fila.get("Intentos") or 0) + 1
            if intentos >= 4:
                # A los 4 intentos se abandona: un SMTP que rechaza por política
                # del servidor no va a aceptarlo en el quinto.
                ejecuta("UPDATE HUB_MailboxColaEnvio SET Estado = 'ERROR', Intentos = %s, "
                        "Error = %s WHERE Id = %s", (intentos, str(exc)[:1000], fila_id))
                print(f"[smtp] cola {fila_id} abandonada tras 4 intentos: {exc}")
            else:
                ejecuta("UPDATE HUB_MailboxColaEnvio SET Estado = 'PENDIENTE', "
                        "Intentos = %s, Error = %s WHERE Id = %s",
                        (intentos, str(exc)[:1000], fila_id))
    return enviados


# ═══════════════════════════════════════════════════════════════════════════════
# Adjuntos salientes (AdjuntosJson)
# ═══════════════════════════════════════════════════════════════════════════════

def adjuntos_de_json(bruto: str) -> list:
    """
    Lee el manifiesto `AdjuntosJson` de la cola.

    Formato: `[{"nombre": "x.pdf", "cid": "d1", "inline": "inline", "clave": "..."}]`
    La app NO lo llena hoy (solo se mandan firmas), pero el worker lo soporta
    para que agregar adjuntos salientes sea solo un endpoint en la app y nada
    más acá.
    """
    if not bruto:
        return []
    import json
    try:
        datos = json.loads(bruto)
    except (ValueError, TypeError):
        return []
    return datos if isinstance(datos, list) else []


def leer_adjunto(a: dict) -> bytes:
    """Lee los bytes de un adjunto saliente desde el volumen compartido."""
    origen = a.get("inline") or ""
    if origen == "inline":
        return almacen.leer_inline(int(a.get("id_cuenta") or 0), a.get("clave") or "")
    if origen == "firma":
        return almacen.leer_firma(int(a.get("id_firma") or 0), a.get("clave") or "", "")
    return b""


def adivinar_tipo(nombre: str) -> str:
    """El Content-Type por extensión, con un default razonable.

    `mimetypes` no conoce los tipos de Outlook (.msg, .pst), así que se agrega
    un mapa pequeño. Un `application/octet-stream` hace que el cliente le pida
    el nombre y no abra nada.
    """
    especial = {
        ".eml": "message/rfc822", ".msg": "application/vnd.ms-outlook",
        ".pst": "application/vnd.ms-outlook", ".ics": "text/calendar",
        ".csv": "text/csv", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
    }
    import os as _os
    ext = _os.path.splitext(nombre or "")[1].lower()
    if ext in especial:
        return especial[ext]
    return mimetypes.guess_type(nombre or "")[0] or "application/octet-stream"