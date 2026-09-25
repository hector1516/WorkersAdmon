"""
Worker de Telegram: consume la cola HUB_TelegramQueue y envía mensajes
vía la API de Telegram. Corre bajo supervisor como 'telegram_worker'.

Flujo:
  1. Procesa mensajes entrantes (/start) → vinculación automática por nombre
  2. Lee pendientes de la cola (máx 10 por ciclo)
  3. Para cada uno: envía texto o adjunto vía send_telegram_message()
  4. Marca ENVIADO o FALLADO (reintenta máx 3 veces)
  5. Limpieza periódica de historial (>30 días)
  6. duerme 20 segundos y repite
"""
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eccsa_db as db

LOOP_INTERVAL = 20  # segundos entre ciclos
MAX_INTENTOS = 3
CLEANUP_INTERVAL = 3600  # 1 hora
MAX_HISTORIAL = 2000  # limpiar si hay más de N registros
TELEGRAM_BOT_TOKEN_KEY = 'telegram_bot_token'


def _get_bot_token():
    """Obtiene el token del bot desde HUB_Config."""
    try:
        cfg = db.get_telegram_config()
        return cfg.get('bot_token')
    except Exception:
        return None


def _send_message(chat_id, text):
    """Envía un mensaje de texto vía la API de Telegram."""
    import urllib.request
    token = _get_bot_token()
    if not token:
        return False, 'No bot token'
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = f'{{"chat_id":{chat_id},"text":"{text}","parse_mode":"Markdown"}}'.encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(req, timeout=15)
    result = __import__('json').loads(resp.read().decode())
    return result.get('ok', False), result.get('description', '')


def _get_updates(offset=None):
    """Obtiene mensajes entrantes del bot."""
    import urllib.request
    import json
    token = _get_bot_token()
    if not token:
        return []
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    if offset:
        url += f"?offset={offset}"
    req = urllib.request.Request(url, headers={'Content-Type': 'application/json'}, method='GET')
    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read().decode())
    if result.get('ok'):
        return result.get('result', [])
    return []


def _try_vinculate_by_name(first_name, last_name, chat_id):
    """Intenta vincular un chat_id con un usuario HUB por nombre."""
    full_name = f"{first_name} {last_name}".strip()
    if not full_name:
        return False

    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                # Buscar por nombre completo exacto
                cur.execute(
                    "SELECT Id, Nombre FROM HUB_Users WHERE LTRIM(RTRIM(Nombre)) = %s AND Activo = 1",
                    (full_name,)
                )
                match = cur.fetchone()

                # Si no hay match exacto, intentar con LIKE
                if not match:
                    cur.execute(
                        "SELECT Id, Nombre FROM HUB_Users WHERE Nombre LIKE %s AND Activo = 1",
                        (f"%{full_name}%",)
                    )
                    candidates = cur.fetchall()
                    if len(candidates) == 1:
                        match = candidates[0]

                if match:
                    user_id = match['Id']
                    # Verificar si ya está vinculado
                    cur.execute(
                        "SELECT ChatId FROM HUB_TelegramUsuarios WHERE IdUsuario = %s",
                        (user_id,)
                    )
                    existing = cur.fetchone()
                    if existing:
                        if str(existing['ChatId']) != str(chat_id):
                            cur.execute(
                                "UPDATE HUB_TelegramUsuarios SET ChatId = %s, Activo = 1, NombreTelegram = %s WHERE IdUsuario = %s",
                                (chat_id, full_name, user_id)
                            )
                        return True
                    else:
                        cur.execute(
                            "INSERT INTO HUB_TelegramUsuarios (IdUsuario, ChatId, NombreTelegram, Activo) VALUES (%s, %s, %s, 1)",
                            (user_id, chat_id, full_name)
                        )
                    conn.commit()
                    return True
    except Exception as e:
        print(f"  [VINCULATE ERROR] {e}")
    return False


def process_incoming():
    """Procesa mensajes entrantes del bot (vinculación automática)."""
    token = _get_bot_token()
    if not token:
        return

    # Obtener updates pendientes
    try:
        updates = _get_updates()
    except Exception:
        return

    for update in updates:
        update_id = update.get('update_id')
        msg = update.get('message', {})
        if not msg:
            continue

        text = (msg.get('text') or '').strip()
        chat = msg.get('chat', {})
        chat_id = chat.get('id')
        from_user = msg.get('from', {})
        first_name = from_user.get('first_name', '')
        last_name = from_user.get('last_name', '')

        if not chat_id:
            continue

        # Procesar /start
        if text == '/start':
            # Verificar si ya está vinculado
            try:
                with db.get_connection() as conn:
                    with conn.cursor(as_dict=True) as cur:
                        cur.execute(
                            "SELECT ChatId, Activo FROM HUB_TelegramUsuarios WHERE ChatId = %s",
                            (chat_id,)
                        )
                        vinculado = cur.fetchone()
            except Exception:
                vinculado = None

            if vinculado and vinculado.get('Activo'):
                _send_message(chat_id, "Ya estas vinculado al HUB. Puedes recibir alertas.")
            else:
                # Intentar vinculación automática por nombre
                if _try_vinculate_by_name(first_name, last_name, chat_id):
                    _send_message(chat_id, "Vinculado automaticamente al HUB. Recibiras alertas cuando se generen eventos.")
                else:
                    _send_message(chat_id, "No pude vincularte automaticamente. Contacta al administrador para vincular tu cuenta.")

        # Acknowledge del update (marcar como procesado)
        try:
            _get_updates(offset=update_id + 1)
        except Exception:
            pass


def process_queue():
    """Procesa mensajes pendientes de la cola."""
    pendientes = db.dequeue_telegram_pendientes(limite=10)
    if not pendientes:
        return 0

    enviados = 0
    for msg in pendientes:
        try:
            adjunto = bytes(msg['Adjunto']) if msg.get('Adjunto') else None
            adj_nombre = msg.get('AdjuntoNombre')

            ok, respuesta = db.send_telegram_message(
                msg['ChatId'], msg['Texto'], adjunto, adj_nombre
            )

            intentos = (msg.get('Intentos') or 0) + 1

            # Si falló con adjunto, reintentar solo texto para no perder la alerta
            # (send_telegram_message ya intenta text-only internamente; esto es red de seguridad)
            if not ok and adjunto:
                ok, respuesta = db.send_telegram_message(
                    msg['ChatId'], msg['Texto'], None, None
                )
                if ok:
                    respuesta = f"{respuesta} (sin adjunto: adjunto rechazado)"

            if ok:
                db.marcar_telegram_enviado(msg['Id'], respuesta)
                enviados += 1
            else:
                db.marcar_telegram_fallado(msg['Id'], respuesta, intentos)
                if intentos < MAX_INTENTOS:
                    print(f"  [RETRY {intentos}/{MAX_INTENTOS}] msg {msg['Id']}: {respuesta}")
                else:
                    print(f"  [FAILED] msg {msg['Id']} after {intentos} attempts: {respuesta}")

        except Exception as e:
            print(f"  [ERROR] msg {msg.get('Id', '?')}: {e}")
            intentos = (msg.get('Intentos') or 0) + 1
            db.marcar_telegram_fallado(msg['Id'], str(e)[:200], intentos)

    return enviados


def cleanup():
    """Limpia registros antiguos de la cola."""
    try:
        historial = db.get_telegram_historial(limite=MAX_HISTORIAL)
        if len(historial) >= MAX_HISTORIAL:
            db.limpiar_telegram_historial(dias=30)
            print(f"  [CLEANUP] Historial limpiado (>30 días)")
    except Exception as e:
        print(f"  [CLEANUP ERROR] {e}")


def main():
    print("[telegram_worker] Iniciado. Procesando cola cada", LOOP_INTERVAL, "segundos")
    last_cleanup = time.time()

    while True:
        try:
            # Primero procesar mensajes entrantes (vinculación)
            process_incoming()

            # Luego enviar mensajes de la cola
            enviados = process_queue()
            if enviados > 0:
                print(f"  [OK] {enviados} mensaje(s) enviado(s)")

            if time.time() - last_cleanup > CLEANUP_INTERVAL:
                cleanup()
                last_cleanup = time.time()

        except KeyboardInterrupt:
            print("\n[telegram_worker] Detenido por el usuario.")
            break
        except Exception as e:
            print(f"  [LOOP ERROR] {e}")

        time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
    main()
