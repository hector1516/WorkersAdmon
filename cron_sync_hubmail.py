"""
cron_sync_hubmail.py — Worker de sincronización de correo (HUBMail → Mailbox)
================================================================================
Este es el `hubmail_worker`: el proceso que mantiene la caché de MySQL al día.
Sustituye al hilo `_background_sync_loop` que vivía DENTRO del backend de
HUBMail (`app/main.py:102-130`), que ya no lo ejecuta.

Qué hace, en este orden, una vez por ciclo y por cuenta canónica:

  1. `sync_account(id)` → refresca carpetas y mensajes en la caché, baja
     adjuntos, aplica retención y dispara el push si llegaron correos nuevos.
  2. Dentro de `sync_account` está `_apply_pending_ops(id)`: HERE se ejecutan
     contra IMAP las operaciones que la app dejó en `HUBMAIL_PendingOps`
     (leído/no leído, flag, borrar, mover, cross-move, APPEND a Enviados).

Reparto con la app: la app escribe en MySQL y encola; este worker es el único
que habla con el buzón. Por eso el worker corre en `workersadmon` y no en la app:
una app que se reinicia no debe dejar de sincronizar, y una app que se despliega
no debe arrastrar un ciclo de IMAP de 5 minutos.

────────────────────────────────────────────────────────────────────────────
LOS TRES GUARDAS DE ESTE ARCHIVO (leer antes de tocarlo)
────────────────────────────────────────────────────────────────────────────

**1. `GET_LOCK` — una sola instancia, impuesta por el servidor MySQL.**
   Dos workers de correo a la vez NO es "redundancia": los dos drenarían la
   misma cola `HUBMAIL_PendingOps`, el `APPEND` a Enviados se ejecutaría dos
   veces (correos duplicados en el buzón del cliente) y los dos abrirían
   conexiones IMAP sobre las mismas cuentas. `GET_LOCK` es un candado del
   servidor tiedo a la conexión, no a un archivo: sobrevive a que maten el
   proceso a la fuerza y no depende de que /data sea un volumen compartido. Si
   no se obtiene, el proceso **se sale** en vez de repetir el trabajo.

**2. `HUBMAIL_SYNC_DRYRUN` — no escribir en el buzón.**
   Con `=1` el worker lee IMAP y llena la caché pero no drena la cola ni ejecuta
   los filtros que escriben. Sirve para correr este worker en paralelo al de
   HUBMail durante la migración: los dos pueden LEER sin problema, lo que no
   puede ser es escribir los dos.

**3. `HUBMAIL_SYNC_ENABLED` — apagar sin editar código.**
   Deja el proceso vivo y el panel en verde, pero sin sincronizar. Es lo que se
   usa para el corte: se apaga un lado, se verifica el otro, y no al revés.

Variables de entorno (todas opcionales salvo la contraseña de MySQL):

  HUBMAIL_DB_PASSWORD     obligatorio
  HUBMAIL_DB_SERVER       172.26.90.159
  HUBMAIL_DB_NAME         HUBMAIL
  HUBMAIL_SYNC_PERIOD     300   (segundos entre ciclos)
  HUBMAIL_SYNC_ENABLED    1
  HUBMAIL_SYNC_DRYRUN     0
  HUBMAIL_ENCRYPTION_KEY  (preferido) o HUBMAIL_KEY_FILE (archivo del volumen)
  HUBMAIL_ATTACHMENTS_DIR /data/attachments
  HUBMAIL_VAPID_PUBLIC / HUBMAIL_VAPID_PRIVATE
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, "/app")

from hubmail_worker.config import settings          # noqa: E402
from hubmail_worker.crypto import fuente_clave      # noqa: E402
from hubmail_worker.db import get_conn              # noqa: E402
from hubmail_worker.sync import (                   # noqa: E402
    get_sync_progress,
    sync_account,
)
from worker_heartbeat import heartbeat              # noqa: E402

WORKER = "hubmail_worker"

# Candado del servidor. El nombre va con prefijo de la app para no chocar con
# ningún otro GET_LOCK del ecosistema.
LOCK_NAME = "eccsa_hubmail_sync_worker"
LOCK_TIMEOUT_S = 3          # no esperamos: si está tomado, hay otro worker vivo

_parar = threading.Event()
_lock_conn = None


# ─────────────────────────────────────────────────────────────────────────────
# Apagado limpio
# ─────────────────────────────────────────────────────────────────────────────
def _al_parar(signum, _frame):
    """SIGTERM/SIGINT: se marca la parada y se sueltan los hilos.

    Se registra para que `docker stop` (que manda SIGTERM y espera 10 s) no
    corte el ciclo a la mitad de una escritura en IMAP.
    """
    print(f"[{WORKER}] señal {signum}: parando tras el ciclo en curso", flush=True)
    _parar.set()


# ─────────────────────────────────────────────────────────────────────────────
# Candado de instancia única (lo impone MySQL, no el filesystem)
# ─────────────────────────────────────────────────────────────────────────────
def _tomar_candado():
    """Intenta el GET_LOCK. Deja la conexión abierta mientras se lo tenga.

    Devuelve True si este proceso es el worker. False si ya hay otro.
    """
    global _lock_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (LOCK_NAME, LOCK_TIMEOUT_S))
        fila = cur.fetchone()
        # El wrapper devuelve dict si se pidió as_dict; aquí es tupla.
        obtenido = fila[0] if fila else None
        conn.commit()
    except Exception as exc:
        print(f"[{WORKER}] no pude pedir el candado: {exc}", flush=True)
        try:
            conn.close()
        except Exception:
            pass
        return False

    if obtenido != 1:
        print(f"[{WORKER}] YA HAY UN WORKER DE CORREO CORRIENDO "
              f"(candado {LOCK_NAME!r} tomado). Este proceso se sale para no "
              f"duplicar sincronización. Apaga el otro antes de encender este.",
              flush=True)
        try:
            conn.close()
        except Exception:
            pass
        return False

    _lock_conn = conn
    return True


def _soltar_candado():
    global _lock_conn
    if _lock_conn is None:
        return
    try:
        cur = _lock_conn.cursor()
        cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
        _lock_conn.commit()
    except Exception:
        pass
    finally:
        try:
            _lock_conn.close()
        except Exception:
            pass
        _lock_conn = None


# ─────────────────────────────────────────────────────────────────────────────
# Inventario de cuentas
# ─────────────────────────────────────────────────────────────────────────────
def _cuentas_canonicas():
    """Cuentas IMAP reales (las compartidas apuntan a su canónica).

    Es el mismo criterio que usaba el hilo de HUBMail: `CanonicalAccountID IS
    NULL`. Sincronizar una cuenta compartida por su cuenta real contaría doble.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("SELECT AccountID FROM HUBMAIL_Accounts WHERE CanonicalAccountID IS NULL "
                    "ORDER BY AccountID")
        return [int(r["AccountID"]) for r in cur.fetchall()]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Ciclo
# ─────────────────────────────────────────────────────────────────────────────
def _ciclo_cuenta(account_id):
    """Un hilo por cuenta, igual que el hilo de HUBMail.

    Cada cuenta lleva su propio reloj: una cuenta lenta (o caída) no rezaga a
    las demás, que es justo el motivo por el que el diseño original era un hilo
    por cuenta y no un `for` secuencial.
    """
    while not _parar.is_set():
        inicio = time.time()
        detalle = f"cuenta {account_id}"
        try:
            if not _parar.is_set():
                sync_account(account_id)
                prog = get_sync_progress().get(account_id) or {}
                if prog:
                    # `get_sync_progress` es un dict EN MEMORIA de este proceso;
                    # se lee aquí y no desde la app, que ya no comparte memoria.
                    for clave in ("last_folder", "messages", "new", "error"):
                        if clave in prog:
                            detalle += f" · {clave}={prog[clave]}"
                detalle += f" · {time.time() - inicio:.1f}s"
            else:
                detalle = f"cuenta {account_id}: parado, no se sincronizó"
        except Exception as exc:
            # Un ciclo fallido NO debe tumbar el hilo: la próxima vuelta reintenta.
            detalle = f"cuenta {account_id}: ERROR {exc}"[:280]
            print(f"[{WORKER}] ciclo de {account_id} falló: {exc}", flush=True)

        heartbeat(WORKER, detail=detalle)

        # Duerme el resto del periodo, pero despierto cada segundo para que el
        # apagado no espere cinco minutos.
        espera = settings.sync_period
        for _ in range(max(1, espera)):
            if _parar.wait(1):
                return


def _avisar_arranque(ids):
    modo = "ENSAYO (no escribe en IMAP)" if settings.dry_run else "normal"
    print(f"[{WORKER}] arrancando · {len(ids)} cuenta(s) · periodo "
          f"{settings.sync_period}s · modo {modo}", flush=True)
    if not settings.sync_enabled:
        print(f"[{WORKER}] HUBMAIL_SYNC_ENABLED=0: el proceso queda vivo pero NO "
              f"sincroniza. Sirve para el corte sin apagar el contenedor.", flush=True)
    print(f"[{WORKER}] clave de cifrado: {fuente_clave()}", flush=True)
    if not settings.vapid_private_key:
        print(f"[{WORKER}] sin HUBMAIL_VAPID_PRIVATE: no habrá push de correo nuevo.", flush=True)


def main():
    signal.signal(signal.SIGTERM, _al_parar)
    signal.signal(signal.SIGINT, _al_parar)

    if not _tomar_candado():
        return 3          # código de salida distingue "ya había otro" de un fallo

    if not settings.sync_enabled:
        _avisar_arranque([])
        # Aunque esté apagado se queda vivo: el panel lo muestra y el heartbeat
        # dice la verdad, en vez de aparecer como caído.
        while not _parar.is_set():
            heartbeat(WORKER, detail="desactivado con HUBMAIL_SYNC_ENABLED=0")
            _parar.wait(60)
        _soltar_candado()
        return 0

    try:
        ids = _cuentas_canonicas()
    except Exception as exc:
        print(f"[{WORKER}] no pude leer HUBMAIL_Accounts: {exc}", flush=True)
        _soltar_candado()
        return 4

    _avisar_arranque(ids)
    if not ids:
        print(f"[{WORKER}] no hay cuentas canónicas en HUBMAIL_Accounts: no hay "
              f"nada que sincronizar.", flush=True)

    for aid in ids:
        t = threading.Thread(target=_ciclo_cuenta, args=(aid,),
                             daemon=True, name=f"{WORKER}-{aid}")
        t.start()
        # Escalona las conexiones IMAP del arranque (el hilo de HUBMail dormía
        # 5 s entre cada una; sin esto, N cuentas abren N sockets de golpe).
        _parar.wait(5)

    # Hilo supervisor: sólo avisa y mantiene vivo el proceso si todos los hilos
    # de cuenta se hubieran caído.
    while not _parar.is_set():
        vivos = sum(1 for t in threading.enumerate()
                    if t.name.startswith(f"{WORKER}-"))
        if vivos == 0 and ids:
            print(f"[{WORKER}] no queda ningún hilo de cuenta vivo; revisa los logs",
                  flush=True)
            heartbeat(WORKER, detail="ERROR: ningún hilo de cuenta vivo")
        _parar.wait(30)

    _soltar_candado()
    print(f"[{WORKER}] detenido", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())