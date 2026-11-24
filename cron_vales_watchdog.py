# Log universal (base de datos + consola); si el import falla, sigue sin log.
try:
    from worker_log import log as wlog
except Exception:  # pragma: no cover
    def wlog(*a, **k):
        return False

"""Watchdog del vales_worker.

Lee /data/vales_worker_heartbeat (lo escribe el worker al inicio de cada ciclo,
cada ~15 s). Si el heartbeat tiene mas de UMBRAL segundos, el worker esta
colgado o murio sin que supervisor lo note: se reinicia con supervisorctl y se
avisa a los admins de vales por Telegram (max 1 vez/hora).

Se ejecuta como un programa de supervisor aparte (vales_watchdog).
"""
import os
import subprocess
import time
from datetime import datetime, timedelta

import eccsa_db as db

HEARTBEAT = "/data/vales_worker_heartbeat"
UMBRAL_SEG = int(os.getenv("VALES_WATCHDOG_UMBRAL", "600"))  # 10 min
INTERVALO = int(os.getenv("VALES_WATCHDOG_INTERVALO", "60"))


def _leer_heartbeat():
    try:
        with open(HEARTBEAT, "r") as f:
            return float((f.read() or "0").strip())
    except Exception:
        return 0.0


def _reiniciar_worker():
    try:
        r = subprocess.run(
            ["supervisorctl", "restart", "vales_worker"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"[vales_watchdog] restart rc={r.returncode} out={r.stdout.strip()} err={r.stderr.strip()}", flush=True)
        return r.returncode == 0
    except Exception as e:
        print(f"[vales_watchdog] No se pudo reiniciar: {e}", flush=True)
        return False


def _alertar(segundos):
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'vales_watchdog_alerta'")
            row = cur.fetchone()
            ultimo = row["Valor"] if row else ""
        ahora = datetime.now()
        if ultimo:
            try:
                if (ahora - datetime.strptime(ultimo, "%Y-%m-%d %H:%M:%S")) < timedelta(hours=1):
                    return
            except Exception:
                pass
        texto = (f"ALERTA vales_worker\n"
                 f"Heartbeat sin actualizar {int(segundos/60)} min. "
                 f"Se reinicio el worker automaticamente.")
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT t.ChatId
                FROM HUB_TelegramUsuarios t
                JOIN HUB_Users u ON u.Id = t.IdUsuario
                WHERE t.Activo = 1 AND u.AccesoAdminVales = 1
            """)
            chats = [r["ChatId"] for r in cur.fetchall()]
        for cid in chats:
            db.queue_telegram_alerta('VALE_QR_GENERADO', int(cid), texto[:4000])
        with conn.cursor() as cur:
            cur.execute("""
                IF EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'vales_watchdog_alerta')
                    UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = 'vales_watchdog_alerta'
                ELSE
                    INSERT INTO HUB_Config (Clave, Valor, Actualizado) VALUES ('vales_watchdog_alerta', %s, GETDATE())
            """, (ahora.strftime("%Y-%m-%d %H:%M:%S"), ahora.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[vales_watchdog] alerta fallo: {e}", flush=True)


def _worker_habilitado():
    """False si el worker está fuera de /data/workers_enabled.txt.

    Un worker deshabilitado a propósito NO tiene latido; sin esta guarda el
    vigilante lo daba por colgado, intentaba reiniciarlo (y fallaba, porque
    supervisor ya no lo conoce) y mandaba una alerta cada ciclo.
    """
    try:
        with open('/data/workers_enabled.txt', encoding='utf-8') as f:
            return 'vales_worker' in [l.strip() for l in f if l.strip()]
    except Exception:
        return True


def main():
    wlog("vales_watchdog", "Arrancó")
    print(f"[vales_watchdog] Iniciado (umbral {UMBRAL_SEG}s, intervalo {INTERVALO}s)", flush=True)
    while True:
        hb = _leer_heartbeat()
        if hb <= 0:
            # Sin heartbeat: el worker nunca arranco o borraron el archivo.
            # Dar margen en el primer minuto para no reiniciar en bucle.
            print("[vales_watchdog] Sin heartbeat (worker aun no arranca?)", flush=True)
        else:
            edad = time.time() - hb
            if edad > UMBRAL_SEG:
                if not _worker_habilitado():
                    # Está apagado a propósito: se calla y no se reinicia.
                    time.sleep(INTERVALO)
                    continue
                print(f"[vales_watchdog] Heartbeat viejo: {int(edad)}s > {UMBRAL_SEG}s. Reiniciando...", flush=True)
                _alertar(edad)
                _reiniciar_worker()
                # Esperar a que el nuevo worker escriba heartbeat
                time.sleep(20)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
