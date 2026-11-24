# Log universal (base de datos + consola); si el import falla, sigue sin log.
try:
    from worker_log import log as wlog
except Exception:  # pragma: no cover
    def wlog(*a, **k):
        return False

"""
cron_sync_govale_vournals.py
Worker que sincroniza vales de Go Vale de forma incremental.
Cada 5 minutos (configurable via CRON_GOVALE_INTERVAL) hace login,
extrae los vales nuevos desde el ultimo sync, genera imagen QR
y los vincula con solicitudes HUB pendientes.
"""
import time
import os
import sys
sys.path.insert(0, '/app')

import eccsa_db as db


def generate_qr_image(qr_code_string):
    """Genera imagen PNG del QR desde el string."""
    try:
        import qrcode
        from io import BytesIO

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_code_string)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()
    except Exception as e:
        print(f"[govale_sync] Error generando QR image: {e}")
        return None


def sync_vales():
    """Sincroniza vales de Go Vale de forma incremental."""
    from oxxogas_vales_automation import _launch_browser_and_login, get_govale_credentials

    user, pwd = get_govale_credentials()
    if not user or not pwd:
        print("[govale_sync] No hay credenciales Go Vale")
        return 0

    # Obtener ultimo sync
    last_sync = db.get_govale_config('govale_last_sync')
    if not last_sync:
        last_sync = '2026-08-31 00:00:00'
        db.set_govale_config('govale_last_sync', last_sync)
        print(f"[govale_sync] Primera ejecucion, usando fecha corte: {last_sync}")

    print(f"[govale_sync] Ultimo sync: {last_sync}")
    print("[govale_sync] Haciendo login en Go Vale...")

    browser, context, page, tokens, pw = _launch_browser_and_login(user, pwd)
    nuevos = 0
    try:
        if 'govale-digital.oxxogas.com' not in page.url:
            print("[govale_sync] Login fallido, URL incorrecta:", page.url)
            return 0

        print("[govale_sync] Login OK, extrayendo saldo...")

        # Extraer saldo de la pagina principal.
        # OJO: la home muestra varios montos ($ 14,709.00 cuenta, $0.00 sin asignar,
        # saldos por empresa). Antes un re.search al primer '$' capturaba 0.00 y
        # Field rechazaba vales con "No hay saldo disponible".
        # Además el SPA a veces se lee ANTES de pintar el saldo → reintenta.
        try:
            import re
            from datetime import datetime

            def _parse_monto(s: str) -> float:
                return float(s.replace('$', '').replace(',', '').replace(' ', '').strip())

            def _leer_body() -> str:
                el = page.query_selector('body')
                return el.inner_text() if el else ''

            def _extraer_saldo(text: str):
                """Devuelve (monto_float|None, origen)."""
                m = re.search(
                    r'Saldo\s+de\s+mi\s+cuenta\s*\$?\s*([\d,]+(?:\.\d{1,2})?)',
                    text,
                    re.IGNORECASE | re.DOTALL,
                )
                if m:
                    val = _parse_monto(m.group(1))
                    if val > 0:
                        return val, 'cuenta'
                # Fallback: mayor monto con $ (con o sin espacio)
                candidates = re.findall(r'\$\s*([\d,]+(?:\.\d{1,2})?)', text)
                montos = sorted({_parse_monto(c) for c in candidates}, reverse=True)
                if montos and montos[0] > 0:
                    return montos[0], 'max'
                if m:
                    return _parse_monto(m.group(1)), 'cuenta_zero'
                return None, 'none'

            # Esperar a que el SPA pinte "Saldo de mi cuenta" y el monto real.
            # El label puede aparecer ANTES del número → reintenta hasta encontrar >0
            # o agotar intentos (así no se escribe un 0.00 falso por carrera de render).
            try:
                page.wait_for_selector('text=Saldo de mi cuenta', timeout=15000)
            except Exception:
                pass

            body_text_home = ''
            saldo_val, origen = None, 'none'
            for intento in range(4):
                time.sleep(3 if intento == 0 else 2)
                body_text_home = _leer_body()
                saldo_val, origen = _extraer_saldo(body_text_home)
                if saldo_val and saldo_val > 0:
                    break

            # Leer saldo previo de BD para no pisarlo con 0.00 falso
            saldo_previo_raw = db.get_govale_config('govale_saldo') or '0'
            try:
                saldo_previo = float(saldo_previo_raw)
            except (TypeError, ValueError):
                saldo_previo = 0.0

            if saldo_val is not None and saldo_val > 0:
                saldo_str = f"{saldo_val:.2f}"
                db.set_govale_config('govale_saldo', saldo_str)
                db.set_govale_config('govale_saldo_fecha', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                print(f"[govale_sync] Saldo Go Vale: ${saldo_str} (via {origen})")
            elif saldo_val is not None and saldo_val == 0:
                # Solo guardar 0 si no hay saldo previo válido; si había >0, no pisarlo
                if saldo_previo > 0:
                    print(f"[govale_sync] Saldo extraído 0.00 pero había previo ${saldo_previo:.2f} -> se conserva")
                else:
                    saldo_str = f"{saldo_val:.2f}"
                    db.set_govale_config('govale_saldo', saldo_str)
                    db.set_govale_config('govale_saldo_fecha', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    print(f"[govale_sync] Saldo Go Vale: ${saldo_str} (via {origen}) — confirmado 0 tras reintentos")
            else:
                # No pisar un saldo previo con fallo de lectura
                print("[govale_sync] No se pudo extraer saldo (se conserva el anterior)")
                print("[govale_sync] BODY snippet:", repr(body_text_home[:400]))
        except Exception as e:
            print(f"[govale_sync] Error extrayendo saldo: {e}")

        # Los vales activos NO viven en /vouchers: esa ruta no existe y el SPA
        # rebota a /home. Ahí se pintan dentro de .containerQrs, una tarjeta
        # .qrs por vale, junto con «Generar Vale» y «N Vales Activos».
        # Antes se pedía /vouchers y se leía el menú: el primer <li> del navbar
        # se llamaba «Inicio» y se contaba como vale.
        print("[govale_sync] Navegando al panel (los vales activos están en /home)...")
        if '/home' not in page.url:
            page.goto("https://govale-digital.oxxogas.com/home",
                      wait_until="networkidle", timeout=60000)
        time.sleep(5)
        try:
            page.wait_for_selector('.containerQrs', timeout=30000)
        except Exception:
            pass

        body_text = page.query_selector('body').inner_text() if page.query_selector('body') else ''

        # Si sigue la pantalla pública, NO hay sesión: se dice y no se toca el
        # cache. Callarlo era lo que hacía que el latido dijera «vivo» con el
        # espejo congelado.
        texto_plano = " ".join(body_text.split())
        if 'Estamos renovando' in texto_plano or not page.query_selector('.containerQrs'):
            wlog("govale_vouchers_worker",
                 "Go Vale sin sesión: no se pudieron leer los vales",
                 nivel="ERROR",
                 detalle=texto_plano[:280])
            print("[govale_sync] SIN SESIÓN en Go Vale; el cache no se toca")
            # Un solo aviso cada 6 h: el worker corre cada 5 min y no puede
            # llenar el log ni la base con la misma queja.
            try:
                from datetime import datetime as _dt, timedelta as _td
                ultimo = db.get_govale_config('govale_aviso_sesion') or ''
                toca = True
                if ultimo:
                    try:
                        toca = _dt.now() - _dt.strptime(ultimo, '%Y-%m-%d %H:%M:%S') > _td(hours=6)
                    except ValueError:
                        toca = True
                if toca:
                    db.set_govale_config('govale_aviso_sesion', _dt.now().strftime('%Y-%m-%d %H:%M:%S'))
                    wlog("govale_vouchers_worker",
                         "Aviso: la sincronización de vales está ciega (sin sesión)",
                         nivel="WARN",
                         detalle="Se reintenta en cada ciclo; no se emite ni se modifica ningún vale.")
            except Exception as e:
                print(f"[govale_sync] no se pudo registrar el aviso: {e}")
            return 0

        if 'No tienes' in body_text or 'sin resultados' in body_text.lower():
            # Ojo: aquí se salía sin dejar rastro y el latido seguía "vivo".
            # Se guarda lo que de verdad muestra la página para diagnosticar.
            wlog(
                "govale_vouchers_worker",
                "Go Vale respondió «no hay vales»",
                nivel="WARN",
                detalle=(page.title()[:80] + " · " + " ".join(body_text.split())[:280]),
            )
            print("[govale_sync] No hay vales en Go Vale")
            return 0

        # Guardar HTML de la pagina para diagnostico
        try:
            page_html = page.content()
            with open('/data/govale_debug.html', 'w', encoding='utf-8') as f:
                f.write(page_html)
            print("[govale_sync] HTML de la pagina guardado en /data/govale_debug.html")
        except Exception as e:
            print(f"[govale_sync] No se pudo guardar HTML: {e}")

        # La lista REAL de vales no está en /home: hay que pulsar «Vales» en el
        # menú (por URL directa, /vouchers rebota a /home y ahí solo está el
        # contador «N Vales Activos»). Antes se leía esa tarjeta y por eso el
        # sincronizador daba 0 vales aunque hubiera vales activos.
        page.evaluate("""() => {
            const b = [...document.querySelectorAll('li button')]
                .find(x => (x.innerText || '').trim() === 'Vales');
            if (b) b.click();
        }""")
        page.wait_for_timeout(9000)
        try:
            body_text = page.inner_text('body')
        except Exception:
            body_text = ''

        import re as _re
        # Cada vale del listado: folio (9 dígitos), fecha e «Importe original».
        BLOQUE = _re.compile(
            r'(\d{6,15})\s*\n\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*\n\s*'
            r'Importe original\s*\n?\s*\$?\s*([\d,]+\.\d{2})')
        vales_leidos = []
        for folio, fecha, monto in BLOQUE.findall(body_text or ''):
            try:
                vales_leidos.append({'folio': folio, 'fecha': fecha,
                                     'monto': float(monto.replace(',', ''))})
            except ValueError:
                continue

        if not vales_leidos:
            wlog("govale_vouchers_worker",
                 "No hay vales en la lista de Go Vale",
                 nivel="INFO",
                 detalle=f"página autenticada, texto={len(body_text)} chars")
            print("[govale_sync] 0 vales en la lista")
            _anotar_lectura(0)
            return 0

        wlog("govale_vouchers_worker", f"Vales leídos: {len(vales_leidos)}",
             detalle=f"título={page.title()[:60]}, texto={len(body_text)} chars")
        print(f"[govale_sync] Vales leídos: {len(vales_leidos)}")

        sin_qr = 0
        for vale in vales_leidos:
            try:
                # El folio ES el código que se escanea en la estación.
                qr_code = vale['folio']
                if db.vale_exists_in_cache(qr_code):
                    continue
                monto = vale['monto']
                qr_image = generate_qr_image(qr_code)
                ok = db.upsert_vale_cache(
                    qr_code=qr_code, voucher_id=None, monto=monto, saldo=monto,
                    estatus='Activo', contacto='', empresa='', qr_image=qr_image)
                if ok:
                    nuevos += 1
                    print(f"[govale_sync] Nuevo vale: {qr_code} (${monto})")
            except Exception as e:
                print(f"[govale_sync] Error procesando vale: {e}")
                continue

        _anotar_lectura(len(vales_leidos))
        wlog("govale_vouchers_worker", f"Sync: {nuevos} nuevos, {sin_qr} sin código",
             detalle=f"de {len(vales_leidos)} vales")

        # Intentar extraer vales via JavaScript (mas confiable)
        if nuevos == 0:
            print("[govale_sync] Intentando extraer vales via JavaScript...")
            # Guardar el texto de la pagina para diagnostico
            try:
                body_text_saved = page.evaluate("() => document.body.innerText")
                with open('/data/govale_page_text.txt', 'w', encoding='utf-8') as f:
                    f.write(body_text_saved)
                print("[govale_sync] Texto de la pagina guardado en /data/govale_page_text.txt")
            except Exception as e:
                print(f"[govale_sync] No se pudo guardar texto: {e}")

            js_vales = page.evaluate("""() => {
                const vales = [];
                // Buscar en localStorage/sessionStorage datos de vales
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    if (key.toLowerCase().includes('voucher') || key.toLowerCase().includes('vale')) {
                        try {
                            const val = JSON.parse(sessionStorage.getItem(key));
                            if (val && (val.qrCode || val.qr_code || val.code)) {
                                vales.push({qrCode: val.qrCode || val.qr_code || val.code, 
                                           monto: val.initialAmount || val.monto || val.amount || 0,
                                           saldo: val.balance || val.saldo || 0,
                                           estatus: val.voucherStatus || val.estatus || 'Desconocido'});
                            }
                        } catch(e) {}
                    }
                }
                // Buscar en el DOM textos que parezcan QR codes (mas patrones)
                const allText = document.body.innerText;
                const qrPatterns = [
                    /[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}(?:-[A-Z0-9]{3})*/g,
                    /[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}(?:-[A-Z0-9]{4})*/g,
                    /[A-Z]{2,4}[0-9]{4,8}/g,
                ];
                qrPatterns.forEach(pattern => {
                    const matches = allText.match(pattern) || [];
                    matches.forEach(m => {
                        if (!vales.find(v => v.qrCode === m)) {
                            vales.push({qrCode: m});
                        }
                    });
                });
                return vales;
            }""")

            for v in js_vales:
                qr_code = v.get('qrCode', '')
                if not qr_code or db.vale_exists_in_cache(qr_code):
                    continue

                qr_image = generate_qr_image(qr_code)
                ok = db.upsert_vale_cache(
                    qr_code=qr_code,
                    voucher_id=v.get('voucherId'),
                    monto=v.get('initialAmount', 0),
                    saldo=v.get('balance', 0),
                    estatus=v.get('voucherStatus', 'Desconocido'),
                    contacto='',
                    empresa='',
                    qr_image=qr_image
                )
                if ok:
                    nuevos += 1
                    print(f"[govale_sync] Nuevo vale (JS): {qr_code}")

        # Vincular vales con solicitudes pendientes
        if nuevos > 0:
            db.link_vales_cache_to_solicitudes()

        # Actualizar last_sync
        from datetime import datetime
        db.set_govale_config('govale_last_sync', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        print(f"[govale_sync] Sync completado: {nuevos} vales nuevos")
        return nuevos

    except Exception as e:
        print(f"[govale_sync] Error: {e}")
        return 0
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def notificar_saldo_diario():
    """Notificación diaria del saldo Go Vale a las 9:00 AM (una vez por día) vía Telegram.

    Se ejecuta desde el loop del worker (cada 5 min). Usa HUB_Config
    clave `govale_notif_fecha` como deduplicador para no reenviar el mismo día.
    """
    from datetime import datetime

    try:
        now = datetime.now()
        # Ventana: 09:00–09:59 (si el worker estaba caído a las 9, se envía al recuperarse
        # siempre que aún no se haya enviado hoy y sean >= 9:00)
        if now.hour < 9:
            return False

        hoy = now.strftime('%Y-%m-%d')
        if db.get_govale_config('govale_notif_fecha') == hoy:
            return False

        saldo_raw = db.get_govale_config('govale_saldo') or '0'
        try:
            saldo = float(saldo_raw)
        except (TypeError, ValueError):
            saldo = 0.0
        saldo_fecha = db.get_govale_config('govale_saldo_fecha') or 'sin revisar'

        umbral = 2000.0
        bajo = saldo < umbral
        estado = '⚠️ SALDO BAJO' if bajo else '✅ OK'
        alerta = f'{estado} — {"recarga pronto" if bajo else "saldo disponible"}'
        titulo = f"💰 Saldo Go Vale: ${saldo:,.2f}"
        mensaje = (
            f"{alerta}\n"
            f"Saldo actual: ${saldo:,.2f}\n"
            f"Umbral de alerta: ${umbral:,.0f}\n"
            f"Última revisión: {saldo_fecha}\n"
            f"Detalle: https://hub.ecc-sa.com.mx/?page=vales_oxxogas"
        )

        # Enviar por Telegram (evento GOVALE_SALDO)
        try:
            from telegram_alerts import alertar_govale_saldo
            enviados = alertar_govale_saldo(saldo, saldo_fecha, umbral)
            print(f"[govale_sync] Telegram GOVALE_SALDO encolado: {enviados} destinatarios")
        except Exception as e:
            print(f"[govale_sync] Error Telegram: {e}")
            enviados = 0

        # WhatsApp: el aviso diario y, aparte, el de umbral (que es el que hace
        # que alguien refactorie). Son dos eventos porque el diario se lee de
        # arriba y el otro no.
        try:
            from openwa_alerts import alertar_saldo_govale
            wa = alertar_saldo_govale(saldo, saldo_fecha, umbral)
            print(f"[govale_sync] WhatsApp encolado: {wa}")
        except Exception as e:
            print(f"[govale_sync] Error WhatsApp: {e}")

        # Log en HUB_Notificaciones (compat)
        db.log_notification(titulo, mensaje, 'Sistema (9AM)', enviados or 0, 0)
        db.set_govale_config('govale_notif_fecha', hoy)
        print(f"[govale_sync] Notificación diaria 9AM: saldo=${saldo:,.2f} telegram_enviados={enviados or 0}")
        return True
    except Exception as e:
        print(f"[govale_sync] Error notificación diaria: {e}")
        return False


def _anotar_lectura(cuantos):
    """Deja constancia de cuántos vales se vieron al leer la lista.

    Es la diferencia entre "no hay vales" y "el parser está ciego": antes se
    medía el latido y el dato, y con los 9 vales cancelados el aviso de espejo
    viejo se quedaba en rojo aunque todo estuviera bien.
    """
    try:
        from datetime import datetime as _dt
        db.set_govale_config('govale_vales_activos', str(int(cuantos)))
        db.set_govale_config('govale_sync_visto', _dt.now().strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as e:
        print(f"[govale_sync] no se pudo anotar la lectura: {e}")


def main():
    wlog("govale_vouchers_worker", "Arrancó")
    print("[govale_sync] Iniciando worker sync vales Go Vale...")
    INTERVAL = int(os.getenv('CRON_GOVALE_INTERVAL', '300'))  # 5 min default

    while True:
        try:
            sync_vales()
        except Exception as e:
            print(f"[govale_sync] Error en loop: {e}")

        # Notificación diaria de saldo a las 9:00 AM (dedupe por fecha)
        try:
            notificar_saldo_diario()
        except Exception as e:
            print(f"[govale_sync] Error notif diaria: {e}")

        print(f"[govale_sync] Durmiendo {INTERVAL}s...")
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
