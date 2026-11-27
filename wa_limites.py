"""
wa_limites.py — Tope de respuestas del bot de WhatsApp
======================================================

El bot contesta solo; una persona puede dejarlo hablando con alguien que
insiste, o el propio chat puede entrar en bucle (un mensaje del bot que vuelve
como entrante, un reintento de OpenWA). Este módulo es el freno, medido en
mensajes que YA salieron (`HUB_WhatsappEntrante` con `Direccion='OUT'`).

Contrato:

    puede_responder(chat_id, conn=None) -> (bool, motivo)

- `(True, "")`  → puede contestar.
- `(False, "chat")`  → este chat ya llegó a `wa_bot_max_hora_chat`.
- `(False, "total")` → la sesión ya llegó a `wa_bot_max_hora_total`.

Cuenta la hora EN CURSO (hora de Monterrey): a las 10:37 se cuentan las
respuestas desde las 10:00. No hay contador en memoria: la verdad está en la
tabla, así que un reinicio del contenedor no borra el tope.

Si la base falla se PERMITE responder: quedarse mudo por un problema de SQL es
peor que mandar un mensaje de más. El fallo queda en el log.

`conn=None` usa la conexión cacheada de `eccsa_db`, que es por hilo y NO se
cierra aquí (cerrarla dejaría sin conexión al resto del hilo); una conexión
que pasa el llamador tampoco se cierra: es suya.
"""
from tz_mx import now_mx

# Claves de HUB_Config (Valor es NVARCHAR(500), sobra para un entero).
CLAVE_CHAT = "wa_bot_max_hora_chat"
CLAVE_TOTAL = "wa_bot_max_hora_total"

MAX_CHAT_DEFECTO = 20       # respuestas por chat en la hora en curso
MAX_TOTAL_DEFECTO = 200     # respuestas de la sesión en la hora en curso


def inicio_hora(ahora=None):
    """Primer segundo de la hora en curso, en hora de Monterrey."""
    ahora = ahora or now_mx()
    return ahora.replace(minute=0, second=0, microsecond=0)


def _entero(valor, defecto):
    """Entero >= 0 del valor de configuración; el default si no se entiende."""
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto
    return numero if numero >= 0 else defecto


def _conexion():
    """Conexión compartida del hilo (NO cerrar). Separada para poder doblarla."""
    import eccsa_db
    return eccsa_db.get_connection()


def _limites(conn):
    """(max_chat, max_total) de HUB_Config; defaults si la clave no existe."""
    max_chat, max_total = MAX_CHAT_DEFECTO, MAX_TOTAL_DEFECTO
    with conn.cursor() as cur:
        cur.execute(
            "SELECT Clave, Valor FROM HUB_Config WHERE Clave IN (%s, %s)",
            (CLAVE_CHAT, CLAVE_TOTAL))
        for fila in cur.fetchall() or []:
            clave, valor = fila[0], fila[1]
            if clave == CLAVE_CHAT:
                max_chat = _entero(valor, MAX_CHAT_DEFECTO)
            elif clave == CLAVE_TOTAL:
                max_total = _entero(valor, MAX_TOTAL_DEFECTO)
    return max_chat, max_total


def _conteos(conn, chat_id, desde):
    """
    (mensajes_del_chat, mensajes_totales) enviados desde `desde`.

    `Direccion='OUT'` es lo único que se cuenta: lo que entra no gasta cupo.
    El filtro por fecha va como parámetro para que use el índice de la tabla.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS Total, "
            "       SUM(CASE WHEN ChatId = %s THEN 1 ELSE 0 END) AS DelChat "
            "FROM HUB_WhatsappEntrante "
            "WHERE Direccion = 'OUT' AND Fecha >= %s",
            (str(chat_id or ""), desde))
        fila = cur.fetchone()
    total = int(fila[0] or 0) if fila else 0
    del_chat = int(fila[1] or 0) if fila else 0
    return del_chat, total


def puede_responder(chat_id, conn=None):
    """
    ¿El bot puede mandar otra respuesta en este chat, en esta hora?

    Devuelve `(True, "")`, `(False, "chat")` o `(False, "total")`. Nunca lanza:
    ante cualquier error de base permite responder y lo deja en el log.
    """
    propia = conn is None
    try:
        if propia:
            conn = _conexion()
        max_chat, max_total = _limites(conn)
        del_chat, total = _conteos(conn, chat_id, inicio_hora())

        # Se compara con >= porque el mensaje que se quiere mandar sería uno más.
        if del_chat >= max_chat:
            return False, "chat"
        if total >= max_total:
            return False, "total"
        return True, ""
    except Exception as exc:
        print(f"[wa_limites] no se pudo contar lo enviado; se permite responder: {exc}")
        return True, ""
