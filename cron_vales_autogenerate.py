"""Worker automático de generación de vales QR.

Revisa cada 5 minutos vales con estatus APROBADO sin CodigoQR
(y generados desde Field u otros flujos) y los genera vía Go Vale.
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


def get_vales_pendientes_generacion():
    """Vales APROBADO sin QR generado, en orden de llegada (FIFO)."""
    try:
        conn = db.get_connection()
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT Id FROM HUB_SolicitudVales
                WHERE Estatus = 'APROBADO'
                  AND (CodigoQR IS NULL OR LTRIM(RTRIM(CodigoQR)) = '')
                ORDER BY Id ASC
            """)
            return [r["Id"] for r in cur.fetchall()]
    except Exception as e:
        print(f"[vales_worker] Error consultando vales: {e}")
        return []
    finally:
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


def main():
    print("[vales_worker] Iniciado — revisión cada 5 min", flush=True)
    while True:
        try:
            vales = get_vales_pendientes_generacion()
            if vales:
                print(f"[vales_worker] {len(vales)} vale(s) pendientes de generación: {vales}", flush=True)
                for vid in vales:
                    generar_vale_sincrono(vid)
        except Exception as e:
            print(f"[vales_worker] Error en ciclo: {e}", flush=True)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
