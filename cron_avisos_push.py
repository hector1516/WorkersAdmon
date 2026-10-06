"""
cron_avisos_push — Worker que despacha los avisos push de las apps
(programa `avisos_push` bajo supervisor).

Turno de trabajo, uno cada AVISOS_CICLO_SEG (5 min, de sobra para cinco
consultas con índice):

  1. **Detecta** qué eventos ocurrieron desde el turno anterior: la firma de un
     reporte, un kilometraje registrado, la captura de un ticket OxxoGas (la hace
     Field), la firma de una cotización (también Field) y su paso a facturar.
     Aquí se ENCOLA y no se envía: la lista de destinatarios se decide en el
     momento del evento.
  2. **Despacha** según el horario: dentro de jornada cada aviso sale por
     separado; fuera de ella se van acumulando.

Por qué NO lo hace la app (Admon, Field): el evento puede ocurrir en una app y
tener que avisar a un usuario que entra por otra. Admon registra un ticket que
capturó Field; la firma de una cotización la hace el cliente desde Field y la
tiene que ver quien tiene abierto el módulo de Cotizaciones en Admon. Si cada app
avisara de lo suyo, el aviso saldría de donde NO lo espera el usuario.

Nada de esto envía un correo ni un WhatsApp: es únicamente el canal push de las
PWA, que es el que el usuario pidió. Telegram y WhatsApp siguen su propio camino
(`telegram_alerts`, `openwa_alerts`).

Este worker REEMPLAZA a `cron_avisos.py` (programa `avisos`), que quedó muerto:
`api/routers/push.py` consulta columnas que no existen en `HUB_PushSubscriptions`
(está indexada por `UserEmail`, ese código usa `userId`), así que los 3 push que
decía haber enviado cada lunes nunca salieron. Sus dos trabajos —el_aviso de
kilómetros del lunes y el de reportes sin firmar— están en DETECTORES aquí
(`KILOMETROS` y, para los reportes sin firmar, queda pendiente: al final de este
archivo está la nota de por qué no se migró).

El trabajo real (qué se dice, a quién, cuándo) está en `notif_dispatch`, que es
una biblioteca sin bucle y por eso se puede probar sin base de datos y sin
esperar a que sea el día correcto.
"""

import datetime
import os
import sys
import time

# Los módulos locales van primero: el worker corre con `directory=/app`.
for _p in ("/app", os.path.dirname(os.path.abspath(__file__)), "."):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import eccsa_db as db
    import notif_dispatch as nd
except Exception as exc:  # pragma: no cover - arranque
    print(f"[avisos_push] no se pudieron importar los módulos: {exc}")
    raise

try:
    from worker_heartbeat import heartbeat
except Exception:
    # El heartbeat es para que la página de Estado se vea viva; si el módulo no
    # está, el worker sigue funcionando igual (solo se pierde la señal).
    def heartbeat(worker, detail="", count=None):
        return False

APP = os.environ.get("AVISOS_APP", nd.APP_ADMON)

# Cada cuánto se da un turno. Cinco minutos es de sobra para cinco consultas con
# índice, y hace que el atraso máximo de un aviso sea de cinco minutos.
AVISOS_CICLO_SEG = int(os.environ.get("AVISOS_CICLO_SEG", "300"))

# Cada cuántos turnos se limpia lo ya entregado (30 días por omisión).
LIMPIAR_CADA = int(os.environ.get("AVISOS_LIMPIAR_CADA", "480"))   # ~2 días


def _log(mensaje):
    print(f"[avisos_push {time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}", flush=True)


def turno():
    """Un turno completo. Devuelve una línea con lo que hizo, para el log."""
    detectados = nd.detectar(app=APP)
    total = sum(v for v in detectados.values())
    if total:
        partes = ", ".join(f"{k.lower()}={v}" for k, v in detectados.items() if v)
        _log(f"encolados: {partes}")
    resumen = nd.despachar(app=APP)
    return resumen


def main():
    _log(f"worker de avisos push iniciado (app={APP}, ciclo={AVISOS_CICLO_SEG}s)")
    vuelta = 0
    while True:
        try:
            vuelta += 1
            detalle = turno()
            # El heartbeat va SIEMPRE, aunque no haya pasado nada: un worker que
            # no reporta se ve igual que uno que está trabado, y esa es la
            # confusión que ya costó una vez un escáner cinco días ciego.
            heartbeat("avisos_push", detail=detalle, count=vuelta)
            if vuelta % LIMPIAR_CADA == 0:
                n = nd.limpiar()
                _log(f"limpieza de cola: {n} aviso(s) de más de 30 días")
        except Exception as exc:
            # El ciclo NUNCA se detiene por un error puntual: un aviso que falla
            # tiene que ser un aviso perdido, no cinco días de avisos.
            _log(f"error en el turno (se sigue): {exc}")
            import traceback
            traceback.print_exc()
            try:
                heartbeat("avisos_push", detail=f"ERROR: {exc}"[:300])
            except Exception:
                pass
        time.sleep(AVISOS_CICLO_SEG)


if __name__ == "__main__":
    main()


# ── Lo que este worker NO hace, y por qué ────────────────────────────────────
#
# **El aviso de "reportes sin firmar" del worker viejo no se migró.** A diferencia
# de los otros cuatro, ese no anuncia algo que PASÓ: recuerda algo que NO ha
# pasado, y su periodicidad es de una hora, no de evento. Meterlo en la cola de
# eventos lo convertiría en un recordatorio que se repite cada vez que el worker
# corre, y eso no cabe en el modelo de "una fila por evento".
#
# Si se quiere, es un detector más que lee `ReportesServicio` sin firma y encola
# UNA fila por reporte usando el IdReporte como marcador (igual que el de firmas),
# en vez de una fila por turno. Es un detector de ausencias, como el de
# kilómetros, y ya está el precedente de cómo se marca una semana.
