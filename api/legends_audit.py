"""
Legends Audit Worker - Corrige puntos faltantes de la SEMANA ACTUAL.

Cada 5 minutos revisa:
  1. Reportes firmados esta semana sin +10 en ScoreLog → los otorga
  2. Kilometros esta semana sin +5 en ScoreLog → los otorga
  3. Tickets OxxoGas esta semana sin +3 en ScoreLog → los otorga
  4. Vales esta semana sin -2 en ScoreLog → los otorga
  5. Recalcula PuntuacionSemanal = suma de ScoreLog de la semana

NO toca actividades de semanas anteriores.
Corre como proceso propio de supervisor (NO dentro de uvicorn):
con --workers 2, un thread por worker duplicaba ScoreLog en carrera.
"""

import time
import math
import threading
from datetime import datetime, timedelta

_INTERVAL = 300  # 5 minutos


def _get_conn():
    from db import get_connection
    return get_connection()


def _week_start():
    """Inicio de la semana actual (domingo 00:00 HORA MÉXICO).

    Importante: el contenedor corre en UTC, así que calcularlo con
    datetime.now() naive corría el corte de semana 5-6 h antes (sábado 18:00)
    y dejaba PuntuacionSemanal en 0. Ver legends_time.py.
    """
    from legends_time import inicio_semana_mx
    return inicio_semana_mx()


def _get_user_id_by_name(cur, nombre):
    cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (nombre,))
    r = cur.fetchone()
    return r["Id"] if r else None


def _has_score(cur, user_id, metrica, referencia_id):
    cur.execute(
        "SELECT TOP 1 1 AS existe FROM HUB_ScoreLog WHERE IdUsuario=%s AND Metrica=%s AND ReferenciaId=%s",
        (user_id, metrica, referencia_id),
    )
    return cur.fetchone() is not None


# Métricas donde ReferenciaId = id de entidad única (ticket/vale/reporte).
# Idempotencia: no insertar dos veces la misma (usuario, métrica, referencia).
# OJO: 'kilometro' NO está — ahí ReferenciaId es el vehículo (id_auto), no el registro.
_IDEMPOTENT = {
    "reporte_firmado", "ticket_oxxogas", "vale_generado",
    "comida_reporte", "servicio", "firma_remota",
}


def _add_score(cur, user_id, metrica, puntos, referencia_id):
    """Inserta en ScoreLog + actualiza UserScores (idempotente si aplica)."""
    if metrica in _IDEMPOTENT and referencia_id is not None:
        cur.execute(
            "SELECT TOP 1 1 AS existe FROM HUB_ScoreLog WHERE IdUsuario=%s AND Metrica=%s AND ReferenciaId=%s",
            (user_id, metrica, referencia_id),
        )
        if cur.fetchone():
            return  # ya otorgado — no duplicar
    cur.execute(
        "INSERT INTO HUB_ScoreLog (IdUsuario, Metrica, Puntos, ReferenciaId) VALUES (%s,%s,%s,%s)",
        (user_id, metrica, puntos, referencia_id),
    )
    cur.execute("SELECT PuntuacionSemanal, PuntuacionTotal, Nivel FROM HUB_UserScores WHERE IdUsuario=%s", (user_id,))
    row = cur.fetchone()
    if row:
        ns = row["PuntuacionSemanal"] + puntos
        nt = row["PuntuacionTotal"] + puntos
        from routers.legends import _calcular_nivel
        nivel = _calcular_nivel(nt)
        cur.execute(
            "UPDATE HUB_UserScores SET PuntuacionSemanal=%s, PuntuacionTotal=%s, Nivel=%s, UltimoActivo=GETDATE(), FechaCalculo=GETDATE() WHERE IdUsuario=%s",
            (ns, nt, nivel, user_id),
        )
    else:
        ns = max(puntos, 0)
        nt = max(puntos, 0)
        from routers.legends import _calcular_nivel
        nivel = _calcular_nivel(nt)
        cur.execute(
            "INSERT INTO HUB_UserScores (IdUsuario, PuntuacionSemanal, PuntuacionTotal, Nivel, UltimoActivo) VALUES (%s,%s,%s,%s,GETDATE())",
            (user_id, ns, nt, nivel),
        )


def audit_reportes_firmados():
    """+10 por reporte firmado ESTA SEMANA sin score."""
    ws = _week_start()
    conn = _get_conn()
    count = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT r.IdReporte, r.Tecnico
            FROM ReportesServicio r
            WHERE r.Estatus = 'Firmado'
              AND r.Fecha >= %s
              AND NOT EXISTS (
                  SELECT TOP 1 1 AS existe FROM HUB_ScoreLog s
                  WHERE s.ReferenciaId = r.IdReporte AND s.Metrica = 'reporte_firmado'
              )
        """, (ws,))
        for rep in cur.fetchall():
            uid = _get_user_id_by_name(cur, rep["Tecnico"])
            if uid:
                cur.execute("SELECT TOP 1 1 AS existe FROM HUB_Users u WHERE u.Id=%s AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> '' AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario=u.Id)", (uid,))
                if cur.fetchone():
                    _add_score(cur, uid, "reporte_firmado", 10, rep["IdReporte"])
                    count += 1
        if count:
            conn.commit()
            print(f"[legends_audit] +{count*10}pts por {count} reportes firmados esta semana")
    conn.close()
    return count


def audit_kilometros():
    """+5 por cada km ESTA SEMANA sin score."""
    ws = _week_start()
    conn = _get_conn()
    count = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT k.Id, k.IdUsuario
            FROM HUB_RegistroKilometros k
            WHERE k.FechaHora >= %s
              AND NOT EXISTS (
                  SELECT TOP 1 1 AS existe FROM HUB_ScoreLog s
                  WHERE s.ReferenciaId = k.Id AND s.Metrica = 'kilometro'
              )
        """, (ws,))
        for km in cur.fetchall():
            uid = km["IdUsuario"]
            cur.execute("SELECT TOP 1 1 AS existe FROM HUB_Users u WHERE u.Id=%s AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> '' AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario=u.Id)", (uid,))
            if cur.fetchone():
                _add_score(cur, uid, "kilometro", 5, km["Id"])
                count += 1
        if count:
            conn.commit()
            print(f"[legends_audit] +{count*5}pts por {count} kilometros esta semana")
    conn.close()
    return count


def audit_tickets():
    """+3 por cada ticket ESTA SEMANA sin score."""
    ws = _week_start()
    conn = _get_conn()
    count = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT t.Id, t.IdUsuario
            FROM HUB_OxxoGasTickets t
            WHERE t.FechaRegistro >= %s
              AND NOT EXISTS (
                  SELECT TOP 1 1 AS existe FROM HUB_ScoreLog s
                  WHERE s.ReferenciaId = t.Id AND s.Metrica = 'ticket_oxxogas'
              )
        """, (ws,))
        for ticket in cur.fetchall():
            uid = ticket["IdUsuario"]
            cur.execute("SELECT TOP 1 1 AS existe FROM HUB_Users u WHERE u.Id=%s AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> '' AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario=u.Id)", (uid,))
            if cur.fetchone():
                _add_score(cur, uid, "ticket_oxxogas", 3, ticket["Id"])
                count += 1
        if count:
            conn.commit()
            print(f"[legends_audit] +{count*3}pts por {count} tickets esta semana")
    conn.close()
    return count


def audit_vales():
    """-2 por cada vale ESTA SEMANA sin penalizacion."""
    ws = _week_start()
    conn = _get_conn()
    count = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT v.Id, v.IdSolicitante
            FROM HUB_SolicitudVales v
            WHERE v.FechaSolicitud >= %s
              AND NOT EXISTS (
                  SELECT TOP 1 1 AS existe FROM HUB_ScoreLog s
                  WHERE s.ReferenciaId = v.Id AND s.Metrica = 'vale_generado'
              )
        """, (ws,))
        for vale in cur.fetchall():
            uid = vale["IdSolicitante"]
            cur.execute("SELECT TOP 1 1 AS existe FROM HUB_Users u WHERE u.Id=%s AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> '' AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario=u.Id)", (uid,))
            if cur.fetchone():
                _add_score(cur, uid, "vale_generado", -2, vale["Id"])
                count += 1
        if count:
            conn.commit()
            print(f"[legends_audit] -{count*2}pts por {count} vales esta semana")
    conn.close()
    return count


def audit_sync_weekly():
    """Recalcula PuntuacionSemanal desde ScoreLog de esta semana."""
    ws = _week_start()
    conn = _get_conn()
    with conn.cursor(as_dict=True) as cur:
        # Sumar puntos de la semana por usuario
        cur.execute("""
            SELECT s.IdUsuario, SUM(s.Puntos) AS total_semana
            FROM HUB_ScoreLog s
            JOIN HUB_Users u ON s.IdUsuario = u.Id
            WHERE u.Activo = 1 AND s.FechaRegistro >= %s
            GROUP BY s.IdUsuario
        """, (ws,))
        semanales = {r["IdUsuario"]: r["total_semana"] for r in cur.fetchall()}

        # Actualizar todos los usuarios activos con passkey + nickname (HUB_Users)
        cur.execute("""
            SELECT u.Id FROM HUB_Users u
            WHERE u.Activo = 1
              AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> ''
              AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)
        """)
        for u in cur.fetchall():
            uid = u["Id"]
            pts = semanales.get(uid, 0)
            cur.execute(
                "UPDATE HUB_UserScores SET PuntuacionSemanal = %s, FechaCalculo = GETDATE() WHERE IdUsuario = %s",
                (pts, uid),
            )
        conn.commit()
    conn.close()


def run_audit_loop():
    """Loop principal del audit worker."""
    print("[legends_audit] Worker iniciado (cada 5 min, solo semana actual)")
    while True:
        try:
            r1 = audit_reportes_firmados()
            r2 = audit_kilometros()
            r3 = audit_tickets()
            r4 = audit_vales()
            audit_sync_weekly()
            total = r1 + r2 + r3 + r4
            if total > 0:
                print(f"[legends_audit] Corregidos: {r1} reportes, {r2} km, {r3} tickets, {r4} vales")
        except Exception as e:
            print(f"[legends_audit] Error: {e}")
        time.sleep(_INTERVAL)


def start():
    """Inicia el audit worker como thread daemon."""
    t = threading.Thread(target=run_audit_loop, daemon=True, name="legends-audit")
    t.start()
    return t


if __name__ == "__main__":
    run_audit_loop()
