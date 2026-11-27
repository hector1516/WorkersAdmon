"""
cron_whatsapp_firmados.py — manda los reportes firmados al grupo «Servicios».

Corre bajo supervisor como `wa_firmados`. Cada ciclo mira los reportes de
servicio que se firmaron después del último que ya se avisó y los manda al
grupo de WhatsApp de la empresa, con el PDF adjunto.

Por qué un vigilante y no un enganche en Field: el firmado se hace desde Field
y desde el portal público, y el aviso tiene que salir siempre. Un cursor en
`HUB_Config` evita tocar otro repo y aguanta que OpenWA esté caído: lo que no
salió se reintenta en el siguiente ciclo, en orden.

Flujo de cada ciclo:
  1. Lee el cursor `HUB_Config.wa_firmados_ultimo_id`
  2. Busca en `ReportesServicio` los firmados con Id mayor al cursor (máx. 5)
  3. Por cada uno arma el texto y el PDF con la misma ruta del aviso real
     (`openwa_alerts._datos_reporte_para_reenvio`) y lo manda SOLO al grupo
  4. Avanza el cursor al Id enviado; si falla, se queda ahí y se reintenta

Destino único: `HUB_Config.whatsapp_grupo_servicios`. Nada de teléfonos
personales ni de otros chats: si esa clave no trae un `@g.us` válido, el ciclo
no manda nada y lo dice (mandar el PDF de un cliente a un número equivocado no
se puede deshacer).

Variables de entorno:
  WA_FIRMADOS_CICLO_SEG   segundos entre ciclos (default 120)
  WA_FIRMADOS_LOTE        reportes por ciclo (default 5)
  WA_FIRMADOS_DRY_RUN     "1" arma texto y PDF y los deja en el log, sin enviar
                          ni mover el cursor (para probar sin spamear el grupo)
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eccsa_db as db
import openwa_alerts as oa
import openwa_client as ow
from worker_heartbeat import heartbeat

WORKER = 'wa_firmados'
CICLO_SEG = max(30, int(os.environ.get('WA_FIRMADOS_CICLO_SEG') or 120))
LOTE = max(1, min(20, int(os.environ.get('WA_FIRMADOS_LOTE') or 5)))
DRY_RUN = (os.environ.get('WA_FIRMADOS_DRY_RUN') or '').strip().lower() in (
    '1', 'true', 'si', 'sí', 'yes', 'on')

CLAVE_CURSOR = 'wa_firmados_ultimo_id'
CLAVE_GRUPO = 'whatsapp_grupo_servicios'



# ── HUB_Config ───────────────────────────────────────────────────────────────

def _cfg_get(clave):
    """Valor crudo de una clave de HUB_Config (None si no existe)."""
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT CAST(Valor AS VARCHAR(MAX)) AS v "
                            "FROM HUB_Config WHERE Clave = %s", (clave,))
                fila = cur.fetchone()
        return fila.get('v') if fila else None
    except Exception as e:
        print(f"  [{WORKER}] no se pudo leer HUB_Config.{clave}: {e}")
        return None


def _cfg_set(clave, valor):
    """Guarda (o crea) una clave de HUB_Config. Devuelve True si quedó."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_Config AS target
                    USING (SELECT %s AS Clave) AS source
                    ON target.Clave = source.Clave
                    WHEN MATCHED THEN
                        UPDATE SET Valor = %s, Actualizado = GETDATE()
                    WHEN NOT MATCHED THEN
                        INSERT (Clave, Valor) VALUES (%s, %s);
                """, (clave, valor, clave, valor))
                conn.commit()
        return True
    except Exception as e:
        print(f"  [{WORKER}] no se pudo guardar HUB_Config.{clave}: {e}")
        return False


def _cint(valor):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return 0


# ── Reportes firmados ────────────────────────────────────────────────────────

def _reportes_firmados_desde(cursor, limite=LOTE):
    """
    Firmados con Id mayor al cursor, del más viejo al más nuevo.

    Se pide solo lo que arma el mensaje: el resto del reporte lo relee
    `openwa_alerts._datos_reporte_para_reenvio`, que es la ruta del aviso real.
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT TOP (%s) IdReporte, Folio, Cliente
                    FROM ReportesServicio
                    WHERE Estatus = 'Firmado' AND IdReporte > %s
                    ORDER BY IdReporte ASC
                """, (int(limite), int(cursor)))
                return cur.fetchall() or []
    except Exception as e:
        print(f"  [{WORKER}] no se pudieron leer los firmados: {e}")
        return []


def _ultimo_id_firmado():
    """Id más alto ya firmado (0 si no hay ninguno)."""
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("""
                    SELECT MAX(IdReporte) AS Ultimo
                    FROM ReportesServicio
                    WHERE Estatus = 'Firmado'
                """)
                fila = cur.fetchone() or {}
        return _cint(fila.get('Ultimo'))
    except Exception as e:
        print(f"  [{WORKER}] no se pudo leer el último firmado: {e}")
        return 0


def texto_reporte(datos):
    """
    Lo que se lee en el grupo. Corto a propósito: el detalle va en el PDF.
    El texto acompaña al documento como caption, así que «PDF adjunto» es
    literal y no un aviso aparte que llegaría dos veces.
    """
    fecha = ' '.join(p for p in (datos.get('FechaFirma'), datos.get('HoraFirma')) if p)
    lineas = [
        '📄 *Reporte firmado*',
        f"Folio: {datos.get('Folio') or '—'}",
        f"Cliente: {datos.get('Cliente') or '—'}",
        f"Ingeniero: {datos.get('Tecnicos') or '—'}",
    ]
    if fecha:
        lineas.append(f"Fecha: {fecha}")
    lineas += ['', 'PDF adjunto.']
    return '\n'.join(lineas)


def _mandar(fila, grupo):
    """
    Manda un reporte al grupo. Devuelve `(ok, detalle)`.

    Va en un solo mensaje (documento con caption): si el envío falla no queda
    medio aviso en el chat y el cursor puede quedarse sin duplicar nada.

    Si el PDF no se pudo generar se manda el texto solo y se avanza: ese fallo
    viene de los datos del reporte y reintentarlo cada 2 minutos no lo arregla;
    dejar el cursor detenido sí perdería todos los reportes siguientes.
    """
    datos, pdf, nombre = oa._datos_reporte_para_reenvio(fila)
    texto = texto_reporte(datos)
    if not pdf:
        print(f"  [{WORKER}] {fila.get('Folio')}: sin PDF legible, se manda solo el texto")
        return ow.enviar_texto(grupo, texto)
    return ow.enviar_documento(grupo, pdf, nombre, caption=texto)


def procesar_ciclo():
    """
    Una vuelta completa. Devuelve `(enviados, detalle)` para el heartbeat.

    El cursor solo avanza con el envío bueno, y se corta en el primer fallo para
    no saltarse un reporte: el orden del grupo es el de la firma.
    """
    grupo_crudo = (_cfg_get(CLAVE_GRUPO) or '').strip()
    grupo = ow.normalizar_chat_id(grupo_crudo)
    if not grupo or not grupo.endswith('@g.us'):
        return 0, (f"sin grupo válido en HUB_Config.{CLAVE_GRUPO} "
                   f"({grupo_crudo or 'vacío'}); no se manda nada")

    cursor_crudo = _cfg_get(CLAVE_CURSOR)
    if cursor_crudo is None or not str(cursor_crudo).strip():
        # Primer arranque: se arranca desde el último firmado. Con el cursor en
        # 0 se mandaría al grupo todo el historial de reportes de golpe.
        ultimo = _ultimo_id_firmado()
        if _cfg_set(CLAVE_CURSOR, str(ultimo)):
            return 0, (f"cursor sembrado en {ultimo}; "
                       f"se avisarán los firmados nuevos")
        return 0, 'no se pudo sembrar el cursor'

    cursor = _cint(cursor_crudo)
    filas = _reportes_firmados_desde(cursor)
    if not filas:
        return 0, f"sin reportes nuevos (cursor {cursor})"

    enviados, ultimo_id = 0, cursor
    for fila in filas:
        id_reporte = _cint(fila.get('IdReporte'))
        if DRY_RUN:
            datos, pdf, nombre = oa._datos_reporte_para_reenvio(fila)
            texto = texto_reporte(datos)
            print(f"  [DRY-RUN] {fila.get('Folio')} ({id_reporte}) -> {grupo}")
            print(f"    PDF: {nombre or '(sin PDF)'} · {len(pdf or b'')} bytes")
            print('    ' + texto.replace('\n', '\n    '))
            continue

        ok, detalle = _mandar(fila, grupo)
        if not ok:
            print(f"  [FALLA] {fila.get('Folio')} ({id_reporte}): {detalle}")
            return enviados, (f"{enviados} enviado(s); {fila.get('Folio')} falló: "
                              f"{detalle}; se reintenta en el siguiente ciclo")
        if not _cfg_set(CLAVE_CURSOR, str(id_reporte)):
            # El mensaje ya salió: si el cursor no se pudo guardar, el siguiente
            # ciclo repite este reporte. Se corta aquí para no encadenar envíos.
            return enviados, (f"{fila.get('Folio')} enviado pero el cursor no se "
                              f"guardó; se corta el ciclo para no duplicar")
        enviados += 1
        ultimo_id = id_reporte
        print(f"  [OK] {fila.get('Folio')} ({id_reporte}) -> {grupo}")

    if DRY_RUN:
        return 0, f"dry-run: {len(filas)} reporte(s) listos, no se envió ni se movió el cursor"
    return enviados, f"{enviados} reporte(s) al grupo · cursor {ultimo_id}"


# ── Loop ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{WORKER}] Iniciado. Ciclo {CICLO_SEG} s · lote {LOTE} · "
          f"grupo = HUB_Config.{CLAVE_GRUPO}"
          + (" · DRY-RUN (no envía)" if DRY_RUN else ""))
    while True:
        try:
            enviados, detalle = procesar_ciclo()
            if enviados:
                print(f"  [RESUMEN] {detalle}")
            heartbeat(WORKER, detail=detalle, count=enviados)
        except KeyboardInterrupt:
            print(f"\n[{WORKER}] Detenido.")
            break
        except Exception as e:
            print(f"  [LOOP ERROR] {e}")
            traceback.print_exc()
            heartbeat(WORKER, detail=f"error: {e}")
        time.sleep(CICLO_SEG)


if __name__ == '__main__':
    if '--una-vez' in sys.argv:
        # Prueba manual: una vuelta y sale (no mueve el cursor si va en dry-run).
        print(procesar_ciclo())
    else:
        main()
