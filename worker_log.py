"""Log universal de workers: consola + base de datos.

Regla de oro: **nunca tumba al worker**. Si la base no responde, se pierde la
fila y ya; el mensaje igual sale por consola (que es lo que ve Arcane).

Y no se llama en cada vuelta del bucle: sólo en eventos (arranque, trabajo
tomado, resultado, error). Con 15 workers sería un diluvio de filas y la base
está sin RAM.
"""
import time

try:
    import eccsa_db as db
except Exception:  # pragma: no cover
    db = None

_dias_retencion = 7
_ultima_poda = 0.0


def log(worker, mensaje, nivel="INFO", detalle=None):
    """Imprime y guarda una línea de log. Devuelve True si quedó en la base."""
    texto = f"[{worker}] {mensaje}"
    print(texto, flush=True)
    if db is None:
        return False
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO HUB_WorkersLog (Worker, Nivel, Mensaje, Detalle) "
                "VALUES (%s, %s, %s, %s)",
                (str(worker)[:40], str(nivel)[:10], str(mensaje)[:400],
                 (str(detalle)[:400] if detalle else None)),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"[{worker}] no se pudo guardar el log: {str(e)[:120]}", flush=True)
        return False


def podar(dias=None):
    """Borra lo viejo. Como mucho una vez por hora en cada proceso."""
    global _ultima_poda
    if db is None:
        return 0
    if time.time() - _ultima_poda < 3600:
        return 0
    _ultima_poda = time.time()
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE TOP (5000) FROM HUB_WorkersLog "
                "WHERE Creado < DATEADD(day, -%s, GETDATE())",
                (int(dias or _dias_retencion),),
            )
            borradas = cur.rowcount
        conn.commit()
        return borradas
    except Exception as e:
        print(f"[worker_log] no se pudo podar: {str(e)[:120]}", flush=True)
        return 0
