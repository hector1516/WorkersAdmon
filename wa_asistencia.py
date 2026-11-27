"""
Avisos de asistencia por WhatsApp
==================================

El escáner de red ya sabe quién está en la oficina (`HUB_NetworkScanResults`
cruzado con `HUB_NetworkDevices`). Este worker convierte ese dato en cuatro
avisos al **propio empleado**, nunca a su jefe:

  1. **ENTRADA**: el primer escaneo del día. Puntual, tarde (con memoria del
     día anterior y de la semana) o temprano.
  2. **AUSENCIA**: a los `wa_asist_ausencia_min` minutos de la hora de entrada
     (150 por omisión: 08:00 → 10:30; 09:00 → 11:30) y un segundo aviso a las
     13:00. Máximo dos.
  3. **SALIDA**: cuando lleva `wa_asist_salida_gap_min` minutos sin escaneo
     pasadas las 17:00. Si el último escaneo fue 15 minutos o más antes de la
     hora de salida, el texto lo dice.
  4. **RESUMEN**: viernes 18:30, días presente, llegadas tarde y racha. Sin
     datos no se manda.

## Reglas que no se rompen

- Solo al empleado. Hace falta WhatsApp activo en `HUB_UsuariosWhatsapp` **y**
  un dispositivo activo con su `IdUsuario` en `HUB_NetworkDevices`.
- El horario es por persona: `HUB_AsistenciaUsuarioHorario` +
  `HUB_AsistenciaHorarioDias`. `DiaSemana` 1..7 = lunes..domingo y
  `Descanso = 1` no avisa. Si la persona no tiene horario se usan las horas de
  oficina (L-V 09:00-18:30, sábado 09:30-13:30).
- Ventana de envío 08:00-19:30 (sábado hasta 14:00).
- Tope de `wa_asist_max_dia` avisos por persona y día; el resumen del viernes
  no cuenta.
- Nada de burlas si hay tres o más ausencias seguidas o si el aviso saldría
  después de las 20:00: en esos casos el texto es neutro.
- Las frases salen de listas rotativas: el índice es cuántos avisos de ese tipo
  se han enviado ya, así que la misma frase no cae dos días seguidos.

## Dedupe

`HUB_WaAsistenciaAvisos` (migración `0069_wa_asistencia.sql`) guarda cada
intento con `(Tipo, IdUsuario, Fecha)`. El índice único es la garantía: el
mismo aviso no sale dos veces el mismo día. Los fallos también se registran
(`Ok = 0`) para no reintentar en bucle; el tope diario cuenta solo lo enviado.

    python -m py_compile wa_asistencia.py
    python -m unittest tests.test_wa_asistencia -v
"""
import os
import time
from datetime import date, datetime, time as hora_del_dia, timedelta

import canales
from asistencia_core import limpiar_hora
from tz_mx import now_mx

WORKER = "wa_asistencia"

# Cada cuánto se da un turno. Cinco minutos: el escáner barre cada 3 y la
# precisión de los avisos se mide en minutos, no en horas.
WA_ASIST_CICLO_SEG = max(60, int(os.environ.get("WA_ASIST_CICLO_SEG") or 300))

# Ventana de envío. Sábado cierra temprano porque la jornada termina a 13:30.
VENTANA_INICIO = 8 * 60
VENTANA_FIN = 19 * 60 + 30
VENTANA_FIN_SABADO = 14 * 60

# Hora del segundo aviso de ausencia.
SEGUNDA_AUSENCIA = 13 * 60

# Salida: a partir de esta hora se mide el hueco sin escaneo.
SALIDA_DESDE = 17 * 60

# Salida temprana: el último escaneo cae al menos estos minutos antes de la
# hora de salida esperada.
MARGEN_SALIDA_TEMPRANA = 15

# Llegada temprana: al menos estos minutos antes de la hora esperada.
MARGEN_LLEGADA_TEMPRANA = 20

# Resumen del viernes.
RESUMEN_DIA = 4                 # viernes
RESUMEN_DESDE = 18 * 60 + 30

CLAVES_CONFIG = (
    "wa_asist_activo",
    "wa_asist_tolerancia_min",
    "wa_asist_ausencia_min",
    "wa_asist_salida_gap_min",
    "wa_asist_max_dia",
    "wa_asist_resumen",
)

DEFAULTS = {
    "activo": True,
    "tolerancia_min": 10,
    "ausencia_min": 150,
    "salida_gap_min": 30,
    "max_dia": 3,
    "resumen": True,
}

# ── SQL ──────────────────────────────────────────────────────────────────────
# Todo con `%s` (pymssql/DB-Lib no soporta `?`).

SQL_CONFIG = (
    "SELECT Clave, CAST(Valor AS VARCHAR(200)) FROM HUB_Config "
    "WHERE Clave IN (%s, %s, %s, %s, %s, %s)"
)

# Universo: WhatsApp activo Y dispositivo activo. Una persona sin dispositivo
# no tiene forma de aparecer en un escaneo, así que no se le avisa.
SQL_USUARIOS = (
    "SELECT DISTINCT u.Id, u.Nombre, w.Telefono "
    "FROM HUB_Users u "
    "JOIN HUB_UsuariosWhatsapp w "
    "  ON w.IdUsuario = u.Id AND w.Activo = 1 "
    "JOIN HUB_NetworkDevices d "
    "  ON d.IdUsuario = u.Id AND d.Activo = 1 "
    "WHERE LTRIM(RTRIM(ISNULL(w.Telefono, ''))) <> '' "
    "ORDER BY u.Id"
)

SQL_HORARIO = (
    "SELECT TOP 1 hd.HoraEntrada, hd.HoraSalida, hd.Descanso "
    "FROM HUB_AsistenciaUsuarioHorario uh "
    "JOIN HUB_AsistenciaHorarioDias hd ON hd.IdHorario = uh.IdHorario "
    "WHERE uh.IdUsuario = %s AND hd.DiaSemana = %s "
    "ORDER BY uh.VigenteDesde DESC"
)

# La MAC se compara normalizada (mayúsculas y sin espacios) porque el escáner
# la escribe tal como la ve y el inventario a mano.
_MAC_JOIN = (
    "JOIN HUB_NetworkDevices d "
    "  ON UPPER(REPLACE(LTRIM(RTRIM(s.MACAddress)), ' ', '')) = "
    "     UPPER(REPLACE(LTRIM(RTRIM(d.MACAddress)), ' ', '')) "
    " AND d.Activo = 1 "
)

SQL_ESCANEOS = (
    "SELECT s.FechaScan FROM HUB_NetworkScanResults s "
    + _MAC_JOIN +
    "WHERE d.IdUsuario = %s AND s.FechaScan >= %s AND s.FechaScan < %s "
    "ORDER BY s.FechaScan"
)

SQL_SEMANA = (
    "SELECT CAST(s.FechaScan AS date) AS Dia, MIN(s.FechaScan) "
    "FROM HUB_NetworkScanResults s "
    + _MAC_JOIN +
    "WHERE d.IdUsuario = %s AND s.FechaScan >= %s AND s.FechaScan < %s "
    "GROUP BY CAST(s.FechaScan AS date) "
    "ORDER BY Dia"
)

SQL_YA_HOY = (
    "SELECT COUNT(*) FROM HUB_WaAsistenciaAvisos "
    "WHERE Tipo = %s AND IdUsuario = %s AND Fecha = %s"
)

SQL_CONTEO_DIA = (
    "SELECT COUNT(*) FROM HUB_WaAsistenciaAvisos "
    "WHERE IdUsuario = %s AND Fecha = %s AND Ok = 1 AND Tipo <> 'RESUMEN'"
)

SQL_HIST_TIPO = (
    "SELECT COUNT(*) FROM HUB_WaAsistenciaAvisos "
    "WHERE Tipo = %s AND IdUsuario = %s AND Ok = 1"
)

SQL_ABS_DIAS = (
    "SELECT DISTINCT Fecha FROM HUB_WaAsistenciaAvisos "
    "WHERE IdUsuario = %s AND Tipo IN ('AUSENCIA', 'AUSENCIA2') "
    "  AND Fecha < %s AND Ok = 1 "
    "ORDER BY Fecha DESC"
)

SQL_INSERT = (
    "INSERT INTO HUB_WaAsistenciaAvisos "
    "(Tipo, IdUsuario, Fecha, Enviado, Ok, Detalle) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)

# ── Textos ───────────────────────────────────────────────────────────────────
# Sin emojis. Humor ligero y contextual; las listas rotan por persona.

FRASES_PUNTUAL = (
    "{nombre}, entraste {hora}. Puntual.",
    "{nombre}, marcaste {hora}. Ahí andas, puntual.",
    "{nombre}, {hora}. Ni un minuto de más.",
)

FRASES_TARDE = (
    "{nombre}, {hora}. El café no se toma solo.",
    "{nombre}, {hora}. Llegaste, que ya es algo.",
    "{nombre}, {hora}. ¿Otra vez el tráfico?",
)

FRASES_TEMPRANA = (
    "{hora}, madrugaste. ¿Te debía algo el café?",
    "{nombre}, {hora}. El primero del día, otra vez.",
    "{nombre}, {hora}. ¿Insomnio o entusiasmo?",
)

FRASES_AUSENCIA = (
    "{nombre}, no te he visto hoy. ¿Vienes o te quedaste dormido?",
    "{nombre}, hoy no has pasado por la red. ¿Todo bien?",
    "{nombre}, sigo sin verte. ¿Nos avisas?",
)

FRASES_AUSENCIA2 = (
    "{nombre}, sin registro hoy. Segundo aviso.",
    "{nombre}, segundo aviso: no hay escaneo tuyo el día de hoy.",
)

FRASES_SALIDA = (
    "Saliste {hora}. Buen día.",
    "{hora} y para casa. Buen día.",
    "{nombre}, saliste {hora}. Nos vemos mañana.",
)

FRASES_SALIDA_TEMPRANA = (
    "Te fuiste {hora}; hoy sí te apuraste.",
    "Saliste {hora}, antes de la hora. ¿Algo urgente?",
    "{hora} y ya no estabas. ¿Se te ofreció algo?",
)

NEUTRAL_ENTRADA = "{nombre}, tu entrada quedó registrada a las {hora}."
NEUTRAL_SALIDA = "{nombre}, salida registrada a las {hora}."
NEUTRAL_SALIDA_TEMPRANA = (
    "{nombre}, salida registrada a las {hora}, antes de la hora de salida."
)
NEUTRAL_AUSENCIA = "{nombre}, no hay registro de tu entrada el día de hoy."
NEUTRAL_AUSENCIA2 = "{nombre}, segundo aviso: sin escaneo registrado hoy."


# ── Utilidades ───────────────────────────────────────────────────────────────

def _log(mensaje):
    print(f"[{WORKER} {time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}", flush=True)


def _filas(conn, sql, params):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _una(conn, sql, params):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _ent(valor, defecto, minimo=0):
    try:
        return max(minimo, int(str(valor).strip()))
    except (TypeError, ValueError):
        return defecto


def _flag(valor, defecto):
    if valor is None:
        return defecto
    return str(valor).strip().lower() not in ("0", "", "false", "no", "off")


def _hm(momento):
    """«9:02», sin cero a la izquierda en la hora."""
    return f"{momento.hour}:{momento.minute:02d}"


def _texto(valor, limite=200):
    return " ".join(str(valor or "").split())[:limite]


def _chat(telefono):
    """chatId de WhatsApp para el número registrado (o None si no sirve)."""
    try:
        from openwa_client import normalizar_chat_id
        return normalizar_chat_id(telefono)
    except Exception:
        return None


def _frase(lista, indice):
    return lista[indice % len(lista)]


# ── Ventana y horario ────────────────────────────────────────────────────────

def en_ventana(ahora):
    """08:00-19:30; sábado hasta 14:00."""
    minutos = ahora.hour * 60 + ahora.minute
    fin = VENTANA_FIN_SABADO if ahora.weekday() == 5 else VENTANA_FIN
    return VENTANA_INICIO <= minutos <= fin


def _horario_default(dia_semana):
    """Oficina cuando la persona no tiene horario propio."""
    if 1 <= dia_semana <= 5:
        return hora_del_dia(9, 0), hora_del_dia(18, 30)
    if dia_semana == 6:
        return hora_del_dia(9, 30), hora_del_dia(13, 30)
    return None


def horario_dia(conn, usuario_id, dia_semana):
    """
    `(entrada, salida)` del día, o None si es descanso.

    Si la persona no tiene horario se devuelven las horas de oficina. Una fila
    con `Descanso = 1` (o sin horas) es descanso y no se avisa.
    """
    try:
        fila = _una(conn, SQL_HORARIO, (usuario_id, dia_semana))
    except Exception as exc:
        _log(f"sin horario de {usuario_id} día {dia_semana}: {exc}")
        fila = None

    if not fila:
        return _horario_default(dia_semana)

    entrada, salida, descanso = limpiar_hora(fila[0]), limpiar_hora(fila[1]), bool(fila[2])
    if descanso or entrada is None or salida is None:
        return None
    return entrada, salida


# ── Consultas de apoyo ───────────────────────────────────────────────────────

def _escaneos_dia(conn, usuario_id, dia):
    desde = datetime.combine(dia, hora_del_dia.min)
    hasta = desde + timedelta(days=1)
    return [fila[0] for fila in _filas(conn, SQL_ESCANEOS, (usuario_id, desde, hasta))
            if fila and fila[0]]


def _primera_por_dia(conn, usuario_id, desde, hasta):
    """{date: primer escaneo} para el rango [desde, hasta)."""
    salida = {}
    for fila in _filas(conn, SQL_SEMANA, (usuario_id, desde, hasta)):
        try:
            dia, primera = fila[0], fila[1]
        except (TypeError, IndexError):
            continue
        if isinstance(dia, datetime):
            dia = dia.date()
        if isinstance(dia, date) and primera:
            salida[dia] = primera
    return salida


def _ya_hoy(conn, tipo, usuario_id, dia):
    fila = _una(conn, SQL_YA_HOY, (tipo, usuario_id, dia))
    try:
        return bool(fila and int(fila[0] or 0))
    except (TypeError, ValueError):
        return False


def _contar_dia(conn, usuario_id, dia):
    fila = _una(conn, SQL_CONTEO_DIA, (usuario_id, dia))
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _hist_tipo(conn, usuario_id, tipo):
    """Cuántos avisos de este tipo se han enviado (índice de la lista)."""
    fila = _una(conn, SQL_HIST_TIPO, (tipo, usuario_id))
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _fue_tarde(conn, usuario_id, dia, tolerancia_min):
    """¿La primera llegada de ese día pasó la tolerancia?"""
    horario = horario_dia(conn, usuario_id, dia.weekday() + 1)
    if not horario:
        return False
    escaneos = _escaneos_dia(conn, usuario_id, dia)
    if not escaneos:
        return False
    esperada = datetime.combine(dia, horario[0])
    return escaneos[0] > esperada + timedelta(minutes=tolerancia_min)


def _ausencias_seguidas(conn, usuario_id, hoy):
    """
    ¿Hay tres o más días de trabajo ausente justo antes de hoy?

    Se camina hacia atrás desde ayer; los días de descanso no cortan la racha
    ni cuentan. Diez días es suficiente para encontrar tres laborables.
    """
    try:
        filas = _filas(conn, SQL_ABS_DIAS, (usuario_id, hoy))
    except Exception as exc:
        _log(f"sin historial de ausencias de {usuario_id}: {exc}")
        return False

    ausentes = set()
    for fila in filas:
        valor = fila[0] if fila else None
        if isinstance(valor, datetime):
            valor = valor.date()
        if isinstance(valor, date):
            ausentes.add(valor)

    consecutivas = 0
    dia = hoy - timedelta(days=1)
    for _ in range(10):
        if consecutivas >= 3:
            return True
        if dia in ausentes:
            consecutivas += 1
        elif horario_dia(conn, usuario_id, dia.weekday() + 1) is not None:
            break               # día laborable sin ausencia: se corta la racha
        dia -= timedelta(days=1)
    return consecutivas >= 3


def _permitir_humor(conn, usuario_id, ahora):
    if ahora.hour >= 20:
        return False
    return not _ausencias_seguidas(conn, usuario_id, ahora.date())


# ── Textos de cada aviso ─────────────────────────────────────────────────────

def texto_entrada(conn, usuario_id, nombre, llegada, esperada, hoy, cfg, ahora):
    hora = _hm(llegada)
    esperada_dt = datetime.combine(hoy, esperada)
    minutos = (llegada - esperada_dt).total_seconds() / 60.0
    humor = _permitir_humor(conn, usuario_id, ahora)
    historial = _hist_tipo(conn, usuario_id, "ENTRADA")

    if minutos > cfg["tolerancia_min"]:
        if not humor:
            return NEUTRAL_ENTRADA.format(nombre=nombre, hora=hora)
        # El contexto (ayer o la tercera de la semana) alterna con la lista:
        # decirlo todos los días también sería repetirse.
        if historial % 2 == 0 and _fue_tarde(
                conn, usuario_id, hoy - timedelta(days=1),
                cfg["tolerancia_min"]):
            return f"{nombre}, {hora}. Otra vez tarde como ayer."
        if historial % 2 == 0:
            primera = _primera_por_dia(
                conn, usuario_id,
                datetime.combine(hoy - timedelta(days=hoy.weekday()),
                                 hora_del_dia.min),
                datetime.combine(hoy + timedelta(days=1), hora_del_dia.min))
            tardes = 0
            for dia, llegada_dia in primera.items():
                horario_dia_d = horario_dia(conn, usuario_id, dia.weekday() + 1)
                if (dia.weekday() < 5 and horario_dia_d
                        and llegada_dia > datetime.combine(dia, horario_dia_d[0])
                        + timedelta(minutes=cfg["tolerancia_min"])):
                    tardes += 1
            if tardes >= 3:
                return f"{nombre}, {hora}. Tercera tarde de la semana."
        return _frase(FRASES_TARDE, historial).format(nombre=nombre, hora=hora)

    if minutos <= -MARGEN_LLEGADA_TEMPRANA:
        if not humor:
            return NEUTRAL_ENTRADA.format(nombre=nombre, hora=hora)
        return _frase(FRASES_TEMPRANA, historial).format(nombre=nombre, hora=hora)

    if not humor:
        return NEUTRAL_ENTRADA.format(nombre=nombre, hora=hora)
    return _frase(FRASES_PUNTUAL, historial).format(nombre=nombre, hora=hora)


def texto_salida(conn, usuario_id, nombre, ultimo, salida_esperada, hoy, ahora):
    hora = _hm(ultimo)
    temprana = ultimo <= datetime.combine(hoy, salida_esperada) - timedelta(
        minutes=MARGEN_SALIDA_TEMPRANA)
    humor = _permitir_humor(conn, usuario_id, ahora)
    historial = _hist_tipo(conn, usuario_id, "SALIDA")
    if temprana:
        if not humor:
            return NEUTRAL_SALIDA_TEMPRANA.format(nombre=nombre, hora=hora)
        return _frase(FRASES_SALIDA_TEMPRANA, historial).format(nombre=nombre, hora=hora)
    if not humor:
        return NEUTRAL_SALIDA.format(nombre=nombre, hora=hora)
    return _frase(FRASES_SALIDA, historial).format(nombre=nombre, hora=hora)


def texto_ausencia(conn, usuario_id, nombre, hoy, ahora, segundo=False):
    humor = _permitir_humor(conn, usuario_id, ahora)
    historial = _hist_tipo(conn, usuario_id, "AUSENCIA2" if segundo else "AUSENCIA")
    if not humor:
        plantilla = NEUTRAL_AUSENCIA2 if segundo else NEUTRAL_AUSENCIA
        return plantilla.format(nombre=nombre)
    lista = FRASES_AUSENCIA2 if segundo else FRASES_AUSENCIA
    return _frase(lista, historial).format(nombre=nombre)


def texto_resumen(nombre, dias, tardes, racha):
    plural = "días" if dias != 1 else "día"
    texto = f"{nombre}, tu semana: {dias} {plural} presente"
    if tardes:
        texto += f", {tardes} llegada{'s' if tardes != 1 else ''} tarde"
    texto += f". Racha de {racha} día{'s' if racha != 1 else ''}."
    return texto


# ── Envío y registro ─────────────────────────────────────────────────────────

def _enviar_seguro(enviar, telefono, texto):
    """Nunca lanza: `(ok, detalle)` aunque OpenWA explote."""
    chat = _chat(telefono)
    if not chat:
        return False, "telefono sin formato valido"
    try:
        ok, detalle = enviar(chat, texto)
        return bool(ok), str(detalle or "")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _registrar(conn, tipo, usuario_id, ahora, ok, detalle):
    try:
        cur = conn.cursor()
        try:
            cur.execute(SQL_INSERT, (tipo, usuario_id, ahora.date(), ahora,
                                     1 if ok else 0, _texto(detalle)))
            conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass
        return True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _log(f"no se pudo registrar {tipo} de {usuario_id}: {exc}")
        return False


# ── Configuración ────────────────────────────────────────────────────────────

def leer_config(conn):
    valores = {}
    try:
        for fila in _filas(conn, SQL_CONFIG, CLAVES_CONFIG):
            valores[str(fila[0])] = str(fila[1] if fila[1] is not None else "").strip()
    except Exception as exc:
        _log(f"no se pudo leer HUB_Config: {exc}")

    return {
        "activo": _flag(valores.get("wa_asist_activo"), DEFAULTS["activo"]),
        "tolerancia_min": _ent(valores.get("wa_asist_tolerancia_min"),
                               DEFAULTS["tolerancia_min"], minimo=0),
        "ausencia_min": _ent(valores.get("wa_asist_ausencia_min"),
                             DEFAULTS["ausencia_min"], minimo=1),
        "salida_gap_min": _ent(valores.get("wa_asist_salida_gap_min"),
                               DEFAULTS["salida_gap_min"], minimo=1),
        "max_dia": _ent(valores.get("wa_asist_max_dia"),
                        DEFAULTS["max_dia"], minimo=1),
        "resumen": _flag(valores.get("wa_asist_resumen"), DEFAULTS["resumen"]),
    }


# ── Resumen del viernes ──────────────────────────────────────────────────────

def _es_resumen(ahora):
    if ahora.weekday() != RESUMEN_DIA:
        return False
    return ahora.hour * 60 + ahora.minute >= RESUMEN_DESDE


def _resumen_semana(conn, usuario_id, nombre, ahora, cfg):
    """
    Texto del resumen o None si no hay datos. Los días presente salen de los
    escaneos, no de los avisos: si el worker estuvo caído no se inventa nada.
    """
    hoy = ahora.date()
    lunes = hoy - timedelta(days=hoy.weekday())
    primera = _primera_por_dia(
        conn, usuario_id, datetime.combine(lunes, hora_del_dia.min),
        datetime.combine(hoy + timedelta(days=1), hora_del_dia.min))
    if not primera:
        return None

    # Solo días con horario (los de descanso no cuentan aunque haya un escaneo
    # suelto, que sería el equipo de otra persona o un error del inventario).
    presente = {}
    for dia, llegada in primera.items():
        horario = horario_dia(conn, usuario_id, dia.weekday() + 1)
        if horario:
            presente[dia] = (llegada, horario)

    if not presente:
        return None

    tardes = 0
    for dia, (llegada, horario) in presente.items():
        if llegada > datetime.combine(dia, horario[0]) + timedelta(
                minutes=cfg["tolerancia_min"]):
            tardes += 1

    # Racha: días laborables seguidos con presencia, desde el último día
    # presente hacia atrás.
    racha = 0
    dia = max(presente)
    while dia >= lunes:
        horario = horario_dia(conn, usuario_id, dia.weekday() + 1)
        if horario is not None:
            if dia not in presente:
                break
            racha += 1
        dia -= timedelta(days=1)

    return texto_resumen(nombre, len(presente), tardes, racha)


# ── Un usuario ───────────────────────────────────────────────────────────────

def atender_usuario(conn, fila, cfg, ahora, enviar):
    """Los avisos de UNA persona. Devuelve cuántos se enviaron."""
    try:
        usuario_id, nombre, telefono = int(fila[0]), str(fila[1] or "").strip(), fila[2]
    except (TypeError, ValueError, IndexError):
        return 0

    hoy = ahora.date()
    horario = horario_dia(conn, usuario_id, hoy.weekday() + 1)
    if not horario:
        return 0                    # descanso o día sin turno: no se avisa

    esperada, salida_esperada = horario
    escaneos = _escaneos_dia(conn, usuario_id, hoy)
    usados = _contar_dia(conn, usuario_id, hoy)
    enviados = 0

    def intentar(tipo, texto, detalle, cuenta=True):
        nonlocal usados, enviados
        if cuenta and usados >= cfg["max_dia"]:
            return 0
        if _ya_hoy(conn, tipo, usuario_id, hoy):
            return 0
        ok, detalle_envio = _enviar_seguro(enviar, telefono, texto)
        _registrar(conn, tipo, usuario_id, ahora, ok,
                   f"{detalle}: {detalle_envio}".strip(": "))
        if not ok:
            _log(f"no se pudo enviar {tipo} a {usuario_id}: {detalle_envio}")
            return 0
        enviados += 1
        if cuenta:
            usados += 1
        return 1

    # 1) Entrada. Se arma el texto solo si hace falta: mirar el historial y los
    # escaneos de ayer para un aviso que ya se mandó es trabajo tirado.
    if escaneos and not _ya_hoy(conn, "ENTRADA", usuario_id, hoy):
        texto = texto_entrada(conn, usuario_id, nombre, escaneos[0],
                              esperada, hoy, cfg, ahora)
        intentar("ENTRADA", texto, f"entrada {_hm(escaneos[0])}")

    # 2) Ausencia: primer aviso a los `ausencia_min` y segundo a las 13:00.
    if not escaneos:
        limite = datetime.combine(hoy, esperada) + timedelta(
            minutes=cfg["ausencia_min"])
        if ahora >= limite and not _ya_hoy(conn, "AUSENCIA", usuario_id, hoy):
            texto = texto_ausencia(conn, usuario_id, nombre, hoy, ahora)
            intentar("AUSENCIA", texto, "sin escaneo")
        elif (ahora.hour * 60 + ahora.minute >= SEGUNDA_AUSENCIA
              and _ya_hoy(conn, "AUSENCIA", usuario_id, hoy)):
            texto = texto_ausencia(conn, usuario_id, nombre, hoy, ahora,
                                   segundo=True)
            intentar("AUSENCIA2", texto, "sin escaneo (segundo)")

    # 3) Salida: 30 minutos sin escaneo pasadas las 17:00.
    if escaneos and ahora.hour * 60 + ahora.minute >= SALIDA_DESDE:
        ultimo = escaneos[-1]
        if (ahora - ultimo) >= timedelta(minutes=cfg["salida_gap_min"]):
            texto = texto_salida(conn, usuario_id, nombre, ultimo,
                                 salida_esperada, hoy, ahora)
            intentar("SALIDA", texto, f"salida {_hm(ultimo)}")

    # 4) Resumen del viernes. No cuenta para el tope diario.
    if cfg["resumen"] and _es_resumen(ahora):
        texto = _resumen_semana(conn, usuario_id, nombre, ahora, cfg)
        if texto:
            intentar("RESUMEN", texto,
                     f"resumen viernes {hoy.isoformat()}", cuenta=False)

    return enviados


# ── Ciclo ────────────────────────────────────────────────────────────────────

def ciclo(ahora=None, conn=None, enviar=None, cfg=None):
    """
    Un turno completo. Devuelve `(detalle, enviados)` para el log y el
    heartbeat. Un fallo de una persona no tumba el ciclo.
    """
    ahora = ahora or now_mx()
    if not en_ventana(ahora):
        return "fuera de ventana", 0

    propio = conn is None
    if propio:
        import eccsa_db
        conn = eccsa_db.get_connection()
    if enviar is None:
        import openwa_client
        enviar = openwa_client.enviar_texto

    enviados = 0
    try:
        cfg = cfg or leer_config(conn)
        if not cfg["activo"]:
            return "asistencia apagada", 0
        if not canales.activo("whatsapp"):
            return "whatsapp apagado", 0

        partes = []
        for fila in _filas(conn, SQL_USUARIOS, ()):
            try:
                enviados += atender_usuario(conn, fila, cfg, ahora, enviar)
            except Exception as exc:
                _log(f"error con usuario {fila[0] if fila else '?'}: {exc}")
                continue

        if not enviados:
            return "nada que mandar", 0
        return f"asistencia: {enviados} aviso(s)", enviados
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

    _log(f"avisos de asistencia iniciados (ciclo={WA_ASIST_CICLO_SEG}s)")
    while True:
        detalle, enviados = "error", 0
        try:
            detalle, enviados = ciclo()
            if enviados:
                _log(detalle)
        except Exception as exc:
            detalle = f"ERROR: {exc}"[:300]
            _log(f"error en el ciclo (se sigue): {exc}")
            import traceback
            traceback.print_exc()
        # El latido va SIEMPRE: un worker que no reporta se ve igual que uno
        # trabado.
        try:
            heartbeat(WORKER, detail=detalle, count=enviados)
        except Exception:
            pass
        time.sleep(WA_ASIST_CICLO_SEG)


if __name__ == "__main__":
    main()
