"""
legends_time.py — Zona horaria de ECCSA Legends
================================================
Toda la aritmética de "semana" de Legends (la semana corre domingo 00:00 →
sábado 23:59) debe hacerse en **hora México**, no en la hora del contenedor.

Por qué: los contenedores corren en **UTC** (python:3.11-slim sin variable TZ),
mientras que el negocio opera en America/Mexico_City (UTC-6/-5). Con
`datetime.now()` / `date.today()` sin zona, el "domingo 00:00" se calculaba 5-6
horas antes de tiempo: a partir de las 18:00 del sábado (hora México) el código
ya creía que era domingo, la ventana de la semana empezaba después de todos los
eventos de la semana y `PuntuacionSemanal` quedaba en 0 para todos.

`cron_legends.py` ya usaba pytz (bien); el auditor y los endpoints de las apps
no. Este módulo centraliza la regla para que no vuelva a pasar.
"""
import datetime

import pytz

MEXICO = pytz.timezone("America/Mexico_City")


def ahora_mx():
    """Ahora en hora México (con zona)."""
    return datetime.datetime.now(MEXICO)


def inicio_semana_mx(when=None):
    """
    Inicio de la semana en curso: domingo 00:00 hora México.

    Devuelve un datetime *naive* porque es lo que se compara contra columnas
    DATETIME de SQL Server (que guardan hora local de México).
    """
    ahora = when or ahora_mx()
    domingo = ahora - datetime.timedelta(days=(ahora.weekday() + 1) % 7)
    return domingo.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def fecha_semana_mx(when=None):
    """Igual que inicio_semana_mx() pero como date (para endpoints que comparan
    contra DATE)."""
    return inicio_semana_mx(when).date()
