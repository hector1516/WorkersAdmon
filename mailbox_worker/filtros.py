"""
mailbox_worker/filtros.py — reglas de filtrado y respuestas automáticas.

Corre al final del sync de cada cuenta, sobre los mensajes **nuevos** de ese
ciclo (nunca sobre el backlog: ver `aplicar_reglas_de_cuenta`).

─── LA REGLA DE ORO DE ESTE MÓDULO ────────────────────────────────────────────

**Las reglas se aplican por la primera que matchea, y en orden de prioridad.**
No hay O ni negación, a propósito: un motor de reglas con O y negación es un
motor que nadie entiende después de seis meses, y el usuario que no entiende sus
reglas las desactiva. El esquema (`HUB_MailboxReglas`) lo refleja: un `Campo`,
un `Operador`, un `Valor`, una `Accion`.

**Una regla `ARCHIVAR` mueve el mensaje y se detiene.** No sigue evaluando: si
el mensaje ya está en Archivados, la siguiente regla "mover a Spam" lo sacaría
de ahí. Solo `ETIQUETAR` continúa, porque etiquetar no mueve el mensaje.

─── AUTO-RESPUESTAS: LAS TRES GUARDAS ─────────────────────────────────────────

Un auto-responder sin guardas es un arma: contesta al jefe, contesta a sí mismo
por el hilo, y genera 200 correos en cadena. Las tres guardas son:

1. **Nunca a uno mismo**, ni a un correo del mismo dominio corporativo.
2. **Nunca en bucle**: si ya se respondió a ese `Message-ID`, no se vuelve a
   responder. Es la única defensa real contra una cadena de bots que se
   contestan entre ellos.
3. **Nunca a un dominio exento**, ni dentro del horario de oficina si el usuario
   pidió que no.

El auto-responder se ENCOLA, no se manda al instante: pasa por la misma cola y
los mismos reintentos que un correo del usuario.
"""

import re
from datetime import datetime

from .db import ejecuta, filas, una

# Tope del texto que se evalúa con REGEX.
#
# Python `re` no tiene timeout (tampoco en 3.13/3.14). Un patrón con backtracking
# catastrófico sobre un cuerpo de 200 KB congelaría ESTE proceso, y con él TODAS
# las cuentas, porque viven en el mismo worker. Acotando el patrón a 200
# caracteres y el texto a 4000 desaparecen los casos patológicos conocidos. Es
# una mitigación, no una garantía: no hay forma de poner timeout a `re` sin
# moverlo a un hilo.
_MAX_REGEX_PATRON = 200
_MAX_REGEX_TEXTO = 4000

# Dominios de ECCSA: nunca se auto-responden.
_DOMINIOS_PROPIOS = {"ecc-sa.com.mx", "ecc-ssa.com.mx"}

# Columnas del mensaje que las reglas necesitan. Se declara una vez y se usa en
# todos los SELECT para que un cambio de esquema se note en un solo lugar.
_COLS = ("Id, IdCuenta, UID, Carpeta, RemitenteNombre, RemitenteEmail, ParaTexto, "
         "CcTexto, Asunto, Extracto, MessageId, Etiqueta, Visto, Eliminado")


class FiltroError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Coincidencia
# ═══════════════════════════════════════════════════════════════════════════════

def _valor_de_campo(mensaje: dict, campo: str) -> str:
    """
    El texto contra el que se compara, según el campo de la regla.

    `BODY` usa `Extracto`, no el cuerpo de disco: las reglas corren dentro del
    sync, y leer el cuerpo de cada mensaje nuevo convertiría un ciclo barato en
    miles de lecturas de archivo. Un filtro por palabras del CUERPO con un
    extracto de 480 caracteres es menos potente, y es la decisión correcta.
    """
    campo = (campo or "").upper()
    if campo == "FROM":
        return f"{mensaje.get('RemitenteNombre') or ''} {mensaje.get('RemitenteEmail') or ''}"
    if campo == "TO":
        return f"{mensaje.get('ParaTexto') or ''} {mensaje.get('CcTexto') or ''}"
    if campo == "SUBJECT":
        return mensaje.get("Asunto") or ""
    if campo == "DOMINIO":
        return (mensaje.get("RemitenteEmail") or "").split("@")[-1]
    if campo == "BODY":
        return mensaje.get("Extracto") or ""
    return ""


def coincide(mensaje: dict, regla: dict) -> bool:
    """
    ¿La regla matchea este mensaje?

    Todo se compara en minúsculas salvo con REGEX, donde el usuario controla las
    banderas con `(?i)`. La razón: un usuario escribe "Juan" y espera que
    matchee "JUAN PÉREZ".
    """
    texto = _valor_de_campo(mensaje, regla.get("Campo"))
    patron = (regla.get("Valor") or "").strip()
    if not patron:
        return False
    operador = (regla.get("Operador") or "CONTIENE").upper()

    if operador == "REGEX":
        if len(patron) > _MAX_REGEX_Patron:
            print(f"[filtros] regla {regla.get('Id')}: patrón de {len(patron)} caracteres, "
                  f"no se ejecuta (tope {_MAX_REGEX_PATRON})")
            return False
        try:
            rx = re.compile(patron, re.I)
        except re.error as exc:
            print(f"[filtros] regla {regla.get('Id')}: regex inválida, se ignora ({exc})")
            return False
        return bool(rx.search((texto or "")[:_MAX_REGEX_TEXTO]))

    hay_texto = (texto or "").lower().strip()
    hay_patron = patron.lower()
    if operador == "CONTIENE":
        return hay_patron in hay_texto
    if operador == "IGUAL":
        return hay_texto == hay_patron
    if operador == "EMPIEZA":
        return hay_texto.startswith(hay_patron)
    if operador == "TERMINA":
        return hay_texto.endswith(hay_patron)
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Acciones
# ═══════════════════════════════════════════════════════════════════════════════

def _carpeta_por_nombre(cliente, candidatos) -> str:
    """
    La primera carpeta que exista de entre los candidatos.

    Los nombres varían por proveedor e idioma. Si ninguna existe se devuelve el
    primero y el error queda visible en la cola de operaciones: inventar un
    nombre nuevo crearía una carpeta fantasma que el usuario no ve en su cliente
    de correo, y el mensaje "desaparecería" de esta app sin explicación.
    """
    try:
        existentes = {n.lower(): n for n, _f in cliente.list_folders()}
    except Exception:
        existentes = {}
    for nombre in candidatos:
        if nombre in existentes:
            return existentes[nombre]
    return candidatos[0]


def aplicar_accion(cliente, cuenta, mensaje: dict, regla: dict, destino: str,
                   destino_spam: str = "") -> bool:
    """
    Ejecuta la acción de la regla. Devuelve True si se aplicó.

    Cada acción aísla su excepción: una regla mal formada no puede tumbar el
    sync de la cuenta ni impedir las reglas siguientes del mismo mensaje.
    """
    accion = (regla.get("Accion") or "").upper()
    uid, carpeta, mid = mensaje.get("UID"), mensaje.get("Carpeta"), mensaje.get("Id")

    try:
        if accion == "NO_HACER":
            # Existe para dejar la regla documentada sin aplicarla (borrador).
            return False

        if accion == "MARCAR_LEIDO":
            cliente.select_folder(carpeta)
            cliente.set_flag(uid, "\\Seen", True)
            ejecuta("UPDATE HUB_MailboxMensajes SET Visto = 1 WHERE Id = %s", (mid,))
            return True

        if accion == "ARCHIVAR":
            cliente.select_folder(carpeta)
            cliente.move_message(uid, destino)
            ejecuta("UPDATE HUB_MailboxMensajes SET Carpeta = %s WHERE Id = %s",
                    (destino, mid))
            return True

        if accion == "ELIMINAR":
            cliente.select_folder(carpeta)
            cliente.delete_message(uid)
            # No se borra la fila: queda con `Eliminado=1` para que la app lo
            # muestre tachado y el usuario pueda deshacer antes de la retención.
            ejecuta("UPDATE HUB_MailboxMensajes SET Eliminado = 1, Carpeta = %s WHERE Id = %s",
                    (destino, mid))
            return True

        if accion == "SPAM":
            # El destino lo resuelve el llamador una vez por ciclo (destino_spam)
            # y se pasa por `destino`. Mover a spam es un MOVE real, no un
            # flag: el mensaje sale de Entrada y aparece en la carpeta de spam
            # del proveedor, que es donde el usuario lo va a buscar.
            if not destino:
                print(f"[filtros] regla {regla.get('Id')}: SPAM sin carpeta de destino")
                return False
            cliente.select_folder(carpeta)
            cliente.move_message(uid, destino)
            ejecuta("UPDATE HUB_MailboxMensajes SET Carpeta = %s WHERE Id = %s",
                    (destino, mid))
            return True

        if accion == "ETIQUETAR":
            etiqueta = (regla.get("Etiqueta") or "").strip()[:60]
            if not etiqueta:
                print(f"[filtros] regla {regla.get('Id')}: ETIQUETAR sin etiqueta")
                return False
            actuales = [e.strip() for e in (mensaje.get("Etiqueta") or "").split(",")
                        if e.strip()]
            if etiqueta.lower() in [e.lower() for e in actuales]:
                return False        # ya está: no cuenta como aplicación
            actuales.append(etiqueta)
            # Solo local: IMAP no tiene etiquetas y la columna la lee únicamente
            # la app. Es una limitación consciente, no un olvido.
            ejecuta("UPDATE HUB_MailboxMensajes SET Etiqueta = %s WHERE Id = %s",
                    (", ".join(actuales)[:60], mid))
            return True

    except Exception as exc:
        print(f"[filtros] regla {regla.get('Id')} ({accion}) sobre el mensaje {mid}: "
              f"{type(exc).__name__}: {exc}")
        return False

    print(f"[filtros] acción desconocida, se ignora: {accion}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Aplicación por cuenta
# ═══════════════════════════════════════════════════════════════════════════════

def aplicar_reglas_de_cuenta(cliente, cuenta: dict, ids_nuevos=None) -> dict:
    """
    Aplica las reglas activas a los mensajes nuevos de UNA cuenta.

    `ids_nuevos` son los `Id` que este ciclo acaba de indexar. Si viene `None`
    (llamada manual desde el panel) se toman los últimos 200 sin procesar —
    NUNCA todo el índice: la primera vez que alguien activa una regla "archivar",
    aplicarla al backlog entero movería años de historial de la cuenta de golpe.

    Devuelve {evaluados, aplicadas, auto_respuestas}.
    """
    resumen = {"evaluados": 0, "aplicadas": 0, "auto_respuestas": 0}
    cid = cuenta["Id"]

    # Las reglas son POR USUARIO, no por cuenta: si tres personas comparten una
    # casilla, cada una filtra distinto. Y solo las de quienes tienen la cuenta
    # asignada: una regla de otra persona no se ejecuta en un buzón ajeno.
    reglas = filas(
        "SELECT r.Id, r.IdUsuario, r.Prioridad, r.Campo, r.Operador, r.Valor, "
        "       r.Accion, r.Etiqueta "
        "FROM HUB_MailboxReglas r "
        "INNER JOIN HUB_MailboxCuentasLinks l ON l.IdUsuario = r.IdUsuario "
        "WHERE l.IdCuenta = %s AND r.Activa = 1 "
        "ORDER BY r.IdUsuario, r.Prioridad, r.Id",
        (cid,),
    )
    if not reglas:
        return resumen

    candidatos = (_mensajes_por_id(ids_nuevos) if ids_nuevos
                  else filas(f"SELECT TOP (200) {_COLS} FROM HUB_MailboxMensajes "
                             f"WHERE IdCuenta = %s AND Eliminado = 0 "
                             f"ORDER BY FechaIngesta DESC", (cid,)))
    if not candidatos:
        return resumen

    # El destino de archivo se resuelve UNA vez por ciclo, no por mensaje: son dos
    # round trips IMAP por mensaje que se pueden hacer uno por ciclo.
    necesita_carpeta = any((r.get("Accion") or "").upper() in ("ARCHIVAR", "ELIMINAR")
                           for r in reglas)
    destino = ""
    if necesita_carpeta:
        # Archivado si existe; si no, papelera. Es mejor una regla "archivar"
        # que caiga a papelera que una regla que falle todos los días.
        destino = _carpeta_por_nombre(cliente, ("archivados", "archive", "papelera", "trash"))

    # La carpeta de spam se resuelve aparte y NO cae a un nombre inventado: si
    # el proveedor no tiene carpeta de spam, mover ahí crearía una carpeta
    # fantasma que el usuario no ve en su cliente y el correo "desaparecería"
    # de ECCSA. Preferimos que la regla no se aplique y seavise en el log.
    #
    # Los candidatos son en español e inglés porque el buzón de Hostinger está
    # en español ("Correo no deseado") y el de Gmail en inglés ("Spam").
    destino_spam = ""
    if any((r.get("Accion") or "").upper() == "SPAM" for r in reglas):
        destino_spam = _carpeta_por_nombre(cliente, (
            "spam", "junk", "junk e-mail", "correo no deseado", "no deseado",
            "desechados", "spam/quarantine",
        ))

    for mensaje in candidatos:
        resumen["evaluados"] += 1
        for regla in reglas:
            if not coincide(mensaje, regla):
                continue
            if aplicar_accion(cliente, cuenta, mensaje, regla, destino, destino_spam):
                resumen["aplicadas"] += 1
                _marcar_regla(regla)
            # Solo ETIQUETAR deja seguir evaluando: las demás mueven el mensaje
            # y la siguiente regla lo movería otra vez.
            if (regla.get("Accion") or "").upper() not in ("ETIQUETAR", "NO_HACER"):
                break

        if not mensaje.get("Eliminado"):
            resumen["auto_respuestas"] += _responder_automaticamente(cuenta, mensaje)

    return resumen


def _mensajes_por_id(ids) -> list:
    """
    Trae mensajes por id.

    Los ids vienen del sync (no del usuario) pero se filtran igual: el `Id` se
    mete en la consulta como lista de enteros. Sin este filtro, un id con texto
    dentro haría que la consulta fallara por todos los demás.
    """
    limpios = []
    for i in ids or ():
        try:
            limpios.append(int(i))
        except (TypeError, ValueError):
            continue
    if not limpios:
        return []
    marcas = ",".join(str(i) for i in limpios)
    return filas(f"SELECT {_COLS} FROM HUB_MailboxMensajes WHERE Id IN ({marcas})")


def _marcar_regla(regla: dict):
    """Contador de uso, para que la vista muestre qué reglas realmente sirven."""
    try:
        ejecuta("UPDATE HUB_MailboxReglas SET VecesEjecutada = VecesEjecutada + 1, "
                "UltimaEjecucion = GETDATE() WHERE Id = %s", (regla["Id"],))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Respuestas automáticas
# ═══════════════════════════════════════════════════════════════════════════════

def _responder_automaticamente(cuenta: dict, mensaje: dict) -> int:
    """
    Encola un auto-responder si toca. Devuelve 1 si encoló, 0 si no.
    """
    cid = cuenta["Id"]
    remite = (mensaje.get("RemitenteEmail") or "").strip()
    if not remite:
        return 0
    dominio = remite.split("@")[-1].lower()

    # ── Guarda 1: nunca a uno mismo ni al dominio propio ────────────────
    if remite.lower() in {c.lower() for c in correos_propios(cuenta)}:
        return 0
    if dominio in _DOMINIOS_PROPIOS:
        return 0

    respuestas = filas(
        "SELECT Id, IdUsuario, Mensaje, SoloFueraHorario, ExcepcionesDominio, EsDefault "
        "FROM HUB_MailboxRespuestasAuto "
        "WHERE Activa = 1 AND (IdCuenta = %s OR IdCuenta IS NULL)", (cid,))
    if not respuestas:
        return 0

    # ── Guarda 2: no en bucle ───────────────────────────────────────────
    mid = (mensaje.get("MessageId") or "").strip()
    if mid:
        ya = una("SELECT COUNT(*) AS n FROM HUB_MailboxColaEnvio "
                 "WHERE InResponderA = %s AND IdCuenta = %s", (mid, cid))
        if ya and int(ya.get("n") or 0) > 0:
            return 0

    # Específica de esta cuenta primero; si no, la predeterminada del usuario;
    # si no, la primera genérica. Un usuario puede tener varias y esta es la
    # prioridad documentada en la vista.
    elegible = (next((r for r in respuestas if r.get("IdCuenta") == cid), None)
                or next((r for r in respuestas if r.get("IdCuenta") is None
                         and r.get("EsDefault")), None)
                or next((r for r in respuestas if r.get("IdCuenta") is None), None))
    if not elegible:
        return 0

    # ── Guarda 3: exentos y horario ─────────────────────────────────────
    exentos = {d.strip().lower() for d in (elegible.get("ExcepcionesDominio") or "").split(",")
               if d.strip()}
    if dominio in exentos:
        return 0
    if elegible.get("SoloFueraHorario") and dentro_de_horario():
        return 0

    cuerpo = (elegible.get("Mensaje") or "").strip()
    if not cuerpo:
        return 0
    asunto = (mensaje.get("Asunto") or "").strip()

    html = (
        '<html><body style="font-family:sans-serif;font-size:14px;color:#222">'
        f'<p>{escapar(cuerpo).replace(chr(10), "<br>")}</p>'
        '<hr><p style="font-size:12px;color:#777">'
        f'Respuesta automática de {escapar(cuenta.get("Email") or "")}.</p>'
        "</body></html>"
    )
    texto = f"{cuerpo}\n\n-- \nRespuesta automática de {cuenta.get('Email') or ''}."

    try:
        # Sin `IdFirma`: un auto-responder con la firma del usuario parece un
        # correo de verdad, y eso es exactamente lo que no queremos.
        ejecuta(
            "INSERT INTO HUB_MailboxColaEnvio "
            "(IdCuenta, IdUsuario, Para, Asunto, HtmlSnapshot, TextoSnapshot, "
            " InResponderA, Estado, Creado) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDIENTE', GETDATE())",
            (cid, elegible["IdUsuario"], remite,
             (f"RE: {asunto}" if asunto else "Respuesta automática")[:500],
             html, texto, mid or None),
        )
        try:
            ejecuta("UPDATE HUB_MailboxRespuestasAuto SET UltimoUso = GETDATE() WHERE Id = %s",
                    (elegible["Id"],))
        except Exception:
            pass
        print(f"[filtros] auto-respuesta encolada a {remite}")
        return 1
    except Exception as exc:
        print(f"[filtros] no pude encolar la auto-respuesta: {exc}")
        return 0


def correos_propios(cuenta: dict) -> list:
    """Las direcciones que cuentan como "nosotros" para esta cuenta."""
    correos = {cuenta.get("Email") or ""}
    if cuenta.get("Alias"):
        correos.add(cuenta["Alias"])
    return [c for c in correos if c]


def dentro_de_horario() -> bool:
    """
    Horario de oficina: L-V de 9:00 a 18:00.

    Usa la hora del contenedor (la del servidor). Si el contenedor corre en UTC la
    ventana se desplaza y las respuestas "fuera de horario" salen a destiempo.

    Es una limitación conocida y deliberada: corregirla exige calcular contra
    `America/Mexico_City` con tz-aware, y para el caso de uso —no molestar
    mientras la gente está trabajando— un desvío de un par de horas es tolerable.
    Corregirla cuando alguém reporte que su auto-respuesta llegó a las 9:15 de
    un lunes siendo las 8:15 reales.
    """
    ahora = datetime.now()
    if ahora.weekday() >= 5:          # sábado, domingo
        return False
    return 9 <= ahora.hour < 18


def escapar(texto: str) -> str:
    """Escapa para meter texto del usuario dentro de HTML."""
    import html as _h
    return _h.escape(texto or "")