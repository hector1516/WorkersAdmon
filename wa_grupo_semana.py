"""
Crónica semanal con IA del grupo ECCSita
========================================

Los lunes por la mañana (09:00-09:30, hora de México) toma la semana ISO
**anterior** y la resume con IA si tiene mensajes y todavía no está
`RESUMIDA`. La captura de los mensajes la hace `panel/wa_webhook.py`; la
crónica la arma `panel/eccsita_cronica.py`.

Reglas:

- Fuera de la ventana (día y hora de `HUB_Config`) el ciclo no hace nada: un
  worker que resume el martes ya no tiene la semana cerrada.
- Si no hay mensajes esa semana, no se genera nada (una crónica vacía no es
  memoria).
- Si la crónica ya está `RESUMIDA` o `REVISADA`, no se toca: el texto que
  alguien revisó no se pisa. Para rehacerla está el botón «Regenerar» del
  panel.
- **Si la IA falla, la semana se queda `CAPTURADA`.** No se guarda la básica:
  el lunes siguiente se reintenta, y mientras tanto el panel avisa que falta.
  El fallo queda en el log y en el detalle del heartbeat.
- El latido va **cada ciclo**, con o sin trabajo: un worker mudo se ve igual
  que uno trabado.

Contrato:

    ciclo(ahora=None, conn=None, generar=None, cfg=None) -> (detalle, generadas)

- `ahora`: datetime en hora de México; por omisión `tz_mx.now_mx()`.
- `conn`: conexión de BD; si es None se abre y se cierra.
- `generar`: `panel.eccsita_cronica.generar_semana`; las pruebas lo cambian.
- `cfg`: diccionario de `panel.eccsita.config()`; si es None se lee.

    python -m py_compile wa_grupo_semana.py
    python -m unittest tests.test_eccsita_cronica -v
"""
import os
import time
from datetime import timedelta

from tz_mx import now_mx

WORKER = "wa_grupo_semana"

# Una hora basta: la ventana es de 30 minutos y la semana no cambia a media
# mañana. Con 3600 s el lunes cae al menos una vez dentro de la ventana.
WA_GRUPO_SEMANA_CICLO_SEG = max(
    60, int(os.environ.get("WA_GRUPO_SEMANA_CICLO_SEG") or 3600))

# La ventana dura media hora desde la hora configurada.
VENTANA_MINUTOS = 30

# Estados en los que la semana ya no se vuelve a generar sola.
CERRADAS = ("RESUMIDA", "REVISADA")


def _log(mensaje):
    print(f"[{WORKER} {time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}",
          flush=True)


def _dia(valor):
    """Día ISO (1=lunes … 7=domingo); 1 si el valor no sirve."""
    try:
        dia = int(str(valor).strip())
    except (TypeError, ValueError):
        return 1
    return dia if 1 <= dia <= 7 else 1


def _hora(valor):
    """«09:00» -> (9, 0); (9, 0) si el valor no sirve."""
    try:
        partes = str(valor or "").strip().split(":")
        hora, minuto = int(partes[0]), int(partes[1])
    except (IndexError, TypeError, ValueError):
        return 9, 0
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return 9, 0
    return hora, minuto


def en_ventana(ahora, cfg):
    """¿Es el día configurado, dentro de los 30 minutos de la hora dada?"""
    if ahora.isoweekday() != _dia(cfg.get("semana_dia")):
        return False
    hora, minuto = _hora(cfg.get("semana_hora"))
    desde = hora * 60 + minuto
    ahora_min = ahora.hour * 60 + ahora.minute
    return desde <= ahora_min < desde + VENTANA_MINUTOS


def semana_anterior(ahora):
    """(anio, semana) ISO de la semana pasada."""
    anio, semana, _dia_iso = (ahora.date() - timedelta(days=7)).isocalendar()
    return anio, semana


def _conexion():
    import eccsa_db
    return eccsa_db.get_connection()


def ciclo(ahora=None, conn=None, generar=None, cfg=None):
    """
    Un turno. Devuelve `(detalle, generadas)` para el log y el heartbeat.

    No lanza por un fallo de la IA: la semana queda CAPTURADA y el detalle
    dice por qué.
    """
    from panel import eccsita, eccsita_cronica as cronica

    ahora = ahora or now_mx()
    cfg = cfg or eccsita.config()

    if not en_ventana(ahora, cfg):
        return "fuera de ventana", 0

    grupo = (cfg.get("grupo") or "").strip()
    if not grupo:
        return "sin grupo configurado", 0

    anio, semana = semana_anterior(ahora)
    propio = conn is None
    if propio:
        conn = _conexion()
    try:
        fila = eccsita._fila_semana(grupo, anio, semana) or {}
        if (fila.get("Estado") or "").upper() in CERRADAS:
            return f"la semana {semana} de {anio} ya está resumida", 0
        if not eccsita._mensajes_semana(grupo, anio, semana):
            return f"la semana {semana} de {anio} no tiene mensajes", 0

        generar = generar or cronica.generar_semana
        usar_ia = bool(cfg.get("ia", True))
        try:
            datos = generar(conn, anio, semana, usar_ia=usar_ia,
                            exigir_ia=usar_ia)
        except cronica.CronicaIAError as exc:
            _log(f"la IA falló con la semana {semana} de {anio}: {exc}")
            return (f"la IA falló con la semana {semana} de {anio}; "
                    f"queda CAPTURADA ({exc})")[:300], 0

        if not datos:
            return f"la semana {semana} de {anio} no se pudo generar", 0
        return (f"crónica de la semana {semana} de {anio} "
                f"({datos.get('generadoPor') or 'BASICO'})"), 1
    finally:
        if propio:
            try:
                conn.close()
            except Exception:
                pass


def main():
    try:
        from worker_heartbeat import heartbeat
    except Exception:
        def heartbeat(worker, detail="", count=None):
            return False

    _log(f"crónica semanal de ECCSita iniciada "
         f"(ciclo={WA_GRUPO_SEMANA_CICLO_SEG}s)")
    while True:
        detalle, generadas = "error", 0
        try:
            detalle, generadas = ciclo()
            if generadas:
                _log(detalle)
        except Exception as exc:
            detalle = f"ERROR: {exc}"[:300]
            _log(f"error en el ciclo (se sigue): {exc}")
            import traceback
            traceback.print_exc()
        try:
            heartbeat(WORKER, detail=detalle, count=generadas)
        except Exception:
            pass
        time.sleep(WA_GRUPO_SEMANA_CICLO_SEG)


if __name__ == "__main__":
    main()
