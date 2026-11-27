"""
Redaccion con IA del bot publico de WhatsApp de ECCSA
=====================================================

`wa_bot.responder()` decide QUE contestar con reglas. Este modulo solo pule
COMO se lee ese texto, y solo si el dueno lo prendio.

Contrato:

    ia_activa(kb=None) -> bool
    mejorar(texto, kb=None) -> str

- `ia_activa` exige DOS cosas: `HUB_Config.wa_bot_ia` igual a `1` (por
  defecto esta apagada) y una ApiKey + Modelo en `HUB_AIConfig`. Si falta una,
  el bot contesta tal cual lo decidieron las reglas.
- `mejorar` devuelve SIEMPRE un texto seguro: si la IA falla, tarda mas de
  `TIMEOUT_S`, o devuelve algo que cambio los datos (numeros, ligas o emojis),
  regresa el texto original. Nunca lanza.

Lo que la IA NO puede tocar (se valida y si cambia se descarta la respuesta):

- cifras, montos, telefonos, folios y fechas (los digitos del mensaje);
- ligas;
- emojis.

Y no se pule lo que no debe pulirse: un texto de mas de `MAX_TEXTO`
caracteres, un desglose de cotizacion (`Partidas:`) o una lista larga de
vales/reportes se manda literal.
"""
import re
import threading
import time

# Configuracion.
CLAVE_ACTIVA = "wa_bot_ia"         # HUB_Config: "1" prende la redaccion
PROVEEDOR = "google_gemini"
TEMPERATURA = 0.3
TIMEOUT_S = 15

# La IA solo pule textos cortos; lo largo se manda tal cual.
MAX_TEXTO = 600

# Crecimiento maximo tolerado sobre el original.
MAX_CRECIMIENTO = 0.20

# Una lista de mas de estos renglones con guion ya es un desglose (vales,
# reportes, telefonos del equipo): ahi la IA solo puede romper el formato.
MAX_RENGLONES_LISTA = 4

PROMPT = (
    "Eres el asistente de ECCSA en WhatsApp. Reescribe el mensaje para que se "
    "lea natural y amable. NO cambies ni agregues datos, cifras, nombres, "
    "fechas ni ligas. NO inventes. Devuelve solo el mensaje final."
)

_IA_TTL = 60
_IA_CACHE = {"cfg": None, "ts": 0.0}
_FLAG_TTL = 60
_FLAG_CACHE = {"valor": None, "ts": 0.0}


# ─── Configuracion ───────────────────────────────────────────────────────────

def _cache_reset():
    """Solo para pruebas: obliga a releer la configuracion de la base."""
    _IA_CACHE["cfg"] = None
    _IA_CACHE["ts"] = 0.0
    _FLAG_CACHE["valor"] = None
    _FLAG_CACHE["ts"] = 0.0


def _config_ia(kb=None):
    """
    Proveedor, ApiKey y Modelo. Manda lo que traiga `kb` (pruebas); si no,
    `HUB_AIConfig` con 60 s de cache. Nunca lanza: devuelve {} si falla.
    """
    if isinstance(kb, dict):
        pista = {k: kb.get(k) for k in ("ai_provider", "ai_api_key", "ai_model")}
        if any(v for v in pista.values()):
            return {
                "provider": str(pista.get("ai_provider") or PROVEEDOR).strip(),
                "api_key": str(pista.get("ai_api_key") or "").strip(),
                "model": str(pista.get("ai_model") or "").strip(),
            }

    ahora = time.monotonic()
    cache = _IA_CACHE.get("cfg")
    if cache is not None and ahora - _IA_CACHE.get("ts", 0.0) < _IA_TTL:
        return cache

    cfg = {}
    try:
        import eccsa_db
        cfg = eccsa_db.get_ai_config() or {}
    except Exception as exc:
        print(f"[wa_redaccion] no se pudo leer HUB_AIConfig: {exc}")
        cfg = {}

    _IA_CACHE["cfg"] = cfg
    _IA_CACHE["ts"] = ahora
    return cfg


def _bandera(kb=None):
    """
    Valor de `HUB_Config.wa_bot_ia`. Apagada por defecto: mientras el dueno no
    la prenda, el bot no gasta IA.
    """
    if isinstance(kb, dict):
        # `kb` es la foto de la configuracion: si no trae la clave, esta
        # apagada. Asi una kb de prueba nunca toca la base. Se aceptan los dos
        # nombres: el de HUB_Config y el corto de `wa_bot.cargar_kb()`.
        valor = kb.get(CLAVE_ACTIVA, kb.get("ia", "0"))
        return str(valor or "0").strip()

    ahora = time.monotonic()
    cache = _FLAG_CACHE.get("valor")
    if cache is not None and ahora - _FLAG_CACHE.get("ts", 0.0) < _FLAG_TTL:
        return cache

    valor = "0"
    try:
        import eccsa_db
        conn = eccsa_db.get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = %s",
                        (CLAVE_ACTIVA,))
            fila = cur.fetchone()
            valor = str(fila[0] or "").strip() if fila else "0"
        finally:
            try:
                cur.close()
            except Exception:
                pass
    except Exception as exc:
        print(f"[wa_redaccion] no se pudo leer {CLAVE_ACTIVA}: {exc}")
        valor = "0"

    _FLAG_CACHE["valor"] = valor
    _FLAG_CACHE["ts"] = ahora
    return valor


def ia_activa(kb=None):
    """True solo si el dueno la prendio Y hay ApiKey/Modelo configurados."""
    try:
        if _bandera(kb) != "1":
            return False
        cfg = _config_ia(kb) or {}
        return bool(str(cfg.get("api_key") or "").strip()
                    and str(cfg.get("model") or "").strip())
    except Exception:
        return False


# ─── Que se pule ─────────────────────────────────────────────────────────────

def se_pule(texto):
    """
    True si el texto vale la pena pulir: corto y sin estructura que la IA
    pueda romper (cotizacion con partidas, lista larga de vales/reportes).
    """
    texto = str(texto or "")
    if not texto.strip():
        return False
    if len(texto) > MAX_TEXTO:
        return False
    bajo = texto.lower()
    if "partidas:" in bajo:
        return False
    renglones = [r.strip() for r in texto.splitlines() if r.strip()]
    lista = sum(1 for r in renglones if r.startswith(("-", "•", "*")))
    return lista < MAX_RENGLONES_LISTA


# ─── Validacion de datos ─────────────────────────────────────────────────────

_RE_DIGITOS = re.compile(r"\d+")
_RE_LIGAS = re.compile(r"https?://\S+")
_RE_EMOJI = re.compile(
    "[\u2600-\u27bf\u2b00-\u2bff\ufe0f\U0001f000-\U0001faff]")


def _digitos(texto):
    return set(_RE_DIGITOS.findall(texto or ""))


def _ligas(texto):
    return set(_RE_LIGAS.findall(texto or ""))


def _emojis(texto):
    return set(_RE_EMOJI.findall(texto or ""))


def _conserva_datos(original, nuevo):
    """Los digitos, las ligas y los emojis tienen que ser los mismos."""
    return (_digitos(original) == _digitos(nuevo)
            and _ligas(original) == _ligas(nuevo)
            and _emojis(original) == _emojis(nuevo))


def _valida(original, nuevo):
    """True si la respuesta de la IA se puede mandar en lugar del original."""
    if not isinstance(nuevo, str) or not nuevo.strip():
        return False
    nuevo = nuevo.strip()
    if len(nuevo) > len(original) * (1 + MAX_CRECIMIENTO):
        return False
    return _conserva_datos(original, nuevo)


# ─── Proveedor ───────────────────────────────────────────────────────────────

def _prompt(texto):
    """Prompt que se le manda al modelo (incluye la regla de no tocar datos)."""
    return f"{PROMPT}\n\nMensaje original:\n{texto}"


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


def _llamar(prompt, api_key, modelo):
    """
    Llama al proveedor con un tope duro de `TIMEOUT_S`: si el SDK se cuelga,
    el hilo es daemon y se abandona; el mensaje sale con el texto original.
    """
    resultado = {}

    def trabajo():
        try:
            resultado["valor"] = _generar(prompt, api_key, modelo)
        except Exception as exc:            # se relanza en el hilo principal
            resultado["error"] = exc

    hilo = threading.Thread(target=trabajo, daemon=True)
    hilo.start()
    hilo.join(TIMEOUT_S)
    if hilo.is_alive():
        raise TimeoutError(f"la IA tardo mas de {TIMEOUT_S} s")
    if "error" in resultado:
        raise resultado["error"]
    return resultado.get("valor", "")


# ─── API ─────────────────────────────────────────────────────────────────────

def mejorar(texto, kb=None):
    """
    Reescribe el texto para que suene natural, sin cambiar ningun dato.
    Devuelve el original ante cualquier duda, error o demora. Nunca lanza.
    """
    original = str(texto or "")
    try:
        if not original.strip() or not se_pule(original):
            return texto
        if not ia_activa(kb):
            return texto

        cfg = _config_ia(kb) or {}
        provider = str(cfg.get("provider") or PROVEEDOR).strip().lower()
        api_key = str(cfg.get("api_key") or "").strip()
        modelo = str(cfg.get("model") or "").strip()
        if provider != PROVEEDOR or not api_key or not modelo:
            return texto

        nuevo = _llamar(_prompt(original), api_key, modelo)
        if not _valida(original, nuevo):
            return texto
        return nuevo.strip()
    except Exception as exc:
        print(f"[wa_redaccion] se manda el texto original: {exc}")
        return texto
