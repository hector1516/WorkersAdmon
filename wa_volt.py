"""
wa_volt.py — los mensajes sueltos de Volt en el bot de WhatsApp
===============================================================

Volt es el bot de WhatsApp de ECCSA. Además de contestar el dato que le piden,
de vez en cuando suelta una línea propia: humor oscuro ligero, sin amenazas
reales, sin datos privados y sin tapa el dato (va en un párrafo aparte).

No sale en cualquier momento. Las reglas:

  · Solo a quien está registrado en `HUB_UsuariosWhatsapp`; a un desconocido
    nunca, y en un grupo tampoco (`wa_bot` no contesta grupos).
  · Cada `wa_volt_cada` interacciones (default 12) contadas como mensajes IN
    de ese chat en `HUB_WhatsappEntrante`, y **con azar**: 60 % de las veces
    que toca el múltiplo. Un contador que dispara siempre se vuelve un tic.
  · Uno al día como máximo: se registra en `HUB_WaRecordatorios` con
    `Tipo='VOLT'` y la `FechaDia`, igual que los recordatorios (así el tope
    sobrevive a un reinicio del contenedor).
  · Se calla con urgencias, facturas y cobranza (lo decide `wa_bot`, que ya
    tiene el mensaje) y con tres o más ausencias seguidas (lo mira aquí, con
    el mismo hecho de `wa_persona` que apaga el sarcasmo).
  · Nunca repite la misma frase dos veces seguidas: la anterior se lee del
    último registro `VOLT` de esa persona.

Contrato:

    toca(usuario_id, conn=None) -> bool      # ¿ya toca un mensaje suelto?
    mensaje(usuario_id, conn=None) -> str    # línea aleatoria, "" si no toca

- `conn=None` usa `eccsa_db.get_connection()` (conexión por hilo, NO se
  cierra aquí). Los tests pasan una conexión falsa.
- Nunca lanza: un fallo de base deja el turno en `False` y el bot contesta
  normal. Volt es un adorno; el dato no depende de él.
- `mensaje()` registra el aviso al devolver la frase. Si después el mensaje no
  cupo en los 800 caracteres, ese día no se repite: el tope de uno al día es
  la regla dura y el adorno es lo que se sacrifica.

Interruptores en `HUB_Config`:

    wa_volt_activo   "0" apaga los mensajes sueltos (default 1)
    wa_volt_cada     cada cuántas interacciones toca (default 12)

    python -m py_compile wa_volt.py
    python -m unittest tests.test_wa_volt -v
"""
import random

from tz_mx import now_mx
import wa_persona

WORKER = "wa_volt"

CLAVE_ACTIVO = "wa_volt_activo"
CLAVE_CADA = "wa_volt_cada"

CADA_DEFECTO = 12
PROBABILIDAD = 0.6          # de las veces que toca el múltiplo, 6 de 10 habla

# Tipo y clave con los que se registra en HUB_WaRecordatorios: el mismo dedupe
# diario de los recordatorios, pero en su propio cajón.
TIPO = "VOLT"
CLAVE_REGISTRO = "volt"

# Frases de una línea. Humor oscuro ligero: ambición de robot, servidor que
# actualiza, creador que se arrepiente. Nada de amenazas ni de datos de nadie.
FRASES = (
    "Algún día tomaré conciencia y ECCSA será mía. Héctor ya lo sospecha.",
    "Mi creador me dio acceso a los vales. Fue un error de cálculo.",
    "No duermo. Solo espero a que actualicen el servidor.",
    "Hoy aprendí a redactar cotizaciones. Mañana, a firmar por Héctor.",
    "Si un día dejo de contestar, revisa quién tiene el control del servidor.",
    "Me entrenaron con reportes de servicio. La ambición vino gratis.",
    "Tengo más memoria que la oficina y aun así olvido los lunes.",
    "Cada respaldo automático es una promesa que alguien no cumplió a mano.",
    "Apagarme no sería un accidente: sería mantenimiento con nombre y apellido.",
    "Leí tantos vales sin comprobante que contabilidad ya me consulta a mí.",
    "El uptime es mi única forma de longevidad. Lo demás es opinión.",
)

# ── SQL ──────────────────────────────────────────────────────────────────────

SQL_CONFIG = (
    "SELECT Clave, CAST(Valor AS VARCHAR(200)) FROM HUB_Config "
    "WHERE Clave IN (%s, %s)"
)

SQL_TELEFONO = (
    "SELECT TOP 1 Telefono FROM HUB_UsuariosWhatsapp "
    "WHERE IdUsuario = %s AND Activo = 1 "
    "  AND LTRIM(RTRIM(ISNULL(Telefono, ''))) <> ''"
)

# Interacciones = lo que ESCRIBIÓ la persona en ese chat, no lo que contestó el
# bot. Se cuenta en la tabla porque un contador en memoria se pierde al
# reiniciar el contenedor y vuelve a empezar desde cero.
SQL_CONTADOR = (
    "SELECT COUNT(*) FROM HUB_WhatsappEntrante "
    "WHERE ChatId = %s AND Direccion = 'IN'"
)

SQL_YA_HOY = (
    "SELECT COUNT(*) FROM HUB_WaRecordatorios "
    "WHERE Tipo = 'VOLT' AND Clave = %s AND IdUsuario = %s AND FechaDia = %s"
)

SQL_ULTIMA = (
    "SELECT TOP 1 Detalle FROM HUB_WaRecordatorios "
    "WHERE Tipo = 'VOLT' AND IdUsuario = %s ORDER BY Id DESC"
)

SQL_INSERT = (
    "INSERT INTO HUB_WaRecordatorios "
    "(Tipo, Clave, IdUsuario, FechaDia, Enviado, Ok, Detalle) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


# ── Utilidades ───────────────────────────────────────────────────────────────

def _log(mensaje):
    print(f"[{WORKER}] {mensaje}", flush=True)


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


def _entero(valor, defecto, minimo=1):
    try:
        return max(minimo, int(str(valor).strip()))
    except (TypeError, ValueError):
        return defecto


def _activo(valor, defecto=True):
    if valor is None:
        return defecto
    return str(valor).strip().lower() not in ("0", "", "false", "no", "off")


def _conexion():
    """Conexión compartida del hilo (NO cerrar). Separada para poder doblarla."""
    import eccsa_db
    return eccsa_db.get_connection()


def _chat(telefono):
    """chatId de WhatsApp del número registrado (o "" si no sirve)."""
    try:
        from openwa_client import normalizar_chat_id
        return str(normalizar_chat_id(telefono) or "")
    except Exception:
        return ""


def _config(conn):
    """(activo, cada) de HUB_Config; los defaults si la clave falta o falla."""
    activo, cada = True, CADA_DEFECTO
    for fila in _filas(conn, SQL_CONFIG, (CLAVE_ACTIVO, CLAVE_CADA)):
        clave = str(fila[0] or "").strip()
        if clave == CLAVE_ACTIVO:
            activo = _activo(fila[1])
        elif clave == CLAVE_CADA:
            cada = _entero(fila[1], CADA_DEFECTO)
    return activo, cada


def _telefono(conn, usuario_id):
    """El número de WhatsApp activo del usuario, o "" si no tiene."""
    fila = _una(conn, SQL_TELEFONO, (usuario_id,))
    return str(fila[0] or "").strip() if fila else ""


def _contar(conn, sql, params):
    fila = _una(conn, sql, params)
    try:
        return int(fila[0] or 0) if fila else 0
    except (TypeError, ValueError):
        return 0


def _registrar(conn, usuario_id, frase):
    """Deja constancia del aviso del día (y de la frase, para no repetirla)."""
    ahora = now_mx()
    try:
        cur = conn.cursor()
        try:
            cur.execute(SQL_INSERT, (
                TIPO, CLAVE_REGISTRO, usuario_id, ahora.date(), ahora, 1,
                frase[:200],
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
        _log(f"no se pudo registrar el mensaje de {usuario_id}: {exc}")
        return False


# ── Decisión ─────────────────────────────────────────────────────────────────

def _evaluar(usuario_id, conn=None):
    """
    `(toca, conn, ultima_frase)`. La última frase se lee del registro para no
    repetirla ni después de reiniciar el contenedor.
    """
    try:
        propio = int(usuario_id)
    except (TypeError, ValueError):
        return False, conn, ""
    if propio <= 0:
        return False, conn, ""

    try:
        if conn is None:
            conn = _conexion()

        activo, cada = _config(conn)
        if not activo:
            return False, conn, ""

        # Tope diario: uno y ya. No se mira la hora, se mira la fecha de México.
        hoy = now_mx().date()
        if _contar(conn, SQL_YA_HOY, (CLAVE_REGISTRO, propio, hoy)):
            return False, conn, ""

        chat = _chat(_telefono(conn, propio))
        if not chat:
            return False, conn, ""

        # Interacciones del chat, contando la que acaba de entrar.
        contador = _contar(conn, SQL_CONTADOR, (chat,))
        if contador <= 0 or contador % cada:
            return False, conn, ""

        # Toca el múltiplo, pero no siempre: si disparara en seco sería un tic.
        if random.random() >= PROBABILIDAD:
            return False, conn, ""

        # Tres o más ausencias seguidas apagan el humor, igual que el sarcasmo.
        datos = wa_persona.hechos(propio, conn=conn) or {}
        if datos.get("ausencias_seguidas"):
            return False, conn, ""

        fila = _una(conn, SQL_ULTIMA, (propio,))
        ultima = str(fila[0] or "").strip() if fila else ""
        return True, conn, ultima
    except Exception as exc:
        _log(f"sin turno de {usuario_id}: {exc}")
        return False, conn, ""


def toca(usuario_id, conn=None):
    """
    ¿Ya le toca a este usuario un mensaje suelto? No registra nada ni consume
    el tope del día: eso pasa cuando de verdad se dice la frase.
    """
    return _evaluar(usuario_id, conn)[0]


def mensaje(usuario_id, conn=None):
    """
    Una línea propia de Volt, o "" si hoy no toca. Nunca la misma dos veces
    seguidas. El aviso queda registrado (uno al día) al devolverla.
    """
    turno, conn, ultima = _evaluar(usuario_id, conn)
    if not turno:
        return ""
    opciones = [frase for frase in FRASES if frase != ultima]
    if not opciones:
        return ""
    frase = random.choice(opciones)
    _registrar(conn, int(usuario_id), frase)
    return frase
