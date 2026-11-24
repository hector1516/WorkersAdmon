"""vigilante.py — mira el sistema y cuenta lo que pasa en lenguaje humano.

Por qué existe: los incidentes se descubrían por casualidad (un vale de $5,709,
el espejo de vales congelado 466 h, el escáner 5 días ciego). Este worker
revisa cada pocos minutos y avisa por Telegram.

Reglas de oro:

1. **Detectar es de reglas, no de IA.** La IA solo redacta. Si no responde, se
   manda la plantilla y ya: una alerta nunca depende de un modelo.
2. **Habla para todo el personal, no para ingenieros.** Nada de SQL, workers,
   contenedores ni logs. Comparaciones de la vida diaria.
3. **Siempre cierra tranquilo**: lo que pasó ya está resuelto o en camino y
   nadie tiene que hacer nada. Un aviso que asusta es un aviso que se ignora.
4. **No repite.** La misma regla no vuelve a sonar antes de
   `vigilante_repetir_horas` (6 por defecto).
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import eccsa_db as db

NOMBRE = "vigilante"
CICLO_SEG = int(os.getenv("VIGILANTE_CICLO_SEG", "600"))          # 10 min
REPETIR_HORAS = float(os.getenv("VIGILANTE_REPETIR_HORAS", "6"))
UMBRAL_MONTO = float(os.getenv("VIGILANTE_MONTO", "500"))
SALDO_BAJO = float(os.getenv("VIGILANTE_SALDO_BAJO", "2000"))

try:
    from worker_log import log as wlog
except Exception:  # pragma: no cover
    def wlog(*a, **k):
        pass


# ── utilidades ─────────────────────────────────────────────────────────
def _cfg(clave, defecto=None):
    try:
        return db.get_govale_config(clave) or defecto
    except Exception:
        return defecto


def _cfg_set(clave, valor):
    try:
        db.set_govale_config(clave, str(valor))
    except Exception:
        pass


def _una(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        fila = cur.fetchone()
        return fila[0] if fila else None
    finally:
        conn.close()


def _filas(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


# ── reglas (cada una devuelve un hallazgo o None) ─────────────────────
def regla_monto_distinto():
    n = _una("""
        SELECT COUNT(*) FROM HUB_SolicitudVales
        WHERE Estatus = 'GENERADO' AND MontoEmitido IS NOT NULL
          AND ABS(MontoEmitido - %s) > 0.01
          AND FechaSolicitud >= DATEADD(day, -2, GETDATE())
    """, (UMBRAL_MONTO,))
    if not n:
        return None
    return {"regla": "monto_distinto", "nivel": "rojo",
            "dato": {"cantidad": int(n), "monto": UMBRAL_MONTO}}


def regla_monto_sin_registrar():
    n = _una("""
        SELECT COUNT(*) FROM HUB_SolicitudVales
        WHERE Estatus = 'GENERADO' AND MontoEmitido IS NULL
          AND ProgresoFecha < DATEADD(minute, -10, GETDATE())
          AND FechaSolicitud >= DATEADD(day, -7, GETDATE())
    """)
    if not n:
        return None
    return {"regla": "monto_sin_registrar", "nivel": "amarillo",
            "dato": {"cantidad": int(n)}}


def regla_vales_sin_codigo():
    n = _una("""
        SELECT COUNT(*) FROM HUB_SolicitudVales
        WHERE Estatus = 'GENERADO'
          AND (CodigoQR IS NULL OR LTRIM(RTRIM(CodigoQR)) = '')
          AND ProgresoFecha < DATEADD(minute, -10, GETDATE())
          AND FechaSolicitud >= DATEADD(day, -7, GETDATE())
    """)
    if not n:
        return None
    return {"regla": "vale_sin_codigo", "nivel": "rojo",
            "dato": {"cantidad": int(n)}}


def regla_duplicados():
    filas = _filas("""
        SELECT IdAutomovil, COUNT(*) AS n FROM HUB_SolicitudVales
        WHERE FechaSolicitud >= DATEADD(day, -1, GETDATE())
          AND Estatus IN ('GENERADO', 'CERRADO')
        GROUP BY IdAutomovil HAVING COUNT(*) > 1
    """)
    if not filas:
        return None
    return {"regla": "duplicados", "nivel": "rojo",
            "dato": {"autos": len(filas), "vales": sum(int(f["n"]) for f in filas)}}


def regla_cola_trabada():
    n = _una("""
        SELECT COUNT(*) FROM HUB_SolicitudVales
        WHERE Estatus = 'APROBADO'
          AND FechaAprobado < DATEADD(minute, -15, GETDATE())
    """)
    if not n:
        return None
    return {"regla": "cola_trabada", "nivel": "amarillo", "dato": {"cantidad": int(n)}}


def regla_pendientes_viejos():
    n = _una("""
        SELECT COUNT(*) FROM HUB_SolicitudVales
        WHERE Estatus = 'GENERADO' AND IdTicketCierre IS NULL
          AND FechaSolicitud < DATEADD(day, -3, GETDATE())
    """)
    if not n:
        return None
    return {"regla": "sin_ticket", "nivel": "amarillo", "dato": {"cantidad": int(n)}}


def regla_saldo_bajo():
    try:
        saldo = float(_cfg("govale_saldo", 0) or 0)
    except (TypeError, ValueError):
        return None
    if saldo and saldo >= SALDO_BAJO:
        return None
    if not saldo:
        return None
    return {"regla": "saldo_bajo", "nivel": "amarillo", "dato": {"saldo": saldo}}


def regla_errores_log():
    n = _una("""
        SELECT COUNT(*) FROM HUB_WorkersLog
        WHERE Nivel = 'ERROR' AND Creado >= DATEADD(hour, -1, GETDATE())
    """)
    if not n or int(n) < 10:
        return None
    return {"regla": "muchos_errores", "nivel": "amarillo", "dato": {"cantidad": int(n)}}


def regla_base_lenta():
    t0 = time.time()
    try:
        _una("SELECT 1")
    except Exception:
        return {"regla": "base_caida", "nivel": "rojo", "dato": {}}
    segundos = time.time() - t0
    if segundos < 10:
        return None
    return {"regla": "base_lenta", "nivel": "rojo", "dato": {"segundos": round(segundos, 1)}}


REGLAS = (regla_monto_distinto, regla_monto_sin_registrar, regla_vales_sin_codigo,
          regla_duplicados, regla_cola_trabada, regla_pendientes_viejos,
          regla_saldo_bajo, regla_errores_log, regla_base_lenta)


# ── redacción: plantilla siempre, IA si contesta ───────────────────────
PLANTILLAS = {
    "monto_distinto": ("Un vale salió con más dinero del acordado. Ya lo estamos "
                       "corrigiendo y la cuenta está bajo control."),
    "monto_sin_registrar": ("Unos vales quedaron sin anotar su monto. Ya lo estamos "
                            "revisando; no hay nada que hacer de tu parte."),
    "vale_sin_codigo": ("Unos vales salieron sin su código. Ya lo estamos arreglando "
                        "y en cuanto esté, te aparece en la app."),
    "duplicados": ("Se nos repitió un vale. Ya lo estamos corrigiendo y no afecta tu "
                   "trabajo."),
    "cola_trabada": ("Hay vales esperando turno más de la cuenta. Ya los estamos "
                     "atendiendo."),
    "sin_ticket": ("Hay vales sin su ticket desde hace días. Ya lo estamos revisando."),
    "saldo_bajo": ("La cuenta de vales anda con poco saldo. Ya se está recargando; "
                   "mientras tanto, sigue todo normal."),
    "muchos_errores": ("El sistema tuvo varios tropiezos hoy. Ya los estamos "
                       "revisando, nada que te afecte."),
    "base_lenta": ("El sistema está un poco lento, como una fila larga en la caja. "
                   "Ya lo estamos destrabando."),
    "base_caida": ("El sistema no contestó un momento. Ya está volviendo; espera un "
                   "minuto y sigue como siempre."),
    "resumen_bueno": ("Todo tranquilo hoy: los vales salieron bien y el sistema "
                      "trabajó sin quejas. Buen día."),
}


def _redactar_con_ia(hallazgos):
    """Pide a Gemini un mensaje corto y humano. Devuelve None si no se puede."""
    clave, modelo_cfg = _cfg("vigilante_ia_clave"), _cfg("vigilante_ia_modelo")
    if not clave:
        try:
            fila = _filas("SELECT TOP 1 ApiKey, Model FROM HUB_AiConfig WHERE Id = 1")
            if fila:
                clave = (fila[0]["ApiKey"] or "").strip()
                modelo_cfg = modelo_cfg or fila[0]["Model"]
        except Exception:
            pass
    if not clave:
        return None
    try:
        modelo = (modelo_cfg or "gemini-3.5-flash-lite").strip()
        instruccion = (
            "Eres quien avisa al personal de ECCSA. Escribes para todos, no para "
            "ingenieros.\n"
            "Reglas: 1) máximo 3 frases cortas. 2) Cero palabras técnicas: nada de SQL, "
            "workers, contenedores, logs, endpoints, servidores. 3) Explica con una "
            "comparación de la vida diaria. 4) Cierra diciendo que ya lo estamos "
            "atendiendo o que ya quedó, y que nadie tiene que hacer nada. Si el asunto "
            "sigue abierto, di que está en camino; NUNCA afirmes que ya está resuelto si "
            "no lo está. 5) Tono alegre, con humor amable; nunca te burles de nadie. "
            "6) Nunca digas que algo se perdió, se borró o es grave. 7) Devuelve solo el "
            "mensaje, sin comillas ni encabezados."
        )
        datos = json.dumps(hallazgos, ensure_ascii=False)
        cuerpo = json.dumps({
            "system_instruction": {"parts": [{"text": instruccion}]},
            "contents": [{"parts": [{"text": "Esto es lo que pasó: " + datos}]}],
            "generationConfig": {"maxOutputTokens": 220, "temperature": 0.8},
        }).encode()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{modelo}:generateContent?key={clave}")
        req = urllib.request.Request(url, data=cuerpo,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            respuesta = json.loads(r.read().decode("utf-8"))
        texto = respuesta["candidates"][0]["content"]["parts"][0]["text"].strip()
        return texto or None
    except Exception as e:
        print(f"[vigilante] IA no disponible, uso plantilla: {str(e)[:120]}", flush=True)
        return None


def _mensaje(hallazgos):
    if not hallazgos:
        return PLANTILLAS["resumen_bueno"]
    texto = _redactar_con_ia(hallazgos)
    if texto:
        return texto
    partes = [PLANTILLAS.get(h["regla"], "Algo se movió por aquí; ya lo estamos "
                                        "revisando.") for h in hallazgos]
    return " ".join(dict.fromkeys(partes))


# ── envío ──────────────────────────────────────────────────────────────
def _destinatarios():
    """Todos los que tienen Telegram vinculado y activo."""
    try:
        filas = _filas("SELECT ChatId FROM HUB_TelegramUsuarios WHERE Activo = 1")
        return [int(f["ChatId"]) for f in filas]
    except Exception as e:
        print(f"[vigilante] no se pudieron leer los destinatarios: {e}", flush=True)
        return []


def _enviar(texto):
    chats = _destinatarios()
    for chat in chats:
        try:
            db.queue_telegram_alerta("VALE_QR_GENERADO", chat, texto[:4000])
        except Exception as e:
            print(f"[vigilante] no se pudo encolar para {chat}: {e}", flush=True)
    return len(chats)


def _toca(regla):
    """True si esa regla no sonó en las últimas REPETIR_HORAS."""
    ultimo = _cfg(f"vigilante_ultimo_{regla}")
    if not ultimo:
        return True
    try:
        cuando = datetime.strptime(str(ultimo), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.now() - cuando > timedelta(hours=REPETIR_HORAS)


# ── ciclo ──────────────────────────────────────────────────────────────
def ciclo():
    hallazgos = []
    for regla in REGLAS:
        try:
            h = regla()
        except Exception as e:
            print(f"[vigilante] {regla.__name__} falló: {str(e)[:120]}", flush=True)
            continue
        if not h:
            continue
        if not _toca(h["regla"]):
            continue
        hallazgos.append(h)
        _cfg_set(f"vigilante_ultimo_{h['regla']}",
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        wlog(NOMBRE, f"Hallazgo: {h['regla']}",
             nivel="ERROR" if h["nivel"] == "rojo" else "WARN",
             detalle=json.dumps(h["dato"], ensure_ascii=False)[:300])

    if hallazgos:
        texto = _mensaje(hallazgos)
        enviados = _enviar(texto)
        wlog(NOMBRE, f"Aviso enviado a {enviados} chats", detalle=texto[:200])
        print(f"[vigilante] {len(hallazgos)} hallazgo(s), aviso a {enviados} chats")
        return hallazgos

    # Resumen diario cuando no hay nada: una vez al día.
    hora = int(_cfg("vigilante_hora_resumen", "8") or 8)
    hoy = datetime.now().strftime("%Y-%m-%d")
    if datetime.now().hour >= hora and _cfg("vigilante_resumen_fecha") != hoy:
        _enviar(_mensaje([]))
        _cfg_set("vigilante_resumen_fecha", hoy)
        print("[vigilante] resumen diario enviado")
    return []


def main():
    print(f"[vigilante] Iniciado (cada {CICLO_SEG}s)", flush=True)
    try:
        wlog(NOMBRE, "Arrancó")
    except Exception:
        pass
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"[vigilante] error en el ciclo: {str(e)[:200]}", flush=True)
        time.sleep(CICLO_SEG)


if __name__ == "__main__":
    main()
