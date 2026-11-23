"""Worker automático de generación de vales QR.

Revisa la cola cada `VALES_INTERVALO` segundos (15 por defecto) buscando vales
APROBADOS sin CodigoQR y los genera vía Go Vale.

Dos frenos que costaron un incidente (2026-11-22, 9 vales de $777):

- Un vale que acaba de intentarse **no se reintenta de inmediato**: se espera
  `VALES_REINTENTO_SEG` (120 s por defecto). Con el sondeo de 15 s, un fallo
  clasificado como «reintentable» volvía a emitir en el ciclo siguiente.
- El mensaje de arranque dice el intervalo REAL. Antes imprimía «cada 5 min»
  escrito a mano y eso hacía creer que el worker era lento cuando no lo era.
"""
import time
import sys
import os

sys.path.append("/app")
sys.path.append("/")

try:
    import eccsa_db as db
    from oxxogas_vales_automation import crear_vale
    from worker_log import log as wlog
except Exception as e:
    print(f"[vales_worker] Error de importación: {e}")
    sys.exit(1)

# Cada cuánto revisa la cola. Ya NO son 5 minutos: con 15 s el vale se toma
# casi en cuanto se aprueba. No es un candado de concurrencia (el worker es de
# un solo hilo y los genera uno por uno); es solo cada cuánto encuesta. Si no
# hay nada pendiente, lo único que pasa es una consulta a la BD.
INTERVALO = int(os.getenv("VALES_INTERVALO", "15"))

# Espera mínima antes de volver a intentar un vale que ya se intentó. Es un
# freno, no un candado: si el intento anterior falló después de que Go Vale
# emitió (sin confirmarlo), sin esto se emitía otro vale cada 15 s.
REINTENTO_SEG = int(os.getenv("VALES_REINTENTO_SEG", "120"))

_ultimo_aviso_sync = 0.0


def get_vales_pendientes_generacion():
    """Vales APROBADO sin QR generado, en orden de llegada (FIFO)."""
    conn = None
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT Id FROM HUB_SolicitudVales
                WHERE Estatus = 'APROBADO'
                  AND (CodigoQR IS NULL OR LTRIM(RTRIM(CodigoQR)) = '')
                  AND (ProgresoFecha IS NULL
                       OR DATEDIFF(SECOND, ProgresoFecha, GETDATE()) >= %s)
                  -- 85 % = ya se pulsó «Generar»: si falló después, el vale
                  -- pudo emitirse. Reintentarlo solo es cómo se duplicó.
                  AND (Progreso IS NULL OR Progreso < 85)
                ORDER BY Id ASC
            """, (REINTENTO_SEG,))
            return [r["Id"] for r in cur.fetchall()]
    except Exception as e:
        print(f"[vales_worker] Error consultando vales: {e}", flush=True)
        # Forzar reconexion
        try:
            db._local.conn = None
        except Exception:
            pass
        # Re-lanzar para que el loop principal cuente el fallo y reinicie si es necesario
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _clasificar_motivo(texto):
    """Traduce el mensaje del generador a un código que la app sabe pintar."""
    t = (texto or "").lower()
    if "saldo" in t:
        return "SIN_SALDO"
    if "importe" in t or "monto" in t:
        return "MONTO_NO_FIJADO"
    if "kil" in t:
        return "KM_MENORES"
    if "sesi" in t or "login" in t or "credencial" in t:
        return "GOVALE_SIN_SESION"
    return "REINTENTO"


def _anotar_motivo(solicitud_id, codigo, texto):
    """Guarda POR QUÉ no salió el vale, para que la app lo muestre.

    Antes el motivo solo quedaba en el log del worker, así que en la app se
    veía una solicitud atorada sin explicación. Con esto la tarjeta dice si es
    saldo, kilómetros o sesión, y si hay que esperar o hacer algo.
    Un `codigo` vacío borra el motivo (el vale ya salió).
    """
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE HUB_SolicitudVales SET Motivo = %s, MotivoCodigo = %s WHERE Id = %s",
                ((texto or "")[:200] or None, (codigo or "")[:40] or None, solicitud_id),
            )
        conn.commit()
    except Exception as e:
        print(f"[vales_worker] no se pudo guardar el motivo de #{solicitud_id}: {e}")


def generar_vale_sincrono(solicitud_id):
    """Genera vale vía Go Vale de forma síncrona (sin thread)."""
    config = db.get_govale_credentials()
    if not config.get("user") or not config.get("password"):
        print(f"[vales_worker] No hay credenciales Go Vale para vale #{solicitud_id}")
        return False
    resultado = crear_vale(solicitud_id, user=config["user"], pwd=config["password"])
    if "error" in resultado:
        mensaje = str(resultado.get("error"))
        wlog("vales_worker", f"No se generó el vale #{solicitud_id}", nivel="ERROR",
             detalle=mensaje[:400])
        _anotar_motivo(solicitud_id,
                       resultado.get("motivo") or _clasificar_motivo(mensaje),
                       mensaje)
        if resultado.get("reintentable"):
            # Fallo ANTES de generar (login/navegacion): no hay riesgo de
            # duplicar. Se deja APROBADO para que el proximo ciclo reintente, en
            # vez de PENDIENTE, que el worker NUNCA vuelve a mirar.
            print(f"[vales_worker] vale #{solicitud_id} queda APROBADO para reintento")
        else:
            db.revertir_solicitud_pendiente(solicitud_id)
        return False
    # Ya salió: se borra el motivo anterior para que la tarjeta no siga
    # diciendo "se reintenta solo" en un vale que sí se generó.
    _anotar_motivo(solicitud_id, None, None)
    wlog("vales_worker", f"Vale #{solicitud_id} generado", nivel="INFO")
    try:
        from telegram_alerts import alertar_vale_generado
        alertar_vale_generado(solicitud_id)
    except Exception:
        pass
    return True



def detectar_vales_atascados(minutos=15):
    """Vales APROBADO que llevan demasiado tiempo sin generarse."""
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT Id, DATEDIFF(MINUTE, FechaAprobado, GETDATE()) AS Minutos
                FROM HUB_SolicitudVales
                WHERE Estatus = 'APROBADO'
                  AND FechaAprobado IS NOT NULL
                  AND DATEDIFF(MINUTE, FechaAprobado, GETDATE()) >= %s
                ORDER BY FechaAprobado ASC
            """, (int(minutos),))
            return [(r["Id"], r["Minutos"]) for r in cur.fetchall()]
    except Exception as e:
        print(f"[vales_worker] Error detectando atascados: {e}", flush=True)
        return []


def alertar_vales_atascados(stuck):
    """Avisa por Telegram a los admins de vales (max 1 vez/hora)."""
    if not stuck:
        return
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'vales_alerta_atascados'")
            row = cur.fetchone()
            ultimo = row["Valor"] if row else ""
        from datetime import datetime, timedelta
        ahora = datetime.now()
        if ultimo:
            try:
                ult = datetime.strptime(ultimo, "%Y-%m-%d %H:%M:%S")
                if (ahora - ult) < timedelta(hours=1):
                    return
            except Exception:
                pass
        ids = ", ".join("#%d (%d min)" % (v[0], v[1]) for v in stuck[:10])
        texto = ("ALERTA vales OxxoGas\n%d vale(s) APROBADO sin generar: %s\n"
                 "Revisa el worker vales_worker (posible caida o BD)." % (len(stuck), ids))
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT t.ChatId
                FROM HUB_TelegramUsuarios t
                JOIN HUB_Users u ON u.Id = t.IdUsuario
                WHERE t.Activo = 1 AND u.AccesoAdminVales = 1
            """)
            chats = [r["ChatId"] for r in cur.fetchall()]
        for cid in chats:
            db.queue_telegram_alerta('VALE_QR_GENERADO', int(cid), texto[:4000])
        with conn.cursor() as cur:
            cur.execute("""
                IF EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'vales_alerta_atascados')
                    UPDATE HUB_Config SET Valor = %s, Actualizado = GETDATE() WHERE Clave = 'vales_alerta_atascados'
                ELSE
                    INSERT INTO HUB_Config (Clave, Valor, Actualizado) VALUES ('vales_alerta_atascados', %s, GETDATE())
            """, (ahora.strftime("%Y-%m-%d %H:%M:%S"), ahora.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        conn.close()
        print(f"[vales_worker] Alerta de atascados enviada ({len(stuck)} vales, {len(chats)} chats)", flush=True)
    except Exception as e:
        print(f"[vales_worker] No se pudo alertar atascados: {e}", flush=True)

def avisar_sync_viejo(horas=24):
    """Avisa si el espejo de vales de Go Vale lleva demasiado sin traer nada.

    `govale_last_sync` se actualiza igual aunque el parser no encuentre ni un
    vale (pasó del 3-nov al 22-nov: el latido estaba vivo y el dato congelado).
    Por eso se mira el DATO (`HUB_OxxoGas_VouchersCache`), no el latido: es la
    misma lección del escáner que está en AGENTS.md.
    """
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                "SELECT DATEDIFF(HOUR, MAX(FechaSincronizado), GETDATE()) AS horas "
                "FROM HUB_OxxoGas_VouchersCache"
            )
            fila = cur.fetchone() or {}
        horas_sin = fila.get("horas")

        # Si la última lectura buena de la lista encontró 0 vales activos, el
        # espejo está al día en cero y avisar sería una falsa alarma eterna
        # (pasó al cancelar los 9 vales del 22-nov). El aviso vuelve solo
        # cuando Go Vale tenga vales y no lleguen al espejo.
        try:
            activos_raw = db.get_govale_config('govale_vales_activos')
            activos = int(activos_raw) if activos_raw is not None else None
        except (TypeError, ValueError):
            activos = None
        if activos == 0:
            return

        if horas_sin is None or int(horas_sin) >= horas:
            wlog(
                "vales_worker",
                "El espejo de vales de Go Vale no trae nada nuevo",
                nivel="WARN",
                detalle=f"último vale sincronizado hace {horas_sin} h (revisar el parser de la lista)",
            )
    except Exception as e:
        wlog("vales_worker", "No se pudo revisar el espejo de vales", nivel="WARN",
             detalle=str(e)[:200])

def main():
    wlog("vales_worker", "Arrancó", detalle=f"revisión cada {INTERVALO} s, "
         f"reintento a los {REINTENTO_SEG} s")
    print(
        f"[vales_worker] Iniciado — revisión cada {INTERVALO} s "
        f"(reintento de un vale fallido: {REINTENTO_SEG} s)",
        flush=True,
    )
    fallos_consecutivos = 0
    MAX_FALLOS = 5
    while True:
        try:
            # Heartbeat al INICIO del ciclo: si el worker se cuelga,
            # el watchdog lo detecta porque deja de actualizarse.
            try:
                with open("/data/vales_worker_heartbeat", "w") as _hf:
                    _hf.write(str(time.time()))
            except Exception:
                pass
            # Health check: verificar que la BD responde antes de consultar
            try:
                _hc = db.get_connection()
                _hc.cursor().execute("SELECT 1")
                _hc.close()
            except Exception as hc_err:
                print(f"[vales_worker] Health check BD fallido: {hc_err}", flush=True)
                try:
                    db._local.conn = None
                except Exception:
                    pass
                raise
            # Una vez por hora: ¿el espejo de vales sigue trayendo datos?
            global _ultimo_aviso_sync
            if time.time() - _ultimo_aviso_sync >= 3600:
                _ultimo_aviso_sync = time.time()
                avisar_sync_viejo()

            vales = get_vales_pendientes_generacion()
            fallos_consecutivos = 0
            if vales:
                wlog("vales_worker", f"{len(vales)} vale(s) en cola", detalle=str(vales))
                for vid in vales:
                    generar_vale_sincrono(vid)
            # Detectar vales atascados (APROBADO > 15 min) y avisar a admins
            try:
                stuck = detectar_vales_atascados(15)
                if stuck:
                    alertar_vales_atascados(stuck)
            except Exception as e:
                print(f"[vales_worker] Error chequeo atascados: {e}", flush=True)
        except Exception as e:
            fallos_consecutivos += 1
            print(f"[vales_worker] Error en ciclo ({fallos_consecutivos}/{MAX_FALLOS}): {e}", flush=True)
            # Forzar reconexion en el siguiente ciclo
            try:
                db._local.conn = None
            except Exception:
                pass
            if fallos_consecutivos >= MAX_FALLOS:
                print(f"[vales_worker] {MAX_FALLOS} fallos consecutivos. Saliendo para que supervisor reinicie.", flush=True)
                sys.exit(1)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
