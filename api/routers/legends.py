"""
ECCSA Legends - Sistema de Puntuacion y Ranking gamificado.

METRICAS Y PUNTOS:
  - reporte_firmado:   +10 pts
  - kilometro:          +5 pts
  - ticket_oxxogas:     +3 pts
  - vale_generado:      -2 pts
  - comida_reporte:     -1 pts  (penalizacion por tomar hora de comida en reporte)
  - firma_remota:       +8 pts
  - racha_dia:          +3 pts
  - servicio:           Calculado (horas netas / participantes, min 1)

PUNTOS:
  - PuntuacionSemanal: se resetea cada domingo (para ranking semanal)
  - PuntuacionTotal: historico acumulado (nunca se resetea)

NIVELES (basados en PuntuacionTotal):
  Bronce:    0 - 499  🥉
  Plata:     500 - 1499  🥈
  Oro:       1500 - 3499  🥇
  Diamante:  3500+  💎

CRON:
  Domingo 3:00 AM → calcula ganador, guarda historial, resetea puntos semanales.
"""

from fastapi import APIRouter, Depends
from auth import require_user
from db import get_connection

router = APIRouter()

# ── Configuracion de metricas ──
METRICAS = {
    "reporte_firmado":  10,
    "kilometro":         5,
    "ticket_oxxogas":    3,
    "vale_generado":    -2,
    "comida_reporte":   -1,
    "firma_remota":      8,
    "servicio":          0,  # Calculado dinamicamente por registrar_puntos_servicio
    "racha_dia":         3,
}

NIVELES = [
    ("Diamante", 3500),
    ("Oro",      1500),
    ("Plata",     500),
    ("Bronce",      0),
]

ICONO_NIVEL = {
    "Diamante": "💎",
    "Oro":      "🥇",
    "Plata":    "🥈",
    "Bronce":   "🥉",
}


def _calcular_nivel(puntos: int) -> str:
    for nombre, min_pts in NIVELES:
        if puntos >= min_pts:
            return nombre
    return "Bronce"


def _get_posicion_ranking(user_id: int) -> int:
    """Obtiene la posicion actual del usuario en el ranking semanal."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT PuntuacionSemanal FROM HUB_UserScores WHERE IdUsuario = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return 999
        weekly = row["PuntuacionSemanal"]
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM HUB_UserScores s
            JOIN HUB_Users u ON s.IdUsuario = u.Id
            WHERE u.Activo = 1 AND s.PuntuacionSemanal > %s
        """, (weekly,))
        r = cur.fetchone()
        return (r["total"] + 1) if r else 1


def _get_usuario_pasado(user_id: int, old_pos: int, new_pos: int) -> int | None:
    """Encuentra a quien se paso subiendo en el ranking. Retorna el Id del usuario pasado o None."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT TOP 1 s.IdUsuario
            FROM HUB_UserScores s
            JOIN HUB_Users u ON s.IdUsuario = u.Id
            WHERE u.Activo = 1 AND s.IdUsuario != %s
            ORDER BY s.PuntuacionSemanal DESC
        """, (user_id,))
        ranking = cur.fetchall()
    # La posicion old_pos era la del usuario antes; ahora esta en new_pos
    # El usuario en la posicion old_pos (0-indexed: old_pos-1) es a quien se paso
    if old_pos - 1 < len(ranking):
        passed = ranking[old_pos - 1]
        if passed["IdUsuario"] != user_id:
            return passed["IdUsuario"]
    return None


def _registrar_metrica(id_usuario: int, metrica: str, referencia_id: int = None, puntos_override: int = None):
    """Registra una metrica y actualiza la puntuacion del usuario.
    Solo otorga puntos si el usuario tiene passkey (nickname).
    Detecta cambios de nivel y ranking para notificar."""
    if metrica not in METRICAS and puntos_override is None:
        return
    puntos = puntos_override if puntos_override is not None else METRICAS[metrica]

    # Idempotencia: métricas con ReferenciaId = entidad única no deben repetirse.
    # 'kilometro' excluido: su ReferenciaId es el vehículo, no el registro de km.
    _idempotent = {
        "reporte_firmado", "ticket_oxxogas", "vale_generado",
        "comida_reporte", "servicio", "firma_remota",
    }
    if metrica in _idempotent and referencia_id is not None:
        conn0 = get_connection()
        with conn0.cursor() as cur0:
            cur0.execute(
                "SELECT 1 AS existe FROM HUB_ScoreLog WHERE IdUsuario=%s AND Metrica=%s AND ReferenciaId=%s",
                (id_usuario, metrica, referencia_id),
            )
            if cur0.fetchone():
                conn0.close()
                return
        conn0.close()

    old_level = None
    old_pos = None
    new_level = None
    nueva_semanal = 0

    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        # Elegibilidad: passkey (identidad) + Nickname en HUB_Users (no más Etiqueta)
        cur.execute("""
            SELECT TOP 1 1 AS existe FROM HUB_Users u
            WHERE u.Id = %s
              AND u.Nickname IS NOT NULL AND LTRIM(RTRIM(u.Nickname)) <> ''
              AND EXISTS (SELECT 1 FROM HUB_Passkeys p WHERE p.IdUsuario = u.Id)
        """, (id_usuario,))
        if not cur.fetchone():
            conn.close()
            return

        # Capturar estado ANTES del update
        cur.execute("SELECT PuntuacionSemanal, PuntuacionTotal, Nivel FROM HUB_UserScores WHERE IdUsuario = %s", (id_usuario,))
        row = cur.fetchone()
        old_level = row["Nivel"] if row else "Bronce"
        old_weekly = row["PuntuacionSemanal"] if row else 0
        old_total = row["PuntuacionTotal"] if row else 0

        # Posicion anterior
        cur.execute("""
            SELECT COUNT(*) AS total FROM HUB_UserScores s
            JOIN HUB_Users u ON s.IdUsuario = u.Id
            WHERE u.Activo = 1 AND s.PuntuacionSemanal > %s
        """, (old_weekly,))
        old_pos = (cur.fetchone()["total"] + 1) if row else 999

        # Insertar log (re-chequeo dentro de la misma conexión por si hubo carrera)
        if metrica in _idempotent and referencia_id is not None:
            cur.execute(
                "SELECT 1 AS existe FROM HUB_ScoreLog WHERE IdUsuario=%s AND Metrica=%s AND ReferenciaId=%s",
                (id_usuario, metrica, referencia_id),
            )
            if cur.fetchone():
                conn.close()
                return
        cur.execute(
            "INSERT INTO HUB_ScoreLog (IdUsuario, Metrica, Puntos, ReferenciaId) VALUES (%s, %s, %s, %s)",
            (id_usuario, metrica, puntos, referencia_id)
        )

        # Actualizar score
        if row:
            nueva_semanal = old_weekly + puntos
            nueva_total = old_total + puntos
            new_level = _calcular_nivel(nueva_total)
            cur.execute(
                "UPDATE HUB_UserScores SET PuntuacionSemanal = %s, PuntuacionTotal = %s, Nivel = %s, UltimoActivo = GETDATE(), FechaCalculo = GETDATE() WHERE IdUsuario = %s",
                (nueva_semanal, nueva_total, new_level, id_usuario)
            )
        else:
            nueva_semanal = max(puntos, 0)
            nueva_total = max(puntos, 0)
            new_level = _calcular_nivel(nueva_total)
            cur.execute(
                "INSERT INTO HUB_UserScores (IdUsuario, PuntuacionSemanal, PuntuacionTotal, Nivel, UltimoActivo) VALUES (%s, %s, %s, %s, GETDATE())",
                (id_usuario, nueva_semanal, nueva_total, new_level)
            )

        conn.commit()

    # Notificaciones fuera del cursor
    if puntos != 0:
        try:
            from routers.push import notify_level_up, notify_ranking_pass, send_push_notification
            if new_level != old_level:
                notify_level_up(id_usuario, new_level)
            if puntos > 0 and old_pos is not None:
                new_pos = _get_posicion_ranking(id_usuario)
                if new_pos < old_pos:
                    passed_id = _get_usuario_pasado(id_usuario, old_pos, new_pos)
                    if passed_id:
                        notify_ranking_pass(id_usuario, passed_id, new_pos)
        except Exception:
            pass


def registrar_puntos_servicio(id_reporte: int):
    """Calcula y registra puntos por horas de servicio firmado.
    
    Tambien otorga +10 por reporte_firmado si no se ha registrado antes.
    Si TiempoComida=True, aplica -1 punto de penalizacion.

    Formula:
      HorasTotales  = (FechaHoraFin - FechaHoraInicio)
      HorasNetas    = HorasTotales - TiempoTraslado - (TiempoComida ? 1 : 0)
      PuntosPersona = max(floor(HorasNetas / NumIngenieros), 1)
      Penalizacion  = -1 si TiempoComida
    """
    import math
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdReporte, Tecnico, FechaHoraInicio, FechaHoraFin,
                       TiempoTraslado, TiempoComida, Estatus
                FROM ReportesServicio WHERE IdReporte = %s
            """, (id_reporte,))
            reporte = cur.fetchone()
            if not reporte or reporte['Estatus'] != 'Firmado':
                return 0
            if not reporte['FechaHoraInicio'] or not reporte['FechaHoraFin']:
                return 0

            # Otorgar +10 por reporte_firmado si no existe en ScoreLog
            cur.execute("SELECT 1 AS existe FROM HUB_ScoreLog WHERE ReferenciaId = %s AND Metrica = 'reporte_firmado'", (id_reporte,))
            if not cur.fetchone():
                # Buscar IdUsuario del tecnico principal
                cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (reporte['Tecnico'],))
                u = cur.fetchone()
                if u:
                    _registrar_metrica(u['Id'], 'reporte_firmado', referencia_id=id_reporte)

            horas_totales = (reporte['FechaHoraFin'] - reporte['FechaHoraInicio']).total_seconds() / 3600.0
            if horas_totales <= 0:
                return 0

            traslado = float(reporte['TiempoTraslado'] or 0)
            comida = 1.0 if reporte['TiempoComida'] else 0.0
            horas_netas = horas_totales - traslado - comida
            if horas_netas <= 0:
                return 0

            # Participantes
            cur.execute("""
                SELECT u.Nombre FROM ReportesServicioTecnicos t
                JOIN HUB_Users u ON t.IdUsuario = u.Id WHERE t.IdReporte = %s
            """, (id_reporte,))
            adicionales = [r['Nombre'] for r in cur.fetchall()]
            participantes = [reporte['Tecnico']] + adicionales
            num = len(participantes)
            pts = max(math.floor(horas_netas / num), 1)

            count = 0
            for nombre in participantes:
                cur.execute("SELECT Id FROM HUB_Users WHERE Nombre = %s AND Activo = 1", (nombre,))
                u = cur.fetchone()
                if not u:
                    continue
                _registrar_metrica(u['Id'], 'servicio', referencia_id=id_reporte, puntos_override=pts)
                # Penalizacion por comida: -1 punto
                if reporte['TiempoComida']:
                    _registrar_metrica(u['Id'], 'comida_reporte', referencia_id=id_reporte, puntos_override=-1)
                count += 1

            conn.commit()
            print(f"[legends] Reporte {id_reporte}: {pts} pts x {count} ingenieros ({horas_netas:.1f}h netas)")
            return pts * count
    except Exception as e:
        print(f"registrar_puntos_servicio error: {e}")
        return 0


# ── Endpoints ──

@router.get("/score")
def get_my_score(user: dict = Depends(require_user)):
    """Obtiene la puntuacion del usuario actual (semanal + total)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT Id FROM HUB_UserAvatars WHERE IdUsuario = %s", (user["id"],))
        tiene_avatar = bool(cur.fetchone())
        # Nickname ahora vive en HUB_Users (migración 0034); la passkey solo da elegibilidad
        cur.execute("SELECT TOP 1 Nickname FROM HUB_Users WHERE Id = %s", (user["id"],))
        u_row = cur.fetchone()
        nickname = (u_row.get("Nickname") or None) if u_row else None
        cur.execute("SELECT TOP 1 1 AS existe FROM HUB_Passkeys WHERE IdUsuario = %s", (user["id"],))
        tiene_passkey = bool(cur.fetchone())
        cur.execute("SELECT * FROM HUB_UserScores WHERE IdUsuario = %s", (user["id"],))
        row = cur.fetchone()
        if not row:
            return {
                "puntuacion_semanal": 0, "puntuacion_total": 0,
                "racha_dias": 0, "nivel": "Bronce",
                "icono_nivel": "🥉", "ultimo_activo": None, "tiene_avatar": tiene_avatar,
                "nickname": nickname, "tiene_passkey": tiene_passkey
            }
        return {
            "puntuacion_semanal": row["PuntuacionSemanal"],
            "puntuacion_total": row["PuntuacionTotal"],
            "racha_dias": row["RachaDias"],
            "nivel": row["Nivel"],
            "icono_nivel": ICONO_NIVEL.get(row["Nivel"], "🥉"),
            "ultimo_activo": row["UltimoActivo"].isoformat() if row["UltimoActivo"] else None,
            "tiene_avatar": tiene_avatar,
            "nickname": nickname,
            "tiene_passkey": tiene_passkey,
        }


# Descripciones legibles de las metricas
METRICA_DESC = {
    "reporte_firmado":  ("Reporte firmado", "✍️", 10),
    "kilometro":        ("Kilómetros registrados", "⛽", 5),
    "ticket_oxxogas":   ("Ticket OxxoGas", "🎫", 3),
    "vale_generado":    ("Vale generado", "💰", -2),
    "comida_reporte":   ("Hora de comida en reporte", "🍽️", -1),
    "firma_remota":     ("Firma remota de reporte", "📱", 8),
    "servicio":         ("Servicio registrado", "🔧", 0),
    "racha_dia":        ("Racha diaria activa", "🔥", 3),
}


@router.get("/score-log")
def get_score_log(user: dict = Depends(require_user)):
    """Bitácora semanal de puntos del usuario actual.
    Muestra cada evento que sumó o restó puntos esta semana.
    Se resetea cada domingo 3 AM igual que los puntos."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        # Obtener inicio de semana (domingo)
        import datetime as _dt
        today = _dt.date.today()
        sunday = today - _dt.timedelta(days=(today.weekday() + 1) % 7)

        cur.execute("""
            SELECT Id, Metrica, Puntos, ReferenciaId, FechaRegistro
            FROM HUB_ScoreLog
            WHERE IdUsuario = %s AND FechaRegistro >= %s
              AND Metrica != 'sync_completado'
            ORDER BY FechaRegistro DESC
        """, (user["id"], sunday))
        logs = cur.fetchall()

        # Enriquecer con descripciones legibles
        result = []
        for log in logs:
            metrica = log["Metrica"]
            desc, icono, pts_base = METRICA_DESC.get(metrica, (metrica, "📌", 0))
            result.append({
                "id": log["Id"],
                "metrica": metrica,
                "descripcion": desc,
                "icono": icono,
                "puntos": log["Puntos"],
                "fecha": log["FechaRegistro"].isoformat() if log["FechaRegistro"] else None,
                "referencia_id": log["ReferenciaId"],
            })

        # Total de la semana (excluye métricas retiradas)
        cur.execute("""
            SELECT ISNULL(SUM(Puntos), 0) AS total
            FROM HUB_ScoreLog
            WHERE IdUsuario = %s AND FechaRegistro >= %s
              AND Metrica != 'sync_completado'
        """, (user["id"], sunday))
        total_semana = cur.fetchone()["total"]

        return {
            "semana_inicio": sunday.isoformat(),
            "total_semana": total_semana,
            "eventos": result,
            "cron_info": "Los puntos se sincronizan en tiempo real. El ranking se calcula cada domingo a las 3:00 AM y los puntos se resetean.",
        }


@router.get("/ranking")
def get_ranking(user: dict = Depends(require_user)):
    """Ranking semanal de todos los usuarios activos (con o sin avatar/puntos)."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT u.Id AS IdUsuario, u.Nombre,
                   ISNULL(s.PuntuacionSemanal, 0) AS PuntuacionSemanal,
                   ISNULL(s.PuntuacionTotal, 0) AS PuntuacionTotal,
                   ISNULL(s.Nivel, 'Bronce') AS Nivel,
                   ISNULL(s.RachaDias, 0) AS RachaDias,
                   a.AvatarBase64, a.AvatarUrl,
                   u.Nickname AS Nickname
            FROM HUB_Users u
            INNER JOIN HUB_Passkeys p ON u.Id = p.IdUsuario
            LEFT JOIN HUB_UserScores s ON u.Id = s.IdUsuario
            LEFT JOIN HUB_UserAvatars a ON u.Id = a.IdUsuario
            WHERE u.Activo = 1
            GROUP BY u.Id, u.Nombre, u.Nickname, s.PuntuacionSemanal, s.PuntuacionTotal,
                     s.Nivel, s.RachaDias, a.AvatarBase64, a.AvatarUrl
            ORDER BY ISNULL(s.PuntuacionSemanal, 0) DESC
        """)
        rows = cur.fetchall()
        ranking = []
        for i, r in enumerate(rows):
            ranking.append({
                "posicion": i + 1,
                "id_usuario": r["IdUsuario"],
                "nombre": r.get("Nickname") or r["Nombre"],
                "puntuacion_semanal": r["PuntuacionSemanal"],
                "puntuacion_total": r["PuntuacionTotal"],
                "nivel": r["Nivel"],
                "icono_nivel": ICONO_NIVEL.get(r["Nivel"], "🥉"),
                "racha_dias": r["RachaDias"],
                "avatar": r["AvatarBase64"] or r.get("AvatarUrl"),
                "es_yo": r["IdUsuario"] == user["id"],
            })
        return {"ranking": ranking}


@router.get("/winners")
def get_winners_history(user: dict = Depends(require_user)):
    """Historial de ganadores semanales."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT TOP 12 w.PuntuacionSemana, w.FechaInicio, w.FechaFin,
                   w.FechaCalculo, u.Nombre, u.Nickname
            FROM HUB_WeeklyWinners w
            JOIN HUB_Users u ON w.IdUsuario = u.Id
            ORDER BY w.FechaInicio DESC
        """)
        rows = cur.fetchall()
        winners = []
        for r in rows:
            winners.append({
                "nombre": r.get("Nickname") or r["Nombre"],
                "puntuacion": r["PuntuacionSemana"],
                "fecha_inicio": r["FechaInicio"].isoformat() if r["FechaInicio"] else None,
                "fecha_fin": r["FechaFin"].isoformat() if r["FechaFin"] else None,
            })
        return {"winners": winners}


@router.get("/avatar")
def get_my_avatar(user: dict = Depends(require_user)):
    """Obtiene el avatar del usuario actual."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT * FROM HUB_UserAvatars WHERE IdUsuario = %s", (user["id"],))
        row = cur.fetchone()
        if not row:
            return {"generado": False}
        return {
            "generado": True,
            "avatar": row["AvatarBase64"] or row.get("AvatarUrl"),
            "nickname": row.get("Nickname"),
            "prompt": row["PromptUsado"],
            "fecha": row["FechaGenerado"].isoformat() if row["FechaGenerado"] else None,
        }



@router.get("/weekly-winner")
def get_weekly_winner(user: dict = Depends(require_user)):
    """Ganador de la semana mas reciente."""
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT TOP 1 w.*, u.Nombre, u.Nickname
            FROM HUB_WeeklyWinners w
            JOIN HUB_Users u ON w.IdUsuario = u.Id
            ORDER BY w.FechaInicio DESC
        """)
        row = cur.fetchone()
        if not row:
            return {"hay_ganador": False}
        return {
            "hay_ganador": True,
            "nombre": row.get("Nickname") or row["Nombre"],
            "puntuacion": row["PuntuacionSemana"],
            "fecha_inicio": row["FechaInicio"].isoformat() if row["FechaInicio"] else None,
            "fecha_fin": row["FechaFin"].isoformat() if row["FechaFin"] else None,
        }


@router.post("/award")
def award_points(payload: dict, user: dict = Depends(require_user)):
    """Registra una metrica para el usuario."""
    metrica = payload.get("metrica", "")
    referencia_id = payload.get("referencia_id")
    if metrica not in METRICAS:
        return {"error": f"Metrica '{metrica}' no reconocida. Disponibles: {list(METRICAS.keys())}"}
    _registrar_metrica(user["id"], metrica, referencia_id)
    return {"ok": True, "metrica": metrica, "puntos": METRICAS[metrica]}


@router.get("/celebrations")
def get_celebrations(user: dict = Depends(require_user)):
    """Cumpleaños y aniversarios del mes actual, combinados."""
    import datetime as _dt
    now = _dt.datetime.now()
    mes_actual = now.month

    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT u.Nombre,
                CASE
                    WHEN u.FechaNacimiento IS NOT NULL THEN DAY(u.FechaNacimiento)
                    WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                        CAST(SUBSTRING(u.CurpRfc, 9, 2) AS INT)
                    ELSE NULL
                END AS dia,
                CASE
                    WHEN u.FechaNacimiento IS NOT NULL THEN MONTH(u.FechaNacimiento)
                    WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                        CAST(SUBSTRING(u.CurpRfc, 7, 2) AS INT)
                    ELSE NULL
                END AS mes,
                CASE
                    WHEN u.FechaNacimiento IS NOT NULL THEN
                        YEAR(GETDATE()) - YEAR(u.FechaNacimiento)
                    WHEN u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10 THEN
                        CASE
                            WHEN CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT) > 30
                                THEN YEAR(GETDATE()) - (1900 + CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT))
                            ELSE YEAR(GETDATE()) - (2000 + CAST(SUBSTRING(u.CurpRfc, 5, 2) AS INT))
                        END
                    ELSE NULL
                END AS edad
            FROM HUB_Users u
            WHERE u.Activo = 1
                AND (
                    (u.FechaNacimiento IS NOT NULL AND MONTH(u.FechaNacimiento) = %s)
                    OR (u.CurpRfc IS NOT NULL AND LEN(u.CurpRfc) >= 10
                        AND CAST(SUBSTRING(u.CurpRfc, 7, 2) AS INT) = %s)
                )
            ORDER BY dia
        """, (mes_actual, mes_actual))
        cumpleanos = cur.fetchall()

        cur.execute("""
            SELECT u.Nombre,
                DAY(u.FechaIngreso) AS dia,
                MONTH(u.FechaIngreso) AS mes,
                YEAR(GETDATE()) - YEAR(u.FechaIngreso) AS anos
            FROM HUB_Users u
            WHERE u.Activo = 1
                AND u.FechaIngreso IS NOT NULL
                AND MONTH(u.FechaIngreso) = %s
            ORDER BY dia
        """, (mes_actual,))
        aniversarios = cur.fetchall()

    month_names = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
        5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
        9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }
    return {
        "mes": month_names.get(mes_actual, ""),
        "cumpleanos": cumpleanos,
        "aniversarios": aniversarios,
    }


@router.get("/metrics/config")
def get_metrics_config():
    """Configuracion actual de metricas y puntos."""
    return {
        "nombre_puntos": "ECCSA Points",
        "metricas": METRICAS,
        "niveles": {n: p for n, p in NIVELES},
        "iconos": ICONO_NIVEL,
    }


@router.post("/cron/weekly-calculate")
def calculate_weekly_winner():
    """Calcula ganador de la semana anterior, guarda historial, resetea puntos semanales."""
    from datetime import datetime, timedelta
    import pytz

    mexico_tz = pytz.timezone("America/Mexico_City")
    ahora = datetime.now(mexico_tz)
    fin = ahora - timedelta(days=(ahora.weekday() + 1) % 7)
    inicio = fin - timedelta(days=7)

    print(f"[LEGENDS CRON] Calculando ganador: {inicio.date()} al {fin.date()}")

    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        # Buscar usuario con mas puntos semanales
        cur.execute("""
            SELECT TOP 1 IdUsuario, PuntuacionSemanal
            FROM HUB_UserScores
            WHERE PuntuacionSemanal > 0
            ORDER BY PuntuacionSemanal DESC
        """)
        winner = cur.fetchone()

        if not winner:
            print("[LEGENDS CRON] Sin actividad en la semana.")
            return {"ok": False, "detail": "Sin actividad"}

        # Verificar si ya existe registro
        cur.execute(
            "SELECT Id FROM HUB_WeeklyWinners WHERE FechaInicio = %s AND FechaFin = %s",
            (inicio.date(), fin.date())
        )
        if cur.fetchone():
            print("[LEGENDS CRON] Ya existe ganador para esta semana.")
            return {"ok": False, "detail": "Ya calculado"}

        # Guardar ganador
        cur.execute(
            "INSERT INTO HUB_WeeklyWinners (IdUsuario, PuntuacionSemana, FechaInicio, FechaFin) VALUES (%s, %s, %s, %s)",
            (winner["IdUsuario"], winner["PuntuacionSemanal"], inicio.date(), fin.date())
        )

        # Resetear puntos semanales de TODOS
        cur.execute("UPDATE HUB_UserScores SET PuntuacionSemanal = 0")
        conn.commit()

        cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (winner["IdUsuario"],))
        u = cur.fetchone()
        nombre = u["Nombre"] if u else "Desconocido"
        print(f"[LEGENDS CRON] Ganador: {nombre} con {winner['PuntuacionSemanal']} ECCSA Points")

        # Notificar a todos
        try:
            from routers.push import notify_weekly_winner
            notify_weekly_winner(winner["IdUsuario"], winner["PuntuacionSemanal"])
        except Exception:
            pass

        return {
            "ok": True,
            "ganador": nombre,
            "puntos": winner["PuntuacionSemanal"],
            "semana": f"{inicio.date()} al {fin.date()}",
        }
