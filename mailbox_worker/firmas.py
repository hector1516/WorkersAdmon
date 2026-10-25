"""
mailbox_worker/firmas.py — Gestión de firmas de correo de Mailbox.

Cada usuario puede tener varias firmas y marcar una como predeterminada.
La firma se aplica a TODAS las cuentas IMAP asignadas a ese usuario.

Los datos del usuario (nombre, cargo, teléfono, correo, dirección) se
inyectan en las plantillas mediante placeholders:
    {{nombre}}, {{cargo}}, {{telefono}}, {{correo}}, {{direccion}},
    {{web}}, {{logo}}, {{foto}}

El logo de ECCSA y la foto del usuario se adjuntan como imágenes inline
(cid:) al enviar, ya que smtp.py resuelve IdFirma y construye el MIME.
"""

import random
from .db import filas, una, ejecuta, ultimo_id

# Plantillas predefinidas embebidas por si la tabla aún no está poblada.
# Se usan como respaldo y para el editor visual.
PLANTILLAS_PREDEFINIDAS = [
    {
        "nombre": "Corporativa ECCSA",
        "descripcion": "Firma corporativa con tabla, logo de ECCSA a la izquierda y datos a la derecha.",
        "html": """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0 0 10px 0;"><table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="middle" style="padding-right:12px;"><img src="cid:eccsa-logo" width="48" height="48" alt="ECCSA" style="display:block;border:0;" /></td><td align="left" valign="middle" style="font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:14px;color:#1E293B;"><div style="font-weight:bold;color:#FF6B00;font-size:15px;">{{nombre}}</div><div style="font-size:12px;color:#64748B;">{{cargo}}</div></td></tr></table></td></tr><tr><td align="left" valign="top" style="padding:8px 0 0 0;border-top:1px solid #FF6B00;"><table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#64748B;">📞 {{telefono}}</div></td><td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#64748B;">✉️ {{correo}}</div></td><td align="left" valign="top"><div style="font-size:12px;color:#64748B;">🌐 {{web}}</div></td></tr></table></td></tr><tr><td align="left" valign="top" style="padding:6px 0 0 0;"><div style="font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></body></html>""",
        "categoria": "corporativa",
        "orden": 1,
    },
    {
        "nombre": "Moderna ECCSA",
        "descripcion": "Barra lateral naranja de ECCSA, foto del usuario opcional y tipografía limpia.",
        "html": """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0;"><table role="presentation" cellspacing="0" cellpadding="0" border="0" style="background:#FFFFFF;border-left:4px solid #FF6B00;"><tr><td align="left" valign="top" style="padding:10px 14px 10px 14px;"><div style="font-weight:bold;color:#1E293B;font-size:14px;">{{nombre}}</div><div style="font-size:12px;color:#FF6B00;font-weight:600;">{{cargo}}</div><div style="padding-top:6px;font-size:12px;color:#64748B;"><div style="padding-bottom:2px;">📞 {{telefono}}</div><div style="padding-bottom:2px;">✉️ {{correo}}</div><div style="padding-bottom:2px;">🌐 {{web}}</div></div><div style="padding-top:8px;border-top:1px solid #E2E8F0;font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></td></tr></table></body></html>""",
        "categoria": "moderna",
        "orden": 2,
    },
    {
        "nombre": "Simple ECCSA",
        "descripcion": "Firma mínima y elegante. Solo datos esenciales con el logo de ECCSA.",
        "html": """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0 0 10px 0;"><img src="cid:eccsa-logo" width="32" height="32" alt="ECCSA" style="display:block;border:0;" /></td></tr><tr><td align="left" valign="top" style="padding:4px 0 0 0;"><div style="font-weight:bold;color:#1E293B;font-size:13px;">{{nombre}}</div><div style="font-size:12px;color:#FF6B00;">{{cargo}}</div><div style="padding-top:4px;font-size:12px;color:#64748B;">{{telefono}} · {{correo}} · {{web}}</div><div style="padding-top:4px;font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></body></html>""",
        "categoria": "simple",
        "orden": 3,
    },
]

# Datos fijos de ECCSA (no dependen del usuario)
DATOS_ECCSA = {
    "web": "www.ecc-sa.com.mx",
    "direccion": "Monterrey, Nuevo León, México",
    "logo_cid": "eccsa-logo",
}


# ═════════════════════════════════════════════════════════════════════════════
# Lectura de firmas de usuario
# ═════════════════════════════════════════════════════════════════════════════

def listar_firmas_usuario(id_usuario: int) -> list:
    """Todas las firmas de un usuario, ordenadas (predeterminada primero)."""
    try:
        return filas(
            "SELECT Id, IdUsuario, Nombre, Html, Texto, Activa, Predeterminada, "
            "       Creado, Modificado "
            "FROM HUB_MailboxFirmas WHERE IdUsuario = %s AND Activa = 1 "
            "ORDER BY Predeterminada DESC, Nombre",
            (id_usuario,),
        )
    except Exception:
        # Si la tabla no existe todavía, devolvemos lista vacía
        return []


def obtener_firma(id_firma: int) -> dict | None:
    """Una firma por su Id."""
    try:
        return una(
            "SELECT Id, IdUsuario, Nombre, Html, Texto, Activa, Predeterminada, "
            "       Creado, Modificado "
            "FROM HUB_MailboxFirmas WHERE Id = %s",
            (id_firma,),
        )
    except Exception:
        return None


def obtener_firma_predeterminada(id_usuario: int) -> dict | None:
    """La firma predeterminada de un usuario, o la primera activa si no hay predeterminada."""
    try:
        f = una(
            "SELECT TOP (1) Id, IdUsuario, Nombre, Html, Texto, Activa, Predeterminada, "
            "       Creado, Modificado "
            "FROM HUB_MailboxFirmas WHERE IdUsuario = %s AND Activa = 1 AND Predeterminada = 1 "
            "ORDER BY Creado DESC",
            (id_usuario,),
        )
        if f:
            return f
        # Fallback: la primera activa
        return una(
            "SELECT TOP (1) Id, IdUsuario, Nombre, Html, Texto, Activa, Predeterminada, "
            "       Creado, Modificado "
            "FROM HUB_MailboxFirmas WHERE IdUsuario = %s AND Activa = 1 "
            "ORDER BY Creado DESC",
            (id_usuario,),
        )
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Escritura de firmas
# ═════════════════════════════════════════════════════════════════════════════

def crear_firma(
    id_usuario: int,
    nombre: str,
    html: str,
    texto: str | None = None,
    predeterminada: bool = False,
) -> int | None:
    """Crea una firma para un usuario. Si predeterminada, quita la anterior."""
    try:
        if predeterminada:
            ejecuta(
                "UPDATE HUB_MailboxFirmas SET Predeterminada = 0 "
                "WHERE IdUsuario = %s AND Predeterminada = 1",
                (id_usuario,),
            )
        n = ejecuta(
            "INSERT INTO HUB_MailboxFirmas "
            "(IdUsuario, Nombre, Html, Texto, Activa, Predeterminada, Modificado) "
            "VALUES (%s, %s, %s, %s, 1, %s, GETDATE())",
            (
                id_usuario,
                nombre,
                html,
                texto or "",
                1 if predeterminada else 0,
            ),
        )
        return ultimo_id()
    except Exception:
        return None


def actualizar_firma(
    id_firma: int,
    nombre: str | None = None,
    html: str | None = None,
    texto: str | None = None,
    activa: bool | None = None,
    predeterminada: bool | None = None,
) -> bool:
    """Actualiza campos de una firma existente."""
    sets = []
    params = []
    if nombre is not None:
        sets.append("Nombre = %s"); params.append(nombre)
    if html is not None:
        sets.append("Html = %s"); params.append(html)
    if texto is not None:
        sets.append("Texto = %s"); params.append(texto)
    if activa is not None:
        sets.append("Activa = %s"); params.append(1 if activa else 0)
    if predeterminada is not None:
        sets.append("Predeterminada = %s"); params.append(1 if predeterminada else 0)
    if not sets:
        return False
    sets.append("Modificado = GETDATE")
    params.append(id_firma)
    sql = "UPDATE HUB_MailboxFirmas SET " + ", ".join(sets) + " WHERE Id = %s"
    try:
        if predeterminada is True:
            # Obtener id_usuario para quitar predeterminada de las otras
            f = una("SELECT IdUsuario FROM HUB_MailboxFirmas WHERE Id = %s", (id_firma,))
            if f:
                ejecuta(
                    "UPDATE HUB_MailboxFirmas SET Predeterminada = 0 "
                    "WHERE IdUsuario = %s AND Id <> %s AND Predeterminada = 1",
                    (f["IdUsuario"], id_firma),
                )
        ejecuta(sql, tuple(params))
        return True
    except Exception:
        return False


def eliminar_firma(id_firma: int) -> bool:
    """Desactiva una firma (soft delete)."""
    try:
        ejecuta(
            "UPDATE HUB_MailboxFirmas SET Activa = 0, Modificado = GETDATE() WHERE Id = %s",
            (id_firma,),
        )
        return True
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Plantillas predefinidas
# ═════════════════════════════════════════════════════════════════════════════

def listar_plantillas() -> list:
    """Todas las plantillas activas del sistema."""
    try:
        return filas(
            "SELECT Id, Nombre, Descripcion, Html, Categoria, Orden "
            "FROM HUB_MailboxFirmasPlantillas WHERE Activa = 1 "
            "ORDER BY Orden, Nombre",
        )
    except Exception:
        # Respaldo con las plantillas embebidas
        return [
            {
                "Id": None,
                "Nombre": p["nombre"],
                "Descripcion": p["descripcion"],
                "Html": p["html"],
                "Categoria": p["categoria"],
                "Orden": p["orden"],
            }
            for p in PLANTILLAS_PREDEFINIDAS
        ]


def aplicar_plantilla(html_plantilla: str, datos_usuario: dict) -> str:
    """
    Reemplaza los placeholders de una plantilla con los datos del usuario.
    Placeholders soportados:
        {{nombre}}, {{cargo}}, {{telefono}}, {{correo}}, {{direccion}},
        {{web}}, {{logo}}, {{foto}}, {{frase}}
    """
    resultado = html_plantilla
    reemplazos = {
        "{{nombre}}": datos_usuario.get("nombre", "") or "",
        "{{cargo}}": datos_usuario.get("cargo", "") or "",
        "{{telefono}}": datos_usuario.get("telefono", "") or "",
        "{{correo}}": datos_usuario.get("correo", "") or "",
        "{{direccion}}": datos_usuario.get("direccion", DATOS_ECCSA["direccion"]) or DATOS_ECCSA["direccion"],
        "{{web}}": datos_usuario.get("web", DATOS_ECCSA["web"]) or DATOS_ECCSA["web"],
        "{{logo}}": DATOS_ECCSA["logo_cid"],
        "{{foto}}": datos_usuario.get("foto_cid", "") or "",
        "{{frase}}": obtener_frase_aleatoria(),
    }
    for clave, valor in reemplazos.items():
        resultado = resultado.replace(clave, valor)
    return resultado


# ═════════════════════════════════════════════════════════════════════════════
# Frases aleatorias para firmas
# Se elige una frase al cargar la app y se inyecta en {{frase}}
# ═════════════════════════════════════════════════════════════════════════════

_FRASES_CACHE: list | None = None

def _cargar_frases() -> list:
    """Carga las frases activas desde la base, con respaldo embebido."""
    try:
        filas_db = filas(
            "SELECT Frase, Autor FROM HUB_MailboxFirmasFrases WHERE Activa = 1 ORDER BY Orden"
        )
        if filas_db:
            return [(f["Frase"], f.get("Autor") or "") for f in filas_db]
    except Exception:
        pass
    # Respaldo embebido si la tabla no existe
    return [
        ("La inteligencia es la capacidad de evitar hacer trabajo, y de lograr que el trabajo se haga.", "ECCSA"),
        ("El éxito es la suma de pequeños esfuerzos repetidos día tras día.", "ECCSA"),
        ("La mejor manera de predecir el futuro es crearlo.", "ECCSA"),
        ("Trabajar en equipo divide las tareas y multiplica los resultados.", "ECCSA"),
        ("La calidad no es un acto, es un hábito.", "ECCSA"),
        ("Cada cliente merece nuestra mejor atención, siempre.", "ECCSA"),
        ("Innovar es necesario para seguir siendo relevantes.", "ECCSA"),
        ("El compromiso con el servicio nos distingue.", "ECCSA"),
    ]


def obtener_frase_aleatoria() -> str:
    """
    Devuelve una frase aleatoria de la lista activa.
    Se cachea durante la vida del proceso para no consultar la BD cada vez.
    Para forzar una nueva frase, llamar a resetear_cache_frases().
    """
    global _FRASES_CACHE
    if _FRASES_CACHE is None:
        _FRASES_CACHE = _cargar_frases()
    if not _FRASES_CACHE:
        return ""
    frase, autor = random.choice(_FRASES_CACHE)
    if autor:
        return f"{frase} — {autor}"
    return frase


def resetear_cache_frases():
    """Fuerza a recargar las frases desde la base en la próxima llamada."""
    global _FRASES_CACHE
    _FRASES_CACHE = None


def listar_frases() -> list:
    """Todas las frases activas para el editor del admin."""
    try:
        return filas(
            "SELECT Id, Frase, Autor, Activa, Orden, Creado "
            "FROM HUB_MailboxFirmasFrases WHERE Activa = 1 ORDER BY Orden, Id"
        )
    except Exception:
        return []


def agregar_frase(frase: str, autor: str = "", orden: int = 0) -> int | None:
    """Agrega una nueva frase al sistema."""
    try:
        ejecuta(
            "INSERT INTO HUB_MailboxFirmasFrases (Frase, Autor, Activa, Orden) "
            "VALUES (%s, %s, 1, %s)",
            (frase, autor, orden),
        )
        return ultimo_id()
    except Exception:
        return None


def eliminar_frase(id_frase: int) -> bool:
    """Desactiva una frase (soft delete)."""
    try:
        ejecuta(
            "UPDATE HUB_MailboxFirmasFrases SET Activa = 0 WHERE Id = %s",
            (id_frase,),
        )
        return True
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# HTML a texto plano (para el TextoSnapshot)
# ═════════════════════════════════════════════════════════════════════════════

def html_a_texto(html: str) -> str:
    """Convierte HTML simple a texto plano legible."""
    if not html:
        return ""
    import re
    texto = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    texto = re.sub(r"<style.*?</style>", " ", texto, flags=re.S | re.I)
    texto = re.sub(r"<br\s*/?>", "\n", texto, flags=re.I)
    texto = re.sub(r"</p\s*>|</div\s*>|</li\s*>|</tr\s*>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    import html as _html
    texto = _html.unescape(texto)
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n\n", texto)
    return texto.strip()
