"""
Worker de Telegram: consume la cola HUB_TelegramQueue y envía mensajes
vía la API de Telegram. Corre bajo supervisor como 'telegram_worker'.

Flujo de cada ciclo (10 s por defecto):
  1. Al arrancar, y cada TELEGRAM_RECOVERY_SEG, devuelve a PENDIENTE las filas
     que quedaron ENVIANDO porque el worker murió o falló al marcarlas.
  2. Reclama UN mensaje (o los que diga TELEGRAM_MAX_POR_CICLO) y lo envía.
     Entre mensajes hay una pausa para que no lleguen en ráfaga.
  3. Si no había cola, atiende /start cada 60 s (vinculación por nombre).
  4. Escribe heartbeat y limpia el historial una vez por hora.

Por qué un mensaje por ciclo: el worker viejo tomaba 10, los enviaba seguidos y
luego dormía 20 s. Si la cola se acumulaba (por un SIGSEGV o por una caída de
SQL Server), al volver llegaba todo junto. Ahora el ritmo es constante y la cola
se drena de forma visible. Si el proceso se cuelga, el watchdog lo reinicia; si
se cae, supervisor lo levanta y la fila ENVIANDO se recupera sin perderla.
"""
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eccsa_db as db

from worker_heartbeat import heartbeat

# Ritmo. Con 1 mensaje cada 10 s una cola de 20 avisos se drena en ~3 minutos,
# no en un solo segundo, y los eventos nuevos salen con menos de 10 s de retraso.
LOOP_INTERVAL = int(os.environ.get('TELEGRAM_LOOP_SEG', '10'))
MAX_POR_CICLO = int(os.environ.get('TELEGRAM_MAX_POR_CICLO', '1'))
SEND_DELAY = float(os.environ.get('TELEGRAM_SEND_DELAY_SEG', '1.5'))
MAX_INTENTOS = int(os.environ.get('TELEGRAM_MAX_INTENTOS', '3'))
REINTENTO_SEG = int(os.environ.get('TELEGRAM_REINTENTO_SEG', '30'))
CLEANUP_INTERVAL = int(os.environ.get('TELEGRAM_CLEANUP_SEG', '3600'))
MAX_HISTORIAL = int(os.environ.get('TELEGRAM_MAX_HISTORIAL', '2000'))
INCOMING_INTERVAL = int(os.environ.get('TELEGRAM_INCOMING_SEG', '60'))
STUCK_MIN = int(os.environ.get('TELEGRAM_STUCK_MIN', '10'))
RECOVERY_INTERVAL = int(os.environ.get('TELEGRAM_RECOVERY_SEG', '300'))
WATCHDOG_SEG = int(os.environ.get('TELEGRAM_WATCHDOG_SEG', '240'))
RECUPERAR_AL_INICIAR = os.environ.get('TELEGRAM_RECUPERAR_AL_INICIAR', '1') == '1'

# Último update de Telegram ya procesado. Se queda en memoria: al reiniciar se
# vuelven a leer los pendientes, que Telegram conserva 24 h.
_last_update_id = None


def _log(msg):
    """Log con fecha: el de supervisor no la pone y sin ella no se puede saber
    si un mensaje salió a las 12:00 o seis horas después."""
    print(f"[telegram_worker {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _get_bot_token():
    """Token del bot desde HUB_Config. La clave es `token`, no `bot_token`."""
    try:
        cfg = db.get_telegram_config()
        return (cfg.get('token') or '').strip() or None
    except Exception as exc:
        _log(f"No se pudo leer el token: {exc}")
        return None


def _send_message(chat_id, text):
    """Envía un texto por el mismo camino que las alertas (maneja Markdown)."""
    try:
        ok, respuesta = db.send_telegram_message(chat_id, text, None, None)
        return bool(ok), respuesta
    except Exception as exc:
        return False, str(exc)[:200]


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
                    "SELECT Id, Nombre FROM HUB_Users "
                    "WHERE LTRIM(RTRIM(Nombre)) = %s AND Activo = 1",
                    (full_name,),
                )
                match = cur.fetchone()

                # Si no hay match exacto, intentar con LIKE
                if not match:
                    cur.execute(
                        "SELECT Id, Nombre FROM HUB_Users "
                        "WHERE Nombre LIKE %s AND Activo = 1",
                        (f"%{full_name}%",),
                    )
                    candidates = cur.fetchall()
                    if len(candidates) == 1:
                        match = candidates[0]

                if match:
                    user_id = match['Id']
                    cur.execute(
                        "SELECT ChatId, Activo FROM HUB_TelegramUsuarios "
                        "WHERE IdUsuario = %s",
                        (user_id,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        if (str(existing['ChatId']) != str(chat_id)
                                or not existing.get('Activo')):
                            cur.execute(
                                "UPDATE HUB_TelegramUsuarios "
                                "SET ChatId = %s, Activo = 1, NombreTelegram = %s "
                                "WHERE IdUsuario = %s",
                                (chat_id, full_name, user_id),
                            )
                            conn.commit()
                        return True
                    cur.execute(
                        "INSERT INTO HUB_TelegramUsuarios "
                        "(IdUsuario, ChatId, NombreTelegram, Activo) "
                        "VALUES (%s, %s, %s, 1)",
                        (user_id, chat_id, full_name),
                    )
                    conn.commit()
                    return True
    except Exception as exc:
        _log(f"[VINCULATE ERROR] {exc}")
    return False


def process_incoming():
    """Procesa mensajes entrantes del bot (vinculación automática)."""
    global _last_update_id

    if not _get_bot_token():
        return

    try:
        updates = db.get_telegram_updates_offset(
            offset=(_last_update_id + 1 if _last_update_id is not None else None),
            timeout=5,
        )
    except Exception as exc:
        _log(f"[INCOMING ERROR] {exc}")
        return

    for update in updates:
        update_id = update.get('update_id')
        if update_id is not None:
            _last_update_id = max(_last_update_id or 0, int(update_id))

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

        if text == '/start':
            try:
                with db.get_connection() as conn:
                    with conn.cursor(as_dict=True) as cur:
                        cur.execute(
                            "SELECT ChatId, Activo FROM HUB_TelegramUsuarios "
                            "WHERE ChatId = %s",
                            (chat_id,),
                        )
                        vinculado = cur.fetchone()
            except Exception:
                vinculado = None

            if vinculado and vinculado.get('Activo'):
                _send_message(chat_id, "Ya estás vinculado al HUB. Puedes recibir alertas.")
            elif _try_vinculate_by_name(first_name, last_name, chat_id):
                _send_message(
                    chat_id,
                    "Vinculado automáticamente al HUB. Recibirás alertas cuando se generen eventos.",
                )
            else:
                _send_message(
                    chat_id,
                    "No pude vincularte automáticamente. Contacta al administrador para vincular tu cuenta.",
                )


def _es_rate_limit(respuesta):
    texto = (respuesta or '').lower()
    return 'too many requests' in texto or 'retry after' in texto


def _retry_after(respuesta, por_defecto=30):
    """Saca el `retry after N` que manda Telegram; si no viene, usa 30 s."""
    m = re.search(r'retry\s+after\s+(\d+)', respuesta or '', re.IGNORECASE)
    if not m:
        return por_defecto
    return max(1, min(60, int(m.group(1))))


def _marcar_enviado(msg_id, respuesta):
    try:
        db.marcar_telegram_enviado(msg_id, respuesta)
    except Exception as exc:
        _log(f"No se pudo marcar ENVIADO el msg {msg_id}: {exc}")


def _marcar_fallado(msg_id, respuesta, intentos):
    try:
        db.marcar_telegram_fallado(msg_id, respuesta, intentos,
                                   max_intentos=MAX_INTENTOS)
    except Exception as exc:
        _log(f"No se pudo marcar FALLADO el msg {msg_id}: {exc}")


def _enviar_uno(msg):
    """Envía un mensaje reclamado. Devuelve 1 si salió, 0 si no."""
    msg_id = msg.get('Id', '?')
    intentos_previos = int(msg.get('Intentos') or 0)
    intentos = intentos_previos + 1

    try:
        adjunto = bytes(msg['Adjunto']) if msg.get('Adjunto') else None
        adj_nombre = msg.get('AdjuntoNombre')

        # `send_telegram_message` ya reintenta el adjunto como documento y, si
        # aun así falla, manda solo el texto. No repetir ese fallback aquí: dos
        # llamadas podrían entregar el mismo texto dos veces.
        ok, respuesta = db.send_telegram_message(
            msg['ChatId'], msg['Texto'], adjunto, adj_nombre
        )

        if ok:
            _marcar_enviado(msg_id, respuesta)
            _log(f"msg {msg_id} enviado: {respuesta}")
            return 1

        # 429 no consume intento: el mensaje no está mal, solo hay que esperar.
        if _es_rate_limit(respuesta):
            _marcar_fallado(msg_id, respuesta, intentos_previos)
            espera = _retry_after(respuesta)
            _log(f"msg {msg_id} rate limit; espero {espera}s")
            time.sleep(espera)
            return 0

        _marcar_fallado(msg_id, respuesta, intentos)
        if intentos < MAX_INTENTOS:
            _log(f"[RETRY {intentos}/{MAX_INTENTOS}] msg {msg_id}: {respuesta}")
        else:
            _log(f"[FALLADO] msg {msg_id} tras {intentos} intentos: {respuesta}")
        return 0

    except Exception as exc:
        _marcar_fallado(msg_id, str(exc)[:200], intentos)
        _log(f"[ERROR] msg {msg_id}: {exc}")
        return 0


def process_queue():
    """Reclama y envía la cola. Devuelve (enviados, reclamados)."""
    pendientes = db.tomar_telegram_pendientes(
        limite=max(1, MAX_POR_CICLO), reintento_seg=REINTENTO_SEG
    )
    if not pendientes:
        return 0, 0

    enviados = 0
    for i, msg in enumerate(pendientes):
        if i and SEND_DELAY > 0:
            time.sleep(SEND_DELAY)
        enviados += _enviar_uno(msg)
    return enviados, len(pendientes)


def cleanup():
    """Limpia registros antiguos de la cola."""
    try:
        historial = db.get_telegram_historial(limite=MAX_HISTORIAL)
        if len(historial) >= MAX_HISTORIAL:
            db.limpiar_telegram_historial(dias=30)
            _log("Historial limpiado (>30 días)")
    except Exception as exc:
        _log(f"[CLEANUP ERROR] {exc}")


def _recuperar_atascados(minutos):
    """Devuelve a la cola lo que quedó ENVIANDO y ya lleva demasiado tiempo."""
    try:
        n = db.recuperar_telegram_atascados(minutos=minutos,
                                            max_intentos=MAX_INTENTOS)
        if n:
            _log(f"Recuperados {n} mensaje(s) que quedaron ENVIANDO")
        return n
    except Exception as exc:
        _log(f"No se pudieron recuperar los atascados: {exc}")
        return 0


def _iniciar_watchdog(ultimo_ciclo):
    """Si un ciclo no termina en WATCHDOG_SEG, reinicia el proceso.

    Un `timeout` de pymssql o una subida de Telegram puede quedarse colgada sin
    lanzar excepción. El heartbeat se escribe antes de dormir; si deja de
    cambiar, el panel lo nota, pero el worker no se recupera solo. Este hilo lo
    fuerza para que supervisor lo arranque de nuevo.
    """
    if WATCHDOG_SEG <= 0:
        return

    def _vigilar():
        while True:
            time.sleep(max(5, min(30, WATCHDOG_SEG // 4)))
            if time.time() - ultimo_ciclo[0] > WATCHDOG_SEG:
                _log(f"WATCHDOG: ciclo atascado > {WATCHDOG_SEG}s; reinicio forzado")
                try:
                    heartbeat('telegram_worker', detail=f"ATASCADO > {WATCHDOG_SEG}s")
                except Exception:
                    pass
                os._exit(75)

    hilo = threading.Thread(target=_vigilar, name='telegram-watchdog', daemon=True)
    hilo.start()


def main():
    _log(
        f"Iniciado. loop={LOOP_INTERVAL}s max_por_ciclo={MAX_POR_CICLO} "
        f"pausa={SEND_DELAY}s watchdog={WATCHDOG_SEG}s"
    )

    ultimo_ciclo = [time.time()]
    _iniciar_watchdog(ultimo_ciclo)

    # Al arrancar no hay otro worker enviando: recuperar todo lo ENVIANDO ya.
    _recuperar_atascados(0 if RECUPERAR_AL_INICIAR else STUCK_MIN)

    last_cleanup = time.time()
    last_incoming = 0
    last_recovery = time.time()

    while True:
        ultimo_ciclo[0] = time.time()
        try:
            enviados, reclamados = process_queue()
            if enviados:
                _log(f"{enviados} mensaje(s) enviado(s)")

            # La vinculación (/start) espera a que la cola esté sana: primero
            # son los avisos, y solo cuando no hay nada que enviar se atiende.
            if reclamados == 0 and (time.time() - last_incoming) >= INCOMING_INTERVAL:
                process_incoming()
                last_incoming = time.time()

            if (time.time() - last_cleanup) > CLEANUP_INTERVAL:
                cleanup()
                last_cleanup = time.time()

            # Si una fila quedó ENVIANDO porque falló el UPDATE al marcarla,
            # no hay que esperar a que el proceso se reinicie: cada tanto se
            # devuelve a PENDIENTE y se reintenta.
            if (time.time() - last_recovery) >= RECOVERY_INTERVAL:
                _recuperar_atascados(STUCK_MIN)
                last_recovery = time.time()

            heartbeat(
                'telegram_worker',
                detail=f"enviados={enviados}, reclamados={reclamados}",
                count=enviados,
            )

        except KeyboardInterrupt:
            _log("Detenido por el usuario.")
            break
        except Exception as exc:
            _log(f"[LOOP ERROR] {exc}")
            try:
                heartbeat('telegram_worker', detail=f"ERROR: {str(exc)[:250]}")
            except Exception:
                pass

        ultimo_ciclo[0] = time.time()
        time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
    main()
