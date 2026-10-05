"""
mailbox_worker/push.py — notificaciones WebPush.

Es el ÚNICO que envía push. La app registra suscripciones (lo hace el service
worker del navegador, no el usuario), pero no envía: si lo hiciera, un push
disparado por una sesión web moraría con esa sesión.

─── DÓNDE ESTÁ LA LLAVE VAPID, Y POR QUÉ IMPORTA ──────────────────────────────

La clave privada VAPID tiene que ser la MISMA que se le dio al service worker de
la app. Si no coinciden, el navegador rechaza cada push con un error de
suscripción que no dice "la llave está mal": dice algo sobre la firma, que es el
mensaje más difícil de diagnosticar que existe en WebPush.

Por eso las dos la leen de la misma env var (`MAILBOX_VAPID_PRIVATE`), y el
`aviso_arranque()` del worker avisa si falta.

─── EL 404/410 ES EL CASO NORMAL, NO UN ERROR ─────────────────────────────────

Cuando el navegador de un usuario borra la PWA, o el sistema la descarga, el
push service responde **404 Gone** o **410 Gone**. Eso no es un fallo: es la
notificación de que esa suscripción ya no existe. La fila se marca `Activo=0`.

Si no se limpia, la lista de suscripciones crece para siempre y cada correo
intenta entregar push a endpoints muertos, y cada intento muerto es una llamada
HTTP con timeout que retrasa el sync de todos los usuarios.

En iOS esto pasa más: el sistema libera las suscripciones de las PWA que no se
abren en semanas. Por eso `UltimoUso` se actualiza en cada entrega y hay una
purga de suscripciones que no se han usado en mucho.
"""

import json
import logging
from datetime import datetime, timedelta

from .config import settings
from .db import ejecuta, filas, una

log = logging.getLogger("push")

# Una suscripción que no se ha usado en esto se da por muerta. Un iPhone que no
# abre la app en 60 días es un teléfono dado de baja para efectos de push.
_SUSCRIPCION_MUERTA_DIAS = 60

# Tope de suscripciones por entrega. Con 5 dispositivos es más que suficiente y
# evita que un usuario con 50 suscripciones (cada `subscribe` duplicado que se
# coló) retrase a todos los demás.
_MAX_SUSCRIPCIONES = 8


def _vapid_claims() -> dict:
    """
    El `sub` de los claims VAPID tiene que ser un `mailto:` o una URL HTTPS.

    Es el contacto que el push service muestra si un usuario pregunta quién le
    manda notificaciones. Se usa el mismo dominio que la app; no se pone la
    dirección de una persona porque es un dato que no hace falta exponer.
    """
    return {"sub": settings.vapid_subject}


def _listo() -> bool:
    """
    ¿Se puede enviar push?

    No: sin llave VAPID no hay push. Se avisa UNA vez por ciclo, no por intento,
    porque sin esto el log se llenaría de la misma línea en cada correo.
    """
    if settings.vapid_private_key:
        return True
    log.warning("[push] sin MAILBOX_VAPID_PRIVATE: no se envia ninguna notificacion")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Suscripciones
# ═══════════════════════════════════════════════════════════════════════════════

def registrar(id_usuario: int, endpoint: str, p256dh: str, auth: str,
              plataforma: str = "") -> bool:
    """
    Registra (o refresca) la suscripción de un dispositivo.

    Es un UPSERT por el hash del endpoint. SQL Server 2014 no tiene
    `ON DUPLICATE KEY`, así que se hace UPDATE y, si no tocó filas, INSERT. El
    hash es una columna CALCULADA persistida, así que el índice único sobre él
    impide duplicados aunque dos peticiones lleguen a la vez.

    El refresco de `Activo=1` importa: si el navegador re-suscribe un endpoint que
    estaba marcado muerto, hay que volver a activarlo, no ignorarlo.
    """
    if not endpoint or not p256dh or not auth:
        return False
    # El endpoint lo elige el push service, no el usuario, pero un INSERT mal
    # formado aquí rompería la columna calculada del hash. Se acota y se filtra.
    endpoint = endpoint.strip()[:2048]
    if not endpoint.startswith("https://"):
        log.warning("[push] endpoint que no es https, se ignora")
        return False

    try:
        n = ejecuta(
            "UPDATE HUB_MailboxSuscripciones SET IdUsuario = %s, P256dhKey = %s, "
            "AuthKey = %s, Plataforma = %s, Activo = 1, UltimoUso = GETDATE() "
            "WHERE EndpointHash = CONVERT(BINARY(32), HASHBYTES('SHA2_256', %s))",
            (id_usuario, p256dh, auth, (plataforma or "")[:20], endpoint),
        )
        if n > 0:
            return True
        ejecuta(
            "INSERT INTO HUB_MailboxSuscripciones "
            "(IdUsuario, Endpoint, P256dhKey, AuthKey, Plataforma, Activo, UltimoUso) "
            "VALUES (%s, %s, %s, %s, %s, 1, GETDATE())",
            (id_usuario, endpoint, p256dh, auth, (plataforma or "")[:20]),
        )
        return True
    except Exception as exc:
        # Una carrera entre dos `subscribe` del mismo dispositivo puede pegarle
        # al índice único. El otro INSERT ya dejó la fila: no es un error.
        log.warning("[push] no pude registrar la suscripción: %s", exc)
        return False


def dar_de_baja(endpoint: str) -> None:
    """Marca la suscripción como inactiva en vez de borrarla.

    Se conserva la fila y se desactiva. Borrarla sería más limpio, pero el
    `Endpoint` se guarda para diagnóstico ("¿por qué dejó de llegar?") y una
    tabla que crece solo con lo que funciona es más fácil de auditar.
    """
    try:
        ejecuta("UPDATE HUB_MailboxSuscripciones SET Activo = 0 WHERE Endpoint = %s",
                (endpoint,))
    except Exception as exc:
        log.warning("[push] no pude dar de baja la suscripción: %s", exc)


def purgar_muertas() -> int:
    """
    Desactiva las suscripciones que no se han usado en `_SUSCRIPCION_MUERTA_DIAS`.

    Corre una vez por ciclo. Es la red que sigue al 404/410: pasa meses sin que
    el push service avise, y son suscripciones de usuarios que cerraron sesión
    hace meses y nunca volvieron. Mandarles push es gastar una llamada HTTP con
    timeout por cada correo, una y otra vez, sin que nadie las lea.
    """
    try:
        n = ejecuta(
            "UPDATE HUB_MailboxSuscripciones SET Activo = 0 "
            "WHERE Activo = 1 AND (UltimoUso IS NULL OR UltimoUso < %s)",
            (datetime.now() - timedelta(days=_SUSCRIPCION_MUERTA_DIAS),),
        )
        if n:
            log.info("[push] %s suscripción(es) inactiva(s) por no usarse", n)
        return n or 0
    except Exception as exc:
        log.warning("[push] no pude purgar las suscripciones: %s", exc)
        return 0


def _suscripciones_de(id_usuario: int) -> list:
    return filas(
        "SELECT TOP (%s) Endpoint, P256dhKey, AuthKey FROM HUB_MailboxSuscripciones "
        "WHERE IdUsuario = %s AND Activo = 1 ORDER BY UltimoUso DESC",
        (_MAX_SUSCRIPCIONES, id_usuario),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Envío
# ═══════════════════════════════════════════════════════════════════════════════

def avisar_a_usuario(id_usuario: int, titulo: str, cuerpo: str, url: str = "/",
                     tag: str = "", contador: int = 0) -> int:
    """
    Manda un push a todos los dispositivos de un usuario. Devuelve cuántos
    recibieron.

    `tag` y `contador` no son adorno: en iOS y Android, dos notificaciones con el
    MISMO tag se reemplazan en la pantalla de bloqueo. Para "correo nuevo" eso es
    lo correcto (muestra 1, no 50), y el contador se muestra como "×3".
    """
    if not _listo() or not id_usuario:
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("[push] pywebpush no está instalado: no hay push")
        return 0

    subs = _suscripciones_de(id_usuario)
    if not subs:
        return 0

    carga = {"title": titulo, "body": cuerpo, "url": url or "/"}
    if tag:
        carga["tag"] = tag
    if contador:
        carga["count"] = contador
    payload = json.dumps(carga, ensure_ascii=False).encode("utf-8")

    entregadas = 0
    for sub in subs:
        endpoint = sub["Endpoint"]
        try:
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": sub["P256dhKey"], "auth": sub["AuthKey"]},
                },
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims=_vapid_claims(),
                timeout=10,
            )
            entregadas += 1
            # `UltimoSolo` se refresca en cada entrega para que la purga no mate
            # la suscripción de un usuario que sí recibe (típico de escritorio).
            _marcar_uso(endpoint)

        except WebPushException as exc:
            codigo = getattr(getattr(exc, "response", None), "status_code", None)
            if codigo in (404, 410):
                # La suscripción ya no existe. Es el caso NORMAL, no un fallo.
                dar_de_baja(endpoint)
                log.info("[push] suscripción dado de baja (HTTP %s)", codigo)
            elif codigo in (401, 403):
                # La llave VAPID no coincide con la del service worker de la app.
                # Es un error de configuración, no del usuario: por eso NO se
                # da de baja la suscripción (esa sí sirve) y se avisa en log.
                log.error("[push] HTTP %s: la llave VAPID no coincide con la del "
                          "service worker de la app", codigo)
            else:
                log.warning("[push] falló a %s: %s", endpoint[:70], exc)
        except Exception as exc:
            log.warning("[push] error a %s: %s", endpoint[:70], exc)

    return entregadas


def _marcar_uso(endpoint: str):
    try:
        ejecuta("UPDATE HUB_MailboxSuscripciones SET UltimoUso = GETDATE() "
                "WHERE Endpoint = %s", (endpoint,))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# El evento: correo nuevo
# ═══════════════════════════════════════════════════════════════════════════════

def avisar_correo_nuevo(cuenta: dict, id_usuario: int, remitente: str,
                        asunto: str, no_leidos: int = 1) -> int:
    """
    El push de "te llegó correo".

    El cuerpo del push NO lleva el asunto del correo: lleva QUIÉN escribe. Razón:
    un push con el asunto visible en la pantalla de bloqueo del iPhone enseña el
    contenido del correo a cualquiera que mire el teléfono. Con remitente y
    contador, la persona decide si lo abre; ese es el contrato del push.
    """
    nombre = (remitente or "").strip() or "Alguien"
    if len(nombre) > 60:
        nombre = nombre[:57] + "..."
    titulo = "Correo nuevo" if no_leidos <= 1 else f"{no_leidos} correos nuevos"
    cuerpo = nombre if no_leidos <= 1 else f"{nombre} y {no_leidos - 1} más"
    return avisar_a_usuario(
        id_usuario, titulo, cuerpo,
        url=f"/?page=mailbox&cuenta={cuenta.get('Id')}",
        tag=f"mailbox-{cuenta.get('Id')}",      # un tag por cuenta: el push nuevo
        contador=no_leidos,                    # reemplaza al anterior en la pantalla
    )


def avisar_cuentas(id_usuarios, titulo: str, cuerpo: str, url: str = "/") -> int:
    """
    Un push a varios usuarios. Los ids se deduplican antes de mandar.

    Es lo que usan las alertas globales ("la sincronización lleva 2 horas
    fallando"). Mandar dos pushes al mismo usuario porque aparece dos veces en
    el `IN` es un bug que se cuela fácil y molesta.
    """
    if not _listo():
        return 0
    total = 0
    vistos = set()
    for uid in id_usuarios or ():
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        if uid in vistos:
            continue
        vistos.add(uid)
        total += avisar_a_usuario(uid, titulo, cuerpo, url=url)
    return total


def usuarios_con_cuenta(id_cuenta: int) -> list:
    """Los `IdUsuario` que ven una cuenta. Para decidir a quién avisar."""
    try:
        return [f["IdUsuario"] for f in filas(
            "SELECT IdUsuario FROM HUB_MailboxCuentasLinks WHERE IdCuenta = %s", (id_cuenta,))]
    except Exception as exc:
        log.warning("[push] no pude leer los usuarios de la cuenta %s: %s", id_cuenta, exc)
        return []


def no_leidos_de(cuenta_id: int) -> int:
    """Cuántos no leídos tiene la cuenta, para el contador del push."""
    fila = una(
        "SELECT COUNT(*) AS n FROM HUB_MailboxMensajes "
        "WHERE IdCuenta = %s AND Visto = 0 AND Eliminado = 0", (cuenta_id,))
    return int((fila or {}).get("n") or 0)


def avisar_nuevos_de_cuenta(cuenta: dict, remitente: str, asunto: str,
                            total_nuevos: int) -> int:
    """
    El aviso de "llegaron N correos" al final del ciclo de una cuenta.

    Se manda UN push por ciclo y no uno por mensaje: con 20 correos nuevos en un
    ciclo, 20 pushes saturan la pantalla de bloqueo del usuario y a los dos días
    apaga las notificaciones de la app. Un push con "y 19 más" dice lo mismo y se
    lee una vez.
    """
    if total_nuevos <= 0:
        return 0
    cid = cuenta.get("Id")
    pendientes = no_leidos_de(cid)
    enviados = 0
    for uid in usuarios_con_cuenta(cid):
        enviados += avisar_correo_nuevo(cuenta, uid, remitente, asunto,
                                       no_leidos=max(pendientes, total_nuevos))
    return enviados