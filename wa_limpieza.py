"""
wa_limpieza — Purga diaria de WhatsApp (programa `wa_limpieza` bajo supervisor)
===============================================================================

La bandeja del módulo WhatsApp crece sola: un renglón por mensaje en
`HUB_WhatsappEntrante` y una conversación por chat. Sin purga la tabla se
vuelve el archivo histórico de todo lo que llegó y el respaldo crece igual.

Qué borra (una vez al día, entre 03:00 y 03:30, hora de Monterrey):

  * `HUB_WhatsappEntrante` con más de `wa_purga_dias` días (default 90).
  * `HUB_WhatsappConversaciones` con `Estado='CERRADA'` y `Ultima` de más de
    esos mismos días.

Nunca borra conversaciones en `BOT` o `HUMANO`: esas siguen vivas aunque el
último mensaje sea viejo. Tampoco toca OpenWA ni ninguna otra tabla.

Se borra en lotes de 500 (`DELETE TOP (500)` en bucle) para no dejar bloqueada
la tabla mientras la bandeja del panel la está leyendo; cada lote se confirma.

Config (HUB_Config, se lee en cada vuelta, sin reiniciar):

    wa_purga_dias    → días de retención (default 90)
    wa_purga_activa  → 1/0; con 0 el worker late pero no borra nada

El worker revisa cada 30 minutos (WA_LIMPIEZA_CICLO_SEG) y llama
`heartbeat("wa_limpieza", ...)` en cada vuelta, aunque esté fuera de la ventana.
"""
import os
import sys
import time
from datetime import time as _time, timedelta

# Los módulos locales van primero: el worker corre con `directory=/app`.
for _p in ("/app", os.path.dirname(os.path.abspath(__file__)), "."):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from tz_mx import now_mx   # noqa: E402

try:
    from worker_heartbeat import heartbeat
except Exception:  # pragma: no cover - solo se pierde la señal del panel
    def heartbeat(worker, detail="", count=None):
        return False

CLAVE_DIAS = "wa_purga_dias"
CLAVE_ACTIVA = "wa_purga_activa"
DIAS_DEFECTO = 90

LOTE = 500
CICLO_SEG = int(os.environ.get("WA_LIMPIEZA_CICLO_SEG", "1800"))  # 30 min

# Ventana de trabajo: de 03:00 a 03:29:59. Es de madrugada porque es cuando
# nadie contesta WhatsApp y un DELETE grande no compite con la bandeja.
VENTANA_INICIO = _time(3, 0)
VENTANA_FIN = _time(3, 30)

SQL_MENSAJES = ("DELETE TOP (500) FROM HUB_WhatsappEntrante "
                "WHERE Fecha < %s")
SQL_CONVERSACIONES = ("DELETE TOP (500) FROM HUB_WhatsappConversaciones "
                      "WHERE Estado = 'CERRADA' AND Ultima < %s")


def _log(mensaje):
    print(f"[wa_limpieza {time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}",
          flush=True)


def en_ventana(ahora=None):
    """True si `ahora` cae entre 03:00 y 03:30 (hora de Monterrey)."""
    hora = (ahora or now_mx()).time()
    return VENTANA_INICIO <= hora < VENTANA_FIN


def _conexion():
    """Conexión compartida del hilo de `eccsa_db` (NO se cierra aquí)."""
    import eccsa_db
    return eccsa_db.get_connection()


def _entero(valor, defecto):
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto
    return numero if numero >= 1 else defecto


def _activa(valor):
    """1/true/si = activa; 0/false/no = apagada; vacío o raro = activa."""
    texto = str(valor or "").strip().lower()
    if not texto:
        return True
    if texto in ("0", "false", "no", "off"):
        return False
    return True


def leer_config(conn):
    """{"dias": int, "activa": bool}; defaults si la clave falta o la BD falla."""
    cfg = {"dias": DIAS_DEFECTO, "activa": True}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT Clave, Valor FROM HUB_Config WHERE Clave IN (%s, %s)",
                (CLAVE_DIAS, CLAVE_ACTIVA))
            for fila in cur.fetchall() or []:
                clave, valor = fila[0], fila[1]
                if clave == CLAVE_DIAS:
                    cfg["dias"] = _entero(valor, DIAS_DEFECTO)
                elif clave == CLAVE_ACTIVA:
                    cfg["activa"] = _activa(valor)
    except Exception as exc:
        # Sin configuración se usan los defaults: la purga es segura para los
        # datos que deja (90 días) y es mejor no quedarse sin limpiar.
        _log(f"no se pudo leer la configuración; se usan los defaults: {exc}")
    return cfg


def _borrar_lotes(conn, sql, params):
    """Ejecuta `sql` (con TOP 500) hasta que un lote devuelva menos de 500."""
    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            borrados = int(cur.rowcount or 0)
        conn.commit()
        total += borrados
        if borrados < LOTE:
            return total


def purgar(conn, dias, ahora=None):
    """
    Borra lo viejo en lotes. Devuelve `{"mensajes": n, "conversaciones": n}`.

    El corte se calcula con la hora de Monterrey y no con GETDATE() para que
    la retención sea la misma que ve el panel, sin depender del TZ del host.
    """
    corte = (ahora or now_mx()) - timedelta(days=int(dias))
    mensajes = _borrar_lotes(conn, SQL_MENSAJES, (corte,))
    conversaciones = _borrar_lotes(conn, SQL_CONVERSACIONES, (corte,))
    return {"mensajes": mensajes, "conversaciones": conversaciones}


def turno(ahora=None, conn=None):
    """
    Una vuelta del worker. Devuelve la línea para el heartbeat.

    Fuera de la ventana, con la purga apagada o sin nada que borrar, responde
    con un texto corto y no toca filas.
    """
    ahora = ahora or now_mx()
    if not en_ventana(ahora):
        return "fuera de la ventana (03:00-03:30)"

    if conn is None:
        conn = _conexion()
    cfg = leer_config(conn)
    if not cfg["activa"]:
        return "purga desactivada (wa_purga_activa=0)"

    resultado = purgar(conn, cfg["dias"], ahora)
    return (f"{resultado['mensajes']} mensaje(s) y "
            f"{resultado['conversaciones']} conversación(es) de más de "
            f"{cfg['dias']} días")


def main():
    _log(f"worker de limpieza de WhatsApp iniciado (ciclo={CICLO_SEG}s)")
    vuelta = 0
    ultima_purga = None
    while True:
        try:
            vuelta += 1
            ahora = now_mx()
            # Dentro de la ventana se purga UNA vez al día: el reloj se
            # revisa cada 30 min y la ventana admite más de una vuelta.
            if en_ventana(ahora) and ahora.date() != ultima_purga:
                detalle = turno(ahora)
                ultima_purga = ahora.date()
                _log(detalle)
            else:
                detalle = "en espera de la ventana (03:00-03:30)"
            # El latido va siempre: un worker callado se ve igual que uno trabado.
            heartbeat("wa_limpieza", detail=detalle, count=vuelta)
        except Exception as exc:
            # Un error de base no puede matar el worker: mañana vuelve a intentar.
            _log(f"error en la vuelta (se sigue): {exc}")
            import traceback
            traceback.print_exc()
            try:
                heartbeat("wa_limpieza", detail=f"ERROR: {exc}"[:300])
            except Exception:
                pass
        time.sleep(CICLO_SEG)


if __name__ == "__main__":
    main()
