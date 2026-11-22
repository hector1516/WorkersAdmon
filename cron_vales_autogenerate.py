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


def generar_vale_sincrono(solicitud_id):
    """Genera vale vía Go Vale de forma síncrona (sin thread)."""
    config = db.get_govale_credentials()
    if not config.get("user") or not config.get("password"):
        print(f"[vales_worker] No hay credenciales Go Vale para vale #{solicitud_id}")
        return False
    resultado = crear_vale(solicitud_id, user=config["user"], pwd=config["password"])
    if "error" in resultado:
        print(f"[vales_worker] Error vale #{solicitud_id}: {resultado['error']}")
        if resultado.get("reintentable"):
            # Fallo ANTES de generar (login/navegacion): no hay riesgo de
            # duplicar. Se deja APROBADO para que el proximo ciclo reintente, en
            # vez de PENDIENTE, que el worker NUNCA vuelve a mirar.
            print(f"[vales_worker] vale #{solicitud_id} queda APROBADO para reintento")
        else:
            db.revertir_solicitud_pendiente(solicitud_id)
        return False
    print(f"[vales_worker] Vale #{solicitud_id} generado exitosamente")
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
def main():
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
            vales = get_vales_pendientes_generacion()
            fallos_consecutivos = 0
            if vales:
                print(f"[vales_worker] {len(vales)} vale(s) pendientes de generación: {vales}", flush=True)
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
