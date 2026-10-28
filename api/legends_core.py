"""
legends_core.py — Reglas canónicas de ECCSA Legends.
====================================================

FUENTE CANÓNICA: field/api/legends_core.py
Copias idénticas: admon/api/legends_core.py y WorkersAdmon/api/legends_core.py.
Si cambias una regla, cambia las tres (o corre tools/sync_legends.py).

Qué vive aquí (y por qué):
  * Semana ECCSA = domingo 00:00 → sábado 23:59, **hora de la Ciudad de
    México**. Los contenedores corren en UTC; usar datetime.now() sin zona
    movía el corte al sábado 18:00 y dejaba la semana en cero.
  * Temporada = trimestre calendario. Los niveles se calculan con los puntos
    de la temporada, no con el total histórico: así quien entra nuevo compite
    en igualdad y el progreso se nota.
  * Puntos: una sola tabla. El worker y las APIs leen lo mismo.
  * Logros: catálogo en código. Se desbloquean una vez por usuario en
    HUB_UserLogros y su bonus se registra en HUB_ScoreLog con Metrica='logro'
    y ReferenciaId=orden (idempotente por el índice único existente).
  * Racha: días consecutivos con actividad positiva (sin contar racha_dia ni
    logro, que son bonus). Se calcula con las fechas reales de ScoreLog.

El módulo NO importa db, auth ni FastAPI: recibe un cursor/conn. Así lo pueden
usar las dos APIs y los workers sin acoplarse a la app.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable

try:
    import pytz

    MEXICO = pytz.timezone("America/Mexico_City")
except Exception:  # pragma: no cover - respaldo para entornos sin pytz
    MEXICO = dt.timezone(dt.timedelta(hours=-6))


# ─────────────────────────────────────────────────────────────────────────────
# PUNTOS
# ─────────────────────────────────────────────────────────────────────────────

# Métricas que otorgan (o quitan) puntos. El orden importa para la guía.
METRICAS: dict[str, int] = {
    "reporte_firmado": 10,
    "kilometro": 5,
    "ticket_oxxogas": 3,
    "servicio": 0,          # se calcula por horas trabajadas
    "firma_remota": 8,
    "racha_dia": 3,         # bonus diario, lo otorga el auditor
    "vale_generado": -2,    # solo cuando el vale queda GENERADO/CERRADO
    "comida_reporte": -1,
    # Métricas de oficina (Admon)
    "cotizacion_creada": 2,
    "cotizacion_enviada": 5,
    "cliente_alta": 4,
    "reporte_creado": 3,
    "reporte_fotos": 2,
    # Métricas económicas (2026-10-23): si mueves dinero, sumas; si lo
    # proteges, sumas un poco; si lo fugas, restas. La cotización pagada se
    # escala por subtotal con `puntos_cotizacion_pagada()`.
    "cliente_nuevo_cotizado": 5,
    "cotizacion_con_partidas": 2,
    "cotizacion_entregada": 10,
    "cotizacion_pagada": 15,     # base; el override va de 15 a 75 por monto
    "cobranza_registrada": 3,
    "reporte_mismo_dia": 3,
    "vale_con_comprobante": 2,
    "vale_sin_comprobante": -2,
    "reporte_tardio": -2,
    "retrabajo": -5,
    # Bonus de logro: puntos variables, ReferenciaId = orden del logro
    "logro": 0,
}

# Texto para la guía y para el historial. Los puntos se toman de METRICAS;
# aquí solo viven nombre/icono/explicación.
METRICAS_INFO: dict[str, dict[str, Any]] = {
    "reporte_firmado": {
        "nombre": "Reporte firmado", "icono": "✅", "tipo": "positivo",
        "descripcion": "Cada reporte de servicio que queda firmado.",
        "como": "Firma en la app al terminar el servicio.",
    },
    "kilometro": {
        "nombre": "Kilómetros", "icono": "🛣️", "tipo": "positivo",
        "descripcion": "Cada registro de kilometraje del vehículo.",
        "como": "Registra la lectura al salir o al llegar.",
    },
    "ticket_oxxogas": {
        "nombre": "Ticket OxxoGas", "icono": "⛽", "tipo": "positivo",
        "descripcion": "Cada ticket de combustible con foto.",
        "como": "Sube la foto del ticket desde la app.",
    },
    "servicio": {
        "nombre": "Horas de servicio", "icono": "🔧", "tipo": "dinamico",
        "descripcion": "Puntos según las horas netas del reporte, repartidos entre los ingenieros.",
        "como": "Captura bien inicio, fin, traslado y comida del reporte.",
    },
    "firma_remota": {
        "nombre": "Firma remota", "icono": "📱", "tipo": "positivo",
        "descripcion": "El cliente firma el reporte desde su teléfono.",
        "como": "Comparte el enlace de firma remota antes de cerrar.",
    },
    "racha_dia": {
        "nombre": "Racha diaria", "icono": "🔥", "tipo": "positivo",
        "descripcion": "Un bonus por mantener actividad día con día.",
        "como": "Haz al menos una actividad positiva cada día.",
    },
    "vale_generado": {
        "nombre": "Vale generado", "icono": "⛽", "tipo": "negativo",
        "descripcion": "Se descuenta cuando el vale queda generado o cerrado.",
        "como": "Evita vales duplicados o sin comprobante.",
    },
    "comida_reporte": {
        "nombre": "Tiempo de comida", "icono": "🍽️", "tipo": "negativo",
        "descripcion": "Se descuenta si el reporte incluye hora de comida.",
        "como": "Marca la comida solo cuando corresponda.",
    },
    "cotizacion_creada": {
        "nombre": "Cotización creada", "icono": "📝", "tipo": "positivo",
        "descripcion": "Crear una cotización de materiales desde cero.",
        "como": "Da de alta la cotización en Admon.",
    },
    "cotizacion_enviada": {
        "nombre": "Cotización enviada", "icono": "📤", "tipo": "positivo",
        "descripcion": "Enviar la cotización por correo al cliente.",
        "como": "Usa el botón de enviar, no solo guardar.",
    },
    "cliente_alta": {
        "nombre": "Cliente nuevo", "icono": "🏢", "tipo": "positivo",
        "descripcion": "Dar de alta un cliente nuevo.",
        "como": "Captura el cliente completo en Admon.",
    },
    "reporte_creado": {
        "nombre": "Reporte creado", "icono": "📋", "tipo": "positivo",
        "descripcion": "Registrar un reporte de servicio.",
        "como": "Crea el reporte desde Admon o Field.",
    },
    "reporte_fotos": {
        "nombre": "Fotos de evidencia", "icono": "📷", "tipo": "positivo",
        "descripcion": "Subir las fotos de evidencia del reporte.",
        "como": "Adjunta fotos antes de firmar.",
    },
    "cliente_nuevo_cotizado": {
        "nombre": "Cliente nuevo cotizado", "icono": "🎯", "tipo": "positivo",
        "descripcion": "Primera cotización a un cliente que no tenía historial.",
        "como": "Cotiza a un cliente nuevo, no solo recotices a los de siempre.",
    },
    "cotizacion_con_partidas": {
        "nombre": "Cotización con partidas", "icono": "🧾", "tipo": "positivo",
        "descripcion": "Agregar la primera partida a una cotización.",
        "como": "Completa la cotización con materiales y precios.",
    },
    "cotizacion_entregada": {
        "nombre": "Cotización entregada", "icono": "🤝", "tipo": "positivo",
        "descripcion": "El cliente ya recibió/aceptó la cotización (Color=1).",
        "como": "Actualiza el estatus a ENTREGADA en Admon.",
    },
    "cotizacion_pagada": {
        "nombre": "Cotización pagada", "icono": "💵", "tipo": "dinamico",
        "descripcion": "El cliente pagó la cotización. Vale más si el monto es mayor.",
        "como": "Marca la cotización como PAGADA en Admon cuando entre el dinero.",
    },
    "cobranza_registrada": {
        "nombre": "Cobranza registrada", "icono": "🏦", "tipo": "positivo",
        "descripcion": "Registrar el pago de una cotización que no es tuya.",
        "como": "Actualiza a PAGADA la cotización del vendedor.",
    },
    "reporte_mismo_dia": {
        "nombre": "Reporte el mismo día", "icono": "📅", "tipo": "positivo",
        "descripcion": "Cerrar el reporte el mismo día del servicio.",
        "como": "Firma al terminar el trabajo, no días después.",
    },
    "vale_con_comprobante": {
        "nombre": "Vale con comprobante", "icono": "🧾", "tipo": "positivo",
        "descripcion": "Cerrar el vale con su ticket de OxxoGas.",
        "como": "Adjunta el ticket al cerrar el vale.",
    },
    "vale_sin_comprobante": {
        "nombre": "Vale sin comprobante", "icono": "🚫", "tipo": "negativo",
        "descripcion": "Cerrar un vale sin el ticket que respalde el gasto.",
        "como": "Adjunta el ticket antes de cerrar el vale.",
    },
    "reporte_tardio": {
        "nombre": "Reporte tardío", "icono": "⏰", "tipo": "negativo",
        "descripcion": "Firmar un reporte más de 48 horas después del servicio.",
        "como": "Firma el mismo día o al día siguiente.",
    },
    "retrabajo": {
        "nombre": "Retrabajo", "icono": "🔁", "tipo": "negativo",
        "descripcion": "Eliminar un reporte que ya estaba firmado.",
        "como": "Revisa el reporte antes de firmarlo.",
    },
}

# Orden de la guía: primero lo que más suma, al final los descuentos.
ORDEN_GUIA = [
    "cotizacion_pagada", "reporte_firmado", "servicio", "cotizacion_entregada",
    "firma_remota", "cliente_nuevo_cotizado", "reporte_mismo_dia", "kilometro",
    "ticket_oxxogas", "reporte_fotos", "vale_con_comprobante", "racha_dia",
    "cotizacion_enviada", "cotizacion_con_partidas", "cliente_alta",
    "reporte_creado", "cotizacion_creada", "cobranza_registrada",
    "vale_generado", "vale_sin_comprobante", "comida_reporte",
    "reporte_tardio", "retrabajo",
]


def puntos_cotizacion_pagada(subtotal: float | int | None) -> int:
    """Puntos por una cotización pagada, según su subtotal SIN IVA.

    Escala agresiva aprobada el 2026-10-23: el dinero que entra pesa más que
    cualquier actividad. Se usa el subtotal (material + flete) y no el total
    con IVA para que el impuesto no infle el puntaje.
    """
    monto = float(subtotal or 0)
    if monto < 10_000:
        return 15
    if monto < 30_000:
        return 25
    if monto < 60_000:
        return 40
    if monto < 100_000:
        return 55
    return 75


# ─────────────────────────────────────────────────────────────────────────────
# NIVELES (por puntos de temporada)
# ─────────────────────────────────────────────────────────────────────────────

NIVELES: list[dict[str, Any]] = [
    {"nombre": "Bronce",   "min": 0,    "max": 249,  "icono": "🥉", "color": "#CD7F32"},
    {"nombre": "Plata",    "min": 250,  "max": 749,  "icono": "🥈", "color": "#C0C0C0"},
    {"nombre": "Oro",      "min": 750,  "max": 1499, "icono": "🥇", "color": "#FFD700"},
    {"nombre": "Diamante", "min": 1500, "max": 2999, "icono": "💎", "color": "#22D3EE"},
    {"nombre": "Leyenda",  "min": 3000, "max": None, "icono": "🌟", "color": "#A855F7"},
]

NIVEL_POR_NOMBRE = {n["nombre"]: n for n in NIVELES}
ICONO_NIVEL = {n["nombre"]: n["icono"] for n in NIVELES}


def nivel_de(puntos: int) -> dict[str, Any]:
    """Nivel que corresponde a esos puntos de temporada."""
    pts = int(puntos or 0)
    actual = NIVELES[0]
    for nivel in NIVELES:
        if pts >= nivel["min"]:
            actual = nivel
        else:
            break
    return actual


def siguiente_nivel(puntos: int) -> dict[str, Any] | None:
    """Siguiente nivel, o None si ya es Leyenda."""
    actual = nivel_de(puntos)
    idx = NIVELES.index(actual)
    return NIVELES[idx + 1] if idx + 1 < len(NIVELES) else None


def progreso_nivel(puntos: int) -> dict[str, Any]:
    """Progreso dentro del nivel actual (0-100) y puntos que faltan."""
    pts = int(puntos or 0)
    actual = nivel_de(pts)
    sig = siguiente_nivel(pts)
    if not sig:
        return {"actual": actual, "siguiente": None, "pct": 100, "faltan": 0}
    tramo = sig["min"] - actual["min"]
    avance = max(pts - actual["min"], 0)
    pct = int(round((avance / tramo) * 100)) if tramo else 100
    return {
        "actual": actual,
        "siguiente": sig,
        "pct": max(0, min(pct, 100)),
        "faltan": max(sig["min"] - pts, 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOGROS
# ─────────────────────────────────────────────────────────────────────────────

# tipo: qué contador alimenta el progreso.
#   reporte_firmado | ticket_oxxogas | kilometro | servicio | firma_remota
#   cliente_nuevo_cotizado | cotizacion_pagada | reporte_fotos |
#   reporte_mismo_dia | vale_con_comprobante
#   racha (mejor racha) | semana_activa (días con actividad esta semana)
#   podio (top 3 de la semana) | campeon (ganadores semanales)
LOGROS: list[dict[str, Any]] = [
    {
        "id": "primer_reporte", "nombre": "Primer reporte", "icono": "✅",
        "descripcion": "Firma tu primer reporte de servicio.",
        "meta": 1, "tipo": "reporte_firmado", "bonus": 10, "orden": 1,
    },
    {
        "id": "diez_reportes", "nombre": "Servicio impecable", "icono": "📚",
        "descripcion": "Firma 10 reportes de servicio.",
        "meta": 10, "tipo": "reporte_firmado", "bonus": 25, "orden": 2,
    },
    {
        "id": "primer_ticket", "nombre": "Primer ticket", "icono": "🎫",
        "descripcion": "Sube tu primer ticket de OxxoGas.",
        "meta": 1, "tipo": "ticket_oxxogas", "bonus": 5, "orden": 3,
    },
    {
        "id": "cazador_tickets", "nombre": "Cazador de tickets", "icono": "🧾",
        "descripcion": "Sube 10 tickets de OxxoGas.",
        "meta": 10, "tipo": "ticket_oxxogas", "bonus": 20, "orden": 4,
    },
    {
        "id": "primera_firma_remota", "nombre": "Firma a distancia", "icono": "📱",
        "descripcion": "Consigue tu primera firma remota.",
        "meta": 1, "tipo": "firma_remota", "bonus": 10, "orden": 5,
    },
    {
        "id": "racha_3", "nombre": "En racha", "icono": "🔥",
        "descripcion": "Mantén 3 días seguidos con actividad.",
        "meta": 3, "tipo": "racha", "bonus": 10, "orden": 6,
    },
    {
        "id": "racha_7", "nombre": "Imparable", "icono": "⚡",
        "descripcion": "Mantén 7 días seguidos con actividad.",
        "meta": 7, "tipo": "racha", "bonus": 25, "orden": 7,
    },
    {
        "id": "racha_30", "nombre": "Leyenda diaria", "icono": "🌟",
        "descripcion": "Mantén 30 días seguidos con actividad.",
        "meta": 30, "tipo": "racha", "bonus": 50, "orden": 8,
    },
    {
        "id": "kilometros_20", "nombre": "Rodador", "icono": "🛣️",
        "descripcion": "Registra kilometraje 20 veces.",
        "meta": 20, "tipo": "kilometro", "bonus": 15, "orden": 9,
    },
    {
        "id": "servicios_5", "nombre": "Veterano", "icono": "🔧",
        "descripcion": "Completa 5 servicios.",
        "meta": 5, "tipo": "servicio", "bonus": 15, "orden": 10,
    },
    {
        "id": "semana_activa", "nombre": "Semana completa", "icono": "📅",
        "descripcion": "Ten actividad en 5 días de la misma semana.",
        "meta": 5, "tipo": "semana_activa", "bonus": 15, "orden": 11,
    },
    {
        "id": "podio_semanal", "nombre": "Podio", "icono": "🏅",
        "descripcion": "Entra al top 3 de la semana.",
        "meta": 1, "tipo": "podio", "bonus": 20, "orden": 12,
    },
    {
        "id": "campeon_semanal", "nombre": "Campeón", "icono": "🏆",
        "descripcion": "Gana una semana de Legends.",
        "meta": 1, "tipo": "campeon", "bonus": 50, "orden": 13,
    },
    # ── Logros de dinero (2026-10-23) ───────────────────────────────────────
    {
        "id": "primer_cliente", "nombre": "Primer cliente", "icono": "🎯",
        "descripcion": "Cotiza a tu primer cliente nuevo.",
        "meta": 1, "tipo": "cliente_nuevo_cotizado", "bonus": 10, "orden": 14,
    },
    {
        "id": "cazador_clientes", "nombre": "Cazador de clientes", "icono": "🧲",
        "descripcion": "Cotiza a 5 clientes nuevos.",
        "meta": 5, "tipo": "cliente_nuevo_cotizado", "bonus": 25, "orden": 15,
    },
    {
        "id": "primer_pago", "nombre": "Primer pago", "icono": "💵",
        "descripcion": "Cobra tu primera cotización pagada.",
        "meta": 1, "tipo": "cotizacion_pagada", "bonus": 15, "orden": 16,
    },
    {
        "id": "cerrador", "nombre": "Cerrador", "icono": "🤝",
        "descripcion": "Cobra 10 cotizaciones pagadas.",
        "meta": 10, "tipo": "cotizacion_pagada", "bonus": 50, "orden": 17,
    },
    {
        "id": "evidencia", "nombre": "Evidencia", "icono": "📸",
        "descripcion": "Cierra 15 reportes con fotos.",
        "meta": 15, "tipo": "reporte_fotos", "bonus": 20, "orden": 18,
    },
    {
        "id": "mismo_dia", "nombre": "Mismo día", "icono": "📅",
        "descripcion": "Cierra 15 reportes el mismo día del servicio.",
        "meta": 15, "tipo": "reporte_mismo_dia", "bonus": 20, "orden": 19,
    },
    {
        "id": "vale_limpio", "nombre": "Vale limpio", "icono": "🧾",
        "descripcion": "Cierra 10 vales con su comprobante.",
        "meta": 10, "tipo": "vale_con_comprobante", "bonus": 15, "orden": 20,
    },
]

LOGRO_POR_ID = {l["id"]: l for l in LOGROS}
LOGRO_POR_ORDEN = {l["orden"]: l for l in LOGROS}


# ─────────────────────────────────────────────────────────────────────────────
# FECHAS / SEMANA / TEMPORADA
# ─────────────────────────────────────────────────────────────────────────────

def ahora_mx() -> dt.datetime:
    """Ahora con zona de la Ciudad de México."""
    return dt.datetime.now(MEXICO)


def _a_local(when: dt.datetime | dt.date | None = None) -> dt.datetime:
    """Normaliza a datetime local (naive). Acepta aware, naive o date."""
    if when is None:
        return ahora_mx().replace(tzinfo=None)
    if isinstance(when, dt.datetime):
        if when.tzinfo is not None:
            return when.astimezone(MEXICO).replace(tzinfo=None)
        return when
    return dt.datetime.combine(when, dt.time.min)


def fecha_mx(when: dt.datetime | dt.date | None = None) -> dt.date:
    """Fecha local de México."""
    if when is None:
        return ahora_mx().date()
    if isinstance(when, dt.datetime) and when.tzinfo is not None:
        return when.astimezone(MEXICO).date()
    if isinstance(when, dt.datetime):
        return when.date()
    return when


def domingo_antes(fecha: dt.datetime | dt.date) -> dt.date:
    """Domingo de la semana ECCSA que contiene esa fecha."""
    f = fecha_mx(fecha)
    return f - dt.timedelta(days=(f.weekday() + 1) % 7)


def inicio_semana_mx(when: dt.datetime | dt.date | None = None) -> dt.datetime:
    """Domingo 00:00 de la semana en curso (naive, hora México).

    Naive porque es lo que se compara contra DATETIME de SQL Server, que
    guarda hora local de México.
    """
    local = _a_local(when)
    domingo = local - dt.timedelta(days=(local.weekday() + 1) % 7)
    return domingo.replace(hour=0, minute=0, second=0, microsecond=0)


def fin_semana_mx(when: dt.datetime | dt.date | None = None) -> dt.datetime:
    """Domingo 00:00 de la semana siguiente (fin EXCLUSIVO de la actual)."""
    return inicio_semana_mx(when) + dt.timedelta(days=7)


def fecha_semana_mx(when: dt.datetime | dt.date | None = None) -> dt.date:
    """Inicio de la semana como date (para columnas DATE)."""
    return inicio_semana_mx(when).date()


def semana_anterior_mx(when: dt.datetime | dt.date | None = None) -> tuple[dt.datetime, dt.datetime]:
    """(inicio, fin_exclusivo) de la semana anterior completa."""
    actual = inicio_semana_mx(when)
    return actual - dt.timedelta(days=7), actual


def segundos_para_reset(when: dt.datetime | dt.date | None = None) -> int:
    """Segundos que faltan para el próximo domingo 00:00 hora México."""
    ahora = when or ahora_mx()
    if not isinstance(ahora, dt.datetime):
        ahora = _a_local(ahora)
    if ahora.tzinfo is None:
        ahora = MEXICO.localize(ahora)
    proximo_naive = inicio_semana_mx(ahora) + dt.timedelta(days=7)
    proximo = MEXICO.localize(proximo_naive)
    return max(int((proximo - ahora).total_seconds()), 0)


def temporada_actual(when: dt.datetime | dt.date | None = None) -> dict[str, Any]:
    """Temporada = trimestre calendario (Q1..Q4)."""
    hoy = fecha_mx(when)
    q = (hoy.month - 1) // 3 + 1
    inicio = dt.date(hoy.year, 3 * (q - 1) + 1, 1)
    if q == 4:
        fin = dt.date(hoy.year, 12, 31)
    else:
        fin = dt.date(hoy.year, 3 * q + 1, 1) - dt.timedelta(days=1)
    inicio_dt = dt.datetime.combine(inicio, dt.time.min)
    fin_dt_exclusivo = dt.datetime.combine(fin + dt.timedelta(days=1), dt.time.min)
    return {
        "clave": f"{hoy.year}-Q{q}",
        "nombre": f"Temporada Q{q} {hoy.year}",
        "corto": f"Q{q} {hoy.year}",
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "inicio_dt": inicio_dt,
        "fin_dt_exclusivo": fin_dt_exclusivo,
        "dias_restantes": max((fin - hoy).days + 1, 0),
        "dias_totales": (fin - inicio).days + 1,
    }


def _rango_semana(when: dt.datetime | dt.date | None = None) -> tuple[dt.datetime, dt.datetime]:
    inicio = inicio_semana_mx(when)
    return inicio, inicio + dt.timedelta(days=7)


# ─────────────────────────────────────────────────────────────────────────────
# CONSULTAS DE PUNTOS / RACHA
# ─────────────────────────────────────────────────────────────────────────────

def _tabla_existe(cur, tabla: str) -> bool:
    try:
        cur.execute(
            "SELECT 1 AS existe FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s",
            (tabla,),
        )
        return bool(cur.fetchone())
    except Exception:
        return False


def _columna_existe(cur, tabla: str, columna: str) -> bool:
    try:
        cur.execute(
            "SELECT 1 AS existe FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = %s AND COLUMN_NAME = %s",
            (tabla, columna),
        )
        return bool(cur.fetchone())
    except Exception:
        return False


def _dias_actividad(cur, id_usuario: int, desde: dt.date) -> list[dt.date]:
    """Días con actividad POSITIVA (sin bonus de racha ni logros)."""
    cur.execute(
        """
        SELECT DISTINCT CAST(FechaRegistro AS DATE) AS dia
        FROM HUB_ScoreLog
        WHERE IdUsuario = %s
          AND FechaRegistro >= %s
          AND Puntos > 0
          AND Metrica NOT IN ('racha_dia', 'logro', 'sync_completado')
        ORDER BY dia
        """,
        (id_usuario, desde),
    )
    return [r["dia"] for r in cur.fetchall()]


def calcular_racha(dias: Iterable[dt.date], hoy: dt.date) -> int:
    """Racha actual: días consecutivos hasta hoy (o ayer si hoy aún no hay)."""
    s = set(dias)
    cursor = hoy if hoy in s else (hoy - dt.timedelta(days=1) if hoy - dt.timedelta(days=1) in s else None)
    if cursor is None:
        return 0
    n = 0
    while cursor in s:
        n += 1
        cursor -= dt.timedelta(days=1)
    return n


def calcular_racha_max(dias: Iterable[dt.date]) -> int:
    """Mejor racha del periodo consultado."""
    orden = sorted(set(dias))
    if not orden:
        return 0
    mejor = actual = 1
    for prev, cur in zip(orden, orden[1:]):
        if cur - prev == dt.timedelta(days=1):
            actual += 1
            mejor = max(mejor, actual)
        elif cur != prev:
            actual = 1
    return mejor


def _suma_puntos(cur, id_usuario: int, desde: dt.datetime | None = None) -> int:
    if desde is None:
        cur.execute(
            "SELECT ISNULL(SUM(Puntos), 0) AS total FROM HUB_ScoreLog "
            "WHERE IdUsuario = %s AND Metrica <> 'sync_completado'",
            (id_usuario,),
        )
    else:
        cur.execute(
            "SELECT ISNULL(SUM(Puntos), 0) AS total FROM HUB_ScoreLog "
            "WHERE IdUsuario = %s AND FechaRegistro >= %s AND Metrica <> 'sync_completado'",
            (id_usuario, desde),
        )
    row = cur.fetchone()
    return int(row["total"] or 0) if row else 0


def stats_usuario(cur, id_usuario: int, when: dt.datetime | dt.date | None = None) -> dict[str, Any]:
    """Puntos de semana/temporada/total, racha y nivel de un usuario."""
    inicio_sem, _ = _rango_semana(when)
    temporada = temporada_actual(when)
    hoy = fecha_mx(when)

    semanal = _suma_puntos(cur, id_usuario, inicio_sem)
    temporada_pts = _suma_puntos(cur, id_usuario, temporada["inicio_dt"])
    total = _suma_puntos(cur, id_usuario)

    desde = hoy - dt.timedelta(days=180)
    dias = _dias_actividad(cur, id_usuario, desde)
    racha = calcular_racha(dias, hoy)
    racha_max = calcular_racha_max(dias)

    # Última actividad real (sin bonus)
    cur.execute(
        """
        SELECT MAX(FechaRegistro) AS ultimo
        FROM HUB_ScoreLog
        WHERE IdUsuario = %s AND Puntos > 0
          AND Metrica NOT IN ('racha_dia', 'logro', 'sync_completado')
        """,
        (id_usuario,),
    )
    row = cur.fetchone()
    ultimo = row["ultimo"] if row else None

    nivel = nivel_de(temporada_pts)
    sig = siguiente_nivel(temporada_pts)
    prog = progreso_nivel(temporada_pts)

    return {
        "semanal": semanal,
        "temporada": temporada_pts,
        "total": total,
        "racha": racha,
        "racha_max": racha_max,
        "ultimo_activo": ultimo.isoformat() if hasattr(ultimo, "isoformat") else ultimo,
        "ultimo_activo_dt": ultimo,
        "nivel": nivel["nombre"],
        "icono_nivel": nivel["icono"],
        "color_nivel": nivel["color"],
        "siguiente_nivel": sig,
        "progreso_nivel": prog,
        "hoy": hoy.isoformat(),
        "actividad_hoy": hoy in set(dias),
        "temporada_info": temporada,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RANKING
# ─────────────────────────────────────────────────────────────────────────────

def guardar_scores(cur, id_usuario: int, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Escribe la fila HUB_UserScores con los valores recalculados.

    La usan el auditor y `_registrar_metrica` de Field: una sola forma de
    actualizar semana/total/nivel/racha, sin sumas incrementales que se
    desincronizan cuando falla un ciclo.
    """
    stats = stats or stats_usuario(cur, id_usuario)
    tiene_max = _columna_existe(cur, "HUB_UserScores", "RachaMax")
    ultimo = stats.get("ultimo_activo_dt")
    cur.execute("SELECT Id FROM HUB_UserScores WHERE IdUsuario = %s", (id_usuario,))
    existe = cur.fetchone() is not None

    if tiene_max:
        if existe:
            cur.execute(
                "UPDATE HUB_UserScores SET Puntuacion = %s, PuntuacionSemanal = %s, "
                "PuntuacionTotal = %s, Nivel = %s, RachaDias = %s, RachaMax = %s, "
                "UltimoActivo = COALESCE(%s, UltimoActivo), FechaCalculo = GETDATE() "
                "WHERE IdUsuario = %s",
                (stats["total"], stats["semanal"], stats["total"], stats["nivel"],
                 stats["racha"], stats["racha_max"], ultimo, id_usuario),
            )
        else:
            cur.execute(
                "INSERT INTO HUB_UserScores "
                "(IdUsuario, Puntuacion, PuntuacionSemanal, PuntuacionTotal, Nivel, "
                "RachaDias, RachaMax, UltimoActivo) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, GETDATE()))",
                (id_usuario, stats["total"], stats["semanal"], stats["total"], stats["nivel"],
                 stats["racha"], stats["racha_max"], ultimo),
            )
    else:
        if existe:
            cur.execute(
                "UPDATE HUB_UserScores SET Puntuacion = %s, PuntuacionSemanal = %s, "
                "PuntuacionTotal = %s, Nivel = %s, RachaDias = %s, "
                "UltimoActivo = COALESCE(%s, UltimoActivo), FechaCalculo = GETDATE() "
                "WHERE IdUsuario = %s",
                (stats["total"], stats["semanal"], stats["total"], stats["nivel"],
                 stats["racha"], ultimo, id_usuario),
            )
        else:
            cur.execute(
                "INSERT INTO HUB_UserScores "
                "(IdUsuario, Puntuacion, PuntuacionSemanal, PuntuacionTotal, Nivel, "
                "RachaDias, UltimoActivo) "
                "VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, GETDATE()))",
                (id_usuario, stats["total"], stats["semanal"], stats["total"], stats["nivel"],
                 stats["racha"], ultimo),
            )
    return stats


def _puntos_por_usuario(
    cur,
    inicio_semana: dt.datetime,
    inicio_temporada: dt.datetime,
) -> dict[int, dict[str, int]]:
    """Una sola pasada: semana, temporada y total por usuario.

    Así el ranking no depende de que el auditor haya corrido: si el worker
    está caído, la pantalla sigue mostrando los puntos reales de ScoreLog.
    """
    cur.execute(
        """
        SELECT IdUsuario,
               ISNULL(SUM(CASE WHEN FechaRegistro >= %s THEN Puntos ELSE 0 END), 0) AS semanal,
               ISNULL(SUM(CASE WHEN FechaRegistro >= %s THEN Puntos ELSE 0 END), 0) AS temporada,
               ISNULL(SUM(Puntos), 0) AS total
        FROM HUB_ScoreLog
        WHERE Metrica <> 'sync_completado'
        GROUP BY IdUsuario
        """,
        (inicio_semana, inicio_temporada),
    )
    return {
        int(r["IdUsuario"]): {
            "semanal": int(r["semanal"] or 0),
            "temporada": int(r["temporada"] or 0),
            "total": int(r["total"] or 0),
        }
        for r in cur.fetchall()
    }


def ranking(cur, id_actual: int | None = None, when: dt.datetime | dt.date | None = None) -> list[dict[str, Any]]:
    """Ranking de la semana actual.

    Incluye a todos los usuarios activos con passkey. Quien no tiene nickname
    aparece al final con 0 puntos y `elegible=False` (la app le pide completar
    su perfil); no se le inventan puntos viejos.
    """
    temporada = temporada_actual(when)
    inicio_sem = inicio_semana_mx(when)
    puntos = _puntos_por_usuario(cur, inicio_sem, temporada["inicio_dt"])

    cur.execute(
        """
        SELECT u.Id AS id_usuario,
               u.Nombre AS nombre,
               u.Nickname AS nickname,
               ISNULL(s.RachaDias, 0) AS racha,
               CASE WHEN EXISTS (SELECT 1 FROM HUB_UserAvatars a WHERE a.IdUsuario = u.Id)
                    THEN 1 ELSE 0 END AS tiene_avatar
        FROM HUB_Users u
        LEFT JOIN HUB_UserScores s ON s.IdUsuario = u.Id
        WHERE u.Activo = 1
          AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)
        """
    )
    rows = cur.fetchall()
    salida: list[dict[str, Any]] = []
    for r in rows:
        nickname = (r.get("nickname") or "").strip()
        elegible = bool(nickname)
        pts = puntos.get(int(r["id_usuario"]), {"semanal": 0, "temporada": 0, "total": 0})
        pts_sem = pts["semanal"] if elegible else 0
        pts_temp = pts["temporada"] if elegible else 0
        pts_total = pts["total"] if elegible else 0
        nivel = nivel_de(pts_temp)
        salida.append({
            "id_usuario": int(r["id_usuario"]),
            "nombre": r["nombre"],
            "nickname": nickname or r["nombre"],
            "puntos_semana": pts_sem,
            "puntos_temporada": pts_temp,
            "puntos_total": pts_total,
            "racha_dias": int(r["racha"] or 0) if elegible else 0,
            "nivel": nivel["nombre"],
            "icono_nivel": nivel["icono"],
            "color_nivel": nivel["color"],
            "tiene_avatar": bool(r["tiene_avatar"]),
            "elegible": elegible,
            "es_yo": id_actual is not None and int(r["id_usuario"]) == int(id_actual),
        })

    salida.sort(
        key=lambda u: (
            1 if u["elegible"] else 0,
            u["puntos_semana"],
            u["puntos_temporada"],
            u["puntos_total"],
            -u["id_usuario"],
        ),
        reverse=True,
    )
    for i, u in enumerate(salida, start=1):
        u["posicion"] = i
    return salida


def usuario_en_ranking(cur, id_usuario: int, when: dt.datetime | dt.date | None = None) -> dict[str, Any] | None:
    for u in ranking(cur, id_usuario, when):
        if u["id_usuario"] == int(id_usuario):
            return u
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GANADOR SEMANAL / HISTORIAL
# ─────────────────────────────────────────────────────────────────────────────

def calcular_ganador_semana(cur, inicio: dt.datetime, fin_exclusivo: dt.datetime) -> dict[str, Any] | None:
    """Ganador por puntos de ScoreLog en una ventana explícita.

    Desempate: quien llegó primero a su total (MIN(FechaRegistro)) y, si aún
    empata, el IdUsuario más bajo. Nunca se lee PuntuacionSemanal: el auditor
    puede haberla recalculado ya para la semana nueva.
    """
    cur.execute(
        """
        SELECT TOP 1
            s.IdUsuario,
            SUM(s.Puntos) AS puntos,
            MIN(s.FechaRegistro) AS primero
        FROM HUB_ScoreLog s
        JOIN HUB_Users u ON u.Id = s.IdUsuario
        WHERE s.FechaRegistro >= %s
          AND s.FechaRegistro < %s
          AND s.Metrica <> 'sync_completado'
          AND u.Activo = 1
          AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> ''
          AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)
        GROUP BY s.IdUsuario
        HAVING SUM(s.Puntos) > 0
        ORDER BY SUM(s.Puntos) DESC, MIN(s.FechaRegistro) ASC, s.IdUsuario ASC
        """,
        (inicio, fin_exclusivo),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id_usuario": int(row["IdUsuario"]),
        "puntos": int(row["puntos"] or 0),
        "primero": row["primero"].isoformat() if hasattr(row["primero"], "isoformat") else row["primero"],
    }


def guardar_ganador(
    cur,
    inicio: dt.datetime,
    fin_exclusivo: dt.datetime,
    conn=None,
    notificar: bool = False,
) -> dict[str, Any] | None:
    """Guarda el ganador de una semana cerrada si aún no existe.

    NO resetea PuntuacionSemanal: el auditor es el dueño de recalcularla desde
    ScoreLog. Resetear aquí borraba los puntos de las primeras horas del
    domingo (el cron corre a las 3 AM).
    """
    tiene_semana = _columna_existe(cur, "HUB_WeeklyWinners", "SemanaKey")
    clave = inicio.date().isoformat()
    if tiene_semana:
        cur.execute("SELECT Id FROM HUB_WeeklyWinners WHERE SemanaKey = %s", (clave,))
    else:
        cur.execute(
            "SELECT Id FROM HUB_WeeklyWinners WHERE FechaInicio = %s AND FechaFin = %s",
            (inicio.date(), (fin_exclusivo - dt.timedelta(days=1)).date()),
        )
    if cur.fetchone():
        return None

    ganador = calcular_ganador_semana(cur, inicio, fin_exclusivo)
    if not ganador:
        return None

    if tiene_semana:
        cur.execute(
            "INSERT INTO HUB_WeeklyWinners "
            "(IdUsuario, PuntuacionSemana, FechaInicio, FechaFin, SemanaKey, FechaCalculo) "
            "VALUES (%s, %s, %s, %s, %s, GETDATE())",
            (
                ganador["id_usuario"], ganador["puntos"],
                inicio.date(), (fin_exclusivo - dt.timedelta(days=1)).date(), clave,
            ),
        )
    else:
        cur.execute(
            "INSERT INTO HUB_WeeklyWinners "
            "(IdUsuario, PuntuacionSemana, FechaInicio, FechaFin, FechaCalculo) "
            "VALUES (%s, %s, %s, %s, GETDATE())",
            (
                ganador["id_usuario"], ganador["puntos"],
                inicio.date(), (fin_exclusivo - dt.timedelta(days=1)).date(),
            ),
        )

    ganador["semana_key"] = clave
    ganador["fecha_inicio"] = inicio.date().isoformat()
    ganador["fecha_fin"] = (fin_exclusivo - dt.timedelta(days=1)).date().isoformat()
    if conn is not None:
        conn.commit()

    if notificar:
        try:
            from routers.push import notify_weekly_winner
            notify_weekly_winner(ganador["id_usuario"], ganador["puntos"])
        except Exception:
            pass
    return ganador


def normalizar_ganadores_legacy(cur, conn=None) -> int:
    """Pone SemanaKey/FechaInicio/FechaFin correctos a filas viejas.

    La primera fila de producción era 2026-09-14..20 (lunes-domingo). La regla
    nueva es domingo-sábado, así que se reubica en la semana que la contiene y
    se recalcula el ganador desde ScoreLog. Devuelve cuántas filas cambió.
    """
    tiene_semana = _columna_existe(cur, "HUB_WeeklyWinners", "SemanaKey")
    if not tiene_semana:
        return 0
    cur.execute(
        "SELECT Id, FechaInicio FROM HUB_WeeklyWinners "
        "WHERE SemanaKey IS NULL OR LTRIM(RTRIM(SemanaKey)) = ''"
    )
    filas = cur.fetchall()
    cambios = 0
    for fila in filas:
        inicio = inicio_semana_mx(fila["FechaInicio"])
        fin = inicio + dt.timedelta(days=7)
        ganador = calcular_ganador_semana(cur, inicio, fin)
        if not ganador:
            # Semana sin actividad: la fila vieja no representa nada. Se deja
            # con su SemanaKey para no borrar historial a la ligera.
            cur.execute(
                "UPDATE HUB_WeeklyWinners SET SemanaKey = %s, FechaInicio = %s, FechaFin = %s "
                "WHERE Id = %s",
                (inicio.date().isoformat(), inicio.date(),
                 (fin - dt.timedelta(days=1)).date(), fila["Id"]),
            )
        else:
            cur.execute(
                "UPDATE HUB_WeeklyWinners SET IdUsuario = %s, PuntuacionSemana = %s, "
                "FechaInicio = %s, FechaFin = %s, SemanaKey = %s, FechaCalculo = GETDATE() "
                "WHERE Id = %s",
                (ganador["id_usuario"], ganador["puntos"], inicio.date(),
                 (fin - dt.timedelta(days=1)).date(), inicio.date().isoformat(), fila["Id"]),
            )
        cambios += 1
    if conn is not None and cambios:
        conn.commit()
    return cambios


def backfill_ganadores(
    cur,
    conn=None,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    notificar: bool = False,
) -> list[dict[str, Any]]:
    """Rellena todas las semanas cerradas que falten.

    - Normaliza filas legacy.
    - Empieza en la primera actividad (o en `desde`) y termina en la última
      semana COMPLETA (la anterior al domingo en curso).
    - No toca la semana actual ni resetea puntos.
    """
    normalizar_ganadores_legacy(cur, conn)

    if desde is None:
        cur.execute("SELECT MIN(FechaRegistro) AS primera FROM HUB_ScoreLog")
        row = cur.fetchone()
        primera = row["primera"] if row else None
        desde = domingo_antes(primera) if primera else (inicio_semana_mx().date() - dt.timedelta(days=56))

    actual = inicio_semana_mx().date()
    ultima_completa = actual - dt.timedelta(days=7)
    if hasta is not None:
        ultima_completa = min(ultima_completa, hasta)

    tiene_semana = _columna_existe(cur, "HUB_WeeklyWinners", "SemanaKey")
    if tiene_semana:
        cur.execute(
            "SELECT MAX(SemanaKey) AS ultima FROM HUB_WeeklyWinners "
            "WHERE SemanaKey IS NOT NULL AND LTRIM(RTRIM(SemanaKey)) <> ''"
        )
        row = cur.fetchone()
        if row and row.get("ultima"):
            try:
                siguiente = dt.date.fromisoformat(str(row["ultima"])[:10]) + dt.timedelta(days=7)
                desde = max(desde, siguiente)
            except ValueError:
                pass

    inicio = domingo_antes(desde)
    nuevos: list[dict[str, Any]] = []
    while inicio <= ultima_completa:
        ini_dt = dt.datetime.combine(inicio, dt.time.min)
        fin_dt = ini_dt + dt.timedelta(days=7)
        g = guardar_ganador(cur, ini_dt, fin_dt, conn=conn, notificar=notificar)
        if g:
            nuevos.append(g)
        inicio += dt.timedelta(days=7)
    return nuevos


def ganador_de_semana(cur, inicio: dt.datetime, fin_exclusivo: dt.datetime) -> dict[str, Any] | None:
    """Ganador guardado de una ventana (con nombre), o None."""
    tiene_semana = _columna_existe(cur, "HUB_WeeklyWinners", "SemanaKey")
    if tiene_semana:
        cur.execute(
            """
            SELECT TOP 1 w.IdUsuario, w.PuntuacionSemana AS puntos,
                   w.FechaInicio, w.FechaFin, w.SemanaKey,
                   u.Nombre AS nombre, u.Nickname AS nickname
            FROM HUB_WeeklyWinners w
            JOIN HUB_Users u ON u.Id = w.IdUsuario
            WHERE w.SemanaKey = %s
            """,
            (inicio.date().isoformat(),),
        )
    else:
        cur.execute(
            """
            SELECT TOP 1 w.IdUsuario, w.PuntuacionSemana AS puntos,
                   w.FechaInicio, w.FechaFin,
                   u.Nombre AS nombre, u.Nickname AS nickname
            FROM HUB_WeeklyWinners w
            JOIN HUB_Users u ON u.Id = w.IdUsuario
            WHERE w.FechaInicio = %s
            """,
            (inicio.date(),),
        )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id_usuario": int(row["IdUsuario"]),
        "nombre": row.get("nickname") or row["nombre"],
        "puntos": int(row["puntos"] or 0),
        "fecha_inicio": row["FechaInicio"].isoformat() if row["FechaInicio"] else None,
        "fecha_fin": row["FechaFin"].isoformat() if row["FechaFin"] else None,
        "semana_key": row.get("SemanaKey") or (row["FechaInicio"].isoformat() if row["FechaInicio"] else None),
    }


def historial_ganadores(cur, limite: int = 12) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT TOP {int(limite)}
            w.IdUsuario, w.PuntuacionSemana AS puntos,
            w.FechaInicio, w.FechaFin, w.FechaCalculo,
            u.Nombre AS nombre, u.Nickname AS nickname
        FROM HUB_WeeklyWinners w
        JOIN HUB_Users u ON u.Id = w.IdUsuario
        ORDER BY w.FechaInicio DESC
        """
    )
    salida = []
    for r in cur.fetchall():
        salida.append({
            "id_usuario": int(r["IdUsuario"]),
            "nombre": r.get("nickname") or r["nombre"],
            "puntos": int(r["puntos"] or 0),
            "fecha_inicio": r["FechaInicio"].isoformat() if r["FechaInicio"] else None,
            "fecha_fin": r["FechaFin"].isoformat() if r["FechaFin"] else None,
            "calculado": r["FechaCalculo"].isoformat() if r["FechaCalculo"] else None,
        })
    return salida


def historial_usuario(cur, id_usuario: int, semanas: int = 6, when=None) -> list[dict[str, Any]]:
    """Puntos de las últimas N semanas (incluida la actual) para la gráfica."""
    actual = inicio_semana_mx(when).date()
    primera = actual - dt.timedelta(weeks=max(semanas - 1, 0))
    cur.execute(
        "SELECT FechaRegistro, Puntos FROM HUB_ScoreLog "
        "WHERE IdUsuario = %s AND FechaRegistro >= %s AND Metrica <> 'sync_completado'",
        (id_usuario, dt.datetime.combine(primera, dt.time.min)),
    )
    buckets = {primera + dt.timedelta(weeks=i): 0 for i in range(semanas)}
    for r in cur.fetchall():
        d = domingo_antes(r["FechaRegistro"])
        if d in buckets:
            buckets[d] += int(r["Puntos"] or 0)
    return [
        {
            "inicio": d.isoformat(),
            "fin": (d + dt.timedelta(days=6)).isoformat(),
            "puntos": pts,
            "actual": d == actual,
        }
        for d, pts in buckets.items()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# LOGROS: PROGRESO
# ─────────────────────────────────────────────────────────────────────────────

def _conteo_metricas(cur, id_usuario: int) -> dict[str, int]:
    cur.execute(
        """
        SELECT Metrica, COUNT(*) AS n
        FROM HUB_ScoreLog
        WHERE IdUsuario = %s
          AND Metrica IN (
              'reporte_firmado', 'ticket_oxxogas', 'kilometro', 'servicio',
              'firma_remota', 'cliente_nuevo_cotizado', 'cotizacion_pagada',
              'reporte_fotos', 'reporte_mismo_dia', 'vale_con_comprobante'
          )
        GROUP BY Metrica
        """,
        (id_usuario,),
    )
    return {r["Metrica"]: int(r["n"] or 0) for r in cur.fetchall()}


def _dias_semana_activa(cur, id_usuario: int, when=None) -> int:
    inicio, fin = _rango_semana(when)
    cur.execute(
        """
        SELECT COUNT(DISTINCT CAST(FechaRegistro AS DATE)) AS n
        FROM HUB_ScoreLog
        WHERE IdUsuario = %s AND FechaRegistro >= %s AND FechaRegistro < %s
          AND Puntos > 0
          AND Metrica NOT IN ('racha_dia', 'logro', 'sync_completado')
        """,
        (id_usuario, inicio, fin),
    )
    row = cur.fetchone()
    return int(row["n"] or 0) if row else 0


def _campeonatos(cur, id_usuario: int) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM HUB_WeeklyWinners WHERE IdUsuario = %s", (id_usuario,))
    row = cur.fetchone()
    return int(row["n"] or 0) if row else 0


def progreso_logros(
    cur,
    id_usuario: int,
    stats: dict[str, Any] | None = None,
    posicion: int | None = None,
    when=None,
) -> list[dict[str, Any]]:
    """Catálogo + progreso + desbloqueado para un usuario.

    No escribe nada: el auditor es el que persiste en HUB_UserLogros.
    """
    stats = stats or stats_usuario(cur, id_usuario, when)
    conteos = _conteo_metricas(cur, id_usuario)
    dias_semana = _dias_semana_activa(cur, id_usuario, when)
    campeonatos = _campeonatos(cur, id_usuario)
    if posicion is None:
        u = usuario_en_ranking(cur, id_usuario, when)
        posicion = u["posicion"] if u else 999

    desbloqueados: dict[str, dict[str, Any]] = {}
    if _tabla_existe(cur, "HUB_UserLogros"):
        cur.execute(
            "SELECT LogroId, FechaObtenido, Puntos FROM HUB_UserLogros WHERE IdUsuario = %s",
            (id_usuario,),
        )
        for r in cur.fetchall():
            desbloqueados[r["LogroId"]] = {
                "fecha": r["FechaObtenido"].isoformat() if r["FechaObtenido"] else None,
                "puntos": int(r["Puntos"] or 0),
            }

    salida = []
    for logro in LOGROS:
        tipo = logro["tipo"]
        if tipo in conteos:
            progreso = conteos[tipo]
        elif tipo == "racha":
            progreso = int(stats.get("racha_max") or 0)
        elif tipo == "semana_activa":
            progreso = dias_semana
        elif tipo == "podio":
            progreso = 1 if posicion <= 3 else 0
        elif tipo == "campeon":
            progreso = campeonatos
        else:
            progreso = 0

        meta = int(logro["meta"])
        desbloqueado = logro["id"] in desbloqueados
        salida.append({
            **logro,
            "progreso": min(progreso, meta),
            "progreso_real": progreso,
            "pct": int(round(min(progreso / meta, 1) * 100)) if meta else 100,
            "desbloqueado": desbloqueado,
            "completado": progreso >= meta,
            "fecha_obtenido": desbloqueados.get(logro["id"], {}).get("fecha"),
            "puntos_obtenidos": desbloqueados.get(logro["id"], {}).get("puntos", logro["bonus"]),
        })
    return salida


# ─────────────────────────────────────────────────────────────────────────────
# RESUMEN DE LA SEMANA (eventos, por métrica, por día)
# ─────────────────────────────────────────────────────────────────────────────

_DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _descripcion_evento(metrica: str, referencia_id: int | None) -> tuple[str, str]:
    if metrica == "logro":
        logro = LOGRO_POR_ORDEN.get(int(referencia_id)) if referencia_id is not None else None
        if logro:
            return (f"Logro: {logro['nombre']}", logro["icono"])
        return ("Logro desbloqueado", "🏆")
    info = METRICAS_INFO.get(metrica)
    if info:
        return (info["nombre"], info["icono"])
    return (metrica.replace("_", " ").capitalize(), "•")


def resumen_semana(cur, id_usuario: int, when=None) -> dict[str, Any]:
    inicio, fin = _rango_semana(when)
    hoy = fecha_mx(when)
    cur.execute(
        """
        SELECT Id, Metrica, Puntos, ReferenciaId, FechaRegistro
        FROM HUB_ScoreLog
        WHERE IdUsuario = %s AND FechaRegistro >= %s AND FechaRegistro < %s
          AND Metrica <> 'sync_completado'
        ORDER BY FechaRegistro DESC, Id DESC
        """,
        (id_usuario, inicio, fin),
    )
    filas = cur.fetchall()
    eventos = []
    por_metrica: dict[str, dict[str, Any]] = {}
    por_dia = {
        (inicio + dt.timedelta(days=i)).date(): {
            "fecha": (inicio + dt.timedelta(days=i)).date().isoformat(),
            "dia": _DIAS_ES[(inicio + dt.timedelta(days=i)).weekday()],
            "puntos": 0, "eventos": 0,
            "hoy": (inicio + dt.timedelta(days=i)).date() == hoy,
            "futuro": (inicio + dt.timedelta(days=i)).date() > hoy,
        }
        for i in range(7)
    }

    for r in filas:
        metrica = r["Metrica"]
        nombre, icono = _descripcion_evento(metrica, r["ReferenciaId"])
        puntos = int(r["Puntos"] or 0)
        fecha = r["FechaRegistro"]
        fecha_iso = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha)
        eventos.append({
            "id": int(r["Id"]),
            "metrica": metrica,
            "nombre": nombre,
            "icono": icono,
            "puntos": puntos,
            "fecha": fecha_iso,
            "referencia_id": r["ReferenciaId"],
        })
        m = por_metrica.setdefault(metrica, {
            "metrica": metrica, "nombre": nombre, "icono": icono,
            "puntos": 0, "eventos": 0,
        })
        m["puntos"] += puntos
        m["eventos"] += 1
        d = fecha.date() if hasattr(fecha, "date") else fecha
        if d in por_dia:
            por_dia[d]["puntos"] += puntos
            por_dia[d]["eventos"] += 1

    return {
        "inicio": inicio.date().isoformat(),
        "fin": (fin - dt.timedelta(days=1)).date().isoformat(),
        "total": sum(e["puntos"] for e in eventos),
        "eventos": eventos,
        "por_metrica": sorted(por_metrica.values(), key=lambda x: (-x["puntos"], x["nombre"])),
        "por_dia": list(por_dia.values()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# OVERVIEW (un solo payload para Field/Admon)
# ─────────────────────────────────────────────────────────────────────────────

def guia() -> list[dict[str, Any]]:
    salida = []
    for clave in ORDEN_GUIA:
        info = METRICAS_INFO.get(clave)
        if not info:
            continue
        puntos = METRICAS.get(clave, 0)
        if clave == "servicio":
            puntos_texto = "Según horas"
        elif clave == "cotizacion_pagada":
            puntos_texto = "+15 a +75 según monto"
        else:
            puntos_texto = f"{puntos:+d}"
        salida.append({
            "metrica": clave,
            "nombre": info["nombre"],
            "icono": info["icono"],
            "tipo": info["tipo"],
            "puntos": puntos,
            "puntos_texto": puntos_texto,
            "descripcion": info["descripcion"],
            "como": info["como"],
        })
    return salida


def niveles_publicos() -> list[dict[str, Any]]:
    return [
        {
            "nombre": n["nombre"], "icono": n["icono"], "color": n["color"],
            "min": n["min"], "max": n["max"],
        }
        for n in NIVELES
    ]


def overview(cur, id_usuario: int, when=None) -> dict[str, Any]:
    """Todo lo que pinta la pantalla de Legends, en una sola llamada."""
    ranking_rows = ranking(cur, id_usuario, when)
    me = next((u for u in ranking_rows if u["id_usuario"] == int(id_usuario)), None)
    stats = stats_usuario(cur, id_usuario, when)
    temporada = temporada_actual(when)
    inicio_sem, fin_sem = _rango_semana(when)
    inicio_ant, fin_ant = semana_anterior_mx(when)

    ganador = ganador_de_semana(cur, inicio_ant, fin_ant)
    if ganador:
        ganador["es_yo"] = ganador["id_usuario"] == int(id_usuario)
        ganador["es_semana_anterior"] = True

    logros = progreso_logros(cur, id_usuario, stats=stats, posicion=me["posicion"] if me else None, when=when)
    desbloqueados = sum(1 for l in logros if l["desbloqueado"])
    mi_semana = resumen_semana(cur, id_usuario, when)

    usuario = {
        "id_usuario": int(id_usuario),
        "nombre": me["nombre"] if me else "",
        "nickname": (me["nickname"] if me else "") or "",
        "avatar": None,
        "elegible": bool(me and me["elegible"]),
        "tiene_avatar": bool(me and me["tiene_avatar"]),
        "posicion": me["posicion"] if me else None,
        "puntos_semana": stats["semanal"],
        "puntos_temporada": stats["temporada"],
        "puntos_total": stats["total"],
        "nivel": stats["nivel"],
        "icono_nivel": stats["icono_nivel"],
        "color_nivel": stats["color_nivel"],
        "siguiente_nivel": stats["siguiente_nivel"],
        "progreso_nivel": stats["progreso_nivel"],
        "racha_dias": stats["racha"],
        "racha_max": stats["racha_max"],
        "actividad_hoy": stats["actividad_hoy"],
        "ultimo_activo": stats["ultimo_activo"],
        "logros_desbloqueados": desbloqueados,
        "logros_total": len(logros),
    }

    return {
        "usuario": usuario,
        "temporada": {
            "clave": temporada["clave"],
            "nombre": temporada["nombre"],
            "corto": temporada["corto"],
            "inicio": temporada["inicio"],
            "fin": temporada["fin"],
            "dias_restantes": temporada["dias_restantes"],
            "dias_totales": temporada["dias_totales"],
            "puntos": stats["temporada"],
            "nivel": stats["nivel"],
            "siguiente_nivel": stats["siguiente_nivel"],
            "progreso_nivel": stats["progreso_nivel"],
        },
        "semana": {
            "inicio": inicio_sem.date().isoformat(),
            "fin": (fin_sem - dt.timedelta(days=1)).date().isoformat(),
            "reset_en_segundos": segundos_para_reset(when),
            "cierra": "domingo 00:00",
            "premia": "domingo 03:00",
            "mi_puntos": stats["semanal"],
            "mi_posicion": me["posicion"] if me else None,
        },
        "ganador_semana": ganador,
        "ranking": ranking_rows,
        "mi_semana": mi_semana,
        "logros": logros,
        "historial": historial_ganadores(cur, 12),
        "mi_historial": historial_usuario(cur, id_usuario, 6, when),
        "guia": guia(),
        "niveles": niveles_publicos(),
    }
