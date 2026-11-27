"""
Hechos y sarcasmo del bot de WhatsApp para el personal registrado
=================================================================

`hechos(usuario_id)` junta, SOLO del propio usuario, lo que el bot puede usar
para hablarle con gracia en vez de repetir el menú:

  · vales `GENERADO` sin comprobante (cuántos y el más viejo);
  · último reporte de servicio y su estatus;
  · kilómetros del mes y último odómetro (misma lógica de `wa_consultas`);
  · llegadas tarde en los últimos 5 días hábiles, contra su turno;
  · tickets OxxoGas del mes;
  · cotizaciones recientes, solo si está en `wa_bot_cotizaciones`.

`sarcasmo(usuario_id)` devuelve UNA línea corta de humor seco armada con esos
hechos, o `""` si no hay nada real que decir. Nunca inventa un dato y nunca
repite la frase anterior de la misma persona.

Contrato:

    hechos(usuario_id, conn=None) -> dict          # cache 5 min
    sarcasmo(usuario_id, hechos=None, conn=None) -> str
    olvidar(usuario_id=None) -> None               # tira la cache

- `conn=None` usa `eccsa_db.get_connection()`; los tests pasan una conexión
  falsa, así que este módulo no abre SQL Server al cargarse.
- `None` o `""` como `usuario_id` no consulta nada.
- Cualquier error de base deja el hecho en cero y se registra: este módulo
  nunca lanza, porque de él depende que el bot conteste.

Reglas que no se rompen:

- Solo datos del propio usuario. Jamás se menciona a un tercero.
- Con tres o más ausencias seguidas no hay sarcasmo (`""`): ahí va texto
  neutro. `wa_bot` además lo apaga en urgencias, facturas y cobranza.
- El interruptor `HUB_Config.wa_bot_sarcasmo` no se lee aquí: lo aplica
  `wa_bot`, que ya carga la configuración en su base de conocimiento.
- La frase es de una línea: sin saltos de línea, para no romper mensajes.

    python -m py_compile wa_persona.py
    python -m unittest tests.test_wa_persona -v
"""
import random
import time
from datetime import datetime, time as hora_del_dia, timedelta

from tz_mx import now_mx
import wa_asistencia
import wa_consultas

CACHE_TTL = 300                 # 5 minutos por usuario
TOLERANCIA_DEFECTO = 10         # si el turno no fija `Tol_Llegada_Min`
DIAS_TARDE = 5                  # días hábiles que se revisan
DIAS_BUSQUEDA = 14              # calendario máximo para juntar esos 5

SQL_NOMBRE = "SELECT TOP 1 Nombre FROM HUB_Users WHERE Id = %s"

SQL_VALES = """
    SELECT COUNT(*), MIN(v.FechaSolicitud)
    FROM HUB_SolicitudVales v
    WHERE v.IdSolicitante = %s
      AND v.Estatus = 'GENERADO'
      AND NOT EXISTS (SELECT 1 FROM HUB_OxxoGasTickets t
                      WHERE t.IdSolicitudVale = v.Id)
"""

SQL_REPORTE = """
    SELECT TOP 1 r.Folio, r.Fecha, r.Cliente, r.Estatus
    FROM ReportesServicio r
    JOIN ReportesServicioTecnicos t ON t.IdReporte = r.IdReporte
    WHERE t.IdUsuario = %s AND ISNULL(r.Eliminado, 0) = 0
    ORDER BY r.Fecha DESC, r.IdReporte DESC
"""

SQL_TICKETS_MES = """
    SELECT COUNT(*) FROM HUB_OxxoGasTickets
    WHERE IdUsuario = %s AND FechaRegistro >= %s
"""

SQL_COTIZACIONES_MES = """
    SELECT COUNT(*) FROM IndiceMateriales I
    JOIN HUB_Users u ON u.Id = %s
    WHERE I.Autor = u.Nombre AND I.Fecha >= %s
"""

# Hechos por omisión: siempre están todas las claves, así el que consume no
# tiene que preguntar si existe cada una.
HECHOS_VACIOS = {
    "usuario_id": None,
    "nombre": "",
    "vales": {"total": 0, "mas_viejo": None},
    "reporte": None,
    "km_mes": 0,
    "km_registros": 0,
    "odometro": None,
    "odometro_fecha": None,
    "tardes": 0,
    "tardes_dias": 0,
    "tickets_mes": 0,
    "cotizaciones": None,       # None = no autorizado a cotizaciones
    "ausencias_seguidas": False,
}

_CACHE = {}       # usuario_id -> (monotonic, hechos)
_ULTIMA = {}      # usuario_id -> última frase dicha


# ─── Acceso a la base ────────────────────────────────────────────────────────

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


def _conexion():
    """Conexión del HUB; los tests la sustituyen."""
    import eccsa_db
    return eccsa_db.get_connection()


# ─── Hechos ──────────────────────────────────────────────────────────────────

def _nombre(conn, usuario_id):
    fila = _una(conn, SQL_NOMBRE, (usuario_id,))
    return str(fila[0] or "").strip() if fila else ""


def _vales(conn, usuario_id):
    fila = _una(conn, SQL_VALES, (usuario_id,))
    if not fila:
        return {"total": 0, "mas_viejo": None}
    try:
        total = int(fila[0] or 0)
    except (TypeError, ValueError):
        return {"total": 0, "mas_viejo": None}
    return {"total": total, "mas_viejo": fila[1] if total else None}


def _reporte(conn, usuario_id):
    fila = _una(conn, SQL_REPORTE, (usuario_id,))
    if not fila:
        return None
    return {
        "folio": str(fila[0] or "").strip(),
        "fecha": fila[1],
        "cliente": str(fila[2] or "").strip(),
        "estatus": str(fila[3] or "").strip(),
    }


def _kilometros(conn, usuario_id):
    recorrido, registros, odometro, fecha = wa_consultas.kilometros_mes(
        conn, usuario_id)
    return {
        "km_mes": recorrido,
        "km_registros": registros,
        "odometro": odometro,
        "odometro_fecha": fecha,
    }


def _tickets(conn, usuario_id, desde):
    fila = _una(conn, SQL_TICKETS_MES, (usuario_id, desde))
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _cotizaciones(conn, usuario_id, desde):
    """Número de cotizaciones del mes, o None si no está autorizado."""
    if usuario_id not in wa_consultas._ids_autorizados(conn):
        return None
    fila = _una(conn, SQL_COTIZACIONES_MES, (usuario_id, desde))
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _llegadas_tarde(conn, usuario_id, ahora):
    """
    `(tardes, dias)` de los últimos `DIAS_TARDE` días hábiles, sin contar hoy.

    El primer escaneo del día (su equipo en la red) contra la entrada de su
    turno más la tolerancia. Se reusa la regla de `wa_asistencia`: el turno
    vigente, el horario del día y la tolerancia son los mismos que ya usan los
    avisos, y no conviene tener dos definiciones de "llegó tarde".
    """
    dias = []
    dia = ahora.date() - timedelta(days=1)
    for _ in range(DIAS_BUSQUEDA):
        if len(dias) >= DIAS_TARDE:
            break
        if wa_asistencia.horario_dia(conn, usuario_id, dia) is not None:
            dias.append(dia)
        dia -= timedelta(days=1)
    if not dias:
        return 0, 0

    desde = datetime.combine(min(dias), hora_del_dia.min)
    hasta = datetime.combine(max(dias) + timedelta(days=1), hora_del_dia.min)
    primeras = wa_asistencia._primera_por_dia(conn, usuario_id, desde, hasta)

    tardes = 0
    for dia in dias:
        horario = wa_asistencia.horario_dia(conn, usuario_id, dia)
        primera = primeras.get(dia)
        if not horario or not primera:
            continue
        tolerancia = wa_asistencia.tolerancia_de(
            horario, {"tolerancia_min": TOLERANCIA_DEFECTO})
        esperada = datetime.combine(dia, horario[0])
        if primera > esperada + timedelta(minutes=tolerancia):
            tardes += 1
    return tardes, len(dias)


def _leer_hechos(conn, usuario_id, ahora):
    """Los hechos que se puedan leer; lo que falle se queda en cero."""
    datos = dict(HECHOS_VACIOS)
    datos["usuario_id"] = usuario_id
    desde = datetime(ahora.year, ahora.month, 1)

    def intentar(nombre, funcion, *args):
        try:
            datos[nombre] = funcion(*args)
        except Exception as exc:
            print(f"[wa_persona] sin {nombre} de {usuario_id}: {exc}")

    intentar("nombre", _nombre, conn, usuario_id)
    intentar("vales", _vales, conn, usuario_id)
    intentar("reporte", _reporte, conn, usuario_id)
    intentar("tickets_mes", _tickets, conn, usuario_id, desde)
    intentar("cotizaciones", _cotizaciones, conn, usuario_id, desde)
    try:
        datos.update(_kilometros(conn, usuario_id))
    except Exception as exc:
        print(f"[wa_persona] sin kilometros de {usuario_id}: {exc}")

    try:
        tardes, dias = _llegadas_tarde(conn, usuario_id, ahora)
        datos["tardes"], datos["tardes_dias"] = tardes, dias
    except Exception as exc:
        print(f"[wa_persona] sin llegadas de {usuario_id}: {exc}")

    try:
        datos["ausencias_seguidas"] = bool(
            wa_asistencia._ausencias_seguidas(conn, usuario_id, ahora.date()))
    except Exception as exc:
        print(f"[wa_persona] sin ausencias de {usuario_id}: {exc}")
    return datos


def _hechos(usuario_id, conn=None, ahora=None):
    """Hechos cacheados 5 min. Un fallo también se cachea: no se martilla la base."""
    try:
        propio = int(usuario_id)
    except (TypeError, ValueError):
        return dict(HECHOS_VACIOS)
    if propio <= 0:
        return dict(HECHOS_VACIOS)

    momento = ahora or now_mx()
    clave = time.monotonic()
    cache = _CACHE.get(propio)
    if cache is not None and clave - cache[0] < CACHE_TTL:
        return cache[1]

    datos = dict(HECHOS_VACIOS)
    try:
        if conn is None:
            conn = _conexion()
        datos = _leer_hechos(conn, propio, momento)
    except Exception as exc:
        print(f"[wa_persona] sin hechos de {propio}: {exc}")
    _CACHE[propio] = (clave, datos)
    return datos


def hechos(usuario_id, conn=None, ahora=None):
    """
    Hechos del usuario para el sarcasmo. Cachea 5 min y nunca lanza; ante
    cualquier problema devuelve el diccionario vacío (claves completas).
    `ahora` es la hora de Monterrey; solo las pruebas la inyectan.
    """
    return _hechos(usuario_id, conn, ahora)


def olvidar(usuario_id=None):
    """Tira la cache de un usuario (o de todos). Se usa al guardar un dato."""
    if usuario_id is None:
        _CACHE.clear()
        return
    try:
        _CACHE.pop(int(usuario_id), None)
    except (TypeError, ValueError):
        return


# ─── Sarcasmo ────────────────────────────────────────────────────────────────

def _pila(texto):
    partes = str(texto or "").strip().split()
    if not partes:
        return ""
    primero = partes[0]
    return primero.capitalize() if primero.isupper() else primero


def _fecha(valor):
    """Fecha corta en español: «26 nov 2026»."""
    return wa_consultas._fecha(valor)


def _candidatas(datos):
    """
    Frases posibles con los hechos que SÍ existen. Una línea cada una, humor
    seco, sin insultos y sin datos de terceros.
    """
    nombre = _pila(datos.get("nombre"))
    quien = f", {nombre}" if nombre else ""
    lineas = []

    vales = datos.get("vales") or {}
    total = vales.get("total") or 0
    if total:
        plural = "vale" if total == 1 else "vales"
        lineas.append(f"{total} {plural} sin comprobante{quien}. "
                      "¿Los estás coleccionando?")
        viejo = vales.get("mas_viejo")
        if viejo:
            lineas.append(f"El vale más viejo sin comprobante es del "
                          f"{_fecha(viejo)}{quien}. Ya casi cumple años.")

    tardes = datos.get("tardes") or 0
    dias = datos.get("tardes_dias") or 0
    if tardes and dias:
        lineas.append(f"Llegaste tarde {tardes} de los últimos {dias} días "
                      "hábiles; el café ya te aparta lugar.")
        lineas.append(f"{tardes} llegadas tarde en {dias} días{quien}. "
                      "El tráfico ya te conoce.")

    reporte = datos.get("reporte") or {}
    if reporte.get("fecha"):
        lineas.append(f"Tu último reporte fue el {_fecha(reporte['fecha'])}. "
                      "La planta sigue en pie, por si te preguntas.")

    odometro = datos.get("odometro")
    if odometro:
        lineas.append(f"{float(odometro):,.0f} kilómetros. "
                      "El auto va a pedir aumento.")
        lineas.append(f"{float(odometro):,.0f} km en el odómetro{quien}. "
                      "El auto ya pide vacaciones.")

    km_mes = datos.get("km_mes") or 0
    if km_mes:
        lineas.append(f"{float(km_mes):,.0f} km este mes. "
                      "Ni un desvío de más, ¿verdad?")

    tickets = datos.get("tickets_mes") or 0
    if tickets:
        lineas.append(f"{tickets} tickets de OxxoGas este mes. "
                      "La gasolina no se compra sola.")

    cotizaciones = datos.get("cotizaciones")
    if cotizaciones:
        lineas.append(f"{cotizaciones} cotizaciones este mes{quien}. "
                      "A mano o con café, pero salieron.")
    return lineas


def sarcasmo(usuario_id, hechos=None, conn=None):
    """
    Una línea de sarcasmo con un hecho real del usuario, o `""` si no hay
    nada que decir (o si trae tres o más ausencias seguidas: ahí va neutro).

    No repite la frase anterior de la misma persona: si solo queda esa, se
    calla en vez de repetirla.
    """
    try:
        propio = int(usuario_id)
    except (TypeError, ValueError):
        return ""
    if propio <= 0:
        return ""

    datos = hechos if isinstance(hechos, dict) else _hechos(propio, conn)
    if not datos or datos.get("ausencias_seguidas"):
        return ""

    opciones = [linea for linea in _candidatas(datos)
                if linea != _ULTIMA.get(propio)]
    if not opciones:
        return ""
    frase = random.choice(opciones)
    _ULTIMA[propio] = frase
    return frase
