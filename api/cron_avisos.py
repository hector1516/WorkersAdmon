"""Worker único de notificaciones Field (programa `avisos` bajo supervisor).

Empleos:
1. Kilómetros semanal: cada lunes 8:00 AM (hora México, UTC-6 fijo) envía
   push PWA a usuarios activos con auto asignado sin registro desde el lunes.
2. Reportes sin firmar: cada hora revisa; avisa por push SOLO por reportes
   nuevos sin firmar desde el último aviso (deduplicado en HUB_Config clave
   field_avisos_rep_<IdUsuario> con la lista de folios ya avisados).

Loop infinito con sleeps cortos; sobrevive reinicios sin re-enviar.
"""
import datetime
import os
import time
import traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

MX_OFFSET = datetime.timedelta(hours=6)  # México UTC-6 fijo
KM_DIA = 0  # lunes
KM_HORA = 8  # 8:00 AM
REP_INTERVALO = 3600  # revisar reportes cada hora
SLEEP_CHUNK = 300  # revisar reloj cada 5 min


def ahora_mx():
    return datetime.datetime.utcnow() - MX_OFFSET


def lunes_actual_00():
    a = ahora_mx()
    return (a - datetime.timedelta(days=a.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def proximo_lunes_8am():
    a = ahora_mx()
    dias = (7 - a.weekday()) % 7
    cand = (a + datetime.timedelta(days=dias)).replace(hour=KM_HORA, minute=0, second=0, microsecond=0)
    if cand <= a:
        cand += datetime.timedelta(days=7)
    return cand


def _cfg_get(cur, clave):
    cur.execute("SELECT CAST(Valor AS VARCHAR(MAX)) AS v FROM HUB_Config WHERE Clave = %s", (clave,))
    row = cur.fetchone()
    return (row.get("v") if row else None) or ""


def _cfg_set(cur, clave, valor):
    cur.execute("SELECT COUNT(*) AS n FROM HUB_Config WHERE Clave = %s", (clave,))
    if (cur.fetchone() or {}).get("n", 0) > 0:
        cur.execute("UPDATE HUB_Config SET Valor = %s WHERE Clave = %s", (valor, clave))
    else:
        cur.execute("INSERT INTO HUB_Config (Clave, Valor) VALUES (%s, %s)", (clave, valor))


def job_kilometros():
    """Push de lunes 8am a deudores de kilometraje. Devuelve enviados."""
    from db import get_connection
    from routers.push import send_push_notification

    lunes_utc = (lunes_actual_00() + MX_OFFSET).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_connection()
    enviados = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT DISTINCT u.Id, u.Nombre, a.Id AS IdAuto, a.Placas
            FROM HUB_Users u
            INNER JOIN HUB_Automoviles a ON a.IdUsuarioAsignado = u.Id
            WHERE u.Activo = 1
        """)
        for c in cur.fetchall():
            cur.execute("""
                SELECT COUNT(*) AS n FROM HUB_RegistroKilometros
                WHERE IdAutomovil = %s AND FechaHora >= %s
            """, (c["IdAuto"], lunes_utc))
            if (cur.fetchone() or {}).get("n", 0) > 0:
                continue
            try:
                send_push_notification(
                    c["Id"],
                    "🔔 Registra tus kilómetros",
                    f"Hola {c['Nombre']}, recuerda registrar el kilometraje de tu vehículo ({c['Placas'] or 'asignado'}) en la app Field.",
                    url="/kilometros",
                )
                enviados += 1
            except Exception:
                traceback.print_exc()
    return enviados


def job_reportes():
    """Push por reportes NUEVOS sin firmar (deduplicado). Devuelve enviados."""
    from db import get_connection
    from routers.push import send_push_notification

    conn = get_connection()
    enviados = 0
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT Id, Nombre FROM HUB_Users WHERE Activo = 1")
        for u in cur.fetchall():
            try:
                cur.execute("""
                    SELECT r.Folio FROM ReportesServicio r
                    WHERE (r.Tecnico = %s
                       OR r.IdReporte IN (
                           SELECT rt.IdReporte FROM ReportesServicioTecnicos rt
                           INNER JOIN HUB_Users uu ON rt.IdUsuario = uu.Id
                           WHERE uu.Nombre = %s
                       ))
                      AND (r.FirmaConformidad IS NULL OR LTRIM(RTRIM(r.FirmaConformidad)) = '')
                      AND r.Folio IS NOT NULL
                    ORDER BY r.Fecha DESC, r.IdReporte DESC
                """, (u["Nombre"], u["Nombre"]))
                actuales = [r["Folio"] for r in cur.fetchall() if r.get("Folio")]
                clave = f"field_avisos_rep_{u['Id']}"
                avisados = [f for f in _cfg_get(cur, clave).split(",") if f]
                nuevos = [f for f in actuales if f not in avisados]
                if nuevos:
                    cuales = ", ".join(nuevos[:4]) + ("…" if len(nuevos) > 4 else "")
                    send_push_notification(
                        u["Id"],
                        f"✍️ {len(nuevos)} reporte{'s' if len(nuevos) > 1 else ''} sin firmar",
                        f"Hola {u['Nombre']}, pendientes de firma: {cuales}. Ábrelos en Field.",
                        url="/reportes",
                    )
                    enviados += 1
                # Guardar estado actual (incluye firmados que salen solos)
                _cfg_set(cur, clave, ",".join(actuales[:50]))
                conn.commit()
            except Exception:
                traceback.print_exc()
                try:
                    conn.rollback()
                except Exception:
                    pass
    return enviados


def main():
    print("[avisos] worker único iniciado (km lunes 8am + reportes cada hora)", flush=True)
    ultima_semana_km = None
    ultimo_rep = 0.0
    while True:
        try:
            a = ahora_mx()
            # 1) Kilómetros: lunes después de las 8am, una vez por semana
            if a.weekday() == KM_DIA and a.hour >= KM_HORA:
                semana = lunes_actual_00().date().isoformat()
                if semana != ultima_semana_km:
                    n = job_kilometros()
                    print(f"[avisos] km semanal: push enviados: {n}", flush=True)
                    ultima_semana_km = semana
            # 2) Reportes: cada hora
            if time.time() - ultimo_rep >= REP_INTERVALO:
                n = job_reportes()
                print(f"[avisos] reportes: push enviados: {n}", flush=True)
                ultimo_rep = time.time()
        except Exception:
            traceback.print_exc()
        time.sleep(SLEEP_CHUNK)


if __name__ == "__main__":
    main()
