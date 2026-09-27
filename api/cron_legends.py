"""
Worker: Calculo de ganador semanal de ECCSA Legends.
Loop infinito: cada hora verifica si es domingo 3 AM (hora Mexico).
Si es domingo 3 AM, calcula ganador de la semana anterior y resetea puntos.

En Docker, supervisord config:
[program:legends_cron]
command=python3 /app/api/cron_legends.py
directory=/app
autostart=true
autorestart=true
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta
import pymssql
from config import load_db_config


def calcular_ganador_semanal():
    """Calcula ganador de la semana anterior, guarda historial, resetea puntos semanales."""
    import pytz
    mexico_tz = pytz.timezone("America/Mexico_City")
    ahora = datetime.now(mexico_tz)

    # Domingo = weekday() 6. Formula: (weekday + 1) % 7 → domingo = 0
    # Fin de semana = ultimo domingo
    fin = ahora - timedelta(days=(ahora.weekday() + 1) % 7)
    inicio = fin - timedelta(days=7)

    print(f"[LEGENDS CRON] Calculando ganador: {inicio.date()} al {fin.date()}")

    db_cfg = load_db_config()
    conn = pymssql.connect(**db_cfg, as_dict=True)
    cur = conn.cursor()

    cur.execute("""
        SELECT TOP 1 IdUsuario, PuntuacionSemanal
        FROM HUB_UserScores
        WHERE PuntuacionSemanal > 0
        ORDER BY PuntuacionSemanal DESC
    """)
    winner = cur.fetchone()

    if not winner:
        print("[LEGENDS CRON] Sin actividad en la semana.")
        conn.close()
        return False

    cur.execute(
        "SELECT Id FROM HUB_WeeklyWinners WHERE FechaInicio = %s AND FechaFin = %s",
        (inicio.date(), fin.date())
    )
    if cur.fetchone():
        print("[LEGENDS CRON] Ya existe ganador para esta semana.")
        conn.close()
        return False

    cur.execute(
        "INSERT INTO HUB_WeeklyWinners (IdUsuario, PuntuacionSemana, FechaInicio, FechaFin) VALUES (%s, %s, %s, %s)",
        (winner["IdUsuario"], winner["PuntuacionSemanal"], inicio.date(), fin.date())
    )

    cur.execute("UPDATE HUB_UserScores SET PuntuacionSemanal = 0")
    conn.commit()

    cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (winner["IdUsuario"],))
    u = cur.fetchone()
    nombre = u["Nombre"] if u else "Desconocido"
    print(f"[LEGENDS CRON] Ganador: {nombre} con {winner['PuntuacionSemanal']} ECCSA Points")

    try:
        from routers.push import notify_weekly_winner
        notify_weekly_winner(winner["IdUsuario"], winner["PuntuacionSemanal"])
    except Exception:
        pass

    conn.close()
    return True


if __name__ == "__main__":
    import pytz
    print("[LEGENDS CRON] Worker iniciado. Verificando cada hora...")
    ya_ejecutado_hoy = False

    while True:
        try:
            mexico_tz = pytz.timezone("America/Mexico_City")
            ahora = datetime.now(mexico_tz)

            # Domingo = weekday() 6
            es_domingo = ahora.weekday() == 6
            es_3am = ahora.hour == 3

            if es_domingo and es_3am and not ya_ejecutado_hoy:
                print(f"[LEGENDS CRON] Ejecutando calculo semanal: {ahora}")
                calcular_ganador_semanal()
                ya_ejecutado_hoy = True
            elif not es_domingo or not es_3am:
                ya_ejecutado_hoy = False

        except Exception as e:
            print(f"[LEGENDS CRON] Error: {e}")

        time.sleep(3600)  # Cada hora
