"""
Recordatorios proactivos del bot de WhatsApp (fase C)
======================================================

El bot ya contesta lo que le preguntan (`wa_bot`, `wa_consultas`). Esto es el
otro sentido: lo que el bot dice **sin que se lo pregunten**, porque falta un
dato que solo el usuario tiene.

Tres avisos, todos con la misma regla: no se manda nada fuera del horario de
oficina (lunes a viernes 09:00-18:30, hora de México), nunca se le escribe a
quien no tiene WhatsApp registrado y nunca se repite el mismo aviso más de lo
que dice `HUB_Config`.

  1. **KM**: los kilómetros son **semanales**. Si el auto asignado no tiene
     lectura desde el lunes a las 00:00, se le recuerda a su conductor **una vez
     al día** hasta que la registre.
  2. **VALE**: un vale GENERADO lleva más de `wa_rec_vale_horas` horas sin el
     comprobante (`HUB_OxxoGasTickets`). Se avisa a quien lo solicitó, en un
     solo mensaje con todos sus vales pendientes, **una vez al día** y como
     máximo `wa_rec_vale_max` veces por persona. Un vale con más de
     `govale_recordatorio_dias_max` días (default 15) ya no se recuerda: es la
     misma regla de `cron_vales_recordatorios`, y son tickets que no se van a
     subir, así que insistir solo molesta.
  3. **RESUMEN**: el lunes entre 09:00 y 09:30, un resumen corto con lo que el
     usuario tiene pendiente. Sin datos no hay mensaje.

## Dedupe

`HUB_WaRecordatorios` (migración `0067_wa_recordatorios.sql`) guarda cada
intento con su `(Tipo, Clave, IdUsuario, FechaDia)`. La clave del índice único
es justamente eso: no se puede mandar dos veces el mismo aviso el mismo día.
Los **fallos también se registran** (`Ok = 0`): si OpenWA está caído, el ciclo
siguiente no vuelve a intentarlo en bucle; el aviso se reintenta cuando la
ventana de repetición lo permita (o al día siguiente, con la clave de ese día).

El tope diario (`wa_rec_max_dia`, 3 por omisión) se cuenta sobre lo **enviado**
(`Ok = 1`) del día, para que un fallo de OpenWA no le gaste el presupuesto al
usuario.

## Contrato

    ciclo(ahora=None, conn=None, enviar=None, cfg=None) -> (detalle, enviados)

- `ahora`: datetime naive en hora de México. En producción se omite y se usa
  `tz_mx.now_mx()`; las pruebas lo pasan para fijar la hora.
- `conn`: conexión de BD. Si es None se abre con `eccsa_db.get_connection()` y
  se cierra al terminar.
- `enviar`: `callable(chat_id, texto) -> (ok, detalle)`. Por omisión,
  `openwa_client.enviar_texto`. **Nunca lanza**: un fallo de OpenWA se registra
  con `Ok = 0` y el ciclo sigue.
- `cfg`: diccionario ya leído de `HUB_Config`. Si es None se lee.

Este módulo no importa SQL Server al cargarse de forma dura: `eccsa_db` se
importa dentro de `ciclo`, así que las pruebas no necesitan la base.

    python -m py_compile wa_recordatorios.py
    python -m unittest tests.test_wa_recordatorios -v
"""
import os
import time
from datetime import date, datetime, timedelta

from tz_mx import now_mx

WORKER = "wa_recordatorios"

# Cada cuánto se da un turno. Quince minutos: la ventana es de horas, así que no
# hace falta más frecuencia, y un ciclo corto solo multiplicaría las consultas.
WA_REC_CICLO_SEG = max(60, int(os.environ.get("WA_REC_CICLO_SEG") or 900))

# Ventana de envío: lunes a viernes, 09:00 a 18:30 (hora de México).
HORA_INICIO = 9
MINUTO_INICIO = 0
HORA_FIN = 18
MINUTO_FIN = 30

# El resumen del lunes solo sale en su media hora.
RESUMEN_DESDE = 9 * 60
RESUMEN_HASTA = 9 * 60 + 30

# Un auto no se recuerda más de una vez cada 3 días.
DEDUPE_KM_DIAS = 3

MESES = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")

CLAVES_CONFIG = (
    "wa_rec_activo",
    "wa_rec_km_dias",
    "wa_rec_vale_horas",
    "wa_rec_vale_max",
    "wa_rec_resumen",
    "wa_rec_max_dia",
    "govale_recordatorio_dias_max",
)

DEFAULTS = {
    "activo": True,
    "km_dias": 3,
    "vale_horas": 48,
    "vale_max": 3,
    "resumen": True,
    "max_dia": 3,
    "vale_dias_max": 15,
}

# ── SQL ──────────────────────────────────────────────────────────────────────
# Todo con `%s` (pymssql/DB-Lib no soporta `?`).

SQL_CONFIG = (
    "SELECT Clave, CAST(Valor AS VARCHAR(200)) FROM HUB_Config "
    "WHERE Clave IN (%s, %s, %s, %s, %s, %s, %s)"
)

SQL_CONTEO_DIA = (
    "SELECT IdUsuario, COUNT(*) FROM HUB_WaRecordatorios "
    "WHERE FechaDia = %s AND Ok = 1 AND Tipo <> 'VOLT' GROUP BY IdUsuario"
)

SQL_YA_HOY = (
    "SELECT COUNT(*) FROM HUB_WaRecordatorios "
    "WHERE Tipo = %s AND Clave = %s AND IdUsuario = %s AND FechaDia = %s"
)

SQL_KM_DEDUPE = (
    "SELECT COUNT(*) FROM HUB_WaRecordatorios "
    "WHERE Tipo = 'KM' AND Clave = %s AND IdUsuario = %s AND FechaDia >= %s"
)

SQL_VALE_HIST = (
    "SELECT COUNT(*) FROM HUB_WaRecordatorios "
    "WHERE Tipo = 'VALE' AND Clave = %s AND IdUsuario = %s AND Ok = 1"
)

SQL_INSERT = (
    "INSERT INTO HUB_WaRecordatorios "
    "(Tipo, Clave, IdUsuario, FechaDia, Enviado, Ok, Detalle) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

# Autos con conductor y WhatsApp. El `IS NULL` y el `<> ''` son redundantes a
# propósito: si el conductor está asignado pero sin número, no hay a quién
# avisar y el auto no entra al universo.
SQL_UNIVERSO_KM = (
    "SELECT a.Id, a.MarcaModelo, a.Placas, a.IdUsuarioAsignado, "
    "       u.Nombre, w.Telefono "
    "FROM HUB_Automoviles a "
    "JOIN HUB_Users u ON u.Id = a.IdUsuarioAsignado "
    "JOIN HUB_UsuariosWhatsapp w "
    "  ON w.IdUsuario = a.IdUsuarioAsignado AND w.Activo = 1 "
    "WHERE a.IdUsuarioAsignado IS NOT NULL "
    "  AND LTRIM(RTRIM(ISNULL(w.Telefono, ''))) <> '' "
    "ORDER BY a.Id"
)

# Última lectura del auto (haya la fecha que haya) y cuántas lecturas caen
# dentro de la ventana. Si no hay ninguna fila, el auto nunca se ha leído.
SQL_KM_AUTO = (
    "SELECT TOP 1 k.Kilometros, k.FechaHora, "
    "       (SELECT COUNT(*) FROM HUB_RegistroKilometros k2 "
    "         WHERE k2.IdAutomovil = k.IdAutomovil AND k2.FechaHora >= %s) "
    "         AS Recientes "
    "FROM HUB_RegistroKilometros k "
    "WHERE k.IdAutomovil = %s "
    "ORDER BY k.FechaHora DESC, k.Id DESC"
)

SQL_VALES = (
    "SELECT v.Id, v.IdSolicitante, v.MontoUnit, v.FechaSolicitud, "
    "       u.Nombre, w.Telefono "
    "FROM HUB_SolicitudVales v "
    "JOIN HUB_Users u ON u.Id = v.IdSolicitante "
    "JOIN HUB_UsuariosWhatsapp w "
    "  ON w.IdUsuario = v.IdSolicitante AND w.Activo = 1 "
    "WHERE v.Estatus = 'GENERADO' "
    "  AND v.FechaSolicitud <= %s "
    "  AND v.FechaSolicitud >= %s "
    "  AND NOT EXISTS (SELECT 1 FROM HUB_OxxoGasTickets t "
    "                  WHERE t.IdSolicitudVale = v.Id) "
    "  AND LTRIM(RTRIM(ISNULL(w.Telefono, ''))) <> '' "
    "ORDER BY v.FechaSolicitud, v.Id"
)

SQL_USUARIOS_WA = (
    "SELECT w.IdUsuario, u.Nombre, w.Telefono "
    "FROM HUB_UsuariosWhatsapp w "
    "JOIN HUB_Users u ON u.Id = w.IdUsuario "
    "WHERE w.Activo = 1 "
    "  AND LTRIM(RTRIM(ISNULL(w.Telefono, ''))) <> '' "
    "ORDER BY w.IdUsuario"
)

SQL_KM_MES = (
    "SELECT COUNT(*) FROM HUB_RegistroKilometros "
    "WHERE IdUsuario = %s AND FechaHora >= %s"
)

SQL_RESUMEN_VALES = (
    "SELECT COUNT(*) FROM HUB_SolicitudVales v "
    "WHERE v.IdSolicitante = %s AND v.Estatus = 'GENERADO' "
    "  AND NOT EXISTS (SELECT 1 FROM HUB_OxxoGasTickets t "
    "                  WHERE t.IdSolicitudVale = v.Id)"
)

SQL_RESUMEN_REPORTES = (
    "SELECT COUNT(*) FROM ReportesServicio r "
    "JOIN ReportesServicioTecnicos t ON t.IdReporte = r.IdReporte "
    "WHERE t.IdUsuario = %s AND ISNULL(r.Eliminado, 0) = 0 "
    "  AND r.Fecha >= %s AND r.Fecha < %s"
)


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


def _ent(valor, defecto, minimo=1):
    try:
        return max(minimo, int(str(valor).strip()))
    except (TypeError, ValueError):
        return defecto


def _flag(valor, defecto):
    if valor is None:
        return defecto
    return str(valor).strip().lower() not in ("0", "", "false", "no", "off")


def _fecha(valor):
    """Fecha corta en español: «26 nov 2026»."""
    momento = valor
    if isinstance(valor, date) and not isinstance(valor, datetime):
        momento = datetime(valor.year, valor.month, valor.day)
    if not isinstance(momento, datetime):
        texto = str(valor or "").strip()
        momento = None
        for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                momento = datetime.strptime(texto[:19], formato)
                break
            except ValueError:
                continue
    if momento is None:
        return "sin fecha"
    return f"{momento.day} {MESES[momento.month - 1]} {momento.year}"


def _dinero(valor):
    try:
        return f"${float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _chat(telefono):
    """chatId de WhatsApp para el número registrado (o None si no sirve)."""
    try:
        from openwa_client import normalizar_chat_id
        return normalizar_chat_id(telefono)
    except Exception:
        return None


def _texto(valor, limite=200):
    txt = " ".join(str(valor or "").split())
    return txt[:limite]


# ── Ventana de envío ─────────────────────────────────────────────────────────

def en_ventana(ahora):
    """Lunes a viernes, 09:00-18:30. Fuera de ahí no se manda nada."""
    if ahora.weekday() > 4:
        return False
    minutos = ahora.hour * 60 + ahora.minute
    return HORA_INICIO * 60 + MINUTO_INICIO <= minutos <= HORA_FIN * 60 + MINUTO_FIN


def es_resumen(ahora):
    """Lunes entre 09:00 y 09:30."""
    if ahora.weekday() != 0:
        return False
    minutos = ahora.hour * 60 + ahora.minute
    return RESUMEN_DESDE <= minutos <= RESUMEN_HASTA


# ── Configuración ────────────────────────────────────────────────────────────

def leer_config(conn):
    """
    Valores de `HUB_Config` con sus omisiones. Si la consulta falla se usan los
    de fábrica: es mejor un recordatorio con la regla por omisión que ninguno.
    """
    valores = {}
    try:
        for fila in _filas(conn, SQL_CONFIG, CLAVES_CONFIG):
            valores[str(fila[0])] = str(fila[1] if fila[1] is not None else "").strip()
    except Exception as exc:
        print(f"[{WORKER}] no se pudo leer HUB_Config: {exc}")

    return {
        "activo": _flag(valores.get("wa_rec_activo"), DEFAULTS["activo"]),
        "km_dias": _ent(valores.get("wa_rec_km_dias"), DEFAULTS["km_dias"]),
        "vale_horas": _ent(valores.get("wa_rec_vale_horas"), DEFAULTS["vale_horas"]),
        "vale_max": _ent(valores.get("wa_rec_vale_max"), DEFAULTS["vale_max"]),
        "resumen": _flag(valores.get("wa_rec_resumen"), DEFAULTS["resumen"]),
        "max_dia": _ent(valores.get("wa_rec_max_dia"), DEFAULTS["max_dia"]),
        "vale_dias_max": _ent(valores.get("govale_recordatorio_dias_max"),
                              DEFAULTS["vale_dias_max"]),
    }


# ── Dedupe ───────────────────────────────────────────────────────────────────

def _contar_hoy(conn, hoy):
    """Mensajes realmente enviados hoy, por usuario."""
    conteo = {}
    for fila in _filas(conn, SQL_CONTEO_DIA, (hoy,)):
        try:
            conteo[int(fila[0])] = int(fila[1] or 0)
        except (TypeError, ValueError):
            continue
    return conteo


def _ya_hoy(conn, tipo, clave, usuario_id, hoy):
    fila = _una(conn, SQL_YA_HOY, (tipo, clave, usuario_id, hoy))
    try:
        return bool(fila and int(fila[0] or 0))
    except (TypeError, ValueError):
        return False


def _km_reciente(conn, clave, usuario_id, hoy):
    """¿Ya se le recordó este auto hoy? Es un aviso al día, no cada 3 días."""
    fila = _una(conn, SQL_KM_DEDUPE, (clave, usuario_id, hoy))
    try:
        return bool(fila and int(fila[0] or 0))
    except (TypeError, ValueError):
        return False


def _vale_historial(conn, clave, usuario_id):
    """Avisos de vales que YA salieron a esta persona (los fallos no gastan cupo)."""
    fila = _una(conn, SQL_VALE_HIST, (clave, usuario_id))
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _marcar_enviado(conn, candidato, ahora):
    """Pasa el intento (Ok=0) a enviado. Nunca lanza."""
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE HUB_WaRecordatorios SET Ok = 1, Enviado = GETDATE() "
                "WHERE Tipo = %s AND Clave = %s AND IdUsuario = %s AND FechaDia = %s",
                (candidato["tipo"], candidato["clave"],
                 candidato["usuario_id"], ahora.date()))
            conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass
    except Exception:
        pass


def _marcar_fallo(conn, candidato, ahora, detalle):
    """Deja el motivo del fallo en la fila del intento. Nunca lanza."""
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE HUB_WaRecordatorios SET Ok = 0, Detalle = %s "
                "WHERE Tipo = %s AND Clave = %s AND IdUsuario = %s AND FechaDia = %s",
                (_texto(f"{candidato['motivo']}: {detalle}"),
                 candidato["tipo"], candidato["clave"],
                 candidato["usuario_id"], ahora.date()))
            conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass
    except Exception:
        pass


def _registrar(conn, candidato, ahora, ok, detalle):
    """Guarda el intento. Un fallo de OpenWA queda como Ok=0 y no se reintenta hoy."""
    try:
        cur = conn.cursor()
        try:
            cur.execute(SQL_INSERT, (
                candidato["tipo"], candidato["clave"], candidato["usuario_id"],
                ahora.date(), ahora, 1 if ok else 0,
                _texto(f"{candidato['motivo']}: {detalle}" if detalle else candidato["motivo"]),
            ))
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
        print(f"[{WORKER}] no se pudo registrar {candidato['tipo']} "
              f"{candidato['clave']}: {exc}")
        return False


# ── Candidatos ───────────────────────────────────────────────────────────────

def _candidatos_km(conn, cfg, ahora):
    """
    Autos asignados sin lectura **esta semana**, con el texto ya armado.

    Los kilómetros se registran una vez por semana: si ya hay lectura desde el
    lunes a las 00:00, no se pide nada. Si no, se recuerda una vez al día hasta
    que la persona la mande.
    """
    lunes = ahora - timedelta(days=ahora.weekday())
    desde = lunes.replace(hour=0, minute=0, second=0, microsecond=0)
    salida = []
    for fila in _filas(conn, SQL_UNIVERSO_KM, ()):
        try:
            auto_id, marca, placas, usuario_id, nombre, telefono = fila[:6]
            auto_id, usuario_id = int(auto_id), int(usuario_id)
        except (TypeError, ValueError):
            continue

        lectura = _una(conn, SQL_KM_AUTO, (desde, auto_id))
        if lectura:
            try:
                recientes = int(lectura[2] or 0)
            except (TypeError, ValueError):
                recientes = 0
            if recientes:
                continue        # ya hay lectura de esta semana: nada que pedir
            texto = (f"Hola {str(nombre or '').strip()}, no veo kilómetros de "
                     f"{str(marca or '').strip()} ({str(placas or '').strip()}) "
                     f"esta semana; la última lectura fue el {_fecha(lectura[1])}. "
                     "¿Me pasas el odómetro?")
        else:
            texto = (f"Hola {str(nombre or '').strip()}, no tienes lecturas "
                     f"registradas de {str(marca or '').strip()} "
                     f"({str(placas or '').strip()}). "
                     "¿Me pasas la lectura del odómetro?")

        salida.append({
            "tipo": "KM",
            "clave": str(auto_id),
            "usuario_id": usuario_id,
            "telefono": telefono,
            "texto": texto,
            "motivo": f"km auto {auto_id}",
        })
    return salida


def _candidatos_vales(conn, cfg, ahora):
    """
    Vales GENERADO sin comprobante, pasadas las horas de gracia y dentro del
    tope de edad (`govale_recordatorio_dias_max`, default 15 días).

    Se agrupan **por persona**: un solo mensaje con sus vales pendientes. Antes
    salía uno por vale y quien tenía cuatro recibía cuatro mensajes seguidos.
    """
    corte = ahora - timedelta(hours=cfg["vale_horas"])
    corte_viejo = ahora - timedelta(days=cfg["vale_dias_max"])
    por_usuario = {}
    for fila in _filas(conn, SQL_VALES, (corte, corte_viejo)):
        try:
            vale_id, usuario_id = int(fila[0]), int(fila[1])
        except (TypeError, ValueError):
            continue
        por_usuario.setdefault(usuario_id, []).append(
            (vale_id, fila[2], fila[3], str(fila[4] or "").strip(), fila[5]))

    salida = []
    for usuario_id, vales in por_usuario.items():
        nombre, telefono = vales[0][3], vales[0][4]
        lineas = [f"#{v[0]} de {_dinero(v[1])} del {_fecha(v[2])}"
                  for v in vales[:3]]
        if len(vales) > 3:
            lineas.append(f"y {len(vales) - 3} más")
        if len(vales) == 1:
            texto = (f"Hola {nombre}, el vale {lineas[0]} sigue sin "
                     "comprobante. Cuando lo tengas, mándame la foto.")
        else:
            texto = (f"Hola {nombre}, tienes {len(vales)} vales sin comprobante: "
                     + "; ".join(lineas) + ". Mándame las fotos cuando las tengas.")
        salida.append({
            "tipo": "VALE",
            "clave": f"u{usuario_id}",
            "usuario_id": usuario_id,
            "telefono": telefono,
            "texto": texto,
            "motivo": f"{len(vales)} vale(s) sin comprobante",
        })
    return salida


def _candidatos_resumen(conn, ahora):
    """
    Resumen del lunes: un mensaje por usuario con WhatsApp que tenga datos.
    Los kilómetros usan el mismo cálculo que `wa_consultas._respuesta_km`
    (recorrido = lectura mayor menos menor del mes), no la suma de odómetros.
    """
    lunes_actual = ahora.date() - timedelta(days=ahora.weekday())
    ini = lunes_actual - timedelta(days=7)
    fin = lunes_actual
    desde_mes = datetime(ahora.year, ahora.month, 1)
    salida = []

    for fila in _filas(conn, SQL_USUARIOS_WA, ()):
        try:
            usuario_id = int(fila[0])
        except (TypeError, ValueError):
            continue
        nombre = str(fila[1] or "").strip()

        vales = _una(conn, SQL_RESUMEN_VALES, (usuario_id,))
        try:
            vales = int(vales[0] or 0) if vales else 0
        except (TypeError, ValueError):
            vales = 0

        reportes = _una(conn, SQL_RESUMEN_REPORTES, (usuario_id, ini, fin))
        try:
            reportes = int(reportes[0] or 0) if reportes else 0
        except (TypeError, ValueError):
            reportes = 0

        km = ""
        try:
            # Solo se pide el texto si hay lecturas este mes: `_respuesta_km`
            # siempre devuelve algo, y «no tienes kilómetros» no es un dato que
            # merezca un aviso (sería un resumen para todo el mundo).
            tiene = _una(conn, SQL_KM_MES, (usuario_id, desde_mes))
            if tiene and int(tiene[0] or 0):
                import wa_consultas
                km = wa_consultas._respuesta_km(conn, usuario_id)
        except Exception as exc:
            print(f"[{WORKER}] sin kilómetros de {usuario_id}: {exc}")

        lineas = []
        if vales:
            lineas.append(f"- Vales pendientes de comprobante: {vales}")
        if km:
            lineas.append(f"- Kilómetros: {km}")
        if reportes:
            lineas.append(f"- Reportes de servicio de la semana pasada: {reportes}")
        if not lineas:
            continue        # sin datos no se manda nada

        texto = (f"Hola {nombre}, tu resumen de la semana:\n"
                 + "\n".join(lineas))
        salida.append({
            "tipo": "RESUMEN",
            "clave": f"lunes-{ahora.date().isoformat()}",
            "usuario_id": usuario_id,
            "telefono": fila[2],
            "texto": texto,
            "motivo": "resumen lunes",
        })
    return salida


# ── Envío ────────────────────────────────────────────────────────────────────

def _enviar_seguro(enviar, telefono, texto):
    """Nunca lanza: `(ok, detalle)` aunque OpenWA explote."""
    chat = _chat(telefono)
    if not chat:
        return False, "telefono sin formato valido"
    try:
        from wa_envios import repetido
        if repetido(chat, texto):
            return False, "ya enviado hoy"
    except Exception:
        pass
    try:
        ok, detalle = enviar(chat, texto)
        return bool(ok), str(detalle or "")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _permitido(conn, candidato, cfg, ahora):
    """Reglas de repetición que van más allá del día."""
    if candidato["tipo"] == "KM":
        return not _km_reciente(conn, candidato["clave"],
                                candidato["usuario_id"], ahora.date())
    if candidato["tipo"] == "VALE":
        # Un solo mensaje de vales por persona y por día: eso ya lo garantiza
        # `_ya_hoy` con la clave `u<id>`. Aquí solo queda el tope de por vida.
        total = _vale_historial(conn, candidato["clave"],
                                candidato["usuario_id"])
        if total >= cfg["vale_max"]:
            return False
    return True


# ── Ciclo ────────────────────────────────────────────────────────────────────

def ciclo(ahora=None, conn=None, enviar=None, cfg=None):
    """
    Un turno completo. Devuelve `(detalle, enviados)` para el log y el
    heartbeat. No lanza por un fallo de OpenWA ni de un aviso concreto.
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
            return "recordatorios apagados", 0

        candidatos = _candidatos_km(conn, cfg, ahora)
        candidatos += _candidatos_vales(conn, cfg, ahora)
        if cfg["resumen"] and es_resumen(ahora):
            candidatos += _candidatos_resumen(conn, ahora)

        hoy = ahora.date()
        hoy_por_usuario = _contar_hoy(conn, hoy)
        partes = []

        for candidato in candidatos:
            usuario_id = candidato["usuario_id"]
            if hoy_por_usuario.get(usuario_id, 0) >= cfg["max_dia"]:
                continue
            if _ya_hoy(conn, candidato["tipo"], candidato["clave"], usuario_id, hoy):
                continue
            if not _permitido(conn, candidato, cfg, ahora):
                continue

            # Igual que en asistencia: marcar antes de enviar. OpenWA cierra la
            # conexión cacheada al leer su configuración; si el registro va
            # después, falla y el aviso se repite en cada ciclo.
            if not _registrar(conn, candidato, ahora, False, ""):
                continue
            ok, detalle = _enviar_seguro(enviar, candidato["telefono"],
                                         candidato["texto"])
            if ok:
                _marcar_enviado(conn, candidato, ahora)
                enviados += 1
                hoy_por_usuario[usuario_id] = hoy_por_usuario.get(usuario_id, 0) + 1
                partes.append(candidato["tipo"].lower())
            else:
                _marcar_fallo(conn, candidato, ahora, detalle)
                _log(f"no se pudo enviar {candidato['motivo']} "
                     f"a {usuario_id}: {detalle}")

        if not partes:
            return "nada que mandar", 0
        conteo = {}
        for tipo in partes:
            conteo[tipo] = conteo.get(tipo, 0) + 1
        resumen = ", ".join(f"{k}={v}" for k, v in sorted(conteo.items()))
        return f"enviados: {resumen}", enviados
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

    _log(f"recordatorios de WhatsApp iniciados (ciclo={WA_REC_CICLO_SEG}s)")
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
        # trabado, y esa confusión ya costó caro una vez.
        try:
            heartbeat(WORKER, detail=detalle, count=enviados)
        except Exception:
            pass
        time.sleep(WA_REC_CICLO_SEG)


if __name__ == "__main__":
    main()
