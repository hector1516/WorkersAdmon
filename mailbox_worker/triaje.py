"""
mailbox_worker/triaje.py — clasificación del correo y respuesta sugerida
=========================================================================

QUÉ HACE
Cada mensaje nuevo se clasifica UNA vez, con reglas deterministas (sin IA y sin
costo): cabeceras de envío masivo, remitentes con pinta de boletín, rebotes,
correo interno, lista de bloqueo global y resolución del destinatario contra
`HUB_Users`. Solo lo que parece una persona real escribiéndose de buenos modos
a alguien que ya no está llega a la cola de revisión.

SIN IA, A PROPÓSITO (decisión del usuario, 2026-10-24)
No se llama a ningún proveedor externo: no hay cuota que pagar y el módulo
funciona igual con o sin internet. El costo es que un correo de venta en frío a
la dirección de alguien que se fue se parece mucho a un cliente que se equivocó;
eso lo distingue el operador en la cola, con un clic.

MODO REVISIÓN (el que está activo)
Nada se envía solo. Cada candidato queda en `HUB_MailboxClasificacion` con un
borrador y el operador lo aprueba desde el panel (pestaña 🛡️ Triaje). Aprobar
encola el envío en `HUB_MailboxColaEnvio`, que el worker ya sabe drenar.

CRITERIO DE "EX EMPLEADO" (definido por el usuario, 2026-10-24)
Si la dirección del destinatario NO está en `HUB_Users` (o está con
`Activo = 0`), es de alguien que ya no está. Solo aplica a nuestro dominio: un
remitente externo que no está en HUB_Users no es un ex empleado.
"""
import re
from datetime import datetime

from .db import ejecuta, filas, una

DOMINIOS_PROPIOS = {"ecc-sa.com.mx"}

# Slugs que puede devolver la IA -> buzón real. Se pueden sobreescribir con
# HUB_Config: triaje_destino_ventas, triaje_destino_soporte, etc.
DESTINOS = {
    "ventas": "ventas@ecc-sa.com.mx",
    "soporte": "soporte@ecc-sa.com.mx",
    "rrhh": "rrhh@ecc-sa.com.mx",
    "admin": "contacto@ecc-sa.com.mx",
    "general": "contacto@ecc-sa.com.mx",
}

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Direcciones con forma de persona: `nombre.apellido`, `nombre.apellido2`,
# opcionalmente con dígitos. Deja fuera alias funcionales (admon@, ventas@,
# instagram@, temu25@) que NO son ex empleados y a los que sería absurdo
# contestar "esa persona ya no trabaja aquí".
_RE_PERSONA = re.compile(r"^[a-z]+(\.[a-z0-9]+)+$")

# Prefijos que son un buzón, no una persona. Se pueden forzar manualmente con
# HUB_MailboxDirecciones (Estado='activa').
ROLES = {
    "admon", "ifm", "instagram", "facebook", "ventas", "soporte", "admin",
    "contacto", "info", "hola", "rrhh", "cxc", "robot", "sistema", "sistemas",
    "facturas", "pagos", "compras", "no-reply", "noreply", "mailer-daemon",
    "postmaster", "webmaster", "marketing", "boletines", "notificaciones",
}


def parece_persona(email):
    """True si la parte local parece el nombre de una persona."""
    local = (email or "").split("@")[0].lower()
    if not local or local in ROLES:
        return False
    return bool(_RE_PERSONA.match(local))


# Remitentes que son un boletín o un robot, aunque el asunto parezca normal.
# Se detectan por la parte local y por el subdominio de envío: es la única
# forma de descartarlos sin cabeceras (el correo viejo no las tiene) y sin IA.
_RE_BULK_REM = re.compile(
    r"(no-?reply|noreply|news|notif|update|alert|mailer|bounce|promo|marketing|"
    r"bolet[ií]n|newsletter|info|ventas|soporte|support|factur|webmaster|"
    r"postmaster|daemon|weekly|daily|digest)")
_SUBDOMINIOS_BULK = ("news.", "info.", "mail.", "email.", "response.",
                     "mx-news.", "campaign", "sendgrid", "mailchimp", "cmail")


def remitente_masivo(email, nombre=""):
    """True si el REMITENTE tiene pinta de boletín/robot."""
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return True
    local, dominio = e.split("@", 1)
    if local in ROLES or _RE_BULK_REM.search(local):
        return True
    if any(d in dominio for d in _SUBDOMINIOS_BULK):
        return True
    # Sin nombre visible y con parte local que no es un nombre: casi siempre robot.
    if not (nombre or "").strip() and not parece_persona(e):
        return True
    return False


# ── Configuración ─────────────────────────────────────────────────────────────

def _cfg(clave, defecto=""):
    try:
        f = una("SELECT TOP 1 Valor FROM HUB_Config WHERE Clave = %s", (clave,))
        valor = (f or {}).get("Valor")
        return valor if valor not in (None, "") else defecto
    except Exception:
        return defecto


def _cfg_bool(clave, defecto=True):
    v = str(_cfg(clave, "1" if defecto else "0")).strip().lower()
    return v in ("1", "true", "si", "sí", "on")


def destino_de(slug):
    """Slug de destino -> dirección real, configurable por HUB_Config."""
    slug = (slug or "").strip().lower()
    defecto = _cfg("triaje_destino_defecto", "contacto@ecc-sa.com.mx")
    if not slug or slug == "null":
        return defecto
    return _cfg(f"triaje_destino_{slug}", DESTINOS.get(slug, defecto))


# ── Destinatarios ─────────────────────────────────────────────────────────────

def direcciones(texto):
    """Todas las direcciones de un header To/Cc, en minúsculas."""
    return [d.lower().strip(".,;<> ") for d in _RE_EMAIL.findall(texto or "")]


def resolver(email):
    """
    (estado, id_usuario, nombre).
    Estados: activa | exempleado | inexistente | rol | desconocida.

    `exempleado` = está en HUB_Users con Activo = 0.
    `inexistente` = no está, pero tiene forma de persona en nuestro dominio
    (típico de un typo, o de alguien que se fue antes de que existiera la base).
    `rol` = alias funcional (admon@, ventas@, …): no se responde.
    """
    e = (email or "").strip().lower()
    if not e:
        return "desconocida", None, ""

    f = una("SELECT Estado, IdUsuario FROM HUB_MailboxDirecciones WHERE Email = %s", (e,))
    if f and (f.get("Estado") or "auto") in ("activa", "exempleado"):
        return f["Estado"], f.get("IdUsuario"), ""

    u = una("SELECT Id, Nombre, Activo FROM HUB_Users WHERE LOWER(Email) = %s", (e,))
    if u:
        return ("activa" if u.get("Activo") else "exempleado"), u.get("Id"), u.get("Nombre") or ""

    if e.split("@")[-1] in DOMINIOS_PROPIOS:
        # No está en la base: si parece una persona, es un typo o alguien que ya
        # no está; si parece un buzón, es un alias funcional y no se toca.
        return ("inexistente" if parece_persona(e) else "rol"), None, ""
    return "desconocida", None, ""


# ── Bloqueos globales ─────────────────────────────────────────────────────────
# Una entrada bloquea ese remitente (o todo su dominio) en LAS 13 cuentas, no
# solo en la del usuario que lo marcó. Se evalúa antes que las reglas
# personales y antes que cualquier clasificación.

def bloqueos_activos():
    """Todas las entradas activas: [{'Tipo': 'dominio'|'email', 'Valor': ...}]."""
    try:
        return filas("SELECT Id, Tipo, Valor FROM HUB_MailboxBloqueos WHERE Activo = 1")
    except Exception:
        return []


def esta_bloqueado(email, bloqueos=None):
    """True si el remitente (o su dominio) está en la lista."""
    e = (email or "").strip().lower()
    if not e:
        return False
    dominio = e.split("@")[-1] if "@" in e else ""
    for b in (bloqueos if bloqueos is not None else bloqueos_activos()):
        tipo = (b.get("Tipo") or "").strip().lower()
        valor = (b.get("Valor") or "").strip().lower()
        if not valor:
            continue
        if tipo == "email" and e == valor:
            return True
        if tipo == "dominio" and (dominio == valor or dominio.endswith("." + valor)):
            return True
    return False


def _normalizar_entrada(linea):
    """'Foo@Bar.com ' -> ('email','foo@bar.com'); '@bar.com' o 'bar.com' -> dominio."""
    t = (linea or "").strip().lower().strip(",;<>()[]\"'")
    if not t or t.startswith("#"):
        return None
    if t.startswith("@"):
        t = t[1:]
    if "@" in t:
        if "." not in t.split("@")[-1]:
            return None
        return ("email", t)
    # Dominio: sin espacios, con al menos un punto.
    if " " in t or "." not in t:
        return None
    return ("dominio", t)


def bloquear(valor, tipo=None, motivo="", quien="", origen="manual"):
    """
    Agrega un bloqueo. Si no se dice el tipo, se deduce del valor.
    Devuelve (agregado: bool, tipo, valor).
    """
    normal = _normalizar_entrada(valor)
    if not normal:
        raise ValueError(f"no parece un correo ni un dominio: {valor!r}")
    tipo_ded, valor_ded = normal
    tipo = (tipo or tipo_ded).strip().lower()
    if tipo not in ("email", "dominio"):
        raise ValueError("el tipo tiene que ser email o dominio")
    ejecuta(
        "IF NOT EXISTS (SELECT 1 FROM HUB_MailboxBloqueos WHERE Tipo = %s AND Valor = %s) "
        "INSERT INTO HUB_MailboxBloqueos (Tipo, Valor, Motivo, Origen, CreadoPor) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tipo, valor_ded, tipo, valor_ded, (motivo or "")[:200], origen, quien or ""))
    # Si ya existía, se reactiva (pudo haberse desbloqueado antes).
    ejecuta("UPDATE HUB_MailboxBloqueos SET Activo = 1 WHERE Tipo = %s AND Valor = %s",
            (tipo, valor_ded))
    return True, tipo, valor_ded


def importar_bloqueos(texto, quien=""):
    """Importa una lista pegada: una entrada por línea. Devuelve el conteo."""
    agregados = 0
    invalidas = 0
    for linea in (texto or "").splitlines():
        if not linea.strip():
            continue
        try:
            bloquear(linea, motivo="lista importada", quien=quien, origen="importada")
            agregados += 1
        except ValueError:
            invalidas += 1
    return agregados, invalidas


def listar_bloqueos():
    return filas("SELECT Id, Tipo, Valor, Motivo, Origen, Creado, CreadoPor "
                  "FROM HUB_MailboxBloqueos WHERE Activo = 1 ORDER BY Creado DESC")


def eliminar_bloqueo(id_bloqueo):
    ejecuta("UPDATE HUB_MailboxBloqueos SET Activo = 0 WHERE Id = %s", (int(id_bloqueo),))


def bloquear_desde_clasificacion(id_clasif, tipo, quien=""):
    """Bloquea el remitente (o su dominio) de un mensaje de la cola."""
    k = una("SELECT RemitenteEmail FROM HUB_MailboxClasificacion WHERE Id = %s",
            (int(id_clasif),))
    if not k or not k.get("RemitenteEmail"):
        raise ValueError("ese mensaje no tiene remitente")
    rem = k["RemitenteEmail"].strip().lower()
    if tipo == "dominio":
        rem = rem.split("@")[-1]
    return bloquear(rem, tipo=tipo, motivo=f"marcado desde triaje #{id_clasif}",
                    quien=quien)


# ── Reputación del remitente (sin IA) ─────────────────────────────────────────
#
# Dos señales baratas que reemplazan lo que hacía Gemini:
#   · primer contacto: un dominio que nunca nos ha escrito es más sospechoso.
#   · lenguaje promocional: vocabulario de venta en frío en asunto/cuerpo.
# Y auto-aprendizaje: cada rechazo suma al contador del dominio; al llegar a N
# el dominio se bloquea solo.

# Vocabulario de venta en frío (español + inglés). Se busca como subcadena en
# asunto + extracto, en minúsculas, sin tildes.
_TERMINOS_PROMO = (
    # ES
    "servicio", "servicios", "solucion", "soluciones", "oferta", "descuento",
    "demostracion", "reunion", "precios", "cotizacion", "propuesta",
    "colaboracion", "alianza", "campana", "boletin", "oportunidad",
    "crecimiento", "ingresos", "llamada", "agenda", "prueba gratuita",
    "caso de exito", "presentarnos", "contactar", "prospect", "ventas",
    "marketing", "newsletter", "cancelar suscripcion", "descubra",
    "mejore", "aumente", "roi", "lead", "funnel", "pipeline",
    # EN
    "services", "solutions", "offer", "discount", "demo", "meeting", "pricing",
    "quote", "proposal", "partnership", "collaboration", "campaign",
    "newsletter", "opportunity", "growth", "revenue", "schedule", "call",
    "free trial", "case study", "introduce", "outreach", "follow up",
    "touch base", "quick call", "15 minutes", "book a call", "calendly",
    "zoom", "webinar", "testimonial", "unsubscribe", "opt out",
    "limited time", "exclusive", "scale", "lead", "prospect",
)

_LIBRES = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "live.com",
           "icloud.com", "protonmail.com", "proton.me", "aol.com", "msn.com"}


def _sin_tildes(s):
    return (s or "").lower().translate(str.maketrans(
        "áéíóúüñàèìòùâêîôûäëïöü", "aeiouunaeiouaeiouaeiou"))


def upsert_remitente(dominio):
    """
    Registra/actualiza la reputación del dominio. Devuelve True si es la primera
    vez que lo vemos (primer contacto).
    """
    dominio = (dominio or "").strip().lower()
    if not dominio or "@" in dominio:
        return False
    try:
        prev = una("SELECT Veces FROM HUB_MailboxRemitentes WHERE Dominio = %s",
                   (dominio,))
        if prev:
            ejecuta("UPDATE HUB_MailboxRemitentes SET UltimaVez = GETDATE(), "
                    "Veces = Veces + 1 WHERE Dominio = %s", (dominio,))
            return False
        ejecuta("INSERT INTO HUB_MailboxRemitentes (Dominio) VALUES (%s)", (dominio,))
        return True
    except Exception:
        return False


def puntuar_sospecha(msg, primer_contacto, bloqueos=None):
    """
    Puntaje 0-100 de "parece venta en frío" para un candidato huérfano.
    Devuelve (puntaje, [razones]).
    """
    score = 0
    razones = []
    rem = (msg.get("RemitenteEmail") or "").strip().lower()
    dominio = rem.split("@")[-1] if "@" in rem else ""
    asunto = _sin_tildes(msg.get("Asunto") or "")
    extracto = _sin_tildes(msg.get("Extracto") or "")
    texto = asunto + " " + extracto
    nombre = _sin_tildes(msg.get("RemitenteNombre") or "")

    # Primer contacto: el dominio nunca nos había escrito.
    if primer_contacto:
        score += 20
        razones.append("primer contacto")

    # Dominio conocido (alguien ya aprobó un correo suyo): resta.
    try:
        r = una("SELECT EsConocido, Rechazos FROM HUB_MailboxRemitentes "
                "WHERE Dominio = %s", (dominio,))
        if r and r.get("EsConocido"):
            score -= 20
        if r and (r.get("Rechazos") or 0) >= 1:
            score += 15
            razones.append(f"{r['Rechazos']} rechazo(s) previo(s)")
    except Exception:
        pass

    # Sin hilo: no es respuesta a nada (cold outreach).
    if not (msg.get("InReplyTo") or "").strip():
        score += 15
        razones.append("sin hilo")

    # "Re:" inventado: asunto empieza con Re: pero no hay In-Reply-To.
    if asunto.lstrip().startswith("re:") and not (msg.get("InReplyTo") or "").strip():
        score += 20
        razones.append("\"Re:\" sin hilo")

    # Lenguaje promocional.
    hits = []
    for term in _TERMINOS_PROMO:
        if term in texto:
            hits.append(term)
    n_hits = min(len(set(hits)), 6)
    if n_hits:
        score += 5 * n_hits
        razones.append(f"{n_hits} termino(s) promocional(es)")

    # Varios destinatarios (To+Cc) → pinta de envío masivo.
    n_dest = len(direcciones(msg.get("ParaTexto") or "") +
                 direcciones(msg.get("CcTexto") or ""))
    if n_dest >= 3:
        score += 10
        razones.append("varios destinatarios")

    # Remitente genérico de ventas.
    if nombre and any(g in nombre for g in ("sales", "ventas", "info", "contact",
                                           "marketing", "support", "soporte")):
        score += 15
        razones.append("remitente generico")

    # Proveedor gratuito escribiendo a correo corporativo sobre negocio.
    if dominio in _LIBRES:
        score += 10
        razones.append("correo gratuito")

    # Enlace en el extracto.
    if "http://" in texto or "https://" in texto or "www." in texto:
        score += 8
        razones.append("incluye enlace")

    # Asunto en mayúsculas o con signos de exclamación.
    asunto_raw = (msg.get("Asunto") or "")
    if asunto_raw and asunto_raw == asunto_raw.upper() and len(asunto_raw) > 8:
        score += 8
        razones.append("asunto en mayusculas")
    if asunto_raw.count("!") >= 2 or asunto_raw.count("?") >= 2:
        score += 5
        razones.append("signos de exclamacion")

    # Enviar a una dirección inexistente (typo) suele ser humano equivocado:
    # baja un poco el puntaje si el destinatario era un typo probable.
    # (Ya viene en el motivo; aquí no lo sabemos, así que no se toca.)

    score = max(0, min(100, score))
    return score, razones


def _umbral_auto_rechazo():
    try:
        return int(_cfg("triaje_auto_rechazo_umbral", "75") or 75)
    except Exception:
        return 75


def _umbral_auto_bloqueo():
    try:
        return int(_cfg("triaje_auto_bloqueo_rechazos", "3") or 3)
    except Exception:
        return 3


def _registrar_rechazo(dominio, quien=""):
    """
    Suma un rechazo al dominio. Si llega al umbral, lo bloquea solo.
    Devuelve (rechazos, bloqueado_auto).
    """
    dominio = (dominio or "").strip().lower()
    if not dominio or "@" in dominio:
        # Si vino un correo completo, reducir a dominio.
        if "@" in dominio:
            dominio = dominio.split("@")[-1]
        else:
            return 0, False
    try:
        upsert_remitente(dominio)
        ejecuta("UPDATE HUB_MailboxRemitentes SET Rechazos = Rechazos + 1 "
                "WHERE Dominio = %s", (dominio,))
        r = una("SELECT Rechazos, BloqueadoAuto FROM HUB_MailboxRemitentes "
                "WHERE Dominio = %s", (dominio,))
        rechazos = int((r or {}).get("Rechazos") or 0)
        ya = bool((r or {}).get("BloqueadoAuto"))
        bloqueado = False
        if rechazos >= _umbral_auto_bloqueo() and not ya:
            bloquear(dominio, tipo="dominio",
                     motivo=f"auto: {rechazos} rechazos del operador",
                     quien=quien or "sistema", origen="auto")
            ejecuta("UPDATE HUB_MailboxRemitentes SET BloqueadoAuto = 1 "
                    "WHERE Dominio = %s", (dominio,))
            bloqueado = True
        return rechazos, bloqueado
    except Exception:
        return 0, False


def _marcar_conocido(dominio):
    """Marca el dominio como conocido (al aprobar un correo suyo)."""
    dominio = (dominio or "").strip().lower()
    if "@" in dominio:
        dominio = dominio.split("@")[-1]
    if not dominio:
        return
    try:
        upsert_remitente(dominio)
        ejecuta("UPDATE HUB_MailboxRemitentes SET EsConocido = 1 "
                "WHERE Dominio = %s", (dominio,))
    except Exception:
        pass


# ── Clasificación determinista ────────────────────────────────────────────────

def clasificar(msg, bloqueos=None, primer_contacto=False):
    """
    Devuelve un dict con categoria, confianza, motivo y (si aplica)
    destinatario. No llama a la IA.

    `bloqueos` se pasa ya cargado para no consultar la tabla una vez por
    mensaje (el backfill procesa cientos de golpe).
    """
    rem = (msg.get("RemitenteEmail") or "").strip().lower()
    asunto = (msg.get("Asunto") or "").lower()
    dominio_rem = rem.split("@")[-1] if "@" in rem else ""

    if esta_bloqueado(rem, bloqueos):
        return {"categoria": "bloqueado", "confianza": 0.99, "metodo": "regla",
                "motivo": "remitente en la lista de bloqueo"}

    if msg.get("AutoSubmitted") or msg.get("EsBulk") or msg.get("TieneLista"):
        return {"categoria": "bulk", "confianza": 0.95, "metodo": "regla",
                "motivo": "cabeceras de envío masivo o automático"}

    if not rem or rem.startswith("mailer-daemon") or rem.startswith("postmaster") \
            or "undelivered" in asunto or "delivery status" in asunto \
            or "mail delivery failed" in asunto:
        return {"categoria": "rebote", "confianza": 0.95, "metodo": "regla",
                "motivo": "notificación de rebote"}

    if msg.get("AuthResultado") == "fail":
        return {"categoria": "bulk", "confianza": 0.8, "metodo": "regla",
                "motivo": "falló autenticación (SPF/DKIM/DMARC)"}

    # Remitente con pinta de boletín/robot. Va DESPUÉS de las cabeceras (que son
    # la prueba dura) y ANTES de mirar el destinatario: un boletín a la dirección
    # de alguien que ya se fue NO se responde, solo se ignora.
    if remitente_masivo(rem, msg.get("RemitenteNombre")):
        return {"categoria": "bulk", "confianza": 0.8, "metodo": "regla",
                "motivo": "remitente con pinta de boletín o robot"}

    if dominio_rem in DOMINIOS_PROPIOS:
        return {"categoria": "interno", "confianza": 0.9, "metodo": "regla",
                "motivo": "remitente del dominio propio"}

    # Destinatarios: To + Cc. La cuenta propia no cuenta (es el buzón, no una
    # persona a la que se equivocaron).
    propios = {(msg.get("CuentaEmail") or "").strip().lower()}
    candidatos = [d for d in (direcciones(msg.get("ParaTexto")) + direcciones(msg.get("CcTexto")))
                  if d and d not in propios]

    for d in candidatos:
        estado, idu, nombre = resolver(d)
        if estado in ("exempleado", "inexistente"):
            tipo = estado
            motivo_base = ("el destinatario ya no está en HUB_Users"
                           if estado == "exempleado"
                           else "la dirección no existe (probable error de tipeo)")
            score, razones = puntuar_sospecha(msg, primer_contacto, bloqueos)
            if score >= _umbral_auto_rechazo():
                return {"categoria": "sospechoso", "tipo": tipo, "confianza": 0.7,
                        "metodo": "regla",
                        "motivo": f"sospecha {score}: " + ", ".join(razones),
                        "sospecha": score, "razones": razones,
                        "destinatario": d, "id_usuario": idu, "nombre": nombre}
            motivo = motivo_base
            if score >= 40:
                motivo = motivo_base + f" · sospecha {score}: " + ", ".join(razones)
            return {"categoria": "huerfano", "tipo": tipo, "confianza": 0.85,
                    "metodo": "regla", "motivo": motivo,
                    "sospecha": score, "razones": razones,
                    "destinatario": d, "id_usuario": idu, "nombre": nombre}

    if any(resolver(d)[0] == "activa" for d in candidatos):
        return {"categoria": "activo", "confianza": 0.9, "metodo": "regla",
                "motivo": "el destinatario es un usuario activo"}

    return {"categoria": "normal", "confianza": 0.5, "metodo": "regla",
            "motivo": "sin señal de error de destinatario"}


# ── Borrador ──────────────────────────────────────────────────────────────────

def plantilla(msg, destinatario, destino, tipo="exempleado"):
    """
    Asunto y cuerpo del borrador. Plantilla fija, sin IA.

    Dos textos distintos, porque no es lo mismo:
      · `exempleado`  → la persona existió y ya no está.
      · `inexistente` → la dirección nunca existió (casi siempre un typo).
    """
    nombre = (msg.get("RemitenteNombre") or "").strip().split(" ")[0]
    saludo = f"Hola {nombre}" if nombre else "Hola"
    destino = destino or "contacto@ecc-sa.com.mx"
    asunto = (msg.get("Asunto") or "").strip()

    if tipo == "inexistente":
        texto = (
            f"{saludo}:\n\n"
            f"La dirección {destinatario} no existe en ECCSA.\n\n"
            f"Si buscabas a alguien del equipo, escribe a {destino} y te "
            f"canalizamos.\n\n"
            f"Saludos,\nECCSA"
        )
    else:
        texto = (
            f"{saludo}:\n\n"
            f"{destinatario} ya no forma parte del equipo de ECCSA.\n\n"
            f"Para que te atiendan, escribe a {destino} o responde a este correo "
            f"y lo canalizamos.\n\n"
            f"Saludos,\nECCSA"
        )
    return {
        "asunto": (f"RE: {asunto}" if asunto else "Cambio de contacto")[:500],
        "texto": texto,
    }


# ── Proceso ───────────────────────────────────────────────────────────────────

def _mensaje(mid):
    return una(
        "SELECT m.Id, m.IdCuenta, m.RemitenteNombre, m.RemitenteEmail, m.ParaTexto, "
        "       m.CcTexto, m.Asunto, m.Extracto, m.MessageId, m.EsBulk, "
        "       m.AutoSubmitted, m.TieneLista, m.AuthResultado, c.Email AS CuentaEmail "
        "FROM HUB_MailboxMensajes m "
        "JOIN HUB_MailboxCuentas c ON c.Id = m.IdCuenta "
        "WHERE m.Id = %s", (int(mid),))


def procesar_nuevos(ids):
    """
    Clasifica los mensajes recién indexados. Devuelve cuántos se clasificaron.

    Nunca lanza: si algo falla, el sync no se cae por el triaje.
    """
    if not ids or not _cfg_bool("triaje_activo", True):
        return 0
    bloqueos = bloqueos_activos()
    n = 0
    for mid in ids:
        try:
            if una("SELECT Id FROM HUB_MailboxClasificacion WHERE IdMensaje = %s", (mid,)):
                continue
            msg = _mensaje(mid)
            if not msg:
                continue

            dom = (msg.get("RemitenteEmail") or "").split("@")[-1].lower() \
                if "@" in (msg.get("RemitenteEmail") or "") else ""
            primer_contacto = upsert_remitente(dom)

            r = clasificar(msg, bloqueos, primer_contacto)

            destino = destino_de(None) if r["categoria"] == "huerfano" else None
            borrador = (plantilla(msg, r["destinatario"], destino, r.get("tipo"))
                        if r["categoria"] == "huerfano" else {"asunto": None, "texto": None})

            # Solo los huérfanos entran a la cola de revisión. Los sospechosos
            # (venta en frío) se auto-rechazan: se guardan como IGNORADA para
            # auditoría, pero no ensucian la pantalla del operador.
            if r["categoria"] == "sospechoso":
                estado = "IGNORADA"
            else:
                estado = "NUEVA" if r["categoria"] == "huerfano" else "IGNORADA"

            ejecuta(
                "INSERT INTO HUB_MailboxClasificacion "
                "(IdMensaje, IdCuenta, RemitenteEmail, Destinatario, Categoria, "
                " Confianza, Metodo, Motivo, DestinoSugerido, BorradorAsunto, "
                " BorradorTexto, Estado) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (msg["Id"], msg["IdCuenta"], msg.get("RemitenteEmail"),
                 r.get("destinatario"), r["categoria"], r.get("confianza"),
                 r["metodo"], (r.get("motivo") or "")[:400], destino,
                 borrador["asunto"], borrador["texto"], estado),
            )
            n += 1
        except Exception as exc:
            print(f"[triaje] mensaje {mid}: {type(exc).__name__}: {str(exc)[:160]}",
                  flush=True)
    return n


# ── Acciones del panel ────────────────────────────────────────────────────────

def _clasificacion(id_clasif):
    return una(
        "SELECT k.Id, k.IdMensaje, k.IdCuenta, k.Destinatario, k.DestinoSugerido, "
        "       k.BorradorAsunto, k.BorradorTexto, k.Estado, m.RemitenteEmail, "
        "       m.MessageId, c.Email AS CuentaEmail "
        "FROM HUB_MailboxClasificacion k "
        "JOIN HUB_MailboxMensajes m ON m.Id = k.IdMensaje "
        "JOIN HUB_MailboxCuentas c ON c.Id = k.IdCuenta "
        "WHERE k.Id = %s", (int(id_clasif),))


def aprobar(id_clasif, id_usuario, revisado_por=""):
    """
    Encola la respuesta. El worker la envía en su próximo ciclo.

    `IdUsuario` es NOT NULL en la cola: se usa el del operador que aprueba.
    """
    k = _clasificacion(id_clasif)
    if not k:
        raise ValueError("la clasificación ya no existe")
    if (k.get("Estado") or "") != "NUEVA":
        raise ValueError("esa clasificación ya fue revisada")
    if not k.get("RemitenteEmail"):
        raise ValueError("el mensaje no tiene remitente")

    texto = k.get("BorradorTexto") or ""
    html = ('<html><body style="font-family:sans-serif;font-size:14px;color:#222">'
            + texto.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
            + "</body></html>")

    ejecuta(
        "INSERT INTO HUB_MailboxColaEnvio "
        "(IdCuenta, IdUsuario, Para, Asunto, HtmlSnapshot, TextoSnapshot, "
        " InResponderA, Estado, Creado) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDIENTE', GETDATE())",
        (k["IdCuenta"], int(id_usuario or 1), k["RemitenteEmail"],
         k.get("BorradorAsunto") or "Cambio de contacto", html, texto,
         k.get("MessageId")),
    )
    ejecuta("UPDATE HUB_MailboxClasificacion SET Estado = 'APROBADA', "
            "Revisado = GETDATE(), RevisadoPor = %s WHERE Id = %s",
            (revisado_por or "", int(id_clasif)))
    # Un correo aprobado significa que el remitente era real: su dominio deja
    # de ser "primer contacto" sospechoso en el futuro.
    try:
        _marcar_conocido((k.get("RemitenteEmail") or "").split("@")[-1])
    except Exception:
        pass
    return True


def rechazar(id_clasif, revisado_por=""):
    k = _clasificacion(id_clasif)
    ejecuta("UPDATE HUB_MailboxClasificacion SET Estado = 'RECHAZADA', "
            "Revisado = GETDATE(), RevisadoPor = %s WHERE Id = %s AND Estado = 'NUEVA'",
            (revisado_por or "", int(id_clasif)))
    # Auto-aprendizaje: cada rechazo suma al dominio. Al llegar al umbral, el
    # dominio se bloquea solo y futuros correos de él van directo a Spam.
    try:
        if k and k.get("RemitenteEmail"):
            dom = k["RemitenteEmail"].split("@")[-1]
            rechazos, bloqueado = _registrar_rechazo(dom, revisado_por or "operador")
            if bloqueado:
                print(f"[triaje] dominio bloqueado automaticamente: {dom} "
                      f"({rechazos} rechazos)", flush=True)
    except Exception:
        pass
    return True


def pendientes(limite=100):
    return filas(
        "SELECT TOP (%s) k.Id, k.Creado, k.RemitenteEmail, k.Destinatario, "
        "       k.Categoria, k.Metodo, k.Confianza, k.DestinoSugerido, "
        "       k.BorradorAsunto, k.BorradorTexto, k.Motivo, c.Email AS CuentaEmail, "
        "       m.Asunto "
        "FROM HUB_MailboxClasificacion k "
        "JOIN HUB_MailboxCuentas c ON c.Id = k.IdCuenta "
        "JOIN HUB_MailboxMensajes m ON m.Id = k.IdMensaje "
        "WHERE k.Estado = 'NUEVA' ORDER BY k.Creado DESC", (int(limite),))


def resumen():
    """Conteos para la cabecera del panel."""
    datos = {"nueva": 0, "aprobada": 0, "rechazada": 0, "huerfanos_mes": 0,
             "bloqueos": 0, "auto_rechazados_mes": 0, "sospechosos_mes": 0}
    try:
        for f in filas("SELECT Estado, COUNT(*) AS n FROM HUB_MailboxClasificacion "
                       "GROUP BY Estado"):
            if f["Estado"].lower() in datos:
                datos[f["Estado"].lower()] = f["n"]
        f = una("SELECT COUNT(*) AS n FROM HUB_MailboxClasificacion "
                "WHERE Categoria = 'huerfano' AND Creado >= DATEADD(month, -1, GETDATE())")
        datos["huerfanos_mes"] = int((f or {}).get("n") or 0)
        f = una("SELECT COUNT(*) AS n FROM HUB_MailboxClasificacion "
                "WHERE Categoria = 'sospechoso' AND Creado >= DATEADD(month, -1, GETDATE())")
        datos["sospechosos_mes"] = int((f or {}).get("n") or 0)
        # Auto-rechazados = ignoradas cuyo motivo empieza con "sospecha".
        f = una("SELECT COUNT(*) AS n FROM HUB_MailboxClasificacion "
                "WHERE Estado = 'IGNORADA' AND Motivo LIKE 'sospecha%' "
                "AND Creado >= DATEADD(month, -1, GETDATE())")
        datos["auto_rechazados_mes"] = int((f or {}).get("n") or 0)
        f = una("SELECT COUNT(*) AS n FROM HUB_MailboxBloqueos WHERE Activo = 1")
        datos["bloqueos"] = int((f or {}).get("n") or 0)
    except Exception:
        pass
    return datos
