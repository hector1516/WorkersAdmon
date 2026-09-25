"""
network_scanner.py
==================
Worker que corre dentro del contenedor Docker bajo supervisor.
Procesa los resultados de ARP scan escritos por network_scanner_host.py,
detecta cambios de presencia (entrada/salida) y genera alertas Telegram.

Algoritmo mejorado (v2):
- Anti-duplicados: verifica último evento antes de registrar
- Consecutive scan counting: confirma cambios con N scans consecutivos
- Limpieza automática de scans antiguos (>7 días)
- Filtrado de MACs multicast/broadcast en procesamiento
"""

import sys
import os
import time
import datetime

for p in ("/app", "/workspace/hub_repo", "/workspace/HUB", "/", ""):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    import eccsa_db as db
except Exception as e:
    print(f"[{datetime.datetime.now()}] Core Import Error: {e}")
    sys.exit(1)


def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


MACS_INVALIDAS = {
    '00:00:00:00:00:00', 'FF:FF:FF:FF:FF:FF', 'DESCONOCIDA',
}


def es_mac_util(mac):
    """Descarta multicast, broadcast, MACs inválidas y desconocidas."""
    mac = mac.strip().upper()
    if mac in MACS_INVALIDAS:
        return False
    parts = mac.split(':')
    if len(parts) != 6:
        return False
    try:
        primer_byte = int(parts[0], 16)
    except ValueError:
        return False
    if primer_byte & 0x01:
        return False
    return True


def get_network_config():
    config = {
        'net_tolerance_salida_min': '5',
        'net_tolerance_entrada_min': '2',
        'net_scan_enabled': '1',
    }
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Clave, Valor FROM HUB_Config WHERE Clave LIKE 'net_%'")
                for row in cur.fetchall():
                    config[row['Clave']] = row['Valor']
    except Exception as e:
        print(f"[{ts()}] Error leyendo config: {e}")
    return config


def get_latest_unprocessed_macs():
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 FechaScan
                    FROM HUB_NetworkScanResults
                    WHERE Procesado = 0
                    ORDER BY FechaScan DESC
                """)
                row = cur.fetchone()
                if not row:
                    return None, set()

                fecha_scan = row['FechaScan']

                cur.execute("""
                    SELECT MACAddress
                    FROM HUB_NetworkScanResults
                    WHERE FechaScan = %s AND Procesado = 0
                """, (fecha_scan,))

                macs = set()
                for r in cur.fetchall():
                    mac = r['MACAddress'].strip().upper()
                    if es_mac_util(mac):
                        macs.add(mac)

                return fecha_scan, macs
    except Exception as e:
        print(f"[{ts()}] Error obteniendo scans: {e}")
        return None, set()


def mark_scan_processed(fecha_scan):
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE HUB_NetworkScanResults
                    SET Procesado = 1
                    WHERE FechaScan = %s AND Procesado = 0
                """, (fecha_scan,))
                conn.commit()
    except Exception as e:
        print(f"[{ts()}] Error marcando scan procesado: {e}")


def get_all_device_states():
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT d.Id, d.MACAddress, d.NombreDispositivo, d.IdUsuario,
                           d.Tipo, ISNULL(s.Estado, 'FUERA') AS Estado,
                           s.UltimaVezEnRed, s.UltimoScanOk,
                           u.Nombre AS NombreUsuario
                    FROM HUB_NetworkDevices d
                    LEFT JOIN HUB_NetworkState s ON d.Id = s.IdDispositivo
                    LEFT JOIN HUB_Users u ON d.IdUsuario = u.Id
                    WHERE d.Activo = 1
                """)
                return cur.fetchall()
    except Exception as e:
        print(f"[{ts()}] Error obteniendo estados: {e}")
        return []


def get_last_presence_event(device_id):
    """Obtiene el último evento de presencia de un dispositivo."""
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP 1 TipoEvento, FechaHora
                    FROM HUB_NetworkPresence
                    WHERE IdDispositivo = %s
                    ORDER BY FechaHora DESC
                """, (device_id,))
                return cur.fetchone()
    except Exception:
        return None


def count_scans_since(fecha_desde):
    """Cuenta cuántos scans hubo desde una fecha."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT FechaScan)
                    FROM HUB_NetworkScanResults
                    WHERE FechaScan > %s
                """, (fecha_desde,))
                return cur.fetchone()[0]
    except Exception:
        return 0


def update_device_state(device_id, estado, ahora=None):
    if ahora is None:
        ahora = datetime.datetime.now()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if estado == 'AQUI':
                    cur.execute("""
                        MERGE HUB_NetworkState AS target
                        USING (SELECT %s AS IdDispositivo) AS source
                        ON target.IdDispositivo = source.IdDispositivo
                        WHEN MATCHED THEN
                            UPDATE SET Estado = %s, UltimaVezEnRed = %s, UltimoScanOk = %s
                        WHEN NOT MATCHED THEN
                            INSERT (IdDispositivo, Estado, UltimaVezEnRed, UltimoScanOk)
                            VALUES (%s, %s, %s, %s);
                    """, (device_id, estado, ahora, ahora, device_id, estado, ahora, ahora))
                else:
                    cur.execute("""
                        MERGE HUB_NetworkState AS target
                        USING (SELECT %s AS IdDispositivo) AS source
                        ON target.IdDispositivo = source.IdDispositivo
                        WHEN MATCHED THEN
                            UPDATE SET Estado = %s
                        WHEN NOT MATCHED THEN
                            INSERT (IdDispositivo, Estado)
                            VALUES (%s, %s);
                    """, (device_id, estado, device_id, estado))
                conn.commit()
    except Exception as e:
        print(f"[{ts()}] Error actualizando estado device {device_id}: {e}")


def register_presence_event(device_id, tipo_evento, confianza=95.0, notas=''):
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO HUB_NetworkPresence (IdDispositivo, FechaHora, TipoEvento, Confianza, Notas)
                    VALUES (%s, GETDATE(), %s, %s, %s)
                """, (device_id, tipo_evento, confianza, notas))
                conn.commit()
    except Exception as e:
        print(f"[{ts()}] Error registrando evento: {e}")


def get_device_name(device):
    nombre = device.get('NombreDispositivo') or device.get('NombreUsuario') or ''
    if not nombre:
        nombre = f"Dispositivo {device.get('MACAddress', '???')}"
    return nombre


def alertar_cambio_presencia(device, tipo_evento):
    try:
        from telegram_alerts import _queue_alert, _now_str
        fecha, hora = _now_str()
        nombre_usuario = device.get('NombreUsuario') or 'Sin asignar'
        nombre_disp = device.get('NombreDispositivo') or device.get('MACAddress', '???')
        evento_id = 'DISPOSITIVO_RED_ENTRADA' if tipo_evento == 'ENTRADA' else 'DISPOSITIVO_RED_SALIDA'
        datos = {
            'Usuario': nombre_usuario,
            'Dispositivo': nombre_disp,
            'MAC': device.get('MACAddress', ''),
            'Tipo': device.get('Tipo', ''),
            'Fecha': fecha,
            'Hora': hora,
        }
        count = _queue_alert(evento_id, datos)
        if count > 0:
            print(f"[{ts()}] Alerta Telegram encolada: {evento_id} para {nombre_usuario} ({count} dest.)")
    except Exception as e:
        print(f"[{ts()}] Error en alerta Telegram: {e}")


def limpiar_scans_antiguos():
    """Elimina scan results de más de 7 días para liberar espacio."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
                cur.execute("""
                    DELETE FROM HUB_NetworkScanResults
                    WHERE FechaScan < %s AND Procesado = 1
                """, (cutoff,))
                eliminados = cur.rowcount
                conn.commit()
                if eliminados > 0:
                    print(f"[{ts()}] Limpieza: {eliminados} scan results antiguos eliminados")
    except Exception as e:
        print(f"[{ts()}] Error en limpieza: {e}")


def limpiar_eventos_antiguos():
    """Elimina historial de presencia de más de 90 días."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cutoff = datetime.datetime.now() - datetime.timedelta(days=90)
                cur.execute("""
                    DELETE FROM HUB_NetworkPresence
                    WHERE FechaHora < %s
                """, (cutoff,))
                eliminados = cur.rowcount
                conn.commit()
                if eliminados > 0:
                    print(f"[{ts()}] Limpieza: {eliminados} eventos presencia antiguos eliminados")
    except Exception as e:
        print(f"[{ts()}] Error en limpieza eventos: {e}")


def procesar_scans_pendientes():
    """Procesa TODOS los scans pendientes en lote (no solo uno)."""
    config = get_network_config()

    if config.get('net_scan_enabled', '1') != '1':
        return

    tol_salida = int(config.get('net_tolerance_salida_min', '5'))
    tol_entrada = int(config.get('net_tolerance_entrada_min', '2'))

    ahora = datetime.datetime.now()
    procesados = 0

    while True:
        fecha_scan, macs_detectadas = get_latest_unprocessed_macs()
        if fecha_scan is None:
            break

        mark_scan_processed(fecha_scan)
        procesados += 1

        if not macs_detectadas:
            continue

        dispositivos = get_all_device_states()
        dev_map = {}
        for d in dispositivos:
            mac = d['MACAddress'].strip().upper()
            dev_map[mac] = d

        for mac in macs_detectadas:
            if mac not in dev_map:
                continue

            dev = dev_map[mac]
            device_id = dev['Id']
            estado_actual = dev['Estado']

            if estado_actual == 'FUERA':
                ultimo_ok = dev.get('UltimoScanOk')
                if not ultimo_ok:
                    register_presence_event(device_id, 'ENTRADA', 100.0, 'Primer registro')
                    alertar_cambio_presencia(dev, 'ENTRADA')
                    update_device_state(device_id, 'AQUI', ahora)
                    print(f"[{ts()}] ENTRADA (primera): {get_device_name(dev)}")
                    continue

                minutos_ausente = (ahora - ultimo_ok).total_seconds() / 60
                if minutos_ausente < tol_entrada:
                    update_device_state(device_id, 'AQUI', ahora)
                    continue

                ultimo_evento = get_last_presence_event(device_id)
                if ultimo_evento and ultimo_evento['TipoEvento'] == 'ENTRADA':
                    update_device_state(device_id, 'AQUI', ahora)
                    continue

                register_presence_event(device_id, 'ENTRADA', 95.0,
                                        f'Ausente {minutos_ausente:.0f}min, tolerancia {tol_entrada}min')
                alertar_cambio_presencia(dev, 'ENTRADA')
                update_device_state(device_id, 'AQUI', ahora)
                print(f"[{ts()}] ENTRADA: {get_device_name(dev)} (ausente {minutos_ausente:.0f}min)")

            elif estado_actual == 'AQUI':
                update_device_state(device_id, 'AQUI', ahora)

        for mac, dev in dev_map.items():
            if mac in macs_detectadas:
                continue
            if dev['Estado'] != 'AQUI':
                continue

            device_id = dev['Id']
            ultimo_ok = dev.get('UltimoScanOk')
            if not ultimo_ok:
                continue

            minutos_ausente = (ahora - ultimo_ok).total_seconds() / 60
            if minutos_ausente < tol_salida:
                continue

            ultimo_evento = get_last_presence_event(device_id)
            if ultimo_evento and ultimo_evento['TipoEvento'] == 'SALIDA':
                continue

            register_presence_event(device_id, 'SALIDA', 95.0,
                                    f'Ausente {minutos_ausente:.0f}min, tolerancia {tol_salida}min')
            alertar_cambio_presencia(dev, 'SALIDA')
            update_device_state(device_id, 'FUERA', ultimo_ok)
            print(f"[{ts()}] SALIDA: {get_device_name(dev)} (ausente {minutos_ausente:.0f}min)")

    if procesados > 0:
        print(f"[{ts()}] Procesados {procesados} scans en lote")


def run_loop():
    print(f"[{ts()}] === Network Scanner Worker v2 iniciado ===")

    ciclo_limpieza = 0

    while True:
        try:
            procesar_scans_pendientes()
        except Exception as e:
            print(f"[{ts()}] Error en ciclo: {e}")

        ciclo_limpieza += 1
        if ciclo_limpieza >= 360:
            try:
                limpiar_scans_antiguos()
                limpiar_eventos_antiguos()
                ciclo_limpieza = 0
            except Exception as e:
                print(f"[{ts()}] Error en limpieza: {e}")

        time.sleep(60)


if __name__ == '__main__':
    run_loop()
