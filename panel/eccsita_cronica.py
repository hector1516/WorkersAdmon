"""
panel/eccsita_cronica.py — crónica semanal con IA del grupo ECCSita
==================================================================
`eccsita` guarda los mensajes y sabe armar una crónica determinista. Este
módulo le pone la IA encima: junta los mensajes de la semana ISO, arma el
prompt, pide un JSON y lo valida antes de guardar. Sigue habiendo un solo
dueño de las tablas (`panel/eccsita.py`): aquí solo se lee la semana y se le
pasa el resultado a `eccsita._guardar_semana()`.

Contrato:

    generar_semana(conn, anio, semana, usar_ia=True, exigir_ia=False,
                   chat_id=None) -> dict | None

- `conn`: conexión abierta de `panel.db.get_connection()`; se usa para leer
  los mensajes. La escritura la hace `eccsita._guardar_semana()`.
- `usar_ia=False`: crónica determinista (`GeneradoPor='BASICO'`), sin tocar
  la red.
- `exigir_ia=True`: si la IA falla, **no guarda nada** y lanza
  `CronicaIAError`. Lo usa el worker: la semana queda `CAPTURADA` y se
  reintenta el lunes siguiente en vez de mentir con una crónica básica.
  Por omisión cae al resumen determinista, como pide la API.

Reglas de datos (lo que se vigila en las pruebas):

- **Nunca se inventa.** Toda cita de `momentos` o `frases` tiene que aparecer
  textualmente en un mensaje de la semana; la que no aparece se descarta.
- **Nada sensible ni insultante.** Los mensajes con salud, dinero personal,
  familia o insultos graves no entran al prompt; si la IA los cita igual, se
  descartan de la respuesta.
- **Tope de mensajes.** Como mucho `MAX_MENSAJES`; si hay más se muestrea
  parejo por día para no mandar la semana entera al modelo.
- **Timeout duro.** La llamada corre en un hilo con `TIMEOUT_S` (60 s): si el
  SDK se cuelga se abandona y se cae al resumen básico. Nunca tumba al worker.

Forma del JSON que se le pide a la IA:

    {"resumen": "...", "temas": ["..."],
     "personajes": [{"nombre": "...", "que": "..."}],
     "momentos": [{"cita": "...", "por_que": "..."}],
     "frases": ["..."]}

Las citas se guardan en `Momentos` como `{texto, por_que, autor, fecha}`
(el panel las pinta así); `frases` se suman ahí con `tipo='frase'` para no
inventar una columna que la tabla no tiene.
"""
import json
import logging
import re
import threading

from tz_mx import now_mx

from . import db, eccsita

log = logging.getLogger("panel.eccsita_cronica")

CLAVE_IA = "wa_grupo_ia"        # HUB_Config: "1" prendida (por omisión)
PROVEEDOR = "google_gemini"
TEMPERATURA = 0.4
TIMEOUT_S = 60

MAX_MENSAJES = 2000             # tope de mensajes que se le mandan a la IA
MAX_TEXTO_MENSAJE = 400         # recorte por mensaje en el prompt
MAX_PROMPT = 60000              # tope de caracteres del prompt completo
MIN_CITA = 12                   # cita más corta que esto no se acepta

MAX_TEMAS = 8
MAX_PERSONAJES = 8
MAX_MOMENTOS = 5
MAX_FRASES = 8

# ── SQL ──────────────────────────────────────────────────────────────────────
SQL_MENSAJES = (
    "SELECT AutorId, AutorNombre, Tipo, Texto, Fecha "
    "FROM HUB_GrupoMensajes "
    "WHERE ChatId = %s AND Anio = %s AND Semana = %s "
    "ORDER BY Fecha, Id"
)

SQL_CHAT = (
    "SELECT TOP 1 ChatId FROM HUB_GrupoMensajes "
    "WHERE Anio = %s AND Semana = %s "
    "GROUP BY ChatId ORDER BY COUNT(*) DESC"
)

# ── Filtros ──────────────────────────────────────────────────────────────────
# Lo que no debe salir del grupo ni llegar al modelo. Son heurísticas a
# propósito: prefieren perder una broma antes que publicar la salud o las
# deudas de alguien.
_SENSIBLE = re.compile(
    r"\b(enferm\w*|hospital\w*|doctor\w*|medic\w*|cirug\w*|diagn[oó]stic\w*|"
    r"c[aá]ncer|ansiedad|depresi[oó]n|embaraz\w*|terapia|psic[oó]log\w*|"
    r"sueldo\w*|salario\w*|n[oó]mina|deuda\w*|pr[eé]stamo\w*|hipoteca|"
    r"tarjeta de cr[eé]dito|cuenta de banco|ahorro\w*|"
    r"divorcio|espos[oa]|marido|mam[aá]|pap[aá]|hij[oa]s?|familia|"
    r"infidelidad|embarazo)\b",
    re.IGNORECASE)

_INSULTO = re.compile(
    r"\b(pendej\w*|idiot\w*|imb[eé]cil\w*|culer\w*|mierda|ching\w*|"
    r"put[oa]s?|cabr[oó]n\w*|ojete|mam[oó]n\w*|verga\w*|joto\w*|"
    r"maric[oó]n\w*|retrasad\w*)\b",
    re.IGNORECASE)


# ── Utilidades ───────────────────────────────────────────────────────────────
def _norm(texto):
    """Minúsculas, espacios colapsados y signos de los extremos fuera."""
    limpio = " ".join(str(texto or "").lower().split())
    return limpio.strip(" .,;:!?¿¡\"'«»…-—()[]")


def _limpio(valor, limite=200):
    texto = " ".join(str(valor or "").split())
    return texto[:limite]


def es_sensible(texto):
    """True si el texto toca salud, dinero personal, familia o insulto grave."""
    return bool(_SENSIBLE.search(texto or "") or _INSULTO.search(texto or ""))


def _filas(conn, sql, params):
    cur = conn.cursor(as_dict=True)
    try:
        cur.execute(sql, params)
        return list(cur.fetchall() or [])
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _chat(conn, anio, semana, chat_id=None):
    """El grupo configurado; si no hay, el que más habló esa semana."""
    if chat_id:
        return str(chat_id)
    configurado = (eccsita.config().get("grupo") or "").strip()
    if configurado:
        return configurado
    filas = _filas(conn, SQL_CHAT, (int(anio), int(semana)))
    return str(filas[0].get("ChatId") or "") if filas else ""


def _mensajes(conn, chat, anio, semana):
    if not chat:
        return []
    return _filas(conn, SQL_MENSAJES, (chat, int(anio), int(semana)))


def _muestrear(mensajes, maximo=MAX_MENSAJES):
    """
    Tope de mensajes repartido parejo por día.

    Con 2000 mensajes la IA ya no distingue la semana; sin muestreo, un día
    con 900 mensajes se come el prompt entero. Se toma el mismo cupo de cada
    día y lo que sobra se reparte uno a uno.
    """
    mensajes = list(mensajes)
    if len(mensajes) <= maximo:
        return mensajes

    por_dia = {}
    for m in mensajes:
        fecha = m.get("Fecha")
        dia = fecha.date() if hasattr(fecha, "date") else fecha
        por_dia.setdefault(dia, []).append(m)
    dias = sorted(por_dia.items(), key=lambda d: (str(d[0]), ))
    cupo = max(1, maximo // max(1, len(dias)))
    sobra = maximo - cupo * len(dias)

    salida = []
    for _dia, del_dia in dias:
        cantidad = min(len(del_dia), cupo + (1 if sobra > 0 else 0))
        if sobra > 0 and cantidad > cupo:
            sobra -= 1
        if cantidad >= len(del_dia):
            salida.extend(del_dia)
            continue
        if cantidad == 1:
            indices = [len(del_dia) // 2]
        else:
            paso = (len(del_dia) - 1) / (cantidad - 1)
            indices = [round(i * paso) for i in range(cantidad)]
        salida.extend(del_dia[i] for i in indices)
    return salida[:maximo]


# ── Prompt ───────────────────────────────────────────────────────────────────
def _linea(m):
    fecha = m.get("Fecha")
    cuando = ""
    if hasattr(fecha, "strftime"):
        dia = eccsita.DIAS[fecha.weekday()] if hasattr(fecha, "weekday") else ""
        cuando = f"{dia} {fecha.strftime('%d/%m %H:%M')} "
    texto = _limpio(m.get("Texto"), MAX_TEXTO_MENSAJE)
    return f"[{cuando}] {eccsita._nombre(m)}: {texto}"


def armar_prompt(mensajes, anio, semana):
    """Prompt en español con los mensajes y el contrato de salida."""
    inicio, fin = eccsita._semana_fechas(anio, semana)
    rango = (f" ({inicio.strftime('%d/%m')} al {fin.strftime('%d/%m')})"
             if inicio and fin else "")
    cabecera = [
        "Eres el cronista interno del grupo de WhatsApp de ECCSA en México.",
        f"Escribe la crónica de la semana {semana} de {anio}{rango}.",
        "",
        "Reglas que NO se pueden romper:",
        "1. Usa SOLO lo que dicen los mensajes. No inventes personas, hechos,",
        "   fechas ni citas: si algo no está en los mensajes, no existe.",
        "2. La crónica va en 5 a 8 párrafos, en español, con tono de anécdota",
        "   interna: quien la lea tiene que reconocer la semana.",
        "3. Nada de salud, dinero personal, familia ni insultos graves.",
        "   Ese material no se menciona aunque aparezca en los mensajes.",
        "4. Las citas de 'momentos' y 'frases' son TEXTUALES, copiadas de un",
        "   mensaje tal cual, sin corregir ni completar.",
        "",
        "Devuelve SOLO un objeto JSON, sin markdown ni texto alrededor:",
        '{"resumen": "crónica de 5 a 8 párrafos",',
        ' "temas": ["tema recurrente o chiste interno"],',
        ' "personajes": [{"nombre": "quién", "que": "con qué la hace de oro"}],',
        ' "momentos": [{"cita": "cita textual", "por_que": "por qué da meme o podcast"}],',
        ' "frases": ["frase textual que vale la pena conservar"]}',
        "",
        "- temas: 3 a 6; personajes: 2 a 5; momentos: 3 a 5; frases: 3 a 5.",
        "",
        "Mensajes de la semana:",
    ]
    lineas = [_linea(m) for m in mensajes]
    cuerpo = "\n".join(lineas)
    prompt = "\n".join(cabecera) + "\n" + cuerpo
    return prompt[:MAX_PROMPT]


# ── IA ───────────────────────────────────────────────────────────────────────
def _config_ia():
    try:
        return db.get_ai_config() or {}
    except Exception as exc:
        log.warning("no se pudo leer HUB_AIConfig: %s", exc)
        return {}


def ia_disponible(cfg=None):
    """Hay proveedor, ApiKey y modelo. Sin los tres no se gasta nada."""
    cfg = cfg or _config_ia()
    proveedor = str(cfg.get("provider") or PROVEEDOR).strip().lower()
    return bool(proveedor == PROVEEDOR
                and str(cfg.get("api_key") or "").strip()
                and str(cfg.get("model") or "").strip())


def _generar(prompt, api_key, modelo):
    """Una llamada a Gemini. Se sustituye en las pruebas."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    modelo_ia = genai.GenerativeModel(model_name=modelo)
    respuesta = modelo_ia.generate_content(
        prompt,
        generation_config={"temperature": TEMPERATURA},
        request_options={"timeout": TIMEOUT_S},
    )
    return str(getattr(respuesta, "text", "") or "")


def _llamar(prompt, api_key, modelo, timeout=TIMEOUT_S):
    """Llama al proveedor con un tope duro: si se cuelga, se abandona."""
    resultado = {}

    def trabajo():
        try:
            resultado["valor"] = _generar(prompt, api_key, modelo)
        except Exception as exc:
            resultado["error"] = exc

    hilo = threading.Thread(target=trabajo, daemon=True)
    hilo.start()
    hilo.join(timeout)
    if hilo.is_alive():
        raise TimeoutError(f"la IA tardó más de {timeout} s")
    if "error" in resultado:
        raise resultado["error"]
    return resultado.get("valor", "")


def _parse_json(texto):
    """Diccionario del texto del modelo, o None si no es JSON."""
    if not texto:
        return None
    limpio = str(texto).strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```[a-zA-Z]*\s*", "", limpio)
        limpio = re.sub(r"\s*```$", "", limpio)
    ini, fin = limpio.find("{"), limpio.rfind("}")
    if ini < 0 or fin <= ini:
        return None
    try:
        datos = json.loads(limpio[ini:fin + 1])
    except (TypeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _cita_valida(cita, mensajes):
    """
    El mensaje del que sale la cita, o None si no aparece textualmente.

    Se compara normalizado (minúsculas y espacios) porque el modelo copia
    bien pero a veces cambia un salto de línea o el espaciado.
    """
    buscada = _norm(cita)
    if len(buscada) < MIN_CITA or es_sensible(cita):
        return None
    for m in mensajes:
        if buscada in _norm(m.get("Texto")):
            return m
    return None


def _lista_textos(valor, maximo):
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    salida = []
    for item in valor:
        texto = _limpio(item, 300)
        if texto and not es_sensible(texto):
            salida.append(texto)
        if len(salida) >= maximo:
            break
    return salida


def _personajes(valor):
    if isinstance(valor, (str, dict)):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    salida = []
    for item in valor:
        if isinstance(item, dict):
            nombre = _limpio(item.get("nombre") or item.get("quien"), 120)
            que = _limpio(item.get("que") or item.get("por_que")
                          or item.get("detalle"), 300)
        else:
            nombre, que = _limpio(item, 120), ""
        if not nombre or es_sensible(nombre) or es_sensible(que):
            continue
        salida.append({"nombre": nombre, "que": que})
        if len(salida) >= MAX_PERSONAJES:
            break
    return salida


def _momentos(valor, mensajes, maximo=MAX_MOMENTOS):
    if isinstance(valor, (str, dict)):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    salida = []
    vistas = set()
    for item in valor:
        if isinstance(item, dict):
            cita = item.get("cita") or item.get("texto")
            porque = _limpio(item.get("por_que") or item.get("detalle"), 300)
        else:
            cita, porque = item, ""
        origen = _cita_valida(cita, mensajes)
        if not origen:
            continue
        clave = _norm(cita)
        if clave in vistas:
            continue
        vistas.add(clave)
        salida.append({
            "texto": _limpio(cita, 1000),
            "por_que": porque,
            "autor": eccsita._nombre(origen),
            "fecha": eccsita._iso(origen.get("Fecha")),
        })
        if len(salida) >= maximo:
            break
    return salida


def _validar(datos, mensajes):
    """
    Respuesta cruda de la IA -> (resumen, temas, personajes, momentos).

    None si no hay resumen: sin crónica no hay nada que guardar y toca
    reintentar o caer al determinista. Las citas inventadas o sensibles se
    descartan una por una; que la IA pierda una cita no invalida la crónica.
    """
    if not isinstance(datos, dict):
        return None
    resumen = str(datos.get("resumen") or "").strip()
    if not resumen:
        return None

    temas = _lista_textos(datos.get("temas"), MAX_TEMAS)
    personajes = _personajes(datos.get("personajes"))
    momentos = _momentos(datos.get("momentos"), mensajes)

    # Las frases van al mismo campo (Momentos) con su tipo: la tabla no tiene
    # una columna Frases y crear una por ocho frases no se justifica.
    for frase in _lista_textos(datos.get("frases"), MAX_FRASES):
        origen = _cita_valida(frase, mensajes)
        if not origen:
            continue
        if any(_norm(frase) == _norm(m["texto"]) for m in momentos):
            continue
        momentos.append({
            "texto": _limpio(frase, 1000),
            "por_que": "Frase que vale la pena conservar.",
            "tipo": "frase",
            "autor": eccsita._nombre(origen),
            "fecha": eccsita._iso(origen.get("Fecha")),
        })

    return resumen[:20000], temas, personajes, momentos


def _pedir_ia(mensajes, anio, semana):
    """
    (resumen, temas, personajes, momentos) o (None, motivo).

    Un solo reintento y solo si el JSON no parseó: pedir otra vez el mismo
    prompt completo cuando el modelo contestó bien pero tarde no sirve, y
    60 s más de espera por semana es peor que la crónica determinista.
    """
    cfg = _config_ia()
    if not ia_disponible(cfg):
        return None, "la IA no está configurada"

    api_key = str(cfg.get("api_key") or "").strip()
    modelo = str(cfg.get("model") or "").strip()
    prompt = armar_prompt(mensajes, anio, semana)

    datos = _parse_json(_llamar(prompt, api_key, modelo))
    if datos is None:
        insistir = (prompt + "\n\nATENCIÓN: responde SOLO el JSON, sin texto "
                    "alrededor ni bloques de markdown.")
        datos = _parse_json(_llamar(insistir, api_key, modelo))
    if datos is None:
        return None, "la IA no devolvió JSON válido"

    valido = _validar(datos, mensajes)
    if not valido:
        return None, "la IA devolvió una crónica vacía"
    return valido, ""


# ── API del módulo ───────────────────────────────────────────────────────────
class CronicaIAError(RuntimeError):
    """La IA falló y la semana tiene que quedarse CAPTURADA."""


def generar_semana(conn, anio, semana, usar_ia=True, exigir_ia=False,
                   chat_id=None):
    """
    Arma y guarda la crónica de una semana ISO. None si no hay mensajes.

    Con `usar_ia` usa la IA y, si falla, cae al resumen determinista. Con
    `exigir_ia` (el worker) un fallo lanza `CronicaIAError` y no se guarda
    nada, para no marcar RESUMIDA una semana que la IA nunca resumió.
    """
    anio, semana = int(anio), int(semana)
    chat = _chat(conn, anio, semana, chat_id)
    mensajes = _mensajes(conn, chat, anio, semana)
    if not mensajes:
        return None

    utiles = [m for m in mensajes
              if not es_sensible(f"{eccsita._nombre(m)} {eccsita._texto(m)}")]
    muestra = _muestrear(utiles)

    ia = None
    motivo = ""
    if usar_ia:
        if not muestra:
            motivo = "sin mensajes utilizables para la IA"
        else:
            try:
                ia, motivo = _pedir_ia(muestra, anio, semana)
            except Exception as exc:
                motivo = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("la crónica con IA falló (semana %s de %s): %s",
                            semana, anio, motivo)

    if ia:
        resumen, temas, personajes, momentos = ia
        generado = "IA"
    else:
        if usar_ia and exigir_ia:
            raise CronicaIAError(motivo or "la IA no está disponible")
        if usar_ia and motivo:
            log.warning("semana %s de %s: se guarda la básica (%s)",
                        semana, anio, motivo)
        # La básica también respeta el filtro: sus citas son las más largas de
        # la semana y una de ellas no puede ser la salud de alguien. Si todo
        # el material quedó filtrado, se resume la semana completa (sin
        # mensajes no hay crónica posible).
        base = utiles if utiles else mensajes
        resumen, temas, personajes, momentos = eccsita.resumen_basico(
            base, anio, semana)
        momentos = [m for m in momentos
                    if not es_sensible(m.get("texto"))]
        generado = "BASICO"

    participantes = len({(m.get("AutorId") or m.get("AutorNombre")
                          or "(sin nombre)") for m in mensajes})
    eccsita._guardar_semana(chat, anio, semana, mensajes, participantes,
                            resumen, temas, personajes, momentos,
                            generado_por=generado)

    fila = {
        "Estado": "RESUMIDA",
        "GeneradoPor": generado,
        "FechaGeneracion": now_mx(),
        "Resumen": resumen,
        "Temas": eccsita._texto_json(temas),
        "Personajes": eccsita._texto_json(personajes),
        "Momentos": eccsita._texto_json(momentos),
    }
    datos = eccsita._detalle(chat, anio, semana, fila, mensajes)
    if usar_ia and generado == "BASICO" and motivo:
        datos["avisoIa"] = motivo
    return datos
