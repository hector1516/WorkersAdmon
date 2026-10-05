"""
cron_sync_mailbox.py — el proceso de sincronización de ECCSA_Mailbox.

Es lo que arranca supervisor. Este archivo solo orquesta: la lógica de sincronizar
está en `mailbox_worker/sync.py`, la de adjuntos en `stream.py` y la de envío en
`smtp.py`. Que el loop de un worker no mida 500 líneas es la razón por la que se
puede tocar un módulo sin miedo a romper el arranque.

────────────────────────────────────────────────────────────────────────────
QUÉ HACE, EN ORDEN

  1. Toma un candado de aplicación (`sp_getapplock`): una sola instancia.
  2. Levanta el servidor de adjuntos (`stream.servir`) en un hilo.
  3. Por cada cuenta `ACTIVA`, un hilo propio con su reloj propio.
  4. Cada hilo: cola de operaciones → sincronización → reglas → envío → push →
     retención.
  5. Heartbeat por hilo, para que el panel diga la verdad en vez de suponer.

────────────────────────────────────────────────────────────────────────────
LOS TRES GUARDAS DE ESTE ARCHIVO (leer antes de tocarlo)

**1. El candado — una sola instancia, impuesta por el servidor.**

Dos workers de correo a la vez NO es redundancia: los dos drenarían la misma
`HUB_MailboxColaEnvio` (correos duplicados para el cliente), los dos aplicarían
las mismas reglas y los dos abrirían sockets IMAP sobre las mismas cuentas.

`sp_getapplock` es un candado del servidor atado a la conexión, no a un archivo:
sobrevive a que maten el proceso a la fuerza y no depende de que /data sea un
volumen compartido. Si no se obtiene, el proceso **se sale** con código 3 en vez
de repetir el trabajo en silencio.

El candado es `Session`, no `Transaction`: se mantiene mientras la conexión viva
y se usa `sp_releaseapplock` al soltar. Cada hilo de cuenta usa su propia
conexión (el `threading.local` de `db.py`), así que el candado tiene que vivir en
la del hilo principal.

**2. `MAILBOX_DRY_RUN` — no escribir en el buzón.**

Con `=1` el worker LEE IMAP y llena el índice, pero no drena la cola, no ejecuta
reglas que muevan mensajes y no envía. Sirve para correr este worker en paralelo
al viejo durante el corte: los dos pueden LEER sin problema; lo que no puede ser
es escribir los dos.

**3. `MAILBOX_SYNC_ENABLED` — apagar sin editar código.**

Deja el proceso vivo y el panel en verde, pero sin sincronizar. Es lo que se usa
para el corte: se apaga un lado, se verifica el otro, y no al revés.

Variables de entorno (la contraseña de la BD y el token de streaming son
obligatorias; el resto tienen defaults sensatos):

  HUB_DB_SERVER / HUB_DB_USER / HUB_DB_PASSWORD / HUB_DB_DATABASE
  MAILBOX_ENCRYPTION_KEY   o MAILBOX_KEY_FILE   (obligatoria: sin llave no hay
                                                  credenciales descifrables)
  MAILBOX_STREAM_TOKEN     obligatorio: sin esto la app no puede descargar
  MAILBOX_VAPID_PRIVATE    la MISMA que tiene el service worker de la app
  MAILBOX_SYNC_PERIOD      300
  MAILBOX_SYNC_ENABLED     1
  MAILBOX_DRY_RUN          0
  MAILBOX_CONCURRENCIA     cuentas en paralelo (default 4)
  MAILBOX_DATOS_DIR        /data/mailbox  (el volumen COMPARTIDO con la app)
"""

import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mailbox_worker import almacen                      # noqa: E402
from mailbox_worker.config import aviso_arranque, settings   # noqa: E402
from mailbox_worker.db import ping, soltar_candado, tomar_candado   # noqa: E402
from mailbox_worker.sync import (                       # noqa: E402
    cuentas_activas,
    sincronizar_cuenta,
    validar_pendientes,
)

try:
    from worker_heartbeat import heartbeat
except ImportError:            # el worker tiene que arrancar aunque falte el panel
    def heartbeat(_worker, detail="", count=None):
        print(f"[mailbox_worker] {detail}", flush=True)


WORKER = "mailbox_worker"
CANDADO = "mailbox_worker_sync"

_parar = threading.Event()


def _al_parar(_signum=None, _frame=None):
    """SIGTERM/SIGINT. Supervisor manda SIGTERM; sin esto el proceso lo ignora y
    supervisor lo mata con SIGKILL después del timeout, con el ciclo a medias."""
    print(f"[{WORKER}] señal de parada recibida", flush=True)
    _parar.set()


# ═══════════════════════════════════════════════════════════════════════════════
# Ciclo de una cuenta
# ═══════════════════════════════════════════════════════════════════════════════

def _ciclo_cuenta(cuenta: dict):
    """
    El reloj de UNA cuenta, en su hilo.

    Cada cuenta lleva su propio reloj a propósito: una cuenta lenta (o caída, con
    el servidor de correo sin responder) no debe rezagar a las demás. Un `for`
    secuencial haría que una cuenta mala retrasara el correo de todo el mundo.

    El `threading.local` de `db.py` hace que este hilo tenga su propia conexión
    SQL, así que un ciclo lento no bloquea a los demás.
    """
    nombre = cuenta.get("Email") or cuenta.get("Id")
    while not _parar.is_set():
        inicio = time.time()
        detalle = f"cuenta {nombre}"
        try:
            if _parar.is_set():
                detalle = f"cuenta {nombre}: parado, no se sincronizó"
            else:
                resumen = sincronizar_cuenta(cuenta)
                detalle = _resumen_a_texto(resumen) + f" · {time.time() - inicio:.1f}s"
                if resumen.get("error"):
                    print(f"[{WORKER}] {nombre}: {resumen['error']}", flush=True)
        except Exception as exc:
            # Un ciclo fallido NO tumba el hilo: la próxima vuelta reintenta.
            # Si el procesomuriera, ninguna cuenta volvería a sincronizar.
            detalle = f"cuenta {nombre}: ERROR {exc}"[:280]
            print(f"[{WORKER}] ciclo de {nombre} falló: "
                  f"{type(exc).__name__}: {exc}", flush=True)

        heartbeat(WORKER, detail=detalle)

        # Duerme el resto del periodo, pero despierta cada segundo para que el
        # apagado no espere cinco minutos.
        for _ in range(max(1, settings.sync_period)):
            if _parar.wait(1):
                return


def _resumen_a_texto(r: dict) -> str:
    """El resumen del ciclo, para el heartbeat del panel. Solo lo que no es cero."""
    partes = [f"cuenta {r.get('cuenta')}"]
    for clave, etiqueta in (("nuevos", "nuevos"), ("operaciones", "ops"),
                            ("enviados", "enviados"), ("purgados", "purgados"),
                            ("push", "push")):
        if r.get(clave):
            partes.append(f"{etiqueta}={r[clave]}")
    if r.get("error"):
        partes.append(f"ERROR: {r['error'][:120]}")
    return " · ".join(partes)


# ═══════════════════════════════════════════════════════════════════════════════
# Servidor de adjuntos
# ═══════════════════════════════════════════════════════════════════════════════

def _hilo_stream():
    """
    El servidor de adjuntos, en un hilo aparte.

    Va en su propio hilo y no en el principal por una razón concreta: `serve_forever`
    no vuelve. Si corriera en el hilo principal, el loop de sincronización nunca
    empezaría.

    Si el servidor se cae (un `bind` porque el puerto quedó tomado tras un reinicio
    rápido), el proceso NO muere: la sincronización sigue, y lo que se pierde es la
    descarga de adjuntos, que tiene un error visible en la app. Morir sería peor.
    """
    from mailbox_worker import stream
    while not _parar.is_set():
        try:
            stream.servir()
            return
        except OSError as exc:
            print(f"[{WORKER}] el servidor de adjuntos no pudo arrancar: {exc}", flush=True)
            print(f"[{WORKER}] los adjuntos NO se van a poder descargar. La "
                  f"sincronización sigue.", flush=True)
            heartbeat(WORKER, detail="ERROR: servidor de adjuntos caído; "
                                     "la sincronización sigue")
            for _ in range(30):        # reintenta un minuto
                if _parar.wait(2):
                    return
        except Exception as exc:
            print(f"[{WORKER}] el servidor de adjuntos murió: {exc}", flush=True)
            for _ in range(30):
                if _parar.wait(2):
                    return


# ═══════════════════════════════════════════════════════════════════════════════
# Arranque
# ═══════════════════════════════════════════════════════════════════════════════

def _avisar_arranque(n: int):
    print(f"[{WORKER}] arrancando · {n} cuenta(s) · periodo {settings.sync_period}s · "
          f"concurrencia {settings.concurrencia} · {aviso_arranque()}", flush=True)
    if not settings.sync_enabled:
        print(f"[{WORKER}] MAILBOX_SYNC_ENABLED=0: el proceso queda vivo pero NO "
              f"sincroniza. Sirve para el corte sin apagar el contenedor.", flush=True)
    if settings.dry_run:
        print(f"[{WORKER}] MAILBOX_DRY_RUN=1: lee IMAP y llena el índice, pero NO "
              f"escribe en el buzón ni envía. Es el modo del corte.", flush=True)
    if not settings.vapid_private_key:
        print(f"[{WORKER}] sin MAILBOX_VAPID_PRIVATE: no habrá push de correo nuevo. "
              f"Tiene que ser la MISMA que la app.", flush=True)
    if not settings.encryption_key and not settings.key_file:
        print(f"[{WORKER}] sin MAILBOX_ENCRYPTION_KEY ni MAILBOX_KEY_FILE: no se "
              f"puede DESCIFRAR ninguna credencial. Revisa el despliegue.", flush=True)


def main():
    signal.signal(signal.SIGTERM, _al_parar)
    signal.signal(signal.SIGINT, _al_parar)

    # La BD primero: sin ella no hay nada. Se falla rápido y se dice por qué, en
    # vez de arrancar un loop que avisa del mismo error 20 veces por hora.
    if not ping():
        print(f"[{WORKER}] no pude conectarme a la BD. Revisa HUB_DB_* y que el "
              f"contenedor tenga red hacia SQL Server.", flush=True)
        return 4

    # Una sola instancia.
    if not tomar_candado(CANDADO):
        print(f"[{WORKER}] ya hay otro {WORKER} corriendo (candado {CANDADO}). "
              f"No arranco un segundo.", flush=True)
        return 3

    if not settings.sync_enabled:
        _avisar_arranque(0)
        # Apagado pero vivo: el panel lo muestra y el heartbeat dice la verdad,
        # en vez de aparecer como caído.
        while not _parar.is_set():
            heartbeat(WORKER, detail="desactivado con MAILBOX_SYNC_ENABLED=0")
            _parar.wait(60)
        soltar_candado()
        return 0

    # El volumen compartido tiene que existir ANTES de que un hilo escriba en él.
    # Si falta, el `makedirs` de `almacen` lo crea en el path equivocado o falla
    # con permisos, y el síntoma es "los adjuntos no llegan".
    try:
        os.makedirs(settings.datos_dir, exist_ok=True)
        os.makedirs(settings.cache_dir, exist_ok=True)
    except OSError as exc:
        print(f"[{WORKER}] no puedo preparar {settings.datos_dir}: {exc}. "
              f"¿está montado el volumen mailbox_data?", flush=True)
        soltar_candado()
        return 5

    # Las cuentas en PENDIENTE se validan antes de arrancar los hilos: si una
    # credencial está mal, se marca ERROR y no entra al loop a fallar cada 5 min.
    try:
        validar_pendientes()
    except Exception as exc:
        print(f"[{WORKER}] no pude validar las cuentas pendientes: {exc}", flush=True)

    cuentas = cuentas_activas()
    _avisar_arranque(len(cuentas))
    if not cuentas:
        print(f"[{WORKER}] no hay cuentas ACTIVA en HUB_MailboxCuentas. Revisa el "
              f"panel (Correo) o que las cuentas no estén en ERROR.", flush=True)

    # El servidor de adjuntos primero: si la app llama antes de que esté listo,
    # la primera descarga de un adjunto falla con connection refused.
    threading.Thread(target=_hilo_stream, daemon=True, name=f"{WORKER}-stream").start()

    # La cache se limpia una vez por arranque y luego cada hora dentro del ciclo
    # del hilo supervisor. Los adjuntos de hace 6 h no se van a volver a pedir.
    try:
        borrados = almacen.cache_limpiar()
        print(f"[{WORKER}] cache: {almacen.cache_tamano_mb():.1f} MB, "
              f"{borrados} archivo(s) vencido(s)", flush=True)
    except Exception as exc:
        print(f"[{WORKER}] no pude limpiar la cache: {exc}", flush=True)

    # Un hilo por cuenta, escalonado. El escalón no es decorativo: N cuentas
    # abriendo N conexiones IMAP en el mismo segundo se caen entre ellas en
    # Gmail (que tiene un tope de conexiones simultáneas por cuenta, no por
    # usuario), y además el pico de memoria se multiplica.
    limite = max(1, int(settings.concurrencia or 1))
    activos = 0
    for cuenta in cuentas:
        if _parar.is_set():
            break
        while activos >= limite and not _parar.is_set():
            time.sleep(0.5)
            activos = sum(1 for t in threading.enumerate()
                          if t.name.startswith(f"{WORKER}-cuenta"))
        if _parar.is_set():
            break
        nombre = cuenta.get("Email") or cuenta.get("Id")
        t = threading.Thread(target=_ciclo_cuenta, args=(cuenta,),
                             daemon=True, name=f"{WORKER}-cuenta-{nombre}")
        t.start()
        activos += 1
        _parar.wait(settings.escalonar_arranque)

    # Hilo supervisor: mantiene vivo el proceso y avisa si todos los hilos de
    # cuenta se hubieran caído. Sin esto, un `return` accidental dentro del loop
    # dejaría el proceso vivo sin sincronizar nada y el panel en verde.
    while not _parar.is_set():
        vivos = sum(1 for t in threading.enumerate()
                    if t.name.startswith(f"{WORKER}-cuenta"))
        if not vivos and cuentas:
            print(f"[{WORKER}] no queda ningún hilo de cuenta vivo; revisa los logs",
                  flush=True)
            heartbeat(WORKER, detail="ERROR: ningún hilo de cuenta vivo")
        else:
            # Barrido de la cache: los adjuntos cacheados que nadie volvió a
            # pedir se van, para que el volumen no crezca sin control.
            try:
                almacen.cache_limpiar()
                from mailbox_worker.push import purgar_muertas
                purgar_muertas()
            except Exception:
                pass
        _parar.wait(3600)

    soltar_candado()
    print(f"[{WORKER}] detenido", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())