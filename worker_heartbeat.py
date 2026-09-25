"""
worker_heartbeat.py — Registro de "última ejecución" de un worker.
====================================================================
Módulo pequeño (stdlib puro) para que cualquier worker de WorkersAdmon
reporte cuándo corrió por última vez y qué hizo. La página de estado
(`/`) y la API (`/api/status`) leen estos archivos.

Uso dentro de un worker:

    from worker_heartbeat import heartbeat

    while True:
        ...hacer trabajo...
        heartbeat("mi_worker", detail="12 correos procesados", count=12)
        time.sleep(60)

Archivos: /data/heartbeats/<nombre_worker>.json  (volumen persistente)

Formato del JSON:
    {"worker": "mi_worker",
     "last_run": "2026-09-25 18:40:12",
     "detail": "texto libre (truncado a 300 chars)",
     "count": 12}          # opcional
"""
import datetime
import json
import os


def get_data_dir():
    """Directorio de datos persistente (/data en Docker, ./workers_data en local)."""
    for candidate in (os.environ.get("WORKERS_DATA_DIR", "/data"),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers_data")):
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    return os.environ.get("WORKERS_DATA_DIR", "/tmp")


def heartbeat(worker, detail="", count=None):
    """Escribe el heartbeat del worker de forma atómica. Devuelve True si ok."""
    if not worker:
        return False
    try:
        hb_dir = os.path.join(get_data_dir(), "heartbeats")
        os.makedirs(hb_dir, exist_ok=True)
        payload = {
            "worker": str(worker),
            "last_run": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "detail": str(detail or "")[:300],
        }
        if count is not None:
            try:
                payload["count"] = int(count)
            except (TypeError, ValueError):
                payload["count"] = None
        path = os.path.join(hb_dir, f"{worker}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # nunca debe tumbar al worker por esto
        print(f"[heartbeat] ERROR {worker}: {exc}")
        return False


if __name__ == "__main__":
    # Prueba rápida: python3 worker_heartbeat.py mi_worker "hola" 5
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "test_worker"
    det = sys.argv[2] if len(sys.argv) > 2 else "prueba manual"
    cnt = int(sys.argv[3]) if len(sys.argv) > 3 else None
    print("OK" if heartbeat(name, det, cnt) else "FALLO")
