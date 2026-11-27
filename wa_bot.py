"""
Motor de respuestas del bot publico de WhatsApp de ECCSA
========================================================

`responder()` decide QUE contestar; no manda nada. El que manda es el webhook
(`panel/wa_webhook.py`) con `openwa_client.enviar_texto()`. Separarlo asi es lo
que permite probar el motor sin SQL Server, sin OpenWA y sin WhatsApp.

Contrato:

    responder(chat_id, telefono, texto, kb=None, ahora=None) -> str | None
    cargar_kb() -> dict

- `kb` es la base de conocimiento (configuracion + personal). Si no se pasa, se
  lee con `cargar_kb()` (60 s de cache). Los tests pasan un `kb` falso.
- `ahora` permite inyectar la hora de Monterrey en las pruebas.
- `None` significa "no contestar": mensaje vacio, solo emojis, grupo, etc.

Reglas de contenido:

- El DATO va siempre primero. El humor, si aplica, va al final y como maximo
  UNA vez por mensaje; nunca dos veces seguidas el mismo chiste.
- El humor se apaga con palabras urgentes y con los temas factura/cobranza.
- Nunca se dan precios, cotizaciones, datos de otros clientes ni informacion
  interna: se rechaza y se canaliza con el responsable.
- Todo lo que se sabe del personal (nombre y telefono) es publico y se puede
  compartir. La identidad del que escribe se resuelve por los ultimos 10
  digitos del telefono contra `HUB_UsuariosWhatsapp`.
"""
import json
import random
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote

from tz_mx import now_mx
import wa_consultas

SITIO = "https://ecc-sa.com.mx"
MAPA_BASE = "https://maps.google.com/?q="

# Mensajes de mas de esto no se intentan entender: se pide un resumen.
MAX_ENTRADA = 800

# Horario de atencion (hora de Monterrey, UTC-6 todo el ano).
HORARIO_TEXTO = ("lunes a viernes de 09:00 a 18:30 y sabados de 09:30 a 13:30")
HORARIO_LV = (9 * 60, 18 * 60 + 30)          # 09:00 - 18:30
HORARIO_SAB = (9 * 60 + 30, 13 * 60 + 30)    # 09:30 - 13:30

# Claves de HUB_Config que forman la base de conocimiento.
CLAVES_CONFIG = {
    "empresa_telefono": "colab_empresa_telefono",
    "empresa_whatsapp": "colab_empresa_whatsapp",
    "direccion": "colab_empresa_direccion",      # opcional: puede no existir
    "horario_texto": "colab_empresa_horario_texto",
    "horario_lv": "colab_empresa_horario_lv",
    "horario_sab": "colab_empresa_horario_sab",
    "rutas": "wa_bot_rutas",                     # JSON tema -> [ids]
    "servicios": "wa_bot_servicios",             # una linea por servicio
}

KB_TTL = 60
_KB_CACHE = {"kb": None, "ts": 0.0}

KB_DEFECTO = {
    "empresa_telefono": "",
    "empresa_whatsapp": "",
    "direccion": "",
    "horario_texto": HORARIO_TEXTO,
    "horario_lv": "09:00 a 18:30",
    "horario_sab": "09:30 a 13:30",
    "rutas": {},
    "servicios": [],
    "personal": [],
}

# Reparto por defecto. En la base manda `wa_bot_rutas` (tema -> ids de
# HUB_Users); esto es el respaldo para que el bot nunca se quede mudo si la
# clave todavia no existe o viene vacia.
RUTAS_DEFECTO = {
    "ventas": ["Daniel", "Alejandro"],
    "proyectos": ["Daniel", "Alejandro"],
    "sistemas": ["Héctor"],
    "it": ["Héctor"],
    "facturas": ["Rosy"],
    "cobranza": ["Priscila"],
    "reportes": ["Priscila"],
    "automoviles": ["Priscila"],
    "default": ["Priscila"],
}

# Temas en orden de prioridad: la primera coincidencia gana.
TEMAS = (
    ("facturas", ("factura", "facturas", "facturacion", "cfdi",
                  "comprobante fiscal")),
    ("cobranza", ("cobranza", "cobrar", "pago", "pagos", "adeudo", "adeudos",
                  "saldo", "estado de cuenta")),
    ("reportes", ("reporte de servicio", "reportes de servicio", "reporte",
                  "reportes")),
    ("automoviles", ("automovil", "automoviles", "auto", "autos", "vehiculo",
                     "vehiculos", "flotilla", "unidad", "unidades")),
    ("ventas", ("venta", "ventas", "cotizacion", "cotizaciones", "precio",
                "precios", "cuanto cuesta", "cuanto sale", "costo", "comprar",
                "proyecto", "proyectos")),
    ("sistemas", ("sistema", "sistemas", "soporte", "informatica",
                  "computadora", "computadoras", "red", "redes", "it", "ti")),
)

# Palabras que apagan el humor: si hay una, el mensaje es serio.
URGENTES = ("urgente", "emergencia", "accidente", "riesgo", "fuego",
            "no funciona", "caido", "parado", "hoy mismo")

# Lo que el bot NO comparte nunca (precios, cotizaciones, datos ajenos).
PROHIBIDO = ("precio", "precios", "cuanto cuesta", "cuanto sale", "costo",
             "costos", "cotizacion", "cotizaciones", "cotizar", "descuento",
             "presupuesto", "datos de cliente", "datos del cliente",
             "datos de un cliente",
             "otro cliente", "otros clientes", "informacion interna",
             "informacion confidencial", "nomina", "sueldo", "salario")

INTENCIONES = {
    "horario": ("horario", "horarios", "abierto", "abiertos", "abren", "abre",
                "cierran", "cierra", "cerrado", "cerrados", "atienden",
                "hora de atencion", "a que hora"),
    "direccion": ("direccion", "ubicacion", "donde estan", "donde queda",
                  "donde se encuentran", "como llego", "como llegar", "mapa",
                  "sucursal", "oficina"),
    "telefonos": ("telefono", "telefonos", "numero", "numeros", "contacto",
                  "comunicarme", "llamarlos", "llamar"),
    "servicios": ("servicio", "servicios", "que hacen", "que ofrecen",
                  "a que se dedican", "catalogo"),
    "canalizar": ("hablar con alguien", "hablar con una persona", "con quien",
                  "me pasas", "pasame", "asesor", "ejecutivo", "atiende"),
    "saludo": ("hola", "buen dia", "buenos dias", "buenas tardes",
               "buenas noches", "buenas", "que tal", "quien eres",
               "quien habla", "informes", "ayuda"),
}

MENU = (
    "Puedo ayudarte con:\n"
    "1. Horario\n"
    "2. Dirección y mapa\n"
    "3. Teléfonos del equipo\n"
    "4. Servicios\n"
    "5. Hablar con alguien\n"
    "Responde con el número de la opción."
)

# Chistes cortos de oficina. Se usa UNO por mensaje, nunca dos seguidos iguales.
CHISTES = (
    "Por cierto: el cafe de la oficina es el verdadero motor de ECCSA.",
    "Dato curioso: contestar rapido por WhatsApp tambien cuenta como servicio express.",
    "Y si, soy un bot, pero prometo no pedir aumento.",
    "Aqui andamos: ni el lunes se nos escapa.",
    "Modo asistente activado; el cafe lo pongo yo, pero virtual.",
    "Tranquilo, contestar mensajes es lo mio: ni parpadeo.",
)

_ultimo_chiste = None


# ─── Texto ───────────────────────────────────────────────────────────────────

def _normalizar(texto):
    """Minusculas, sin acentos y con espacios colapsados (para comparar)."""
    txt = unicodedata.normalize("NFD", str(texto or ""))
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return " ".join(txt.lower().split())


def _con_contenido(norm):
    """True si queda al menos una letra o un digito (descarta emojis y signos)."""
    return any(c.isalnum() for c in norm)


def _contiene(norm, palabras):
    """Coincidencia por palabra completa (evita 'it' dentro de 'solicito')."""
    for palabra in palabras:
        if re.search(r"\b" + re.escape(palabra) + r"\b", norm):
            return True
    return False


def _ultimos10(telefono):
    digitos = re.sub(r"\D", "", str(telefono or ""))
    return digitos[-10:] if len(digitos) >= 10 else ""


def _nombre_pila(nombre):
    partes = str(nombre or "").strip().split()
    if not partes:
        return ""
    primero = partes[0]
    return primero.capitalize() if primero.isupper() else primero


def _telefono_empresa(kb):
    return str(kb.get("empresa_telefono") or "").strip()


def _dato_empresa(kb):
    """Linea con los telefonos publicos de la empresa (o cadena vacia)."""
    tel = _telefono_empresa(kb)
    wa = str(kb.get("empresa_whatsapp") or "").strip()
    if tel and wa and wa != tel:
        return f"Teléfono: {tel} · WhatsApp: {wa}."
    if tel:
        return f"Teléfono: {tel}."
    if wa:
        return f"WhatsApp: {wa}."
    return ""


def _mapa(direccion):
    return MAPA_BASE + quote(direccion, safe="")


# ─── Base de conocimiento ────────────────────────────────────────────────────

def _leer_config(conn):
    """Lee las claves del bot de HUB_Config (sin romper si falta alguna)."""
    claves = list(CLAVES_CONFIG.values())
    marcadores = ", ".join(["%s"] * len(claves))
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT Clave, Valor FROM HUB_Config WHERE Clave IN ({marcadores})",
            tuple(claves))
        filas = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    valores = {}
    for fila in filas:
        try:
            valores[str(fila[0])] = str(fila[1] or "").strip()
        except Exception:
            continue

    kb = {}
    for nombre, clave in CLAVES_CONFIG.items():
        kb[nombre] = valores.get(clave, "")

    # rutas y servicios tienen forma propia.
    rutas = {}
    if kb.get("rutas"):
        try:
            datos = json.loads(kb["rutas"])
            if isinstance(datos, dict):
                rutas = {str(k): v for k, v in datos.items()}
        except Exception:
            rutas = {}
    kb["rutas"] = rutas

    servicios = []
    if kb.get("servicios"):
        servicios = [linea.strip() for linea in str(kb["servicios"]).splitlines()
                     if linea.strip()]
    kb["servicios"] = servicios
    return kb


def _leer_personal(conn):
    """
    Nombre y telefono de todo el personal con WhatsApp registrado. Es publico:
    la empresa decidio que se pueda compartir.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT u.Id, u.Nombre, w.Telefono "
            "FROM HUB_UsuariosWhatsapp w "
            "JOIN HUB_Users u ON u.Id = w.IdUsuario "
            "WHERE w.Activo = 1")
        filas = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    personal = []
    for fila in filas:
        try:
            personal.append({
                "id": int(fila[0]),
                "nombre": str(fila[1] or "").strip(),
                "telefono": str(fila[2] or "").strip(),
            })
        except Exception:
            continue
    return personal


def cargar_kb():
    """
    Base de conocimiento del bot: HUB_Config + personal con WhatsApp.

    Cachea 60 s. Si la base falla, devuelve los valores por defecto: el bot
    puede seguir dando horario, menu y canalizacion por nombre en vez de
    quedarse mudo.
    """
    ahora = time.monotonic()
    cache = _KB_CACHE.get("kb")
    if cache is not None and ahora - _KB_CACHE.get("ts", 0.0) < KB_TTL:
        return cache

    kb = {clave: (list(valor) if isinstance(valor, list) else
                  dict(valor) if isinstance(valor, dict) else valor)
          for clave, valor in KB_DEFECTO.items()}
    try:
        import eccsa_db
        # Conexion cacheada por hilo: NO se cierra aqui.
        conn = eccsa_db.get_connection()
        kb.update(_leer_config(conn))
        kb["personal"] = _leer_personal(conn)
    except Exception as exc:
        print(f"[wa_bot] no se pudo cargar la configuracion: {exc}")

    _KB_CACHE["kb"] = kb
    _KB_CACHE["ts"] = ahora
    return kb


def _kb_cache_reset():
    """Solo para pruebas: obliga a releer la configuracion."""
    _KB_CACHE["kb"] = None
    _KB_CACHE["ts"] = 0.0


# ─── Personal y canalizacion ─────────────────────────────────────────────────

def _identificar(telefono, kb):
    """
    Quien escribe, por los ultimos 10 digitos. Si no esta registrado o la
    consulta fallo (kb sin personal), se trata como desconocido.
    """
    digitos = _ultimos10(telefono)
    if not digitos:
        return None
    for persona in kb.get("personal") or []:
        if not isinstance(persona, dict):
            continue
        if _ultimos10(persona.get("telefono")) == digitos:
            return persona
    return None


def _buscar_personal(valor, kb):
    """Resuelve un id o un nombre de `wa_bot_rutas` contra el personal."""
    personal = [p for p in (kb.get("personal") or []) if isinstance(p, dict)]
    txt = str(valor).strip()
    for persona in personal:
        if persona.get("id") is not None and str(persona["id"]) == txt:
            return persona
    norm = _normalizar(txt)
    if norm:
        for persona in personal:
            nombre = _normalizar(persona.get("nombre"))
            if nombre and (nombre == norm or nombre.startswith(norm + " ")):
                return persona
    return None


def _resolver_ruta(tema, kb):
    """
    Lista de `(nombre, telefono)` para un tema. Manda `wa_bot_rutas`; si la
    clave no existe o no cubre el tema, se usa RUTAS_DEFECTO.
    """
    tema = _normalizar(tema) or "default"
    rutas = kb.get("rutas") if isinstance(kb.get("rutas"), dict) else {}

    valores = rutas.get(tema)
    if valores is None:
        for clave, valor in rutas.items():
            if _normalizar(clave) == tema:
                valores = valor
                break
    # El mapa configurado manda; si no cubre el tema, va el reparto por
    # defecto del tema (sistemas -> Hector, etc.) y al final el default.
    if valores is None:
        valores = RUTAS_DEFECTO.get(tema)
    if valores is None:
        valores = rutas.get("default")
    if valores is None:
        valores = RUTAS_DEFECTO["default"]
    if isinstance(valores, (str, int)):
        valores = [valores]

    items, vistos = [], set()
    for valor in valores or []:
        persona = _buscar_personal(valor, kb)
        if persona:
            nombre = str(persona.get("nombre") or "").strip()
            telefono = str(persona.get("telefono") or "").strip()
        elif str(valor).strip().isdigit():
            # Un id que no esta en HUB_UsuariosWhatsapp: no se inventa nombre.
            nombre, telefono = "", ""
        else:
            nombre, telefono = str(valor).strip(), ""
        clave = (nombre.lower(), telefono)
        if nombre and clave not in vistos:
            vistos.add(clave)
            items.append((nombre, telefono))
    return items


def _frase_responsables(items, kb):
    """'Daniel (5281...) y Alejandro (5281...)' + telefono de la empresa."""
    partes, falta_telefono = [], False
    for nombre, telefono in items:
        if telefono:
            partes.append(f"{nombre} ({telefono})")
        else:
            partes.append(nombre)
            falta_telefono = True

    if not partes:
        frase = "el equipo de ECCSA"
        falta_telefono = True
    elif len(partes) == 1:
        frase = partes[0]
    else:
        frase = ", ".join(partes[:-1]) + " y " + partes[-1]

    texto = frase
    if falta_telefono:
        dato = _dato_empresa(kb)
        if dato:
            texto += f". {dato}"
    return texto


def _respuesta_canalizar(kb, tema, prefijo=None):
    items = _resolver_ruta(tema, kb)
    frase = _frase_responsables(items, kb)
    cuerpo = f"te canalizo con {frase}"
    if not cuerpo.endswith("."):
        cuerpo += "."
    if prefijo:
        return f"{prefijo} {cuerpo}"
    return cuerpo[0].upper() + cuerpo[1:]


def _persona_mencionada(norm, kb):
    """Primera persona del equipo cuyo nombre de pila aparece en el mensaje."""
    for persona in kb.get("personal") or []:
        if not isinstance(persona, dict):
            continue
        nombre = str(persona.get("nombre") or "").strip()
        if not nombre:
            continue
        pila = _normalizar(nombre.split()[0])
        if len(pila) >= 3 and re.search(r"\b" + re.escape(pila) + r"\b", norm):
            return persona
    return None


def _tema(norm):
    """Tema de negocio del mensaje (o None si no se reconoce)."""
    for tema, palabras in TEMAS:
        if _contiene(norm, palabras):
            return tema
    return None


def _es_urgente(norm):
    return _contiene(norm, URGENTES)


def _es_prohibido(norm):
    return _contiene(norm, PROHIBIDO)


# ─── Datos publicos ──────────────────────────────────────────────────────────

def _esta_abierto(ahora=None):
    """L-V 09:00-18:30, sabado 09:30-13:30, domingo cerrado."""
    momento = ahora or now_mx()
    if isinstance(momento, datetime) and momento.tzinfo is not None:
        from tz_mx import to_mx
        momento = to_mx(momento)
    minuto = momento.hour * 60 + momento.minute
    if momento.weekday() <= 4:            # lunes a viernes
        return HORARIO_LV[0] <= minuto <= HORARIO_LV[1]
    if momento.weekday() == 5:            # sabado
        return HORARIO_SAB[0] <= minuto <= HORARIO_SAB[1]
    return False                          # domingo


def _respuesta_horario(kb, ahora=None):
    estado = "abiertos" if _esta_abierto(ahora) else "cerrados"
    horario = str(kb.get("horario_texto") or HORARIO_TEXTO).strip().rstrip(".")
    return f"Ahora estamos {estado}. Horario: {horario}."


def _respuesta_direccion(kb):
    direccion = str(kb.get("direccion") or "").strip()
    if direccion:
        return (f"Estamos en {direccion}. Mapa: {_mapa(direccion)} "
                f"Sitio: {SITIO}.")
    texto = "No tengo cargada la dirección exacta."
    dato = _dato_empresa(kb)
    if dato:
        texto += f" Te la comparte el equipo: {dato}"
    return f"{texto} Sitio: {SITIO}."


def _respuesta_telefonos(kb, persona=None):
    if persona:
        nombre = str(persona.get("nombre") or "").strip()
        telefono = str(persona.get("telefono") or "").strip()
        texto = f"{nombre}: {telefono}." if telefono else f"{nombre} no tiene teléfono registrado."
        dato = _dato_empresa(kb)
        return f"{texto} {dato}".strip()

    lineas = [f"- {p.get('nombre')}: {p.get('telefono')}"
              for p in (kb.get("personal") or [])
              if isinstance(p, dict) and p.get("nombre") and p.get("telefono")]
    if lineas:
        texto = "Teléfonos del equipo:\n" + "\n".join(lineas)
        dato = _dato_empresa(kb)
        return f"{texto}\n{dato}".strip()
    dato = _dato_empresa(kb)
    return dato or f"Te comparten el teléfono en {SITIO}."


def _respuesta_servicios(kb):
    servicios = [s for s in (kb.get("servicios") or []) if str(s).strip()]
    if servicios:
        return "Servicios:\n" + "\n".join(f"- {s}" for s in servicios)
    dato = _dato_empresa(kb)
    return ("No tengo el catálogo a mano." +
            (f" Te lo comparten: {dato}" if dato else f" Escríbenos en {SITIO}."))


def _respuesta_menu(kb):
    return MENU


# ─── Humor ───────────────────────────────────────────────────────────────────

def _elegir_chiste():
    """Un chiste al azar, nunca el mismo que el anterior."""
    global _ultimo_chiste
    opciones = [i for i in range(len(CHISTES)) if i != _ultimo_chiste]
    indice = random.choice(opciones) if opciones else 0
    _ultimo_chiste = indice
    return CHISTES[indice]


def _con_humor(texto, persona, norm, tema, siempre=False):
    """
    Agrega UN chiste al final si el que escribe esta registrado y el mensaje no
    es urgente ni de factura/cobranza. El dato ya va en `texto`.

    Se calla cuando la respuesta es negativa ("no tienes...", "no encontre...")
    o es una canalizacion: burlarse de un problema o de un dato que no existe
    es justo lo que un cliente interno no agradece. En el saludo si va, y en el
    resto es ocasional para que no se vuelva ruido.
    """
    if not persona or _es_urgente(norm) or tema in ("facturas", "cobranza"):
        return texto
    plano = _normalizar(texto)
    if any(marca in plano for marca in ("no tienes", "no encontre", "no pude",
                                        "no hay", "solo las consulta",
                                        "te canalizo")):
        return texto
    # Un desglose de cotización (costos, partidas) no lleva chiste: tapa datos.
    if plano.startswith("cotizacion") or "partidas:" in plano:
        return texto
    saludo = plano.startswith("hola")
    if not (siempre or saludo) and random.random() > 0.5:
        return texto
    return f"{texto}\n\n{_elegir_chiste()}"


# ─── Saludos ─────────────────────────────────────────────────────────────────

CAPACIDADES = ("horario, dirección y mapa, teléfonos del equipo, servicios, "
               "y canalización con la persona correcta")


def _saludo_desconocido():
    return (f"Soy el asistente de ECCSA. Con gusto te ayudo con {CAPACIDADES}. "
            "¿Qué necesitas?")


def _saludo_registrado(nombre):
    return (f"¡Hola, {nombre}! Soy el asistente de ECCSA. Puedo ayudarte con "
            f"{CAPACIDADES}. Cuando esté disponible, también podrás consultar "
            "aquí tus tickets, vales y reportes.")


# ─── Entrada ─────────────────────────────────────────────────────────────────

def _opcion_menu(norm):
    """Numero de opcion del menu escrito por el contacto (1-5), o None."""
    m = re.match(r"^(?:opcion\s*)?([1-5])$", norm.strip())
    return int(m.group(1)) if m else None


def _respuesta_opcion(opcion, kb, ahora):
    if opcion == 1:
        return _respuesta_horario(kb, ahora)
    if opcion == 2:
        return _respuesta_direccion(kb)
    if opcion == 3:
        return _respuesta_telefonos(kb)
    if opcion == 4:
        return _respuesta_servicios(kb)
    return _respuesta_canalizar(kb, "default")


def responder(chat_id, telefono, texto, kb=None, ahora=None):
    """
    Devuelve la respuesta para un mensaje entrante, o None si no hay que
    contestar. No envia nada y no toca la base si recibe `kb`.
    """
    # Grupos: el webhook ya los filtra, pero el motor tambien se cuida.
    if chat_id and str(chat_id).strip().endswith("@g.us"):
        return None
    if texto is None:
        return None

    crudo = str(texto).strip()
    if not crudo:
        return None
    norm = _normalizar(crudo)
    if not _con_contenido(norm):
        return None

    if kb is None:
        kb = cargar_kb()
    persona = _identificar(telefono, kb)
    nombre = _nombre_pila(persona.get("nombre")) if persona else ""

    # Un mensaje enorme no se intenta interpretar: se pide un resumen.
    if len(crudo) > MAX_ENTRADA:
        if nombre:
            return (f"{nombre}, tu mensaje es muy largo. "
                    "¿Me lo resumes en una o dos líneas, por favor?")
        return ("Tu mensaje es muy largo. "
                "¿Me lo resumes en una o dos líneas, por favor?")

    # Opcion numerica del menu.
    opcion = _opcion_menu(norm)
    if opcion is not None:
        return _con_humor(_respuesta_opcion(opcion, kb, ahora), persona, norm,
                          None)

    # Consultas internas de solo lectura: solo para personal registrado. Si no
    # hay consulta clara, se sigue el flujo general del bot.
    if persona:
        consulta = wa_consultas.responder(norm, persona.get("id"))
        if consulta:
            return _con_humor(consulta, persona, norm, _tema(norm))

    tema = _tema(norm)

    # Nunca: precios, cotizaciones, datos de otros clientes, info interna.
    if _es_prohibido(norm):
        return _respuesta_canalizar(
            kb, tema or "default", prefijo="No puedo compartir eso;")

    persona_nombrada = _persona_mencionada(norm, kb)
    if persona_nombrada and _contiene(norm, INTENCIONES["canalizar"]):
        items = [(str(persona_nombrada.get("nombre") or "").strip(),
                  str(persona_nombrada.get("telefono") or "").strip())]
        return f"Te canalizo con {_frase_responsables(items, kb)}."
    if persona_nombrada and _contiene(norm, INTENCIONES["telefonos"]):
        return _con_humor(_respuesta_telefonos(kb, persona_nombrada), persona,
                          norm, tema)

    if _contiene(norm, INTENCIONES["horario"]):
        cuerpo = _respuesta_horario(kb, ahora)
    elif _contiene(norm, INTENCIONES["direccion"]):
        cuerpo = _respuesta_direccion(kb)
    elif _contiene(norm, INTENCIONES["servicios"]):
        cuerpo = _respuesta_servicios(kb)
    elif _contiene(norm, INTENCIONES["telefonos"]):
        cuerpo = _respuesta_telefonos(kb)
    elif _contiene(norm, INTENCIONES["canalizar"]):
        cuerpo = _respuesta_canalizar(kb, tema or "default")
    elif persona and _contiene(norm, INTENCIONES["saludo"]):
        cuerpo = _saludo_registrado(nombre)
    elif _contiene(norm, INTENCIONES["saludo"]):
        return _saludo_desconocido()      # desconocido: formal y sin chiste
    elif tema:
        cuerpo = _respuesta_canalizar(kb, tema)
    elif persona:
        cuerpo = _saludo_registrado(nombre)
    else:
        return _respuesta_menu(kb)

    return _con_humor(cuerpo, persona, norm, tema)
