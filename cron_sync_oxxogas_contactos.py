"""
cron_sync_oxxogas_contactos.py
Worker que actualiza el cache de contactos de Go Vale cada 6 horas.
Se ejecuta bajo supervisor.
"""
import time
import os
import sys
sys.path.insert(0, '/app')

from oxxogas_vales_automation import get_oxxogas_empresas_contactos as _get_contactos, get_govale_credentials
import eccsa_db as db


def sync_contactos():
    """Obtiene contactos de Go Vale y actualiza el cache."""
    user, pwd = get_govale_credentials()
    if not user or not pwd:
        print("[cron_contactos] No hay credenciales")
        return

    print("[cron_contactos] Obteniendo contactos de Go Vale...")
    data = _get_contactos(user, pwd)
    contactos = data.get('contactos', [])
    
    if not contactos:
        print("[cron_contactos] No se obtuvieron contactos")
        return

    print(f"[cron_contactos] {len(contactos)} contactos recibidos")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            for c in contactos:
                c = c.strip()
                if not c:
                    continue
                # Upsert en cache
                cur.execute("""
                    MERGE HUB_OxxoGas_ContactosCache AS target
                    USING (SELECT %s AS Contacto, 'CONTACTO' AS Tipo) AS source
                    ON target.Contacto = source.Contacto
                    WHEN MATCHED THEN
                        UPDATE SET FechaActualizacion = GETDATE(), Activo = 1
                    WHEN NOT MATCHED THEN
                        INSERT (Contacto, Tipo, FechaActualizacion, Activo)
                        VALUES (source.Contacto, source.Tipo, GETDATE(), 1);
                """, (c,))
            conn.commit()
            print(f"[cron_contactos] Cache actualizado: {len(contactos)} contactos")
    except Exception as e:
        print(f"[cron_contactos] Error: {e}")
    finally:
        conn.close()


def main():
    print("[cron_contactos] Iniciando worker contactos Go Vale...")
    INTERVAL = int(os.getenv('CRON_OXXOGAS_CONTACTOS_INTERVAL', '21600'))  # 6h default
    
    while True:
        try:
            sync_contactos()
        except Exception as e:
            print(f"[cron_contactos] Error en loop: {e}")
        
        print(f"[cron_contactos] Durmiendo {INTERVAL}s...")
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()