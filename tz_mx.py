"""
Zona horaria estándar del ecosistema ECCSA: Monterrey, Nuevo León, México.

México abolió el horario de verano en 2022 para la mayoría del país
(incluyendo Nuevo León). Por lo tanto, Monterrey es UTC-6 todo el año,
sin cambios de DST.

REGLA: nunca usar datetime.now() sin zona, datetime.utcnow() - N, ni
depender de la variable de entorno TZ del contenedor. Siempre usar
now_mx() de este módulo. Así el código funciona igual sin importar la
configuración del contenedor.

Uso:
    from tz_mx import now_mx, TZ_MX

    ahora = now_mx()                    # datetime naive en hora Monterrey
    dt_local = dt.astimezone(TZ_MX)     # convertir un datetime con tz
"""
from datetime import datetime, timezone, timedelta

# Monterrey, NL, México: UTC-6 fijo (sin horario de verano desde 2022)
TZ_MX = timezone(timedelta(hours=-6))


def now_mx() -> datetime:
    """Hora actual de Monterrey como datetime naive (sin tzinfo)."""
    return datetime.now(TZ_MX).replace(tzinfo=None)


def to_mx(dt: datetime) -> datetime:
    """Convierte cualquier datetime (con o sin tz) a hora Monterrey naive."""
    if dt.tzinfo is None:
        # Asumir UTC si no tiene zona
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_MX).replace(tzinfo=None)
