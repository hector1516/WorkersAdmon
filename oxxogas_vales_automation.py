"""
oxxogas_vales_automation.py
Módulo Playwright para autenticación PKCE y creación de vales QR en Go Vale.
Se ejecuta dentro del contenedor Docker (Python 3.11) con Playwright instalado.
"""
import json
import time
import re
import base64
import pymssql
from config_db import load_db_config

DB_CONFIG = load_db_config()

TENANT_ID = "45f029e7-17ac-4e1d-8fab-d91d0054de43"
CLIENT_ID = "633e24b6-cde8-430b-b483-03bf2e65bef8"
AUTHORITY = f"https://oxxogasciam.ciamlogin.com/{TENANT_ID}"
REDIRECT_URI = "https://govale-digital.oxxogas.com/home"
SCOPE = "openid profile offline_access"
SUBSCRIPTION_KEY = "7fa1c16aded241789bb79f17d7784a87"
API_BASE = "https://apis-services-ext.oxxogas.com/gaxops/govale/v1/GoVale"
INVOICES_BASE = "https://apis-ogw.oxxogas.com/gaxops/govale/invoices"
INVOICES_SUBSCRIPTION_KEY = "2475c773d11a469e951f47edaad3a1ee"


def get_db_connection():
    return pymssql.connect(
        server=DB_CONFIG['server'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        database=DB_CONFIG['database'],
        port=int(DB_CONFIG.get('port', 1433)),
        tds_version='7.4'
    )


def get_govale_credentials():
    conn = get_db_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            user = ""
            pwd = ""
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'govale_user'")
            r = cur.fetchone()
            if r and r['Valor']:
                user = r['Valor'].strip()
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = 'govale_password'")
            r = cur.fetchone()
            if r and r['Valor']:
                pwd = r['Valor'].strip()
            return user, pwd
    finally:
        conn.close()


def _launch_browser_and_login(user, pwd):
    """Lanza Chromium, navega a Go Vale, intercepta tokens, completa login."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    captured_tokens = {}

    def on_request(request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and auth[7:]:
            captured_tokens['access_token'] = auth[7:]
            captured_tokens['url'] = request.url

    page.on("request", on_request)

    page.goto("https://govale-digital.oxxogas.com/home", wait_until="networkidle", timeout=60000)
    time.sleep(3)

    try:
        # 1. Click en "Ingresa" para abrir el formulario de login
        try:
            ingresa_btn = page.query_selector('button:has-text("Ingresa")')
            if ingresa_btn and ingresa_btn.is_visible():
                ingresa_btn.click()
                print("[govale] Click en botón 'Ingresa'")
                time.sleep(3)
        except Exception as e:
            print(f"[govale] No se encontró botón Ingresa: {e}")

        # 2. Esperar campo de email - probar múltiples selectores
        azure_selectors = [
            'input[name="username"]',           # Angular (Go Vale)
            'input[formcontrolname="email"]',    # Angular
            'input[name="loginfmt"]',            # Azure AD estándar
            'input[type="email"]',               # Genérico
            '#i0116',                            # Microsoft login input
            'input[placeholder*="mail"]',        # Placeholder
            'input[aria-label*="mail"]',
        ]

        email_filled = False
        for sel in azure_selectors:
            try:
                page.wait_for_selector(sel, timeout=5000)
                page.fill(sel, user)
                email_filled = True
                print(f"[govale] Email llenado con selector: {sel}")
                break
            except Exception:
                continue

        if not email_filled:
            # Inspeccionar qué hay en la página
            all_inputs = page.query_selector_all('input')
            print(f"[govale] No se encontró email. Inputs en página: {len(all_inputs)}")
            for inp in all_inputs:
                print(f"  input: type={inp.get_attribute('type')} placeholder={inp.get_attribute('placeholder')} name={inp.get_attribute('name')} id={inp.get_attribute('id')}")
            # Tomar screenshot para debug
            page.screenshot(path="/tmp/govale_debug.png")
            print("[govale] Screenshot guardado en /tmp/govale_debug.png")
            return browser, context, page, captured_tokens, pw

        # Click en "Siguiente" / "Next" / "Continue"
        next_selectors = [
            'button:has-text("Siguiente")',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button:has-text("Continuar")',
            '#idSIButton9',                      # Microsoft "Next" button
            'input[type="submit"]',
        ]
        for sel in next_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    print(f"[govale] Click next con selector: {sel}")
                    break
            except Exception:
                continue

        time.sleep(3)

        # Campo de contraseña
        pwd_selectors = [
            'input[name="password"]',           # Angular (Go Vale)
            'input[name="passwd"]',             # Azure AD estándar
            'input[type="password"]',           # Genérico
            'input[formcontrolname="password"]', # Angular
            '#i0118',                            # Microsoft password input
            'input[placeholder*="contraseña"]',
            'input[placeholder*="assword"]',
        ]

        pwd_filled = False
        for sel in pwd_selectors:
            try:
                page.wait_for_selector(sel, timeout=10000)
                page.fill(sel, pwd)
                pwd_filled = True
                print(f"[govale] Password llenado con selector: {sel}")
                break
            except Exception:
                continue

        if pwd_filled:
            # Click en "Sign in" / "Iniciar"
            submit_selectors = [
                'button:has-text("Sign in")',
                'button:has-text("Iniciar sesión")',
                'button:has-text("Iniciar")',
                'button:has-text("Ingresar")',
                'button:has-text("Entrar")',
                '#idSIButton9',                   # Microsoft "Sign in" button
                'input[type="submit"]',
                'button[type="submit"]',
            ]
            for sel in submit_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        print(f"[govale] Click submit con selector: {sel}")
                        break
                except Exception:
                    continue
        else:
            page.screenshot(path="/tmp/govale_debug_pwd.png")
            print("[govale] No se encontró campo de password. Screenshot guardado.")

        time.sleep(5)

        # Manejar "Stay signed in?" prompt de Microsoft
        kmsi_selectors = [
            '#idSIButton9',          # "Yes" button (Stay signed in)
            'button:has-text("Yes")',
            'button:has-text("Sí")',
            '#KmsiCheckboxField',    # Checkbox "Don't show this again"
        ]
        for sel in kmsi_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    print(f"[govale] Click 'Stay signed in' con selector: {sel}")
                    break
            except Exception:
                continue

        # Esperar a que la app Angular cargue (dashboard)
        print("[govale] Esperando carga del dashboard...")
        time.sleep(10)

        # Extraer token de sessionStorage (MSAL lo guarda ahí)
        token_extracted = page.evaluate("""() => {
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key.includes('accesstoken') && key.includes('.default')) {
                    try {
                        const val = JSON.parse(sessionStorage.getItem(key));
                        if (val && val.secret) return val.secret;
                    } catch(e) {}
                }
            }
            // Fallback: buscar cualquier accesstoken con scope de Go Vale
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key.includes('accesstoken') && key.includes('633e24b6')) {
                    try {
                        const val = JSON.parse(sessionStorage.getItem(key));
                        if (val && val.secret) return val.secret;
                    } catch(e) {}
                }
            }
            return null;
        }""")

        if token_extracted:
            captured_tokens['access_token'] = token_extracted
            print("[govale] Token extraído de sessionStorage")
        else:
            print("[govale] No se encontró token en sessionStorage")
            print("[govale] Último URL:", page.url)
    except Exception as e:
        print(f"[govale] Excepción en login: {e}")

    return browser, context, page, captured_tokens, pw


def login_and_get_token(user=None, pwd=None):
    """Login completo vía Playwright, devuelve access_token."""
    if not user or not pwd:
        user, pwd = get_govale_credentials()
    if not user or not pwd:
        return None

    browser, context, page, tokens, pw = _launch_browser_and_login(user, pwd)
    try:
        return tokens.get('access_token')
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def crear_vale(solicitud_id, user=None, pwd=None):
    """Crea un vale navegando la UI de Go Vale con Playwright (Angular Material).
    Usa la tabla de mapeo HUB_OxxoGas_Mapeo para empresa/contacto.
    Retorna dict con resultado o error."""
    import eccsa_db as db
    
    if not user or not pwd:
        user, pwd = get_govale_credentials()
    if not user or not pwd:
        return {'error': 'No hay credenciales de Go Vale configuradas.'}

    # Obtener datos de la solicitud
    conn = db.get_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT s.Id, s.IdSolicitante, s.Placa, s.Descripcion, s.Cantidad, s.MontoUnit,
                       u.Nombre AS Solicitante, a.Id AS IdAutomovil, s.Estatus
                FROM HUB_SolicitudVales s
                LEFT JOIN HUB_Users u ON s.IdSolicitante = u.Id
                LEFT JOIN HUB_Automoviles a ON a.Placas = s.Placa
                WHERE s.Id = %s
            """, (solicitud_id,))
            row = cur.fetchone()
            if not row:
                return {'error': 'Solicitud no encontrada'}
            # Normalizar keys a mayúsculas
            sol = {k.upper(): v for k, v in row.items()}
            if sol['ESTATUS'] != 'APROBADO':
                return {'error': f"Solicitud no está aprobada (estatus: {sol['ESTATUS']})"}
    finally:
        conn.close()

# Obtener mapeo por usuario (el mapeo es Usuario → Contacto, sin placa)
    mapeo_raw = db.get_oxxogas_mapeo_by_usuario(sol['IDSOLICITANTE'])
    if not mapeo_raw:
        return {'error': f'No hay mapeo OxxoGas para el usuario {sol["SOLICITANTE"]}. Configúrelo en Admin Vales QR > Mapeo OxxoGas.'}
    
    # Normalizar keys a mayúsculas
    mapeo = {k.upper(): v for k, v in mapeo_raw.items()}
    
    # Empresa es fija en Go Vale, no se mapea
    # La key viene como CONTACTOOXXOGAS (sin underscore)
    contacto_nombre = mapeo.get('CONTACTOOXXOGAS')
    if not contacto_nombre:
        print(f"[govale] Keys disponibles: {list(mapeo.keys())}")
        return {'error': 'No se encontró ContactoOxxoGas en mapeo'}
    
    concepto = f"Vale QR - {sol['PLACA']} - {sol['DESCRIPCION']}"
    monto = float(sol['MONTOUNIT'])
    cantidad = int(sol['CANTIDAD'])

    browser, context, page, tokens, pw = _launch_browser_and_login(user, pwd)
    try:
        if 'govale-digital.oxxogas.com' not in page.url:
            return {'error': 'No se pudo iniciar sesión en Go Vale.'}
        print("[govale] Login OK, navegando a Generar Vale...")

        # Click "Generar Vale" desde el dashboard
        gen_link = page.query_selector('text=Generar Vale')
        if gen_link:
            gen_link.click()
        else:
            page.goto("https://govale-digital.oxxogas.com/vouchers/1", wait_until="networkidle", timeout=60000)
        time.sleep(8)

        body_text = page.query_selector('body').inner_text() if page.query_selector('body') else ''
        if 'Generar Vale' not in body_text:
            return {'error': 'No se pudo cargar el formulario de Generar Vale.'}

        # 1. Seleccionar empresa (primer mat-select, obligatorio aunque sea fija)
        triggers = page.query_selector_all('.mat-mdc-select-trigger')
        if triggers:
            triggers[0].click()
            time.sleep(2)
            options = page.query_selector_all('mat-option')
            if options:
                options[0].click()
                time.sleep(3)
                print(f"[govale] Empresa seleccionada: {options[0].inner_text().strip()}")

        # 2. Seleccionar contacto (segundo mat-select) - re-queryear después de empresa
        triggers2 = page.query_selector_all('.mat-mdc-select-trigger')
        if len(triggers2) > 1:
            triggers2[1].click()
            time.sleep(2)
            contact_options = page.query_selector_all('mat-option')
            selected_contact = False
            for opt in contact_options:
                opt_text = opt.inner_text().strip().upper()
                if contacto_nombre.upper() in opt_text:
                    opt.click()
                    time.sleep(2)
                    print(f"[govale] Contacto seleccionado: {opt.inner_text().strip()}")
                    selected_contact = True
                    break
            if not selected_contact and contact_options:
                contact_options[0].click()
                time.sleep(2)
                print(f"[govale] Contacto no encontrado, usando primero: {contact_options[0].inner_text().strip()}")
        else:
            print(f"[govale] WARNING: Solo {len(triggers2)} mat-selects, contacto no disponible")
            page.screenshot(path="/tmp/govale_debug_triggers.png")

        # 3. Llenar importe
        inputs = page.query_selector_all('input[type="text"]')
        for inp in inputs:
            if inp.is_visible():
                parent_text = inp.evaluate('e => e.parentElement.textContent.trim()')
                if 'Importe' in parent_text or '$' in parent_text:
                    inp.click()
                    time.sleep(0.5)
                    inp.fill(str(int(monto)))
                    print(f"[govale] Importe: ${monto}")
                    break

        time.sleep(1)

        # 3. Llenar cantidad
        for inp in inputs:
            if inp.is_visible() and not inp.is_disabled():
                ph = inp.get_attribute('placeholder') or ''
                if 'Máximo' in ph or '9' in ph:
                    inp.click()
                    time.sleep(0.5)
                    inp.fill(str(cantidad))
                    print(f"[govale] Cantidad: {cantidad}")
                    break

        time.sleep(1)

        # 4. Llenar concepto
        for inp in inputs:
            if inp.is_visible():
                parent_text = inp.evaluate('e => e.parentElement.textContent.trim()')
                if 'Concepto' in parent_text:
                    inp.click()
                    time.sleep(0.5)
                    inp.fill(concepto[:100])
                    print(f"[govale] Concepto: {concepto[:50]}")
                    break

        time.sleep(1)

        # 5. Click "Generar Vale"
        gen_btn = page.query_selector('button:has-text("Generar Vale")')
        if gen_btn and not gen_btn.is_disabled():
            gen_btn.click()
            time.sleep(5)
            print("[govale] Click en Generar Vale")
        else:
            return {'error': 'Botón Generar Vale deshabilitado.'}

        # 7. Verificar resultado y extraer folio/QR
        page.screenshot(path="/tmp/govale_result.png")
        body_text = page.query_selector('body').inner_text() if page.query_selector('body') else ''
        page_url = page.url

        folio_oxxogas = ''
        qr_code = ''
        qr_url = ''

        # Extraer el folio. La pagina de resultado es el LISTADO de vales, no
        # una confirmacion: cada fila trae el folio en grande y debajo la
        # fecha, y arriba hay un menu con "Vales", "Facturas", "Filtros"...
        #
        # El patron anterior era r'(Vale|Folio|#)[\s:]*([A-Z0-9\-]{6,})' sobre
        # TODO el texto, asi que cogia la primera palabra de 6+ letras que
        # siguiera a "Vale" en el menu. Por eso se guardaba "Filtros" como si
        # fuera el folio: el vale si se creaba, pero el dato quedaba basura.
        #
        # Ahora se ancla a la estructura real de la fila: un numero de 6-15
        # digitos seguido de una fecha dd/mm/aaaa. Si eso no aparece, se
        # descarta antes que devolver cualquier cosa.
        import re
        FOLIO_POR_FECHA = re.compile(r'(\d{6,15})\s*\n\s*\d{1,2}/\d{1,2}/\d{2,4}')
        m = FOLIO_POR_FECHA.search(body_text)
        if m:
            folio_oxxogas = m.group(1)
        else:
            # Respaldo: numero suelto de 6-15 digitos en las primeras filas.
            for cand in re.findall(r'(?<![\d.])\d{6,15}(?![\d.])', body_text[:2000]):
                if not cand.startswith(('19', '20')):   # no es un año
                    folio_oxxogas = cand
                    break
        if not folio_oxxogas:
            print('[govale] NO se pudo extraer el folio del listado; '
                  'el vale puede haberse creado pero no se puede referenciar')

        # Buscar QR en la página. OJO: el listado NO muestra el QR (sale al
        # reenviar o en el detalle), asi que aqui casi nunca habra uno. Por eso
        # abajo ya no se marca GENERADO solo por esto.
        qr_img = page.query_selector('img[src*="data:image"], img[alt*="QR"], img[src*="qr"]')
        if qr_img:
            qr_src = qr_img.get_attribute('src')
            if qr_src and qr_src.startswith('data:image'):
                qr_code = qr_src.split(',')[1] if ',' in qr_src else qr_src
                qr_url = qr_src
            elif qr_src:
                qr_url = qr_src

        success = False
        if 'vouchers' in page_url and '/1' not in page_url:
            success = True
        elif any(w in body_text.lower() for w in ['éxito', 'generado', 'exitoso', 'correctamente']):
            success = True

        if success:
            # Guardar en BD con todos los datos.
            #
            # El folio es lo que hace falta: identifica el vale en Go Vale. Sin
            # el, la app no puede referenciarlo y el worker no puede distinguir
            # este vale de uno nuevo. El QR, en cambio, NO sale de esta pagina
            # (el listado no lo muestra), asi que suele quedar vacio.
            #
            # Antes se ponia GENERADO igual y se imprimia "generado
            # exitosamente" con qr=no. Como el worker solo reintenta los
            # APROBADO sin QR, el vale se quedaba GENERADO para siempre y nadie
            # se enteraba: era un fallo silencioso.
            #
            # Ahora: si hay folio, el vale existe y queda GENERADO. Si NO hay
            # folio, se deja en APROBADO para que el worker lo reintente, y se
            # avisa en el log, porque sin folio no se sabe si se duplico.
            est = 'GENERADO' if folio_oxxogas else 'APROBADO'
            conn = db.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE HUB_SolicitudVales
                        SET Estatus = %s,
                            IdValeGoVale = %s,
                            CodigoQR = %s,
                            UrlQR = %s,
                            FechaAprobado = GETDATE()
                        WHERE Id = %s
                    """, (est, folio_oxxogas,
                          qr_code[:2000] if qr_code else '', qr_url[:500] if qr_url else '',
                          solicitud_id))
                    conn.commit()
                    print(f"[govale] BD actualizada: {est} folio={folio_oxxogas or '(sin folio)'}, "
                          f"qr={'sí' if qr_code else 'no (el listado de Go Vale no lo muestra)'}")
            finally:
                conn.close()

            return {'success': True, 'data': {
                'message': ('Vale generado exitosamente' if folio_oxxogas
                            else 'Vale creado en Go Vale pero NO se pudo leer el folio; '
                                 'queda APROBADO para reintentar'),
                'folio_oxxogas': folio_oxxogas,
                'qr_code': qr_code,
                'qr_url': qr_url,
                'empresa': 'ECCSA',  # empresa fija
                'contacto': contacto_nombre
            }}

        if 'error' in body_text.lower():
            idx = body_text.lower().index('error')
            return {'error': f'Go Vale: {body_text[max(0,idx-30):idx+150]}'}

        if '$0.00' in body_text and 'Saldo disponible' in body_text:
            return {'error': 'Saldo insuficiente para generar vale.'}

        return {'error': 'No se pudo confirmar la generación del vale.'}

    except Exception as e:
        print(f"[govale] Excepción: {e}")
        return {'error': f'Excepción en Playwright: {str(e)}'}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def crear_vale_desde_solicitud(solicitud_id, user=None, pwd=None):
    """Crea un vale desde una solicitud aprobada. Actualiza la solicitud."""
    return crear_vale(solicitud_id, user=user, pwd=pwd)


def generar_vale_background(solicitud_id):
    """Genera vale en background thread sin bloquear la UI."""
    import threading
    import eccsa_db as db

    def _worker():
        try:
            config = db.get_govale_credentials()
            if not config.get('user') or not config.get('password'):
                print(f"[govale_bg] No hay credenciales para vale #{solicitud_id}")
                return
            resultado = crear_vale(solicitud_id, user=config['user'], pwd=config['password'])
            if 'error' in resultado:
                print(f"[govale_bg] Error generando vale #{solicitud_id}: {resultado['error']}")
                db.revertir_solicitud_pendiente(solicitud_id)
            else:
                print(f"[govale_bg] Vale #{solicitud_id} generado exitosamente")
                # Notificar por Telegram
                try:
                    from telegram_alerts import alertar_vale_generado
                    alertar_vale_generado(solicitud_id)
                except Exception:
                    pass
        except Exception as e:
            print(f"[govale_bg] Excepción generando vale #{solicitud_id}: {e}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    print(f"[govale_bg] Hilo iniciado para vale #{solicitud_id}")


def cargar_vehiculos(user=None, pwd=None):
    """Obtiene lista de vehículos/empresas desde Go Vale vía UI."""
    if not user or not pwd:
        user, pwd = get_govale_credentials()
    if not user or not pwd:
        return []

    browser, context, page, tokens, pw = _launch_browser_and_login(user, pwd)
    try:
        if 'govale-digital.oxxogas.com' not in page.url:
            return []

        # Navegar a Empresas para obtener la lista
        page.goto("https://govale-digital.oxxogas.com/home", wait_until="networkidle", timeout=60000)
        time.sleep(10)

        body_text = page.query_selector('body').inner_text() if page.query_selector('body') else ''
        # La información de empresas está en el dashboard
        return []  # Por ahora devolver vacío, se implementa después
    except Exception:
        return []
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def get_oxxogas_empresas_contactos(user=None, pwd=None):
    """Obtiene lista de empresas y contactos de Go Vale para el selector de mapeo."""
    if not user or not pwd:
        user, pwd = get_govale_credentials()
    if not user or not pwd:
        return {'empresas': [], 'contactos': []}

    browser, context, page, tokens, pw = _launch_browser_and_login(user, pwd)
    try:
        if 'govale-digital.oxxogas.com' not in page.url:
            return {'empresas': [], 'contactos': []}

        # Ir a "Generar Vale" para ver el dropdown de empresas
        gen_link = page.query_selector('text=Generar Vale')
        if gen_link:
            gen_link.click()
            time.sleep(5)

        empresas = []
        contactos = []

        # Click en empresa dropdown
        triggers = page.query_selector_all('.mat-mdc-select-trigger')
        if triggers:
            # Empresas
            triggers[0].click()
            time.sleep(2)
            options = page.query_selector_all('mat-option')
            for opt in options:
                txt = opt.inner_text().strip()
                if txt:
                    empresas.append(txt)
            # Cerrar
            page.keyboard.press("Escape")
            time.sleep(1)

            # Contactos
            if len(triggers) > 1:
                triggers[1].click()
                time.sleep(2)
                options = page.query_selector_all('mat-option')
                for opt in options:
                    txt = opt.inner_text().strip()
                    if txt:
                        contactos.append(txt)
                page.keyboard.press("Escape")
                time.sleep(1)

        return {'empresas': empresas, 'contactos': contactos}

    except Exception as e:
        print(f"[govale] Error obteniendo empresas/contactos: {e}")
        return {'empresas': [], 'contactos': []}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def get_solicitud_pendiente_by_placa(placa):
    """Busca solicitud pendiente por placa."""
    conn = get_db_connection()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT TOP 1 Id, Estatus
                FROM HUB_SolicitudVales
                WHERE Placa = %s AND Estatus IN ('PENDIENTE', 'APROBADO')
                ORDER BY FechaSolicitud DESC
            """, (placa.strip(),))
            return cur.fetchone()
    finally:
        conn.close()
