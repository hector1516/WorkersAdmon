"""
cron_sync_pdf_storage.py - Worker diario: genera PDFs del día anterior y los guarda en el shared.

Se ejecuta a las 03:00 AM (configurable via CRON_PDF_HORA / CRON_PDF_MIN).
Recorre los 5 módulos (Cotizaciones Materiales, CSP, Reportes, OC y Remisiones), genera cada PDF
y lo guarda en el shared vía pdf_storage.save_pdf().
Registra última ejecución en HUB_Config (clave: 'pdf_worker_last_run').
"""

import time
import sys
import os
import datetime

for p in ("/workspace/hub_repo", "/", ""):
    if p and os.path.isdir(p):
        sys.path.append(p)

try:
    import eccsa_db as db
    import pdf_storage
    import pdf_generator
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Core Import Error: {e}")
    sys.exit(1)

HORA_EJECUCION = int(os.environ.get("CRON_PDF_HORA", 3))
MIN_EJECUCION = int(os.environ.get("CRON_PDF_MIN", 0))


def _ts():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _segundos_hasta_proxima_ejecucion():
    now = datetime.datetime.now()
    proximo = now.replace(hour=HORA_EJECUCION, minute=MIN_EJECUCION, second=0, microsecond=0)
    if proximo <= now:
        proximo += datetime.timedelta(days=1)
    return (proximo - now).total_seconds()


def _update_last_run():
    """Actualiza HUB_Config con la fecha/hora de la última ejecución."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    MERGE HUB_Config AS target
                    USING (SELECT 'pdf_worker_last_run' AS Clave) AS source
                    ON target.Clave = source.Clave
                    WHEN MATCHED THEN
                        UPDATE SET Valor = %s, Actualizado = GETDATE()
                    WHEN NOT MATCHED THEN
                        INSERT (Clave, Valor) VALUES ('pdf_worker_last_run', %s);
                """, (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),) * 2)
                conn.commit()
    except Exception as e:
        print(f"[{_ts()}] Error updating last_run: {e}")


def _get_report_date(report):
    """Extrae un date de un report dict (FechaHoraInicio o Fecha)."""
    fecha = report.get('FechaHoraInicio') or report.get('Fecha')
    if isinstance(fecha, datetime.datetime):
        return fecha.date()
    if isinstance(fecha, datetime.date):
        return fecha
    return None


def generar_pdfs_dia(fecha):
    """
    Genera los PDFs faltantes de un día específico y los guarda en el shared.
    Saltar los que ya existen en el shared.
    Retorna dict con conteos: {"materiales": N, "servproy": N, "reportes": N, "oc": N, "saltados": N, "errores": N}
    """
    stats = {"materiales": 0, "servproy": 0, "reportes": 0, "oc": 0,
             "remisiones": 0, "saltados": 0, "errores": 0}

    # 1. Cotizaciones Materiales
    folios_cm = db.get_folios_cotizaciones_materiales_by_range(fecha, fecha)
    for row in folios_cm:
        try:
            folio_num = int(row['Folio'])
            folio_str = f"CM{folio_num:05d}"
            if pdf_storage.pdf_exists("materiales", folio_str, fecha):
                stats["saltados"] += 1
                continue
            pdf_bytes = pdf_generator.generate_quotation_pdf(folio_num)
            if pdf_bytes:
                pdf_storage.save_pdf("materiales", folio_str, pdf_bytes, fecha)
                stats["materiales"] += 1
        except Exception as e:
            print(f"[{_ts()}] Error CM {row.get('Folio')}: {e}")
            stats["errores"] += 1

    # 2. Cotizaciones Servicios/Proyectos
    folios_csp = db.get_folios_cotizaciones_servproy_by_range(fecha, fecha)
    for row in folios_csp:
        try:
            folio_str = row['Folio']
            if pdf_storage.pdf_exists("servproy", folio_str, fecha):
                stats["saltados"] += 1
                continue
            pdf_bytes = pdf_generator.generate_cotizacion_servproy_pdf(folio_str)
            if pdf_bytes:
                pdf_storage.save_pdf("servproy", folio_str, pdf_bytes, fecha)
                stats["servproy"] += 1
        except Exception as e:
            print(f"[{_ts()}] Error CSP {row.get('Folio')}: {e}")
            stats["errores"] += 1

    # 3. Reportes de Servicio
    folios_rs = db.get_folios_reportes_servicio_by_range(fecha, fecha)
    for row in folios_rs:
        try:
            folio_str = row['Folio']
            fecha_doc = row.get('Fecha') or row.get('FechaHoraInicio') or fecha
            if isinstance(fecha_doc, datetime.datetime):
                fecha_doc = fecha_doc.date()
            if pdf_storage.pdf_exists("reportes", folio_str, fecha_doc):
                stats["saltados"] += 1
                continue
            report = db.get_service_report_full_by_folio(folio_str)
            if report:
                pdf_bytes = pdf_generator.generate_service_report_pdf(report)
                if pdf_bytes:
                    pdf_storage.save_pdf("reportes", folio_str, pdf_bytes, fecha_doc)
                    stats["reportes"] += 1
        except Exception as e:
            print(f"[{_ts()}] Error RS {row.get('Folio')}: {e}")
            stats["errores"] += 1

    # 4. Órdenes de Compra
    folios_oc = db.get_folios_ordenes_compra_by_range(fecha, fecha)
    for row in folios_oc:
        try:
            folio_str = row['FolioOC']
            if pdf_storage.pdf_exists("oc", folio_str, fecha):
                stats["saltados"] += 1
                continue
            pdf_bytes = pdf_generator.generate_orden_compra_pdf(folio_str)
            if pdf_bytes:
                pdf_storage.save_pdf("oc", folio_str, pdf_bytes, fecha)
                stats["oc"] += 1
        except Exception as e:
            print(f"[{_ts()}] Error OC {row.get('FolioOC')}: {e}")
            stats["errores"] += 1

    # 5. Remisiones (RM-CM#####-NN) → Remisiones_Materiales/<año>/<mes>/
    rems = db.get_remisiones_by_range(fecha, fecha)
    for row in rems:
        try:
            folio_str = row['FolioRemision']
            if pdf_storage.pdf_exists("remisiones", folio_str, fecha):
                stats["saltados"] += 1
                continue
            pdf_bytes = pdf_generator.generate_remision_pdf(row['IdRemision'])
            if pdf_bytes:
                pdf_storage.save_pdf("remisiones", folio_str, pdf_bytes, fecha)
                stats["remisiones"] += 1
        except Exception as e:
            print(f"[{_ts()}] Error RM {row.get('FolioRemision')}: {e}")
            stats["errores"] += 1

    return stats


def main():
    print(f"[{_ts()}] === PDF Storage Worker iniciado (ejecuta a las {HORA_EJECUCION:02d}:{MIN_EJECUCION:02d}) ===")

    while True:
        try:
            segundos = _segundos_hasta_proxima_ejecucion()

            if segundos > 120:
                print(f"[{_ts()}] Próxima ejecución en {segundos/3600:.1f}h. Durmiendo...")
                time.sleep(min(segundos - 60, 3600))
                continue

            # Esperar hasta la ventana de ejecución
            if segundos > 0:
                time.sleep(segundos)

            # Generar PDFs del día anterior
            fecha_objetivo = (datetime.date.today() - datetime.timedelta(days=1))
            print(f"[{_ts()}] Iniciando generación de PDFs para {fecha_objetivo}...")

            stats = generar_pdfs_dia(fecha_objetivo)
            total_gen = (stats['materiales'] + stats['servproy'] +
                         stats['reportes'] + stats['oc'] + stats['remisiones'])

            print(f"[{_ts()}] Generación completada para {fecha_objetivo}:")
            print(f"  ✅ Generados: {stats['materiales']} CM, {stats['servproy']} CSP, "
                  f"{stats['reportes']} RS, {stats['oc']} OC, "
                  f"{stats['remisiones']} RM = {total_gen} total")
            print(f"  ⏭️ Saltados (ya existían): {stats['saltados']}")
            if stats['errores'] > 0:
                print(f"  ❌ Errores: {stats['errores']}")

            _update_last_run()
            print(f"[{_ts()}] Última ejecución registrada en HUB_Config.")

        except KeyboardInterrupt:
            print(f"\n[{_ts()}] Detenido por el usuario.")
            break
        except Exception as e:
            print(f"[{_ts()}] Error en loop principal: {e}")

        time.sleep(60)


if __name__ == '__main__':
    main()
