"""
Worker de WhatsApp: consume la cola HUB_WhatsappQueue y entrega los mensajes
por la API de OpenWA. Corre bajo supervisor como 'openwa_worker'.

Es el gemelo de `cron_sync_telegram.py`, con una diferencia importante: no hay
que vincular cuentas ni escuchar el bot, asi que todo el trabajo es entregar lo
que hay en la cola.

Flujo de cada ciclo:
  1. Lee hasta 10 mensajes pendientes, del mas viejo al mas nuevo
  2. Entrega cada uno: solo texto, documento (PDF) o imagen (foto)
  3. Marca ENVIADO, o suma un intento y deja PENDIENTE hasta 3
  4. Una vez por hora borra lo cerrado hace mas de 30 dias
  5. Duerme 20 segundos y repite

Sobre los reintentos: si OpenWA esta caido no se pierden los avisos, quedan
PENDIENTE y se reintentan. Un adjunto pesa mucho, asi que la cola tambien se
limpia sola; si se llegara a acumular, lo que se pierde es el historial viejo,
nunca un aviso sin enviar.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eccsa_db as db
import openwa_client as ow

LOOP_INTERVAL = 20        # segundos entre ciclos
MAX_INTENTOS = 3
LOTE = 10                 # mensajes por ciclo
CLEANUP_INTERVAL = 3600   # una hora


def process_queue(lote=LOTE, max_intentos=MAX_INTENTOS):
    """
    Entrega un lote de la cola. Devuelve cuantos salieron.

    Cada mensaje se manda con el texto ya renderizado que se guardo al encolarlo:
    aqui no se vuelve a componer, porque si la plantilla cambio despues el
    aviso encolado debe salir como se encolo.
    """
    enviados = 0
    for fila in db.dequeue_openwa_pendientes(lote):
        texto = fila.get('Texto') or ''
        chat = fila.get('ChatId') or ''
        adjunto = fila.get('Adjunto')
        nombre = fila.get('AdjuntoNombre')
        tipo = (fila.get('AdjuntoTipo') or '').lower()
        intentos = (fila.get('Intentos') or 0) + 1

        if adjunto and tipo == 'imagen':
            ok, detalle = ow.enviar_imagen(chat, adjunto, nombre or 'imagen.jpg', texto)
        elif adjunto and tipo == 'documento':
            ok, detalle = ow.enviar_documento(chat, adjunto, nombre or 'documento.pdf', texto)
        else:
            ok, detalle = ow.enviar_texto(chat, texto)

        if ok:
            db.marcar_openwa_enviado(fila['Id'])
            enviados += 1
            print(f"  [OK] {fila['IdEvento']} -> {chat}")
        else:
            # El detalle nunca trae la API key: el cliente la tapa antes de
            # devolverlo, porque esto va al log.
            db.marcar_openwa_fallido(fila['Id'], detalle, intentos, max_intentos)
            estado = 'FALLADO' if intentos >= max_intentos else 'reintentando'
            print(f"  [FALLA {intentos}/{max_intentos}] {fila['IdEvento']} -> {chat}: "
                  f"{detalle} ({estado})")
    return enviados


def cleanup(dias=30):
    """Borra lo ya cerrado hace mucho. Los PENDIENTE no se tocan jamas."""
    try:
        if db.limpiar_openwa_historial(dias):
            print(f"  [LIMPIEZA] historial de mas de {dias} dias eliminado")
    except Exception as e:
        print(f"  [LIMPIEZA ERROR] {e}")


def main():
    cfg = ow.obtener_config()
    print("[openwa_worker] Iniciado. Procesando cola cada", LOOP_INTERVAL, "segundos")
    print(f"[openwa_worker] destino: {cfg['base_url'] or '(sin definir)'} · "
          f"sesion: {cfg['session_id'] or '(sin definir)'} · "
          f"API key: {'si' if cfg['api_key'] else 'NO (se pega en la pestana)'}")
    if not cfg['api_key']:
        # Aviso una vez al arrancar y se sigue: la cola se acumula sola y en
        # cuanto se pegue la key sale todo lo pendiente, en orden.
        print("[openwa_worker] sin API key: los avisos se iran acumulando en la "
              "cola hasta que se pegue en Notificaciones > Conexion > OpenWA")

    last_cleanup = time.time()
    while True:
        try:
            enviados = process_queue()
            if enviados:
                print(f"  [RESUMEN] {enviados} aviso(s) entregado(s)")
            if time.time() - last_cleanup > CLEANUP_INTERVAL:
                cleanup()
                last_cleanup = time.time()
        except KeyboardInterrupt:
            print("\n[openwa_worker] Detenido.")
            break
        except Exception as e:
            print(f"  [LOOP ERROR] {e}")
        time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
    main()
