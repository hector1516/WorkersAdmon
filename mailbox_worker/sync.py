"""
mailbox_worker/sync.py — el motor de sincronización de Mailbox.

Qué hace, una vez por ciclo y por cuenta:

  1. Drena `HUB_MailboxColaOperaciones`: lo que el usuario hizo en la app
     (marcar leído, destacar, borrar, mover) y que hay que aplicar en IMAP.
  2. Baja los mensajes nuevos de cada carpeta y los mete en el ÍNDICE
     (`HUB_MailboxMensajes`), con su manifiesto de adjuntos.
  3. Aplica las reglas de filtrado del usuario.
  4. Drena `HUB_MailboxColaEnvio`: manda por SMTP lo que el usuario encoló.
  5. Aplica la retención.

─── LO QUE NO SE HACE, Y ES LA RAZÓN DE QUE ESTE ARCHIVO EXISTA ────────────

**Los adjuntos NO se descargan.** Nunca. El manifiesto sí (viene en el
BODYSTRUCTURE, que es un round trip chico y trae nombre, tamaño y tipo), pero
los BYTES no: se piden cuando el usuario abre el adjunto, a través del
servidor de streaming. Antes, `BODY[]` bajaba los adjuntos piggyback con cada
mensaje nuevo: una bandeja con un correo de 8 MB se descargaba entera cada
5 minutos.

**El cuerpo HTML tampoco se baja en el sync.** Se pide `RFC822.TEXT`, que es
solo el texto, y el HTML queda para cuando el usuario abre el mensaje: pesa
3-5 veces más y la mayoría nunca se abre.

**El texto plano sí se guarda en el índice** (columna `Extracto`). Es lo que
hace que la búsqueda funcione sin leer 100 mil cuerpos de disco.
"""

import hashlib
import os
import re
import time
from datetime import datetime, timedelta

from . import almacen
from .config import settings
from .crypto import decrypt_secret
from .db import ejecuta, filas, get_connection, una, ultimo_id
from .imap_client import IMAPClient, IMAPError

_EMAIL_RE = re.compile(r"[^\s<>,;]+@[^\s<>,;]+\.[A-Za-z]{2,}")

# Cuántos mensajes se traen por ciclo como máximo. El tope por cuenta manda
# sobre esto; esto es solo para no abrir un ciclo infinito en una cuenta con
# 40 mil mensajes sin leer.
_LOTE = 200

# Cada cuántos ciclos se vuelve a preguntar al buzón qué carpetas tiene.
_RELISTAR_CADA = 6           # con el ciclo de 300 s, ~30 minutos

# Máximos mensajes NUEVOS que se traen por carpeta y por ciclo. Un número de
# budget, no un tope por carpeta: así una carpeta con 50 mil mensajes nunca
# bloquea al worker, va vaciándose de a poco entre ciclos.
_PRESUPUESTO_CICLO = 600

# Veces que se ha sincronizado cada cuenta, en memoria del proceso.
_CICLOS: dict = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Cuentas
# ═══════════════════════════════════════════════════════════════════════════════

def cuentas_activas():
    """
    Cuentas que hay que sincronizar. Solo `ACTIVA`.

    Una cuenta en `PENDIENTE` es recién creada por el panel y todavía no se
    validó. Si se sincronizara con credenciales malas, cada ciclo intentaría
    conectar y fallaría. La valida `validar_pendientes` antes de promoverla.
    """
    return filas(
        "SELECT Id, Alias, Email, ServidorIMAP, PuertoIMAP, ServidorSMTP, PuertoSMTP, "
        "       TipoAuth, CredencialCifrada, UsarSSL, CarpetaRaiz, VentanaDias, MaxMensajes "
        "FROM HUB_MailboxCuentas WHERE Estado = 'ACTIVA' ORDER BY Id"
    )


def validar_pendientes():
    """
    Prueba las cuentas `PENDIENTE` y las promoter ACTIVA, o deja el error.

    Como mucho una vez cada 6 h por cuenta, para no gastarse el rate limit del
    proveedor con cuentas mal configuradas. El panel escribe el estado
    inicial; el worker no necesita que nadie le avise.
    """
    pendientes = filas(
        "SELECT Id, Email, ServidorIMAP, PuertoIMAP, TipoAuth, CredencialCifrada "
        "FROM HUB_MailboxCuentas WHERE Estado = 'PENDIENTE'"
    )
    ahora = datetime.now()
    for c in pendientes:
        previo = una("SELECT UltimoSync FROM HUB_MailboxCuentas WHERE Id = %s", (c["Id"],))
        if previo and previo.get("UltimoSync") and previo["UltimoSync"] > ahora - timedelta(hours=6):
            continue
        try:
            _abrir(c, probar=True)
            ejecuta(
                "UPDATE HUB_MailboxCuentas SET Estado = 'ACTIVA', UltimoError = NULL, "
                "UltimoSync = GETDATE() WHERE Id = %s", (c["Id"],))
            print(f"[sync] cuenta {c['Email']} validada y activada", flush=True)
        except IMAPError as exc:
            ejecuta(
                "UPDATE HUB_MailboxCuentas SET Estado = 'ERROR', UltimoError = %s, "
                "UltimoSync = GETDATE() WHERE Id = %s", (str(exc)[:500], c["Id"]))
            print(f"[sync] la cuenta {c['Email']} NO conectó: {exc}", flush=True)


def _abrir(cuenta: dict, probar: bool = False) -> IMAPClient:
    """
    Abre una sesión IMAP con la credencial DESCIFRADA.

    `decrypt_secret` LANZA si no encuentra la llave, y está bien que lo haga: si
    devolviera "" el worker conectaría con contraseña vacía, fallaría, y el
    síntoma sería "no llegan correos" sin relación aparente con la llave.
    """
    credencial = decrypt_secret(cuenta.get("CredencialCifrada") or "")
    if not credencial:
        raise IMAPError(f"la cuenta {cuenta.get('Email')} no tiene credencial guardada")

    cliente = IMAPClient(
        host=cuenta["ServidorIMAP"],
        port=cuenta.get("PuertoIMAP") or 993,
        username=cuenta["Email"],
        password=credencial,
        timeout=settings.imap_timeout,
        oauth2=(cuenta.get("TipoAuth") or "PASSWORD").upper() == "OAUTH2",
    )
    cliente.connect()
    if probar:
        cliente.close()
    return cliente


def _dejar_error(cuenta_id: int, exc):
    """Deja el error en la cuenta. La app lo muestra al usuario."""
    try:
        ejecuta("UPDATE HUB_MailboxCuentas SET UltimoError = %s WHERE Id = %s",
                (str(exc)[:500], cuenta_id))
    except Exception as e:
        print(f"[sync] no pude ni dejar el error: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Ciclo de una cuenta
# ═══════════════════════════════════════════════════════════════════════════════

def sincronizar_cuenta(cuenta: dict) -> dict:
    """
    Un ciclo completo de UNA cuenta. Devuelve un resumen para el heartbeat.

    Cada etapa aísla su excepción: que falle una carpeta no puede impedir que se
    sincronicen las otras, y que falle un mensaje no puede impedir el resto.
    """
    cid = cuenta["Id"]
    resumen = {"cuenta": cuenta.get("Email"), "nuevos": 0, "carpetas": 0,
               "operaciones": 0, "enviados": 0, "purgados": 0, "push": 0, "error": ""}
    inicio = time.time()
    cliente = None

    try:
        cliente = _abrir(cuenta)

        if not settings.dry_run:
            resumen["operaciones"] = aplicar_operaciones(cliente, cuenta)

        # Los ids de lo NUEVO de este ciclo. Las reglas se aplican solo a estos:
        # si se les pasara el backlog, la regla "archivar los de no-reply" movería
        # años de historial la primera vez que se activara, y volvería a mover los
        # mismos 200 mensajes en cada ciclo siguiente.
        nuevos_ids = []

        # UN `LIST` por ciclo y por cuenta. Es lo que hace aparecer las carpetas
        # que el usuario crea en Gmail/Hostinger. Va ANTES del bucle de sync:
        # si el listado cambia la lista de carpetas, este ciclo ya las incluye y
        # no hace falta esperar al siguiente para verlas como pestañas.
        #
        # Solo cada `_RELISTAR_CADA` ciclos. Un `LIST` es barato pero no gratis,
        # y el nombre de una carpeta no cambia cada 5 minutos.
        # El contador vive en el MÓDULO, no en el dict de la cuenta: ese dict se
        # relee de la base en cada ciclo, así que un contador guardado ahí se
        # reiniciaría a 1 siempre y nunca llegaría al umbral. Al reiniciar el
        # worker vuelve a 0, que es justo lo que se quiere: tras un redeploy se
        # pregunta de inmediato qué carpetas hay.
        _CICLOS[cuenta["Id"]] = _CICLOS.get(cuenta["Id"], 0) + 1
        ciclo_num = _CICLOS[cuenta["Id"]]
        if ciclo_num == 1 or ciclo_num % _RELISTAR_CADA == 0:
            cuantas = descubrir_carpetas(cliente, cuenta)
            if cuantas:
                print(f"[sync] {cuenta['Email']}: {cuantas} carpetas en el buzón "
                      f"(listado #{ciclo_num})", flush=True)

        pendientes = _carpetas(cuenta)
        for carpeta in _priorizar(pendientes, cuenta):
            try:
                ids, cuantos = _sincronizar_carpeta(cliente, cuenta, carpeta)
                nuevos_ids.extend(ids)
                resumen["nuevos"] += cuantos
                resumen["carpetas"] += 1
            except IMAPError as exc:
                # Una carpeta mal (borrada en el servidor, renombrada) no debe
                # tumbar la cuenta entera.
                print(f"[sync] {cuenta['Email']} / {carpeta}: {exc}", flush=True)
            if resumen["nuevos"] >= _PRESUPUESTO_CICLO:
                # Se acabó el presupuesto del ciclo. Las carpetas que queden
                # siguen en la cola del siguiente: nadie se salta nada, solo se
                # reparte el trabajo. Sin esto, encender 15 carpetas en una
                # cuenta con años de correo deja al worker horas sin cerrar el
                # ciclo y el resto de las cuentas se atrasa.
                print(f"[sync] {cuenta['Email']}: presupuesto del ciclo "
                      f"({_PRESUPUESTO_CICLO}) alcanzado, el resto va al "
                      f"próximo ciclo", flush=True)
                break

        if not settings.dry_run:
            from .filtros import aplicar_reglas_de_cuenta
            try:
                resumen["reglas"] = aplicar_reglas_de_cuenta(cliente, cuenta,
                                                             nuevos_ids or None)
            except Exception as exc:
                print(f"[sync] reglas de {cuenta['Email']}: {exc}", flush=True)

            from .smtp import enviar_cola
            try:
                resumen["enviados"] = enviar_cola(cuenta)
            except Exception as exc:
                print(f"[sync] envío de {cuenta['Email']}: {exc}", flush=True)

            try:
                resumen["purgados"] = _retencion(cuenta)
            except Exception as exc:
                print(f"[sync] retención de {cuenta['Email']}: {exc}", flush=True)

            # El push va AL FINAL y UNA VEZ por ciclo. Mandarlo por mensaje
            # satura la pantalla de bloqueo: con 20 correos nuevos en un ciclo, 20
            # pushes hacen que el usuario apague las notificaciones de la app en
            # dos días. Un "y 19 más" dice lo mismo y se lee una vez.
            if resumen["nuevos"] > 0:
                try:
                    from .push import avisar_nuevos_de_cuenta
                    resumen["push"] = avisar_nuevos_de_cuenta(
                        cuenta, _remitente_de_cuenta(cuenta), "", resumen["nuevos"])
                except Exception as exc:
                    print(f"[sync] push de {cuenta['Email']}: {exc}", flush=True)

    except Exception as exc:
        resumen["error"] = f"{type(exc).__name__}: {exc}"[:250]
        _dejar_error(cid, exc)
    finally:
        if cliente is not None:
            try:
                cliente.close()
            except Exception:
                pass

    if not resumen["error"]:
        try:
            ejecuta("UPDATE HUB_MailboxCuentas SET UltimoSync = GETDATE(), "
                    "UltimoError = NULL WHERE Id = %s", (cid,))
        except Exception:
            pass

    resumen["segundos"] = round(time.time() - inicio, 1)
    return resumen


# Carpetas del sistema: existen en todos los buzones y ninguna se lee desde la
# app de correo. Se detectan por los FLAGS que declara el servidor (`\Trash`,
# `\Junk`, `\Drafts`), NO por el nombre: en Gmail se llaman "[Gmail]/Spam" y en
# Hostinger "Correo no deseado", y una lista de nombres sería medio diccionario
# que además falla en español, en inglés y en lo que invente el siguiente
# proveedor.
_FLAG_SISTEMA = ("\\trash", "\\junk", "\\spam", "\\drafts", "\\archive", "\\all")


def es_carpeta_sistema(nombre: str, flags: str) -> bool:
    r"""
    ¿Es una carpeta del sistema, según lo que declara el servidor?

    Además de los flags, se reconoce INBOX y Sent por nombre porque son las dos
    que el usuario sí lee y por tanto sí se sincronizan siempre.

    `\Archive` y `\All` se tratan como del sistema a propósito: "Todos los
    mensajes" de Gmail tiene años de historial y sincronizarlo son millones de
    filas. Si el usuario lo quiere, se enciende desde la app.
    """
    f = (flags or "").lower()
    if nombre.upper() in ("INBOX", "SENT"):
        return False
    if any(marca in f for marca in _FLAG_SISTEMA):
        return True
    return False


def descubrir_carpetas(cliente, cuenta: dict) -> int:
    """
    Registra las carpetas REALES del buzon en el catalogo.

    Esto es lo que hace que "Newsletters" o "Clientes" aparezcan como pestañas:
    el usuario las crea en Gmail, no en esta app, y si nadie pregunta al
    servidor qué carpetas existen, ECCSA no se entera.

    Se hace un `LIST` (barato, un round trip) y se actualiza el catálogo con
    UPSERT: lo que no se hace es `STATUS` por carpeta, porque son N round trips y
    `STATUS` de una carpeta no aporta nada que el próximo ciclo no vuelva a
    decir. El contador sin leer sale del propio índice una vez que la carpeta se
    sincroniza.
    """
    cid = cuenta["Id"]
    try:
        carpetas = cliente.list_folders()
    except Exception as exc:
        print(f"[sync] {cuenta['Email']}: LIST falló ({exc})", flush=True)
        return 0

    for nombre, flags in carpetas:
        sistema = es_carpeta_sistema(nombre, flags)
        try:
            # UPSERT. `DelSistema` se recalcula en cada listado porque una
            # carpeta que el usuario renombró deja de ser la de papelera, y
            # `Sincronizar` NO se toca: es la decisión del usuario y un LIST no
            # tiene por qué sobrescribirla.
            #
            # Una carpeta NUEVA entra con `Sincronizar = 0`: aparece como
            # pestaña, se ve cuántos correos tiene, y el usuario decide si entra.
            #
            # Encenderla sola sería un desastre de Costs: la cuenta 4 tiene 646
            # carpetas y `robot@` tiene "INBOX/IT" con 3 800 mensajes. Con la
            # ventana de 90 días, la primera vuelta de una carpeta grande NO se
            # termina, y como el trabajo se reparte con un presupuesto, esa
            # carpeta se reprocesa eternamente sin avanzar. Con el default
            # apagado el usuario enciende las tres o cuatro que usa, que es lo
            # que va a usar.
            ejecuta(
                "IF EXISTS (SELECT 1 FROM HUB_MailboxCarpetas "
                "            WHERE IdCuenta = %s AND Nombre = %s) "
                "   UPDATE HUB_MailboxCarpetas SET DelSistema = %s, UltimaListado = GETDATE() "
                "    WHERE IdCuenta = %s AND Nombre = %s; "
                "ELSE INSERT INTO HUB_MailboxCarpetas (IdCuenta, Nombre, DelSistema, Sincronizar, UltimaListado) "
                "     VALUES (%s, %s, %s, 0, GETDATE());",
                (cid, nombre, 1 if sistema else 0, cid, nombre,
                 cid, nombre, 1 if sistema else 0))
        except Exception as exc:
            print(f"[sync] {cuenta['Email']}/{nombre}: catálogo ({exc})", flush=True)

    # `STATUS` de las carpetas del catálogo: cuántos mensajes hay en el buzón y
    # cuántos sin leer. NO descarga contenido, es un comando por carpeta.
    #
    # Solo para las carpetas que el usuario tiene a la vista: preguntar por la
    # papelera y por "Todos los mensajes" son round trips que no cambian nada de
    # lo que la app muestra.
    try:
        visibles = [f["Nombre"] for f in filas(
            "SELECT Nombre FROM HUB_MailboxCarpetas "
            "WHERE IdCuenta = %s AND DelSistema = 0", (cid,))]
    except Exception:
        visibles = [n for n, _f in carpetas]

    for nombre in visibles:
        try:
            st = cliente.status_folder(nombre)
            if st:
                ejecuta("UPDATE HUB_MailboxCarpetas SET TotalEnBuzon = %s, "
                        "NoLeidosEnBuzon = %s WHERE IdCuenta = %s AND Nombre = %s",
                        (st.get("total"), st.get("no_leidos"), cid, nombre))
        except Exception as exc:
            # Una carpeta que disappeared del buzón no puede tumbar el listado.
            print(f"[sync] {cuenta['Email']}/{nombre}: STATUS ({exc})", flush=True)

    return len(carpetas)


def _priorizar(carpetas: list, cuenta: dict) -> list:
    """
    Ordena las carpetas para que el trabajo importante no espere.

    La carpeta raíz primero, y después las que MENOS mensajes indexados tenga:
    es una cola de Constructor. Con presupuesto por ciclo, si las carpetas
    grandes van siempre de últimas, "Newsletters" con 300 mensajes nunca se
    sincroniza porque cada ciclo se agota el presupuesto en "Todos los mensajes".
    """
    raiz = cuenta.get("CarpetaRaiz") or "INBOX"

    def tamano(nombre):
        if nombre == raiz:
            return -1                      # la raíz, siempre primera
        fila = una("SELECT COUNT(*) AS Total FROM HUB_MailboxMensajes "
                   "WHERE IdCuenta = %s AND Carpeta = %s", (cuenta["Id"], nombre))
        return int(fila["Total"]) if fila else 0

    try:
        return sorted(carpetas, key=tamano)
    except Exception:
        return carpetas


def _carpetas(cuenta: dict) -> list:
    r"""
    Las carpetas que se sincronizan en este ciclo.

    Antes era una sola: la raíz. Ahora son las marcadas `Sincronizar = 1` en el
    catálogo, que es donde el usuario decide cuáles traen.

    La raíz va SIEMPRE, aunque alguien la apagara por error: es la carpeta de la
    que no se puede depender de que esté en ningún lado.
    """
    raiz = cuenta.get("CarpetaRaiz") or "INBOX"
    try:
        marcadas = [f["Nombre"] for f in filas(
            "SELECT Nombre FROM HUB_MailboxCarpetas "
            "WHERE IdCuenta = %s AND Sincronizar = 1 ORDER BY Nombre",
            (cuenta["Id"],))]
    except Exception as exc:
        print(f"[sync] {cuenta['Id']}: catálogo no disponible ({exc})", flush=True)
        marcadas = []

    carpetas = []
    for nombre in [raiz] + marcadas:
        if nombre not in carpetas:
            carpetas.append(nombre)
    return carpetas


# ═══════════════════════════════════════════════════════════════════════════════
# Sincronización de una carpeta
# ═══════════════════════════════════════════════════════════════════════════════

def _sincronizar_carpeta(cliente: IMAPClient, cuenta: dict, carpeta: str) -> tuple:
    """
    Trae lo que falta de UNA carpeta. Devuelve `(ids_nuevos, cuantos)`.

    Tres pasos, ordenados por costo:
      1. `UID SEARCH SINCE <fecha>` → los UIDs candidatos. Un round trip.
      2. Los que no estén en el índice → `RFC822.TEXT` (texto, SIN adjuntos).
      3. Por cada nuevo → `BODYSTRUCTURE` (manifiesto, SIN los bytes).

    Los ids que devuelve son los que después evalúan las reglas: si no se
    devolvieran, las reglas correrían sobre el backlog y moverían en bucle los
    mismos mensajes en cada ciclo.
    """
    cid = cuenta["Id"]
    cliente.select_folder(carpeta)

    desde = _fecha_desde(cuenta)
    uids = cliente.fetch_uid_list(["SINCE", desde])
    if not uids:
        return [], 0

    tope = min(int(cuenta.get("MaxMensajes") or 5000), _LOTE)
    uids = sorted(uids)[-tope:]
    if not uids:
        return [], 0

    # Los UIDs que ya tenemos. Solo se preguntan los del rango que viene, con
    # TOP: pedir los 40 mil de una cuenta grande sería traer más de lo
    # necesario.
    ya = {
        int(f["UID"]) for f in filas(
            "SELECT TOP (%s) UID FROM HUB_MailboxMensajes "
            "WHERE IdCuenta = %s AND Carpeta = %s AND UID >= %s",
            (tope * 2, cid, carpeta, uids[0]))
    }
    faltan = [u for u in uids if u not in ya]
    if not faltan:
        return [], 0

    ids = []
    for uid in faltan:
        try:
            nuevo_id = _indexar(cliente, cuenta, carpeta, uid)
            if nuevo_id:
                ids.append(nuevo_id)
        except IMAPError as exc:
            print(f"[sync] {cuenta['Email']}/{carpeta}/{uid}: {exc}", flush=True)
        except Exception as exc:
            # Un MIME inválido no puede parar la carpeta entera.
            print(f"[sync] {cuenta['Email']}/{carpeta}/{uid}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    return ids, len(ids)


def _fecha_desde(cuenta: dict) -> str:
    """Desde qué fecha buscar. La ventana de retención de la cuenta manda."""
    dias = int(cuenta.get("VentanaDias") or 90)
    return (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")


def _indexar(cliente: IMAPClient, cuenta: dict, carpeta: str, uid: int):
    """
    Trae un mensaje al índice. Devuelve el `Id` nuevo, o None si ya existía.

    Pide el texto (sin adjuntos), las cabeceras (remitente, asunto, fecha) y la
    estructura MIME (manifiesto de adjuntos, sin sus bytes).
    """
    cid = cuenta["Id"]

    texto = cliente.cuerpo_texto(uid)
    cabeceras = cliente.fetch_encabezados(uid)
    adjuntos = cliente.estructura(uid).get("adjuntos") or []

    # La clave del cuerpo es un hash de (cuenta, carpeta, uid). Si el mismo
    # mensaje se re-indexa, se sobrescribe el archivo en vez de acumular copias.
    clave = hashlib.sha1(f"{cid}:{carpeta}:{uid}".encode("utf-8")).hexdigest()[:24]
    cuerpo_guardado = False
    cuerpo_truncado = False

    if not settings.dry_run:
        html = cliente.cuerpo_html(uid)
        if html and almacen.guardar_cuerpo(cid, clave, html):
            cuerpo_guardado = True

    _id_nuevo = _insertar(
        cuenta=cuenta, carpeta=carpeta, uid=uid, clave=clave,
        cabeceras=cabeceras,
        extracto=_a_una_linea(texto),
        bytes_cuerpo=len((texto or "").encode("utf-8")),
        adjuntos=adjuntos,
        cuerpo_guardado=cuerpo_guardado,
        cuerpo_truncado=cuerpo_truncado,
    )
    return _id_nuevo


def _insertar(cuenta, carpeta, uid, clave, cabeceras, extracto, bytes_cuerpo,
              adjuntos, cuerpo_guardado, cuerpo_truncado):
    """Devuelve el `Id` del mensaje: el nuevo si se insertó, None si se actualizó."""
    """
    Inserta el mensaje y su manifiesto de adjuntos.

    Es un UPSERT por (cuenta, carpeta, UID), que es UNIQUE en la base. SQL
    Server 2014 no tiene ON DUPLICATE KEY: se intenta el UPDATE y, si no hubo
    filas, el INSERT. MERGE haría lo mismo en una sentencia, pero con triggers
    es notoriously difícil de depurar.
    """
    cid = cuenta["Id"]
    fecha = cabeceras.get("date") or datetime.now()

    n = ejecuta(
        "UPDATE HUB_MailboxMensajes SET RemitenteNombre = %s, RemitenteEmail = %s, "
        "ParaTexto = %s, CcTexto = %s, Asunto = %s, Extracto = %s, FechaCorreo = %s, "
        "Visto = %s, MessageId = %s, BytesCuerpo = %s, CuerpoGuardado = %s "
        "WHERE IdCuenta = %s AND Carpeta = %s AND UID = %s",
        (cabeceras.get("from_name"), cabeceras.get("from_email"),
         cabeceras.get("to"), cabeceras.get("cc"), cabeceras.get("subject"),
         extracto, fecha, 1 if cabeceras.get("seen") else 0,
         cabeceras.get("message_id"), bytes_cuerpo,
         1 if cuerpo_guardado else 0, cid, carpeta, uid),
    )
    if n > 0:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO HUB_MailboxMensajes "
        "(IdCuenta, UID, MessageId, Carpeta, RemitenteNombre, RemitenteEmail, ParaTexto, "
        " CcTexto, Asunto, Extracto, FechaCorreo, FechaIngesta, Visto, Marcado, Respondido, "
        " TieneAdjuntos, NumAdjuntos, ClaveCuerpo, BytesCuerpo, CuerpoGuardado, CuerpoTruncado, "
        " Eliminado) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, GETDATE(), %s, 0, 0, %s, %s, "
        "        %s, %s, %s, %s, 0)",
        (cid, uid, cabeceras.get("message_id"), carpeta,
         cabeceras.get("from_name"), cabeceras.get("from_email"),
         cabeceras.get("to"), cabeceras.get("cc"), cabeceras.get("subject"),
         extracto, fecha, 1 if cabeceras.get("seen") else 0,
         1 if adjuntos else 0, len(adjuntos), clave, bytes_cuerpo,
         1 if cuerpo_guardado else 0, 1 if cuerpo_truncado else 0),
    )
    nuevo_id = ultimo_id()
    cur.close()

    for a in adjuntos:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO HUB_MailboxAdjuntos "
                "(IdMensaje, Parte, Nombre, ContentType, Cid, Size, EsInline, InlineGuardado) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 0)",
                (nuevo_id, a["parte"], a["nombre"],
                 (a.get("tipo") or "").lower() or None, a.get("cid") or None,
                 int(a.get("size") or 0), 1 if a.get("cid") else 0),
            )
        except Exception as exc:
            # La clave única es (IdMensaje, Parte). Si BODYSTRUCTURE declaró dos
            # partes con el mismo número (algunos servidores con multiparte
            # raro), la segunda se salta y se sigue. Perder una fila del
            # manifiesto es mejor que perder el mensaje entero.
            print(f"[sync] adjunto {a.get('parte')} no se pudo guardar: {exc}", flush=True)
        cur.close()

    return nuevo_id


def _a_una_linea(texto: str, limite: int = 480) -> str:
    """Colapsa a una línea y quita los espacios repetidos."""
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).strip()[:limite]


def _remitente_de_cuenta(cuenta: dict) -> str:
    """
    Quién escribió el último correo, para el push.

    El push NO lleva el asunto: eso se vería en la pantalla de bloqueo del
    iPhone sin abrir el correo. Solo el remitente, que es justo lo que hace falta
    para decidir si se abre.
    """
    fila = una(
        "SELECT TOP (1) ISNULL(RemitenteNombre, RemitenteEmail) AS R "
        "FROM HUB_MailboxMensajes WHERE IdCuenta = %s AND Eliminado = 0 "
        "ORDER BY FechaCorreo DESC", (cuenta["Id"],))
    return ((fila or {}).get("R") or "").strip()


def correos_en(texto: str) -> list:
    """Direcciones de un texto (Para/Cc)."""
    return _EMAIL_RE.findall(texto or "")


# ═══════════════════════════════════════════════════════════════════════════════
# Operaciones encoladas por la app
# ═══════════════════════════════════════════════════════════════════════════════

def aplicar_operaciones(cliente: IMAPClient, cuenta: dict) -> int:
    """
    Aplica en IMAP lo que el usuario hizo en la app.

    Es el otro lado del patrón: la app escribe la fila y el estado local ya
    cambió, el worker la ejecuta contra el buzón. El botón se ve pulsado al
    instante sin esperar al ciclo.

    Una operación con tipo desconocido se marca ERROR, NO "hecha". El worker
    viejo las marcaba todas `done` en silencio: pérdida de datos disfrazada de
    éxito.
    """
    cid = cuenta["Id"]
    pendientes = filas(
        "SELECT Id, IdMensaje, Operacion, Valor FROM HUB_MailboxColaOperaciones "
        "WHERE IdCuenta = %s AND Estado = 'PENDIENTE' ORDER BY Creado", (cid,))
    if not pendientes:
        return 0

    aplicadas = 0
    for op in pendientes:
        tipo = (op["Operacion"] or "").lower()

        # Las operaciones de CUENTA se resuelven antes de buscar el mensaje:
        # crear una carpeta llega con IdMensaje NULL, así que el SELECT de abajo
        # no encontraría nada y la marcaría como ERROR sin llegar a intentarlo.
        if tipo == "crear_carpeta":
            try:
                creada = cliente.create_folder(op["Valor"])
                # Se registra aunque esté VACÍA. Si no, la carpeta existe en el
                # correo del usuario pero no aparece en las pestañas hasta que
                # alguien le mueva un mensaje, y para moverle algo hay que ver
                # la carpeta: no se puede usar lo que no aparece.
                _registrar_carpeta(op["IdCuenta"], creada, sistema=0)
                _cerrar_operacion(op["Id"], "APLICADA", "")
                aplicadas += 1
            except IMAPError as exc:
                _reintentar_operacion(op, exc)
            continue

        if not op["IdMensaje"]:
            _cerrar_operacion(op["Id"], "ERROR", "operación sin mensaje")
            continue

        mensaje = una("SELECT UID, Carpeta FROM HUB_MailboxMensajes WHERE Id = %s",
                      (op["IdMensaje"],))
        if not mensaje:
            _cerrar_operacion(op["Id"], "ERROR", "el mensaje ya no está en el índice")
            continue

        try:
            if tipo not in ("seen", "flag", "delete", "move"):
                _cerrar_operacion(op["Id"], "ERROR", f"operación desconocida: {tipo}")
                continue

            cliente.select_folder(mensaje["Carpeta"])
            if tipo == "seen":
                cliente.set_flag(mensaje["UID"], "\\Seen", (op["Valor"] or "1") != "0")
            elif tipo == "flag":
                cliente.set_flag(mensaje["UID"], "\\Flagged", (op["Valor"] or "1") != "0")
            elif tipo == "delete":
                cliente.delete_message(mensaje["UID"])
                ejecuta("UPDATE HUB_MailboxMensajes SET Eliminado = 1 WHERE Id = %s",
                        (op["IdMensaje"],))
            else:
                cliente.move_message(mensaje["UID"], op["Valor"])
                ejecuta("UPDATE HUB_MailboxMensajes SET Carpeta = %s WHERE Id = %s",
                        (op["Valor"], op["IdMensaje"]))

            _cerrar_operacion(op["Id"], "APLICADA", "")
            aplicadas += 1
        except IMAPError as exc:
            _reintentar_operacion(op, exc)

    return aplicadas


def _registrar_carpeta(cuenta_id: int, nombre: str, sistema: int = 0):
    """Registra la carpeta en el catálogo, si no está. Nunca falla la sync."""
    if not nombre:
        return
    try:
        ejecuta("IF NOT EXISTS (SELECT 1 FROM HUB_MailboxCarpetas "
                "WHERE IdCuenta = %s AND Nombre = %s) "
                "INSERT INTO HUB_MailboxCarpetas (IdCuenta, Nombre, DelSistema) "
                "VALUES (%s, %s, %s)",
                (cuenta_id, nombre, cuenta_id, nombre, 1 if sistema else 0))
    except Exception:
        # La tabla puede no existir todavía (migración sin aplicar). Perder el
        # catálogo de carpetas no puede tumbar la sincronización.
        pass


def _cerrar_operacion(op_id: int, estado: str, error: str):
    ejecuta("UPDATE HUB_MailboxColaOperaciones SET Estado = %s, Error = %s, "
            "Aplicado = GETDATE() WHERE Id = %s", (estado, error or None, op_id))


def _reintentar_operacion(op: dict, exc: Exception):
    """
    Deja la operación PENDIENTE para el siguiente ciclo, hasta 5 intentos.

    A los 5 se abandona con ERROR. Si no se abandonara, una carpeta renombrada
    en el servidor reintentaría para siempre y llenaría el log.
    """
    fila = una("SELECT Intentos FROM HUB_MailboxColaOperaciones WHERE Id = %s", (op["Id"],))
    intentos = int((fila or {}).get("Intentos") or 0) + 1
    if intentos >= 5:
        _cerrar_operacion(op["Id"], "ERROR", f"5 intentos fallidos: {exc}"[:400])
    else:
        ejecuta("UPDATE HUB_MailboxColaOperaciones SET Intentos = %s, Error = %s WHERE Id = %s",
                (intentos, str(exc)[:400], op["Id"]))


# ═══════════════════════════════════════════════════════════════════════════════
# Retención
# ═══════════════════════════════════════════════════════════════════════════════

def _retencion(cuenta: dict) -> int:
    """
    Purga lo viejo, en dos cortes porque pesan distinto:

      · Cuerpo en disco → a los 90 días. El mensaje sigue en el índice pero sin
        contenido: se puede buscar y ver de quién era. Se marca
        `CuerpoTruncado` para que la vista diga "ya se purgó" y no un error.
      · El mensaje entero → a los 365 días.

    Los inline que ya no tienen mensaje se limpian aquí: el ON DELETE CASCADE
    de SQL Server se lleva la fila de `HUB_MailboxAdjuntos` pero no los bytes
    de disco, y sin esto el volumen crecería para siempre.
    """
    cid = cuenta["Id"]
    purgados = 0

    corte_cuerpo = datetime.now() - timedelta(hours=settings.horas_cuerpo)
    for f in filas(
        "SELECT TOP (%s) Id, ClaveCuerpo FROM HUB_MailboxMensajes "
        "WHERE IdCuenta = %s AND FechaCorreo < %s AND CuerpoGuardado = 1",
        (500, cid, corte_cuerpo),
    ):
        if f.get("ClaveCuerpo"):
            almacen.borrar_cuerpo(cid, f["ClaveCuerpo"])
        ejecuta("UPDATE HUB_MailboxMensajes SET CuerpoGuardado = 0, CuerpoTruncado = 1, "
                "ClaveCuerpo = NULL WHERE Id = %s", (f["Id"],))
        purgados += 1

    corte_indice = datetime.now() - timedelta(days=settings.dias_indice)
    for f in filas(
        "SELECT TOP (%s) Id FROM HUB_MailboxMensajes WHERE IdCuenta = %s AND FechaCorreo < %s",
        (500, cid, corte_indice),
    ):
        # El ON DELETE CASCADE borra HUB_MailboxAdjuntos.
        ejecuta("DELETE FROM HUB_MailboxMensajes WHERE Id = %s", (f["Id"],))
        purgados += 1

    return purgados
