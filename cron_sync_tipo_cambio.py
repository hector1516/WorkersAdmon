import time
import sys
import os
import datetime

# Adjust sys.path to resolve local python modules inside the container / local run
for p in ("/workspace/hub_repo", "/", ""):
    if p and os.path.isdir(p):
        sys.path.append(p)

try:
    import eccsa_db as db
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Core Import Error: {e}")
    sys.exit(1)

# Hora (local del contenedor, hora de México) a la que se ejecuta la actualización diaria.
HORA_EJECUCION = int(os.environ.get("CRON_TC_HORA", 6))
MIN_EJECUCION = int(os.environ.get("CRON_TC_MIN", 0))

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting daily USD/MXN tipo de cambio sync "
      f"(runs at {HORA_EJECUCION:02d}:{MIN_EJECUCION:02d}).")

def _segundos_hasta_proxima_ejecucion():
    """Segundos hasta la próxima ejecución diaria programada (hora/min del contenedor)."""
    now = datetime.datetime.now()
    proximo = now.replace(hour=HORA_EJECUCION, minute=MIN_EJECUCION, second=0, microsecond=0)
    if proximo <= now:
        proximo += datetime.timedelta(days=1)
    return (proximo - now).total_seconds()

while True:
    try:
        valor, fuente, actualizado = db.actualizar_tipo_cambio_auto(panel=0.0, oferta_pond=0.0)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Tipo de cambio check. Obtenido={valor} "
              f"(fuente={fuente}), última guardada={actualizado}")
        if valor is None:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No se pudo obtener un valor; reintentará en la próxima ventana.")
    except Exception as err:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error en tipo de cambio loop: {err}")

    segundos = _segundos_hasta_proxima_ejecucion()
    if segundos <= 7200:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Volviendo a comprobar en {segundos/60:.1f} min "
              f"(hasta la ventana de {HORA_EJECUCION:02d}:{MIN_EJECUCION:02d}).")
        time.sleep(min(segundos, 900))
    else:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Planificado para {HORA_EJECUCION:02d}:{MIN_EJECUCION:02d} "
              f"(dormir {segundos/3600:.1f} h).")
        time.sleep(segundos)