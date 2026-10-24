"""
mailbox_worker/triaje.py — clasificación del correo y respuesta sugerida
=========================================================================

QUÉ HACE
Cada mensaje nuevo se clasifica UNA vez, en tres pasos ordenados por costo:

  1. **Reglas deterministas** (0 tokens): cabeceras de envío masivo, rebotes,
     correo interno, y resolución del destinatario contra HUB_Users.
  2. **Compuerta** (0 tokens): solo pasa a revisión lo que parece una persona
     real escribiéndose de buenos modos a alguien que ya no está.
  3. **Gemini** (opcional y con tope): solo para los candidatos de la compuerta,
     y solo para CONFIRMAR que es una persona y sugerir el buzón de destino.

LA IA NO REDACTA LA RESPUESTA. Se usa una plantilla aprobada y la IA solo
decide a qué buzón redirigir. Es a la vez lo más barato y lo más seguro: la
empresa nunca manda un texto que no aprobó.

MODO REVISIÓN (el que está activo)
Nada se envía solo. Cada candidato queda en `HUB_MailboxClasificacion` con un
borrador y el operador lo aprueba desde el panel (pestaña 🛡️ Triaje). Aprobar
encola el envío en `HUB_MailboxColaEnvio`, que el worker ya sabe drenar.

CRITERIO DE "EX EMPLEADO" (definido por el usuario, 2026-10-24)
Si la dirección del destinatario NO está en `HUB_Users` (o está con
`Activo = 0`), es de alguien que ya no está. Solo aplica a nuestro dominio: un
remitente externo que no está en HUB_Users no es un ex empleado.
"""
import json
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
    """Slug de la IA -> dirección real, configurable por HUB_Config."""
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
    (estado, id_usuario, nombre). Estados: activa | exempleado | desconocida.

    `HUB_MailboxDirecciones` manda si tiene un estado manual ('activa' o
    'exempleado'); si está en 'auto' o no existe, se resuelve contra HUB_Users.
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

    # No está en la base de usuarios: si es de nuestro dominio, es de alguien
    # que ya no está (criterio del usuario).
    if e.split("@")[-1] in DOMINIOS_PROPIOS:
        return "exempleado", None, ""
    return "desconocida", None, ""


# ── Clasificación determinista ────────────────────────────────────────────────

def clasificar(msg):
    """
    Devuelve un dict con categoria, confianza, motivo y (si aplica)
    destinatario. No llama a la IA.
    """
    rem = (msg.get("RemitenteEmail") or "").strip().lower()
    asunto = (msg.get("Asunto") or "").lower()
    dominio_rem = rem.split("@")[-1] if "@" in rem else ""

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
        if estado == "exempleado":
            return {"categoria": "huerfano", "confianza": 0.85, "metodo": "regla",
                    "motivo": "el destinatario ya no está en HUB_Users",
                    "destinatario": d, "id_usuario": idu, "nombre": nombre}

    if any(resolver(d)[0] == "activa" for d in candidatos):
        return {"categoria": "activo", "confianza": 0.9, "metodo": "regla",
                "motivo": "el destinatario es un usuario activo"}

    return {"categoria": "normal", "confianza": 0.5, "metodo": "regla",
            "motivo": "sin señal de error de destinatario"}


# ── IA (opcional, con tope) ───────────────────────────────────────────────────

def _ia_disponible():
    if not _cfg_bool("triaje_ia", True):
        return False
    tope = int(_cfg("triaje_ia_tope_dia", "25") or 25)
    hoy = una("SELECT COUNT(*) AS n FROM HUB_MailboxClasificacion "
              "WHERE Metodo = 'ia' AND Creado >= CONVERT(date, GETDATE())")
    return int((hoy or {}).get("n") or 0) < tope


def _ia(msg, destinatario):
    """
    Confirma si es una persona y sugiere buzón. Devuelve None si no hay clave,
    si se agotó el tope o si el proveedor falla: el triaje sigue sin IA.
    """
    try:
        import google.generativeai as genai
        from eccsa_db import get_ai_config

        cfg = get_ai_config() or {}
        api_key = cfg.get("api_key") or ""
        modelo = cfg.get("model") or "gemini-2.0-flash"
        if not api_key:
            return None

        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(
            model_name=modelo,
            system_instruction="Clasificas correo corporativo. Respondes SOLO JSON válido.",
        )
        prompt = (
            "Un correo llegó a ECCSA y uno de sus destinatarios ya no trabaja aquí.\n"
            f"Remitente: {msg.get('RemitenteEmail')}\n"
            f"Asunto: {(msg.get('Asunto') or '')[:120]}\n"
            f"Extracto: {(msg.get('Extracto') or '')[:200]}\n"
            f"Destinatario huérfano: {destinatario}\n\n"
            "¿Quien escribe es una PERSONA que se equivocó de dirección "
            "(no publicidad, no boletín, no automatizado)? ¿A qué buzón conviene "
            "redirigirla?\n"
            'Responde: {"persona": true|false, "destino": '
            '"ventas|soporte|rrhh|admin|general|null", "confianza": 0.0-1.0}'
        )
        r = m.generate_content(
            prompt,
            generation_config={"max_output_tokens": 120, "temperature": 0},
        )
        datos = json.loads((getattr(r, "text", "") or "{}").strip())
        uso = getattr(r, "usage_metadata", None)
        return {
            "persona": bool(datos.get("persona")),
            "destino": (datos.get("destino") or "").strip() or None,
            "confianza": float(datos.get("confianza") or 0),
            "modelo": modelo,
            "tokens_in": int(getattr(uso, "prompt_token_count", 0) or 0),
            "tokens_out": int(getattr(uso, "candidates_token_count", 0) or 0),
        }
    except Exception as exc:
        print(f"[triaje] IA no disponible: {type(exc).__name__}: {str(exc)[:160]}",
              flush=True)
        return None


# ── Borrador ──────────────────────────────────────────────────────────────────

def plantilla(msg, destinatario, destino):
    """Asunto y cuerpo del borrador. Plantilla fija: la IA no redacta."""
    nombre = (msg.get("RemitenteNombre") or "").strip().split(" ")[0]
    saludo = f"Hola {nombre}" if nombre else "Hola"
    destino = destino or "contacto@ecc-sa.com.mx"
    asunto = (msg.get("Asunto") or "").strip()
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
    n = 0
    for mid in ids:
        try:
            if una("SELECT Id FROM HUB_MailboxClasificacion WHERE IdMensaje = %s", (mid,)):
                continue
            msg = _mensaje(mid)
            if not msg:
                continue

            r = clasificar(msg)
            ia = None
            if r["categoria"] == "huerfano" and _ia_disponible():
                ia = _ia(msg, r.get("destinatario"))
                if ia:
                    r["metodo"] = "ia"
                    r["confianza"] = ia["confianza"] or r["confianza"]
                    if not ia["persona"]:
                        # La IA dice que no es una persona: no se responde.
                        r["categoria"] = "bulk"
                        r["motivo"] = "la IA lo descartó como no humano"
                    elif ia.get("destino"):
                        r["destino_slug"] = ia["destino"]

            destino = destino_de(r.get("destino_slug")) if r["categoria"] == "huerfano" else None
            borrador = (plantilla(msg, r["destinatario"], destino)
                        if r["categoria"] == "huerfano" else {"asunto": None, "texto": None})

            ejecuta(
                "INSERT INTO HUB_MailboxClasificacion "
                "(IdMensaje, IdCuenta, RemitenteEmail, Destinatario, Categoria, "
                " Confianza, Metodo, Motivo, DestinoSugerido, BorradorAsunto, "
                " BorradorTexto, Estado, Modelo, TokensIn, TokensOut) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'NUEVA', %s, %s, %s)",
                (msg["Id"], msg["IdCuenta"], msg.get("RemitenteEmail"),
                 r.get("destinatario"), r["categoria"], r.get("confianza"),
                 r["metodo"], (r.get("motivo") or "")[:400], destino,
                 borrador["asunto"], borrador["texto"],
                 (ia or {}).get("modelo"), (ia or {}).get("tokens_in"),
                 (ia or {}).get("tokens_out")),
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
    return True


def rechazar(id_clasif, revisado_por=""):
    ejecuta("UPDATE HUB_MailboxClasificacion SET Estado = 'RECHAZADA', "
            "Revisado = GETDATE(), RevisadoPor = %s WHERE Id = %s AND Estado = 'NUEVA'",
            (revisado_por or "", int(id_clasif)))
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
    datos = {"nueva": 0, "aprobada": 0, "rechazada": 0, "ia_mes": 0,
             "tokens_mes": 0, "huerfanos_mes": 0}
    try:
        for f in filas("SELECT Estado, COUNT(*) AS n FROM HUB_MailboxClasificacion "
                       "GROUP BY Estado"):
            if f["Estado"].lower() in datos:
                datos[f["Estado"].lower()] = f["n"]
        f = una("SELECT COUNT(*) AS ia, ISNULL(SUM(TokensIn + TokensOut), 0) AS tk "
                "FROM HUB_MailboxClasificacion WHERE Metodo = 'ia' "
                "AND Creado >= DATEADD(month, -1, GETDATE())")
        datos["ia_mes"] = int((f or {}).get("ia") or 0)
        datos["tokens_mes"] = int((f or {}).get("tk") or 0)
        f = una("SELECT COUNT(*) AS n FROM HUB_MailboxClasificacion "
                "WHERE Categoria = 'huerfano' AND Creado >= DATEADD(month, -1, GETDATE())")
        datos["huerfanos_mes"] = int((f or {}).get("n") or 0)
    except Exception:
        pass
    return datos
