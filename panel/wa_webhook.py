"""
panel/wa_webhook.py — receptor de mensajes entrantes de WhatsApp (OpenWA)
=========================================================================
OpenWA entrega un webhook por cada mensaje de la sesion de WhatsApp. La ruta
es publica (sin sesion ni CSRF) porque quien llama es el gateway, no una
persona: el secreto viaja en la propia ruta, `/api/wa/entrante/<token>`, ya
que OpenWA no siempre conserva cabeceras propias.

Esta fase guarda y, si la conversacion sigue en BOT, contesta:

  1. compara el token contra `HUB_Config.wa_bot_token` (tiempo constante);
  2. ignora lo que manda el propio bot (`fromMe`); de los grupos solo guarda
     el de ECCSita (`wa_grupo_eccsita_id`), sin contestar, y descarta el resto;
  3. deduplica por id de mensaje (OpenWA reintenta si no recibe 200);
  4. inserta en `HUB_WhatsappEntrante` y asegura la fila del chat en
     `HUB_WhatsappConversaciones` (nueva en estado BOT);
  5. si el chat esta en BOT, pide la respuesta a `wa_bot.responder()`, la manda
     con `openwa_client.enviar_texto()` y guarda la salida (Direccion OUT);
  6. responde 200 rapido con un JSON minimo.

El grupo de ECCSita se guarda en `HUB_GrupoMensajes` (ver `panel/eccsita.py`)
con su semana ISO en hora de Mexico. `wa_grupo_captura = 0` apaga la captura.
Si guardar el grupo falla, el webhook igual responde 200: OpenWA no debe
reintentar por culpa de la cronica.

Reglas:
  * NUNCA lanza hacia el servidor: un 500 hace que OpenWA reintente el mismo
    mensaje y, peor, puede cortar la entrega de los siguientes.
  * El cuerpo varia entre versiones de OpenWA: se guarda el JSON crudo y de ahi
    se extrae lo que se pueda, sin exigir una forma exacta.
  * Si el bot falla (sin configuracion, OpenWA caido, etc.) el webhook igual
    responde 200 y solo lo deja en el log: el mensaje ya quedo guardado y una
    persona puede contestarlo desde el panel.
"""
import hmac
import json
import time
from datetime import datetime, timezone

from tz_mx import now_mx, to_mx

from . import db

CLAVE_TOKEN = "wa_bot_token"
CLAVE_GRUPO = "wa_grupo_eccsita_id"
CLAVE_CAPTURA = "wa_grupo_captura"

MAX_ID = 120          # HUB_WhatsappEntrante.IdMensaje
MAX_CHAT = 80         # .ChatId
MAX_TELEFONO = 20     # .Telefono
MAX_NOMBRE = 120      # .Nombre
MAX_TEXTO = 2000      # .Texto
MAX_AUTOR = 80        # HUB_GrupoMensajes.AutorId

# WhatsApp puede mandar un LID (`...@lid`) en vez del telefono. OpenWA lo
# resuelve, pero es una llamada HTTP: se guarda el resultado una hora para no
# pedirlo en cada mensaje del mismo chat.
_CACHE_LID = {}
LID_TTL = 3600


def _token_configurado():
    """Valor de `wa_bot_token` en HUB_Config ("" si no existe o la BD falla)."""
    try:
        valores = db.get_config_values([CLAVE_TOKEN])
    except Exception:
        return ""
    return (valores.get(CLAVE_TOKEN) or "").strip()


def _mismo_token(recibido, esperado):
    """Comparacion en tiempo constante (no filtra cuantos caracteres coinciden)."""
    try:
        return hmac.compare_digest(str(recibido or "").encode("utf-8"),
                                   str(esperado or "").encode("utf-8"))
    except Exception:
        return False


# ─── Extraccion tolerante del cuerpo ─────────────────────────────────────────
def _candidato(cuerpo):
    """
    El mensaje suele venir dentro de `data`/`payload`, pero no siempre. Se
    devuelve el diccionario que mas se parece a un mensaje; si no hay, el
    propio cuerpo.
    """
    if not isinstance(cuerpo, dict):
        return {}
    for clave in ("data", "payload", "message"):
        hijo = cuerpo.get(clave)
        if isinstance(hijo, dict) and any(
                k in hijo for k in ("body", "text", "from", "chatId", "fromMe")):
            return hijo
    return cuerpo


def _buscar(msg, claves):
    """Primer valor no vacio de `claves`, mirando tambien `_data` (whatsapp-web.js)."""
    for origen in (msg, msg.get("_data") if isinstance(msg.get("_data"), dict) else {}):
        for clave in claves:
            valor = origen.get(clave)
            if valor not in (None, "", [], {}):
                return valor
    return None


def _texto(valor, limite):
    """Cadena recortada al ancho de la columna (None si no hay nada)."""
    if valor is None:
        return None
    if isinstance(valor, dict):
        valor = valor.get("_serialized") or valor.get("id") or ""
    texto = str(valor).strip()
    return texto[:limite] if texto else None


def _verdadero(msg, claves):
    valor = _buscar(msg, claves)
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "yes", "si", "sí")
    return bool(valor)


def _telefono(chat_id):
    """Los digitos del chat (`521812...@c.us` -> `521812...`)."""
    if not chat_id:
        return None
    digitos = "".join(c for c in str(chat_id).split("@")[0] if c.isdigit())
    return digitos[:MAX_TELEFONO] or None


def _fecha(valor):
    """
    `timestamp` de OpenWA suele ser epoch en segundos (a veces milisegundos, a
    veces ISO). Si no se entiende, se usa la hora de Monterrey de ahora. Nunca
    se escribe una fecha absurda: fuera de 2000-2100 se descarta.
    """
    try:
        if isinstance(valor, str) and not valor.strip().isdigit():
            dt = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        else:
            num = float(valor)
            if num > 1e11:                 # milisegundos
                num /= 1000.0
            dt = datetime.fromtimestamp(num, tz=timezone.utc)
        fecha = to_mx(dt)
        if 2000 <= fecha.year <= 2100:
            return fecha
    except Exception:
        pass
    return now_mx()


def _id_mensaje(msg):
    valor = _buscar(msg, ("id", "messageId", "msgId"))
    if isinstance(valor, dict):
        valor = valor.get("_serialized") or valor.get("id")
    return _texto(valor, MAX_ID)


def _chat_id(msg):
    valor = _buscar(msg, ("chatId", "chat_id", "from", "author"))
    if valor is None:
        chat = msg.get("chat")
        if isinstance(chat, dict):
            valor = chat.get("id") or chat.get("_serialized")
    if isinstance(valor, dict):
        valor = valor.get("_serialized") or valor.get("id")
    return _texto(valor, MAX_CHAT)


def _nombre(msg):
    valor = _buscar(msg, ("notifyName", "senderName", "sender_name",
                          "pushname", "pushName", "nombre", "name"))
    if valor is None:
        chat = msg.get("chat")
        if isinstance(chat, dict):
            valor = chat.get("name")
    return _texto(valor, MAX_NOMBRE)


def _autor(msg):
    """Quien manda en un grupo: `author`/`participant`, no el id del grupo."""
    valor = _buscar(msg, ("author", "participant", "sender", "from"))
    if isinstance(valor, dict):
        valor = valor.get("_serialized") or valor.get("id")
    return _texto(valor, MAX_AUTOR)


TIPOS = {"chat": "text", "ptt": "audio", "document": "document"}


def _tipo(msg, texto):
    """text, image, sticker, audio, video (o `otro` si no se reconoce)."""
    valor = _buscar(msg, ("type", "mediaType", "messageType"))
    if isinstance(valor, dict):
        valor = valor.get("_serialized") or valor.get("id")
    tipo = str(valor or "").strip().lower()
    tipo = TIPOS.get(tipo, tipo)

    if tipo not in ("text", "image", "sticker", "audio", "video"):
        mimetype = str(_buscar(msg, ("mimetype", "mimeType")) or "").lower()
        for prefijo, nombre in (("image/", "image"), ("audio/", "audio"),
                                ("video/", "video")):
            if mimetype.startswith(prefijo):
                tipo = nombre
                break
    if tipo not in ("text", "image", "sticker", "audio", "video"):
        tipo = "text" if texto else "otro"
    return tipo[:20]


def _extraer(cuerpo):
    """Normaliza el mensaje a las columnas de HUB_WhatsappEntrante."""
    msg = _candidato(cuerpo)
    chat_id = _chat_id(msg)
    texto = _texto(_buscar(msg, ("body", "text", "content", "caption")),
                   MAX_TEXTO)
    return {
        "id_mensaje": _id_mensaje(msg),
        "chat_id": chat_id,
        "telefono": _telefono_real(chat_id, _telefono(chat_id)),
        "nombre": _nombre(msg),
        "texto": texto,
        "fecha": _fecha(_buscar(msg, ("timestamp", "t", "time"))),
        "propio": _verdadero(msg, ("fromMe", "from_me")),
        "grupo": (_verdadero(msg, ("isGroup", "isGroupMsg", "isGroupChat"))
                  or str(chat_id or "").endswith("@g.us")),
        # Solo los usa la captura de ECCSita; el chat individual los ignora.
        "autor_id": _autor(msg),
        "autor_nombre": _nombre(msg),
        "tipo": _tipo(msg, texto),
    }


def _telefono_real(chat_id, telefono):
    """
    Telefono del chat; si WhatsApp mando un LID, lo resuelve con OpenWA.

    Nunca lanza: si OpenWA no contesta se devuelve el id tal cual y el bot
    atiende como desconocido, que es el peor caso aceptable.
    """
    chat = str(chat_id or "")
    if not chat.endswith("@lid"):
        return telefono
    cache = _CACHE_LID.get(chat)
    if cache and cache[1] > time.time():
        return cache[0] or telefono
    real = ""
    try:
        from openwa_client import telefono_de_contacto
        real = (telefono_de_contacto(chat) or "")[:MAX_TELEFONO]
    except Exception as exc:
        print(f"[wa_webhook] no se pudo resolver el LID {chat}: {exc}")
    _CACHE_LID[chat] = (real, time.time() + LID_TTL)
    return real or telefono


# ─── Guardado ────────────────────────────────────────────────────────────────
def _guardar(datos, crudo):
    """
    Inserta el mensaje y asegura la conversacion en UNA transaccion.
    Devuelve "guardado" o "duplicado".
    """
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            if datos["id_mensaje"]:
                cur.execute(
                    "SELECT 1 FROM HUB_WhatsappEntrante WHERE IdMensaje = %s",
                    (datos["id_mensaje"],))
                if cur.fetchone():
                    return "duplicado"

            cur.execute(
                "INSERT INTO HUB_WhatsappEntrante "
                "(IdMensaje, ChatId, Telefono, Nombre, Texto, Fecha, "
                " Estado, IdUsuario, IdCliente, Crudo) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'NUEVO', NULL, NULL, %s)",
                (datos["id_mensaje"], datos["chat_id"], datos["telefono"],
                 datos["nombre"], datos["texto"], datos["fecha"],
                 json.dumps(crudo, ensure_ascii=False)))

            # La conversacion nace en BOT; si ya existe solo se mueve `Ultima`
            # (el estado y la asignacion los decide una persona, no el mensaje).
            cur.execute(
                "MERGE HUB_WhatsappConversaciones AS t "
                "USING (SELECT %s AS ChatId) AS s ON t.ChatId = s.ChatId "
                "WHEN MATCHED THEN UPDATE SET Ultima = %s "
                "WHEN NOT MATCHED THEN INSERT (ChatId, Estado, Ultima) "
                "VALUES (%s, 'BOT', %s);",
                (datos["chat_id"], datos["fecha"],
                 datos["chat_id"], datos["fecha"]))
        conn.commit()
        return "guardado"
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        # 2601/2627: el indice unico atrapo una segunda entrega simultanea.
        mensaje = str(exc)
        if "2601" in mensaje or "2627" in mensaje:
            return "duplicado"
        raise
    finally:
        conn.close()


# ─── Captura del grupo de ECCSita ────────────────────────────────────────────
def _grupo_capturado(chat_id):
    """
    True solo si `chat_id` es exactamente el grupo configurado y la captura
    esta encendida (`wa_grupo_captura` ausente = encendida).
    """
    chat = str(chat_id or "").strip()
    if not chat.endswith("@g.us"):
        return False
    try:
        valores = db.get_config_values([CLAVE_GRUPO, CLAVE_CAPTURA])
    except Exception:
        return False
    grupo = (valores.get(CLAVE_GRUPO) or "").strip()
    if not grupo or chat != grupo:
        return False
    captura = valores.get(CLAVE_CAPTURA)
    if captura is not None and str(captura).strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


def _capturar_grupo(datos, crudo):
    """Guarda el mensaje del grupo. Nunca lanza: el webhook necesita su 200."""
    if not datos.get("id_mensaje"):
        # Sin id no hay dedupe posible: se descarta en vez de llenar la tabla
        # de filas que OpenWA volvería a entregar.
        return "ignorado"
    try:
        from . import eccsita
        return eccsita.guardar_mensaje(datos, crudo)
    except Exception as exc:
        print(f"[wa_webhook] no se pudo guardar el mensaje de ECCSita: {exc}")
        return "error"


# ─── Respuesta del bot ───────────────────────────────────────────────────────
def _estado_conversacion(chat_id):
    """Estado de la conversacion ('BOT', 'HUMANO', ...) o "" si no existe."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT Estado FROM HUB_WhatsappConversaciones WHERE ChatId = %s",
                (chat_id,))
            fila = cur.fetchone()
    finally:
        conn.close()
    return str(fila[0]).strip().upper() if fila and fila[0] else ""


def _guardar_salida(chat_id, telefono, texto):
    """Registra la respuesta del bot como mensaje de salida ya enviado."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO HUB_WhatsappEntrante "
                "(IdMensaje, ChatId, Telefono, Nombre, Texto, Estado, Direccion) "
                "VALUES (NULL, %s, %s, NULL, %s, 'ENVIADO', 'OUT')",
                (chat_id, telefono, (texto or "")[:MAX_TEXTO]))
        conn.commit()
    finally:
        conn.close()


def _contestarbot(datos):
    """
    Contesta el mensaje si el chat sigue en BOT. Nunca lanza: el webhook ya
    guardo el mensaje y OpenWA solo necesita su 200.
    """
    try:
        if _estado_conversacion(datos["chat_id"]) != "BOT":
            return
        # Tope por hora (chat y sesion). Si ya se llego, el mensaje queda
        # guardado para que lo vea una persona y el bot se queda callado.
        import wa_limites
        permitido, motivo = wa_limites.puede_responder(datos["chat_id"])
        if not permitido:
            print(f"[wa_webhook] no se contesta {datos['chat_id']}: "
                  f"limite por hora alcanzado ({motivo}); queda para una persona")
            return
        import wa_bot
        from openwa_client import enviar_texto

        respuesta = wa_bot.responder(datos["chat_id"], datos["telefono"],
                                     datos["texto"])
        if not respuesta:
            return
        ok, detalle = enviar_texto(datos["chat_id"], respuesta)
        if not ok:
            print(f"[wa_webhook] no se pudo enviar la respuesta: {detalle}")
            return
        _guardar_salida(datos["chat_id"], datos["telefono"], respuesta)
    except Exception as exc:
        print(f"[wa_webhook] el bot fallo y se ignora: {exc}")


# ─── Entrada ─────────────────────────────────────────────────────────────────
def atender(handler, token):
    """
    Atiende `POST /api/wa/entrante/<token>`. Siempre responde (200 normal, 404
    token malo) y nunca deja escapar una excepcion.
    """
    esperado = _token_configurado()
    if not esperado or not _mismo_token(token, esperado):
        # 404, no 401: no se revela que la ruta existe.
        handler._json({"ok": False}, 404)
        return

    try:
        crudo = handler._read_json()
        datos = _extraer(crudo)

        if not datos["chat_id"]:
            handler._json({"ok": True, "estado": "sin_chat"}, 200)
            return
        if datos["propio"]:
            handler._json({"ok": True, "estado": "ignorado"}, 200)
            return
        if datos["grupo"]:
            # De todos los grupos solo se escucha ECCSita, y ese no se contesta.
            if _grupo_capturado(datos["chat_id"]):
                estado = _capturar_grupo(datos, crudo)
            else:
                estado = "ignorado"
            handler._json({"ok": True, "estado": estado}, 200)
            return

        estado = _guardar(datos, crudo)
        if estado == "guardado":
            # Despues de guardar: si el chat esta en BOT, el bot contesta.
            # Va envuelto para que un fallo del bot no rompa el webhook.
            _contestarbot(datos)
        handler._json({"ok": True, "estado": estado}, 200)
    except Exception:
        # OpenWA reintenta si no recibe 200. El cuerpo ya pudo quedar guardado o
        # no, pero un error aqui no debe tumbar la entrega de los siguientes.
        try:
            handler._json({"ok": False}, 200)
        except Exception:
            pass
