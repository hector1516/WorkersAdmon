"""
Field · notificaciones push.

Contrato: `shell/docs/PUSH.md` (repo ECCSA-Shell). No reescribir el contrato
desde aquí; si algo no cabe, se actualiza el doc.

─── LA DECISIÓN DE LAS CLAVES (leer antes de tocar nada) ───────────────────────
En la base hay DOS pares VAPID distintos:

  · `HUB_PushConfig` (Id=1)          → el que firma el despachador central
                                       (WorkersAdmon) y el que usa Admon.
  · `HUB_Config.vapid_public/private` → el de Mailbox.

Field usa **`HUB_PushConfig`**, o sea Opción A: la misma que Admon y que el
despachador. La razón es que el servicio de push rechaza la firma si la clave con
la que se suscribió el equipo no es la misma con la que se firma el aviso, y ese
rechazo **no dice por qué**: se ve como "no llegó a ningún dispositivo".

Este archivo antes generaba su propio par en `HUB_Config` y escribía las
suscripciones en `HUB_PushSubscriptions`, que está indexada por `UserEmail` y no
tiene columna `App` (además el código consultaba columnas inexistentes: `userId`,
`p256dh`, `auth`). Por eso Field llevaba mesesregistrando "3 push enviados" y
entregando cero, sin que ningún log dijera por qué.

Dos mecanismos distintos, a propósito:

  · `send_push_notification()` envía AHORA MISMO, desde la app. Se usa para lo
    que la propia Field provoca y le importa al mismo usuario (un ticket que se
    acaba de registrar, un punto de Legends). Va directo al push service.
  · `encolar_aviso()` mete una fila en `HUB_AvisosCola` y NO envía. Lo usa lo que
    tiene que salir aunque el usuario no esté en Field (el reporte que firmó
    otra persona, el vale que generó el worker). El envío lo hace el despachador
    central, que es quien tiene el horario y el resumen.

Los dos usan las MISMAS claves y la MISMA tabla, así que no hay forma de que un
equipo reciba dos veces el mismo aviso.
"""

import base64
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from auth import require_user
from db import get_connection

router = APIRouter()

# Discrimina las suscripciones de esta app. Sin esto, el aviso de una app cae
# dentro del service worker de otra y la gente acaba apagando los permisos.
APP_PUSH = "field"

# En iOS no se usan imágenes remotas: el ícono va empaquetado con la app.
ICON_PUSH = "/icons/icon-192x192.png"


# ── Claves VAPID ───────────────────────────────────────────────────────────────

def _normalizar_vapid_privada(priv: str) -> str:
    """Deja la clave privada en el formato que `pywebpush` sí entiende.

    Quiere base64url de los 32 bytes CRUDOS. Si quedara una PEM o un DER en
    base64, el envío falla SIEMPRE y el error que sale no dice nada útil: parece
    que el push no llegó a ningún dispositivo. Se normaliza al LEER, no solo al
    escribir, porque corregirlo es barato y el síntoma es el peor posible.
    """
    original = (priv or "").strip()
    if not original:
        return original

    def _b64d(texto: str) -> bytes:
        pad = "=" * ((4 - len(texto) % 4) % 4)
        return base64.urlsafe_b64decode((texto + pad).replace("-", "+").replace("_", "/"))

    try:
        if len(_b64d(original)) == 32:
            return original          # ya está bien
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives import serialization
        if "BEGIN" in original:
            clave = serialization.load_pem_private_key(original.encode(), password=None)
        else:
            clave = serialization.load_der_private_key(_b64d(original), password=None)
        crudo = clave.private_numbers().private_value.to_bytes(32, "big")
        return base64.urlsafe_b64encode(crudo).rstrip(b"=").decode()
    except Exception:
        return original


def _vapid() -> tuple[str, str, str]:
    """(pública, privada, email) del par que firma el despachador.

    Devuelve strings vacíos si no hay configuración: los endpoints avisan con un
    503 legible en vez de fallar en el envío.
    """
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT VapidPublicKey, VapidPrivateKey, VapidEmail FROM HUB_PushConfig WHERE Id = 1"
            )
            row = cur.fetchone() or {}
        publica = (row.get("VapidPublicKey") or "").strip()
        privada = _normalizar_vapid_privada(row.get("VapidPrivateKey") or "")
        email = (row.get("VapidEmail") or "robot@ecc-sa.com.mx").strip()
        return publica, privada, email
    except Exception as exc:
        print(f"[push] no se pudieron leer las claves VAPID: {exc}")
        return "", "", ""


def _detectar_plataforma(user_agent: str) -> str:
    """'iOS' | 'Android' | 'Escritorio'. Solo informativo: la vista dice
    '2 iPhone, 1 PC' en vez de un número pelado."""
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "iOS"
    # iPadOS 13+ se reporta como Macintosh; aquí solo llega el User-Agent.
    if "macintosh" in ua:
        return "iOS"
    if "android" in ua:
        return "Android"
    return "Escritorio"


# ── Endpoints (los cinco del contrato) ────────────────────────────────────────

@router.get("/vapid-public-key")
def get_vapid_public_key(user: dict = Depends(require_user)):
    """La clave pública. El cliente la pide antes de suscribirse, para no
    tenerla duplicada en el bundle ni tener que actualizarla en dos lados cuando
    se rote el par."""
    publica, _, _ = _vapid()
    if not publica:
        raise HTTPException(503, "Web Push no configurado (HUB_PushConfig)")
    return {"publicKey": publica}


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: dict = {}
    plataforma: str = ""


@router.post("/subscribe")
def subscribe_push(request: Request, body: PushSubscribeRequest,
                   user: dict = Depends(require_user)):
    """Registra (o re-registra) la suscripción de ESTE dispositivo.

    UPSERT por endpoint: la suscripción pertenece al service worker y sobrevive
    al cierre de sesión, así que si el usuario entra con otra cuenta en el mismo
    teléfono el endpoint seguiría apuntando al usuario anterior. `revincular()`
    en el cliente corrige eso sin volver a pedir el permiso.

    SQL Server 2014 no tiene ON CONFLICT: UPDATE y, si no tocó filas, INSERT.
    """
    endpoint = (body.endpoint or "").strip()
    if not endpoint:
        raise HTTPException(400, "Suscripción vacía")
    if len(endpoint) > 2000:
        raise HTTPException(400, "Suscripción demasiado larga")
    p256dh = str((body.keys or {}).get("p256dh") or "").strip()[:500]
    auth = str((body.keys or {}).get("auth") or "").strip()[:500]
    if not p256dh or not auth:
        raise HTTPException(400, "Suscripción incompleta")

    plataforma = (body.plataforma or "").strip()[:20] or _detectar_plataforma(
        request.headers.get("User-Agent", ""))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE HUB_PushSuscripciones SET IdUsuario = %s, P256dhKey = %s, "
                "AuthKey = %s, Plataforma = %s, Activo = 1, UltimoUso = GETDATE() "
                "WHERE Endpoint = %s",
                (user["id"], p256dh, auth, plataforma, endpoint),
            )
            if cur.rowcount == 0:
                try:
                    # EndpointHash es columna CALCULADA (PERSISTED): no se manda.
                    cur.execute(
                        "INSERT INTO HUB_PushSuscripciones "
                        "(App, IdUsuario, Endpoint, P256dhKey, AuthKey, Plataforma, "
                        " Creado, Activo) VALUES (%s, %s, %s, %s, %s, %s, GETDATE(), 1)",
                        (APP_PUSH, user["id"], endpoint, p256dh, auth, plataforma),
                    )
                except Exception:
                    # Carrera entre dos POST del mismo equipo: si otra petición
                    # ganó, el UPDATE de arriba ya lo dejó bien. No es un error
                    # para el usuario.
                    cur.execute(
                        "UPDATE HUB_PushSuscripciones SET IdUsuario = %s, Activo = 1 "
                        "WHERE Endpoint = %s",
                        (user["id"], endpoint),
                    )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "plataforma": plataforma}


@router.post("/unsubscribe")
def unsubscribe_push(body: PushSubscribeRequest, user: dict = Depends(require_user)):
    """Da de baja SOLO este dispositivo. Las filas se marcan Activo=0 en vez de
    borrarse: si el usuario vuelve, la fila sigue ahí y reactivarla es un UPDATE
    en vez de un INSERT."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE HUB_PushSuscripciones SET Activo = 0 "
                "WHERE Endpoint = %s AND IdUsuario = %s AND App = %s",
                ((body.endpoint or "").strip(), user["id"], APP_PUSH),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/suscripciones")
def get_suscripciones(user: dict = Depends(require_user)):
    """Equipos de ESTE usuario, para poder decir 'este teléfono sí, esa laptop
    no'."""
    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT Id, Plataforma, Creado, UltimoUso FROM HUB_PushSuscripciones "
                "WHERE IdUsuario = %s AND App = %s AND Activo = 1 ORDER BY Creado DESC",
                (user["id"], APP_PUSH),
            )
            filas = cur.fetchall()
            return {"suscripciones": [
                {"id": f["Id"], "plataforma": f["Plataforma"] or "?",
                 "creado": str(f["Creado"]),
                 "ultimoUso": str(f["UltimoUso"]) if f["UltimoUso"] else None}
                for f in filas]}
    finally:
        conn.close()


@router.post("/prueba")
def push_prueba(user: dict = Depends(require_user)):
    """Un aviso de prueba SOLO a los equipos de quien lo apretó.

    A propósito NO es broadcast: el botón 🧪 tiene que probar el teléfono de
    quien lo apretó, no avisar a media empresa de que alguien está probando.
    """
    from pywebpush import webpush, WebPushException  # noqa: F401  (error legible)

    _, privada, email = _vapid()
    if not privada:
        raise HTTPException(503, "Web Push no configurado (HUB_PushConfig)")

    conn = get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT Endpoint, P256dhKey, AuthKey FROM HUB_PushSuscripciones "
                "WHERE IdUsuario = %s AND App = %s AND Activo = 1",
                (user["id"], APP_PUSH),
            )
            subs = cur.fetchall()
    finally:
        conn.close()

    if not subs:
        return {"sent": 0, "fallidos": 0,
                "detail": "No hay ningún equipo suscrito en esta cuenta."}

    payload = json.dumps({
        "title": "🔔 Field · aviso de prueba",
        "body": "Si lees esto, las notificaciones de Field funcionan en este equipo.",
        "url": "/configuracion",
        "tag": "field-prueba",
        "badge_count": 1,
    })
    enviados, fallidos = 0, 0
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["Endpoint"],
                    "keys": {"p256dh": sub["P256dhKey"], "auth": sub["AuthKey"]},
                },
                data=payload,
                vapid_private_key=privada,
                vapid_claims={"sub": f"mailto:{email}"},
            )
            enviados += 1
        except Exception as exc:
            codigo = getattr(exc, "response", None)
            codigo = getattr(codigo, "status_code", None)
            if codigo in (404, 410):
                _borrar_suscripcion(sub["Endpoint"])
            fallidos += 1
    return {"sent": enviados, "fallidos": fallidos,
            "detail": f"{enviados} enviado(s), {fallidos} fallido(s)"}


# ── Envío directo (lo que la propia Field provoca) ─────────────────────────────

def _borrar_suscripcion(endpoint: str):
    """404/410 = la suscripción murió (se desinstaló la PWA o el sistema la
    borró). Se elimina para no reintentar contra la nada en cada aviso."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM HUB_PushSuscripciones WHERE Endpoint = %s", (endpoint,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _enviar_a_suscripciones(subs, titulo: str, mensaje: str, url: str, tag: str):
    """Envía a una lista de suscripciones. Devuelve (enviados, fallidos).

    `tag` agrupa: si llegan tres avisos del mismo tipo, el segundo reemplaza al
    primero en vez de apilar tres. En iPhone la pila se llena rápido y tapar la
    pantalla es peor que resumir.
    """
    from pywebpush import webpush

    _, privada, email = _vapid()
    if not privada or not subs:
        return 0, 0

    payload = json.dumps({
        "title": titulo,
        "body": mensaje,
        "url": url or "/",
        "icon": ICON_PUSH,
        "tag": tag,
        # En iOS `badge` es un NÚMERO y en Android la URL de una imagen: por eso
        # va el conteo y el service worker decide (ver PUSH.md §5).
        "badge_count": 1,
    })
    enviados, fallidos = 0, 0
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["Endpoint"],
                    "keys": {"p256dh": sub["P256dhKey"], "auth": sub["AuthKey"]},
                },
                data=payload,
                vapid_private_key=privada,
                vapid_claims={"sub": f"mailto:{email}"},
            )
            enviados += 1
        except Exception as exc:
            codigo = getattr(exc, "response", None)
            codigo = getattr(codigo, "status_code", None)
            if codigo in (404, 410):
                _borrar_suscripcion(sub["Endpoint"])
            else:
                print(f"[push] error transitorio en {str(sub.get('Endpoint'))[:60]}: {exc}")
            fallidos += 1
    return enviados, fallidos


def send_push_notification(user_id: int, title: str, body: str, url: str = "/"):
    """Envía un aviso a los equipos de UN usuario, ahora mismo.

    Devuelve (enviados, fallidos) para que quien llama pueda verlo en el log.
    """
    try:
        conn = get_connection()
        try:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(
                    "SELECT Endpoint, P256dhKey, AuthKey FROM HUB_PushSuscripciones "
                    "WHERE IdUsuario = %s AND App = %s AND Activo = 1",
                    (user_id, APP_PUSH),
                )
                subs = cur.fetchall()
        finally:
            conn.close()
        if not subs:
            return 0, 0
        return _enviar_a_suscripciones(subs, title, body, url,
                                        tag=f"field-{hashlib.md5(title.encode()).hexdigest()[:8]}")
    except Exception as exc:
        print(f"[push] no se pudo enviar a {user_id}: {exc}")
        return 0, 0


def encolar_aviso(tipo: str, id_usuario: int, titulo: str, mensaje: str, url: str) -> bool:
    """Mete el aviso en la cola del despachador central y NO envía.

    Para lo que tiene que salir aunque el usuario no esté usando Field ahora:
    el reporte que firmó otra persona, el vale que generó el worker. El
    despachador (WorkersAdmon) decide el horario y lo entrega.

    Escribir en la cola y no enviar desde aquí es a propósito: si la app mandara
    el aviso, se iría desde el servidor de Field y el usuario lo recibiría igual,
    pero se saltaría el horario laboral y el resumen, y quedaría fuera del log
    único de avisos.
    """
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO HUB_AvisosCola "
                    "(App, Tipo, IdUsuario, Estado, EsResumen, Titulo, Mensaje, Url, Creado) "
                    "VALUES (%s, %s, %s, 'PENDIENTE', 0, %s, %s, %s, GETDATE())",
                    (APP_PUSH, tipo, int(id_usuario),
                     (titulo or "")[:200], (mensaje or "")[:1000], (url or "/")[:200]),
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        # El índice único es (IdUsuario, Tipo, Creado): dos inserts del mismo
        # usuario en el mismo segundo son el MISMO aviso, no dos.
        texto = str(exc).lower()
        if "duplicate" in texto or "2627" in texto:
            return True
        print(f"[push] no se pudo encolar {tipo} para {id_usuario}: {exc}")
        return False


def notify_all_users(title: str, body: str, exclude_user_id: int = None):
    """Aviso a todos los usuarios activos (puntos de Legends, altas)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor(as_dict=True) as cur:
                if exclude_user_id:
                    cur.execute(
                        "SELECT Id FROM HUB_Users WHERE Activo = 1 AND Id <> %s",
                        (exclude_user_id,))
                else:
                    cur.execute("SELECT Id FROM HUB_Users WHERE Activo = 1")
                ids = [r["Id"] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        print(f"[push] no se pudo listar los usuarios: {exc}")
        return
    for uid in ids:
        send_push_notification(uid, title, body)


def _get_user_name(user_id: int) -> str:
    """Nombre del usuario desde HUB_Users."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Nombre FROM HUB_Users WHERE Id = %s", (user_id,))
            row = cur.fetchone()
            return row["Nombre"] if row else "Alguien"
    except Exception:
        return "Alguien"


def _get_nickname(user_id: int) -> str:
    """Nickname del usuario (HUB_Users.Nickname, migración 0034)."""
    try:
        conn = get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Nickname FROM HUB_Users WHERE Id = %s", (user_id,))
            row = cur.fetchone()
            nick = (row.get("Nickname") or "").strip() if row else ""
            return nick or _get_user_name(user_id)
    except Exception:
        return _get_user_name(user_id)


# ── Legends (avisos a todos) ──────────────────────────────────────────────────

NIVEL_EMOJI = {"Bronce": "🥉", "Plata": "🥈", "Oro": "🥇", "Diamante": "💎"}


def notify_level_up(user_id: int, new_level: str):
    name = _get_nickname(user_id)
    notify_all_users("🎉 ¡Sube de nivel!", f"{name} alcanzó nivel {new_level} "
                       f"{NIVEL_EMOJI.get(new_level, '🎯')}")


def notify_ranking_pass(user_id: int, passed_user_id: int, new_position: int):
    passer = _get_nickname(user_id)
    passed = _get_nickname(passed_user_id)
    notify_all_users("🏆 ¡Movimiento en el ranking!",
                     f"{passer} pasó a {passed} — ahora en posición #{new_position}")


def notify_new_user(user_id: int, nickname: str):
    notify_all_users("👋 ¡Nuevo en Legends!", f"{nickname} se unió al equipo")


def notify_weekly_winner(user_id: int, points: int):
    notify_all_users("🏆 ¡Ganador de la semana!",
                     f"{_get_nickname(user_id)} ganó la semana con {points} puntos")


class PushSendRequest(BaseModel):
    title: str
    body: str
    user_ids: list[int] | None = None
    all: bool = False


@router.post("/send")
def send_manual(req: PushSendRequest, user: dict = Depends(require_user)):
    """Envío manual de push (solo admins)."""
    if not user.get("is_admin"):
        raise HTTPException(403, "Solo administradores")
    titulo = (req.title or "").strip()[:120]
    cuerpo = (req.body or "").strip()[:500]
    if not titulo or not cuerpo:
        raise HTTPException(400, "Título y mensaje requeridos")
    count = 0
    if req.all:
        conn = get_connection()
        try:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Id FROM HUB_Users WHERE Activo = 1")
                ids = [r["Id"] for r in cur.fetchall()]
        finally:
            conn.close()
        for uid in ids:
            enviados, _ = send_push_notification(uid, titulo, cuerpo)
            count += enviados
    else:
        for uid in req.user_ids or []:
            try:
                enviados, _ = send_push_notification(int(uid), titulo, cuerpo)
                count += enviados
            except Exception:
                pass
    return {"ok": True, "sent": count}
