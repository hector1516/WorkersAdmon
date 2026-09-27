# Streamlit es OPCIONAL: los workers de este contenedor usan solo la lógica de
# sincronización de este módulo. La app de HUB (Streamlit) ya no forma parte del
# ecosistema, así que el import no debe romper a los workers si no está.
try:
    import streamlit as st
except ImportError:  # pragma: no cover - ruta de los workers
    st = None
import eccsa_db as db
import imaplib
import email
from email.header import decode_header
import re
import datetime
# pandas solo lo usa show_page() (la UI de HUB, que se retiró): import opcional
# para que los workers de este contenedor no dependan de él.
try:
    import pandas as pd
except ImportError:  # pragma: no cover - ruta de los workers
    pd = None
import time
from views.utils import custom_spinner

def get_current_week_start():
    # Force start to 24th July 2026 for testing match
    return datetime.datetime(2026, 7, 24, 0, 0, 0)

def fetch_and_sync_oxxogas_emails(user_id=None):
    """Conecta a Gmail via IMAP usando la App Password guardada del usuario y descarga las facturas de OxxoGas."""
    if not user_id and st is not None:
        user_id = st.session_state.get("user_id")
    if not user_id:
        return 0, "No se identificó el usuario logueado."

    # Load credentials from DB
    credentials = None
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT GmailAccount, RefreshToken FROM HUB_GmailTokens WHERE IdUsuario = %s", (int(user_id),))
                credentials = cur.fetchone()
    except Exception as e:
        return 0, f"Error al cargar credenciales de Gmail: {e}"

    if not credentials or not credentials['RefreshToken']:
        return 0, "No tienes una cuenta de Gmail vinculada. Ve al popup 'Gmail OxxoGas' para configurarla."

    gmail_user = credentials['GmailAccount']
    app_password = credentials['RefreshToken'].replace(" ", "") # Remove spaces

    synced_count = 0
    try:
        # Connect to Gmail IMAP
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, app_password)
        
        # Fetch messages since the beginning of this week (server-side filter to avoid downloading everything)
        week_start = get_current_week_start()
        since_str = week_start.strftime("%d-%b-%Y")

        # Select folder/label "OXXOGas"
        # Gmail labels map directly to folders in IMAP
        status, _ = mail.select("OXXOGas")
        if status != 'OK':
            # Fallback to inbox and search label:OXXOGas
            status, _ = mail.select("INBOX")
            if status != 'OK':
                return 0, "No se pudo acceder a la bandeja de entrada de Gmail."
            # Search for label "OXXOGas" from the current week onwards
            status, messages = mail.search(None, f'X-GM-RAW "label:OXXOGas newer_than:30d"')
        else:
            # Successfully selected label folder directly
            status, messages = mail.search(None, f'(SINCE {since_str})')

        if status != 'OK' or not messages[0]:
            mail.logout()
            return 0, "No se encontraron correos con la etiqueta OXXOGas."

        mail_ids = messages[0].split()

        for mail_id in reversed(mail_ids): # Process newest first
            # Lightweight header fetch to skip messages already saved (avoids re-downloading attachments)
            msg_id_known = None
            try:
                res_h, hdr_data = mail.fetch(mail_id, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                if res_h == 'OK' and hdr_data and hdr_data[0]:
                    raw_hdr = hdr_data[0][1]
                    if raw_hdr:
                        hdr_msg = email.message_from_bytes(raw_hdr)
                        mid = hdr_msg.get("Message-ID")
                        if mid:
                            msg_id_known = mid.strip()
            except Exception:
                msg_id_known = None

            if msg_id_known:
                with db.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM HUB_OxxoGasVales WHERE MessageId = %s", (msg_id_known,))
                        if cur.fetchone()[0] > 0:
                            continue

            # Fetch message envelope con BODY.PEEK[] para NO marcar el correo como
            # leído (RFC822 activaría el flag \Seen). Así los correos se procesan
            # y quedan como NO leídos en Gmail.
            res, msg_data = mail.fetch(mail_id, "(BODY.PEEK[])")
            if res != 'OK':
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Get Subject and decode
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")

            # Get Date and parse
            date_str = msg["Date"]
            email_date = email.utils.parsedate_to_datetime(date_str)
            
            # Make dates offset-naive to compare
            if email_date.tzinfo is not None:
                email_date = email_date.replace(tzinfo=None)

            # Filter: ONLY messages from this week onwards
            if email_date < week_start:
                continue

            # Message ID to avoid duplicates
            msg_id = msg["Message-ID"] or f"fallback_{mail_id.decode()}_{email_date.timestamp()}"

            # Check if already processed
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM HUB_OxxoGasVales WHERE MessageId = %s", (msg_id,))
                    if cur.fetchone()[0] > 0:
                        continue # Already saved

            # Extract sender
            sender, encoding = decode_header(msg["From"])[0]
            if isinstance(sender, bytes):
                sender = sender.decode(encoding or "utf-8", errors="ignore")

            # Parse attachments (PDF & XML)
            pdf_data = None
            pdf_name = None
            xml_data = None
            xml_name = None
            body_text = ""

            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get("Content-Disposition")
                
                # Extract attachments
                if disposition and "attachment" in disposition.lower():
                    filename = part.get_filename()
                    if filename:
                        # Decode attachment name
                        decoded_name, enc = decode_header(filename)[0]
                        if isinstance(decoded_name, bytes):
                            filename = decoded_name.decode(enc or "utf-8", errors="ignore")
                        
                        file_bytes = part.get_payload(decode=True)
                        if filename.lower().endswith(".pdf"):
                            pdf_name = filename
                            pdf_data = file_bytes
                        elif filename.lower().endswith(".xml"):
                            xml_name = filename
                            xml_data = file_bytes
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    body_text += payload.decode(part.get_content_charset() or "utf-8", errors="ignore")

            # Attempt to extract total amount and metadata from XML if missing
            monto = None
            xml_folio = None
            xml_estacion = None
            xml_litros = None
            xml_concepto = None

            if xml_data:
                try:
                    xml_str = xml_data.decode("utf-8", errors="ignore")
                    
                    # 1. Extract Total amount
                    total_match = re.search(r'Total="([\d\.]+)"', xml_str)
                    if total_match:
                        monto = float(total_match.group(1))

                    # 2. Extract Folio CFDI
                    folio_match = re.search(r'\bFolio="([^"]+)"', xml_str)
                    if folio_match:
                        xml_folio = folio_match.group(1)

                    # 3. Extract Emisor / Estacion (Nombre o LugarExpedicion)
                    emisor_match = re.search(r'<cfdi:Emisor\s+[^>]*Nombre="([^"]+)"', xml_str)
                    if emisor_match:
                        xml_estacion = emisor_match.group(1)
                    else:
                        lugar_match = re.search(r'LugarExpedicion="([^"]+)"', xml_str)
                        if lugar_match:
                            xml_estacion = f"CP {lugar_match.group(1)}"

                    # 4. Extract Gas details (Litros and Concept/Description)
                    # We match both Magna/Premium style concept (15101514) and other products
                    concept_match = re.search(r'<cfdi:Concepto\s+[^>]*Cantidad="([\d\.]+)"[^>]*ClaveUnidad="LTR"[^>]*Descripcion="([^"]+)"', xml_str)
                    if concept_match:
                        xml_litros = float(concept_match.group(1))
                        xml_concepto = concept_match.group(2)
                    else:
                        # Fallback generic concept search
                        generic_concept = re.search(r'<cfdi:Concepto\s+[^>]*Cantidad="([\d\.]+)"[^>]*Descripcion="([^"]+)"', xml_str)
                        if generic_concept:
                            xml_litros = float(generic_concept.group(1))
                            xml_concepto = generic_concept.group(2)
                except Exception:
                    pass

            if monto is None and body_text:
                # Regex match standard patterns for money totals
                monto_match = re.search(r'(?:total|monto|importe)[^\d]*([\d\.,]+)', body_text, re.IGNORECASE)
                if monto_match:
                    try:
                        monto = float(monto_match.group(1).replace(",", ""))
                    except Exception:
                        pass

             # Insert vale record in database
            try:
                with db.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO HUB_OxxoGasVales 
                            (MessageId, Fecha, Remitente, Asunto, Monto, PdfName, PdfContent, XmlName, XmlContent, IdUsuario, XmlFolio, XmlEstacion, XmlLitros, XmlConcepto) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (msg_id, email_date.strftime("%Y-%m-%d %H:%M:%S"), sender[:255], subject[:255], 
                              monto, pdf_name[:255] if pdf_name else None, pdf_data, 
                              xml_name[:255] if xml_name else None, xml_data, int(user_id),
                              xml_folio, xml_estacion, xml_litros, xml_concepto))
                        conn.commit()
                synced_count += 1
            except Exception as e:
                print(f"Error inserting ticket {msg_id}: {e}")

        # Update LastSyncTime in HUB_GmailTokens
        try:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE HUB_GmailTokens 
                        SET LastSyncTime = GETDATE() 
                        WHERE IdUsuario = %s
                    """, (int(user_id),))
                    conn.commit()
        except Exception as sync_time_err:
            print(f"Error updating LastSyncTime: {sync_time_err}")

        mail.logout()
        return synced_count, "Sincronización finalizada exitosamente."

    except Exception as e:
        return 0, f"Error de conexión IMAP: {e}"

def show_page():
    if not st.session_state.get("acceso_vales_oxxogas", False):
        st.session_state.page = "menu"
        st.rerun()

    # Header Layout
    col_back, col_title = st.columns([2.2, 7.8], vertical_alignment="center")
    with col_back:
        if st.button("⬅️ Volver al Panel HUB", key="btn_back_to_hub", use_container_width=True):
            st.session_state.page = "menu"
            st.rerun()
    with col_title:
        st.markdown("<h1 style='color: #FFFFFF; margin:0; line-height: 1.1;'>🚗 Vales OxxoGas</h1>", unsafe_allow_html=True)
        st.markdown("<p style='color: #94A3B8; margin:0;'>Registro, conciliación y consumos de gasolina.</p>", unsafe_allow_html=True)

    st.write("---")

    # ── Saldo Go Vale (llamativo; rojo si < $2,000) ──────────────────────────
    try:
        _saldo_raw = db.get_govale_config('govale_saldo') or '0'
        _saldo_fecha = db.get_govale_config('govale_saldo_fecha') or 'Nunca'
        _saldo = float(_saldo_raw)
    except (TypeError, ValueError):
        _saldo = 0.0
        _saldo_fecha = 'Nunca'

    _umbral = 2000.0
    _bajo = _saldo < _umbral
    # Rojo cuando queda menos de $2,000; verde cuando hay saldo sano
    _color_txt = '#FEE2E2' if _bajo else '#DCFCE7'
    _color_num = '#EF4444' if _bajo else '#22C55E'
    _bg = 'linear-gradient(135deg, #7F1D1D 0%, #450A0A 100%)' if _bajo \
        else 'linear-gradient(135deg, #052E16 0%, #022C22 100%)'
    _borde = '#EF4444' if _bajo else '#16A34A'
    _alerta = '⚠️ Saldo bajo — recarga pronto' if _bajo else '✅ Saldo disponible para generar vales'

    st.markdown(
        f"""
        <div style="
            background: {_bg};
            border: 2px solid {_borde};
            border-radius: 14px;
            padding: 18px 26px;
            margin: 4px 0 14px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            box-shadow: 0 4px 18px rgba(0,0,0,.35);
        ">
            <div>
                <div style="color:#94A3B8; font-size:.85rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase;">
                    💰 Saldo Go Vale
                </div>
                <div style="color: {_color_num}; font-size: 2.4rem; font-weight: 800; line-height: 1.15; margin-top:2px;">
                    ${_saldo:,.2f}
                </div>
                <div style="color: {_color_txt}; font-size: .95rem; font-weight: 600; margin-top:2px;">
                    {_alerta}
                </div>
            </div>
            <div style="text-align:right; color:#94A3B8; font-size:.8rem; min-width:160px;">
                <div>🔄 Revisado:</div>
                <div style="color:#CBD5E1; font-weight:600;">{_saldo_fecha}</div>
                <div style="margin-top:8px; font-size:.75rem;">Umbral alerta: ${_umbral:,.0f}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Fetch last sync time for header display
    user_id = st.session_state.get("user_id")
    last_sync_display = "Nunca"
    try:
        with db.get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT LastSyncTime FROM HUB_GmailTokens WHERE IdUsuario = %s", (int(user_id),))
                token_row = cur.fetchone()
                if token_row and token_row.get("LastSyncTime"):
                    last_sync_display = token_row["LastSyncTime"].strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        pass

    # Create Tabs
    tab_registro, tab_conciliacion, tab_consumos = st.tabs(["📋 Registro", "🔗 Conciliación", "📊 Consumos"])

    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 1: REGISTRO (principal - estilo tabla manuscrita)
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_registro:
        st.markdown("##### 📋 Registro de Tickets")
        is_admin = st.session_state.get("acceso_usuarios", False)

        registros = []
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    if is_admin:
                        cur.execute("""
                            SELECT T.FechaRegistro, U.Nombre AS Nombre,
                                   T.Estacion AS Estacion,
                                   T.FolioTicket AS Folio,
                                   A.MarcaModelo + ' (' + A.Placas + ')' AS Carro,
                                   C.Cliente AS Empresa,
                                   T.Descripcion AS Proyecto
                            FROM HUB_OxxoGasTickets T
                            LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
                            LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
                            LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
                            ORDER BY T.FechaRegistro DESC
                        """)
                    else:
                        cur.execute("""
                            SELECT T.FechaRegistro, U.Nombre AS Nombre,
                                   T.Estacion AS Estacion,
                                   T.FolioTicket AS Folio,
                                   A.MarcaModelo + ' (' + A.Placas + ')' AS Carro,
                                   C.Cliente AS Empresa,
                                   T.Descripcion AS Proyecto
                            FROM HUB_OxxoGasTickets T
                            LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
                            LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
                            LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
                            WHERE T.IdUsuario = %s
                            ORDER BY T.FechaRegistro DESC
                        """, (int(user_id),))
                    registros = cur.fetchall()
        except Exception as e:
            st.error(f"Error al cargar registros: {e}")

        if registros:
            df_reg = pd.DataFrame(registros)
            df_reg.columns = ["Fecha", "Nombre", "Estación", "Folio", "Carro", "Empresa", "Proyecto"]
            
            col_grid, col_sort = st.columns([9, 1])
            with col_sort:
                sort_col = st.selectbox("Ordenar por:", ["Fecha", "Nombre", "Estación", "Folio", "Carro", "Empresa"], key="registro_sort_col")
                sort_asc = st.toggle("Ascendente", value=False, key="registro_sort_asc")
            df_reg = df_reg.sort_values(by=sort_col, ascending=sort_asc).reset_index(drop=True)
            
            st.data_editor(
                df_reg,
                use_container_width=True,
                height=400,
                hide_index=True,
                disabled=True,
                key="registro_main_grid"
            )
        else:
            st.info("No hay tickets registrados aún.")

    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 2: CONCILIACIÓN (tickets + facturas Gmail)
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_conciliacion:
        # Sync trigger
        col_sync1, col_sync2 = st.columns([7.5, 2.5])
        with col_sync1:
            st.write("🔄 **Sincronización:** Obtenga las últimas facturas recibidas con la etiqueta `OXXOGas` en su Gmail.")
            st.markdown(f"<p style='color: #4CAF50; font-size: 0.9rem; margin-top: 2px; margin-bottom: 0px; font-weight: 600;'>🔄 Última sincronización: {last_sync_display}</p>", unsafe_allow_html=True)
        with col_sync2:
            _busy_sync = "busy_sync_emails"
            if not st.session_state.get(_busy_sync, False):
                if st.button("🔄 Sincronizar Correos", key="btn_sync_emails", use_container_width=True, type="primary"):
                    st.session_state[_busy_sync] = True
                    st.rerun()
            if st.session_state.get(_busy_sync):
                with custom_spinner("Conectando con Gmail IMAP..."):
                    count, msg = fetch_and_sync_oxxogas_emails()
                st.session_state[_busy_sync] = False
                if count > 0:
                    st.success(f"🎉 ¡Sincronizados {count} nuevos registros de vales!")
                else:
                    st.info(msg)
                time.sleep(1.5)
                st.rerun()
        st.write("")
        st.markdown("### 🎫 Tickets Físicos Capturados (Todos los Usuarios)")
        tickets_all = []
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    # LEFT JOIN to check if we have a matched Invoice record (using the helper extraction or exact folio)
                    # Orden de columnas pedido: Fecha, Nombre, Cantidad (Monto), Folio, Auto, Cliente, Descripción, Estatus
                    cur.execute("""
                        SELECT T.Id, T.FolioTicket, T.Descripcion, T.FechaRegistro, 
                               T.Estacion AS Estacion,
                               A.MarcaModelo AS Automovil, C.Cliente AS ClienteEmpresa, U.Nombre AS RegistradoPor,
                               V.XmlFolio AS FacturaEnlazadaFolio, V.Monto AS FacturaMonto,
                               V.XmlContent AS FacturaXml
                        FROM HUB_OxxoGasTickets T
                        LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
                        LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
                        LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
                        LEFT JOIN HUB_OxxoGasVales V ON (
                            T.FolioTicket = V.XmlFolio 
                            OR V.XmlContent LIKE '%' + T.FolioTicket + '%'
                        )
                        ORDER BY T.FechaRegistro DESC
                    """)
                    tickets_all = cur.fetchall()
        except Exception as e:
            st.error(f"Error al cargar catálogo de tickets físicos: {e}")

        selected_ticket_folio = None
        selected_ticket_details = None

        if tickets_all:
            df_tk_disp = pd.DataFrame(tickets_all).copy()
            # Add friendly Match indicator
            df_tk_disp["EstadoMatch"] = df_tk_disp["FacturaEnlazadaFolio"].apply(lambda x: f"🟢 Enlazado ({x})" if pd.notna(x) and x else "🟡 Sin Factura")

            # Parse XML para extraer Total (con IVA) y NoIdentificacion (para estación)
            import re
            import xml.etree.ElementTree as ET

            def _parse_factura_xml(xml_bytes):
                """Extrae Total y NoIdentificacion del XML CFDI."""
                if not xml_bytes:
                    return None, None
                try:
                    if isinstance(xml_bytes, bytes):
                        xml_str = xml_bytes.decode('utf-8', errors='ignore')
                    else:
                        xml_str = str(xml_bytes)
                    root = ET.fromstring(xml_str)
                    # Namespace CFDI
                    ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                    # Total del comprobante
                    total = root.attrib.get('Total')
                    # NoIdentificacion del primer concepto
                    no_ident = None
                    concepto = root.find('.//cfdi:Concepto', ns)
                    if concepto is not None:
                        no_ident = concepto.attrib.get('NoIdentificacion')
                    return total, no_ident
                except Exception:
                    return None, None

            def _extract_estacion(no_ident):
                """Extrae el número entre los dos primeros '/' de NoIdentificacion.
                Ej: PL/9561/EXP/ES/2015-... -> 9561"""
                if not no_ident:
                    return ""
                parts = str(no_ident).split('/')
                if len(parts) >= 2:
                    return parts[1]
                return ""

            # Procesar cada fila
            totales = []
            estaciones = []
            for _, row in df_tk_disp.iterrows():
                total, no_ident = _parse_factura_xml(row.get('FacturaXml'))
                if total:
                    try:
                        totales.append(f"${float(total):,.2f}")
                    except (TypeError, ValueError):
                        totales.append("")
                else:
                    totales.append("")
                estaciones.append(_extract_estacion(no_ident))

            df_tk_disp["CantidadMonto"] = totales
            df_tk_disp["EstacionXml"] = estaciones

            # Folio factura: mantener original (con letras)
            df_tk_disp["FacturaFolioLimpio"] = df_tk_disp["FacturaEnlazadaFolio"]

            # Usar estación del XML si existe, sino la de la tabla
            df_tk_disp["EstacionFinal"] = df_tk_disp.apply(
                lambda r: r['EstacionXml'] if r['EstacionXml'] else r.get('Estacion', ''), axis=1
            )

            # Orden: Fecha, Nombre, Cantidad, Folio ticket, Estación, Auto, Cliente, Descripción, Estatus Factura (al final)
            df_tk_grid = df_tk_disp[["FechaRegistro", "RegistradoPor", "CantidadMonto", "FolioTicket", "EstacionFinal", "Automovil", "ClienteEmpresa", "Descripcion", "EstadoMatch"]]
            df_tk_grid.columns = ["Fecha", "Nombre", "Cantidad", "Folio ticket", "Estación", "Auto", "Cliente", "Descripción", "Estatus Factura"]
            
            event_tk = st.dataframe(
                df_tk_grid,
                use_container_width=True,
                height=200,
                on_select="rerun",
                selection_mode="single-row",
                key="oxxogas_tickets_top_grid"
            )
            
            tk_selected_rows = event_tk.selection.rows if hasattr(event_tk, "selection") else []
            if tk_selected_rows:
                selected_tk_idx = tk_selected_rows[0]
                selected_ticket_details = tickets_all[selected_tk_idx]
                selected_ticket_folio = selected_ticket_details["FolioTicket"]
                
                # Fetch matching vale automatically if top ticket is selected by checking both XmlFolio and XmlContent
                try:
                    with db.get_connection() as conn:
                        with conn.cursor(as_dict=True) as cur:
                            cur.execute("""
                                SELECT V.Id, V.MessageId, V.Fecha, V.Remitente, V.Asunto, V.Monto, V.PdfName, V.XmlName, V.FolioTicket,
                                       V.XmlFolio, V.XmlEstacion, V.XmlLitros, V.XmlConcepto, U.Nombre AS CreadoPor, V.XmlContent
                                FROM HUB_OxxoGasVales V
                                LEFT JOIN HUB_Users U ON V.IdUsuario = U.Id
                                WHERE V.XmlFolio = %s
                                   OR V.XmlContent LIKE '%' + %s + '%'
                            """, (str(selected_ticket_folio), str(selected_ticket_folio)))
                            matched_val = cur.fetchone()
                            if matched_val:
                                # Override selected vale to match selected ticket
                                st.session_state["vales_override_vale"] = matched_val
                            else:
                                st.session_state["vales_override_vale"] = "NOT_FOUND"
                except Exception as e:
                    pass
        else:
            st.info("No hay tickets físicos capturados por ningún usuario aún.")

        st.write("")
        st.markdown("### 📧 Facturas Sincronizadas (Gmail)")

        # Load and display table
        vales = []
        user_id = st.session_state.get("user_id")
        is_admin = st.session_state.get("acceso_usuarios", False) # Admins see all vales
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    if is_admin:
                        cur.execute("""
                            SELECT V.Id, V.MessageId, V.Fecha, V.Remitente, V.Asunto, V.Monto, V.PdfName, V.XmlName, V.FolioTicket,
                                   V.XmlFolio, V.XmlEstacion, V.XmlLitros, V.XmlConcepto, U.Nombre AS CreadoPor, V.XmlContent
                            FROM HUB_OxxoGasVales V
                            LEFT JOIN HUB_Users U ON V.IdUsuario = U.Id
                            ORDER BY V.Fecha DESC
                        """)
                    else:
                        cur.execute("""
                            SELECT V.Id, V.MessageId, V.Fecha, V.Remitente, V.Asunto, V.Monto, V.PdfName, V.XmlName, V.FolioTicket,
                                   V.XmlFolio, V.XmlEstacion, V.XmlLitros, V.XmlConcepto, U.Nombre AS CreadoPor, V.XmlContent
                            FROM HUB_OxxoGasVales V
                            LEFT JOIN HUB_Users U ON V.IdUsuario = U.Id
                            WHERE V.IdUsuario = %s
                            ORDER BY V.Fecha DESC
                        """, (int(user_id),))
                    vales = cur.fetchall()
        except Exception as e:
            st.error(f"Error al cargar registros de la base de datos: {e}")



        if not vales:
            st.info("No se encontraron registros de vales OxxoGas guardados para su cuenta.")
        else:
            # Add helper ticket extraction column dynamically for the DataFrame
            df_vales = pd.DataFrame(vales)
            
            def extract_ticket_no(row):
                xml_content = row.get('XmlContent')
                if xml_content:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(xml_content)
                        ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                        concepto = root.find('.//cfdi:Concepto', ns)
                        if concepto is not None:
                            no_ident = concepto.get('NoIdentificacion', '')
                            if '-' in no_ident:
                                return no_ident.split('-')[-1].strip()
                    except:
                        pass
                return "N/A"
                
            df_vales["TicketFolioExtracted"] = df_vales.apply(extract_ticket_no, axis=1)

            if is_admin:
                df_disp = df_vales[["Fecha", "Asunto", "Monto", "PdfName", "XmlName", "TicketFolioExtracted", "CreadoPor"]].copy()
                df_disp.columns = ["Fecha Recibido", "Asunto", "Total ($)", "Archivo PDF", "Archivo XML", "Folio Ticket OxxoGas", "Registrado Por"]
            else:
                df_disp = df_vales[["Fecha", "Asunto", "Monto", "PdfName", "XmlName", "TicketFolioExtracted"]].copy()
                df_disp.columns = ["Fecha Recibido", "Asunto", "Total ($)", "Archivo PDF", "Archivo XML", "Folio Ticket OxxoGas"]

            # Table Grid
            event_v = st.dataframe(
                df_disp,
                use_container_width=True,
                height=220,
                on_select="rerun",
                selection_mode="single-row",
                key="oxxogas_vales_grid"
            )

            # Determine selected vale
            sel_vale = None
            selected_rows = event_v.selection.rows if hasattr(event_v, "selection") else []
            
            # Check overrides from top grid click
            override = st.session_state.get("vales_override_vale")
            if override:
                if override != "NOT_FOUND":
                    sel_vale = override
                st.session_state["vales_override_vale"] = None # clear
            elif selected_rows:
                selected_idx = selected_rows[0]
                sel_vale = df_vales.iloc[selected_idx].to_dict()

            if selected_ticket_details and not sel_vale:
                # We selected a ticket, but no matching mail was found
                st.write("---")
                st.markdown(f"### 🎫 Detalles del Ticket Seleccionado")
                
                col_det1, col_det2 = st.columns([1.5, 1.0])
                with col_det1:
                    st.markdown(f"**Folio Ticket:** `{selected_ticket_details['FolioTicket']}`")
                    st.markdown(f"**Registrado Por:** {selected_ticket_details['RegistradoPor']}")
                    st.markdown(f"**Automóvil:** {selected_ticket_details['Automovil']}")
                    st.markdown(f"**Cliente / Empresa:** {selected_ticket_details['ClienteEmpresa']}")
                    st.markdown(f"**Fecha Registro:** {selected_ticket_details['FechaRegistro']}")
                    st.markdown(f"**Descripción:** {selected_ticket_details['Descripcion'] or 'Sin notas.'}")
                    
                    # Since selected_ticket_details is active but there is no matching mail selected in the bottom table,
                    # query the matched val XML content directly if it has FacturaEnlazadaFolio
                    factura_folio = selected_ticket_details.get("FacturaEnlazadaFolio")
                    matched_xml_content = None
                    if factura_folio:
                        try:
                            with db.get_connection() as conn:
                                with conn.cursor(as_dict=True) as cur:
                                    cur.execute("SELECT XmlContent, XmlFolio, XmlEstacion, XmlLitros, XmlConcepto, Monto FROM HUB_OxxoGasVales WHERE XmlFolio = %s", (str(factura_folio),))
                                    row_v = cur.fetchone()
                                    if row_v:
                                        matched_xml_content = row_v["XmlContent"]
                                        st.success(f"🟢 Factura Enlazada Encontrada: `{row_v['XmlFolio']}`")
                                        st.markdown(f"⛽ **Estación de Gas:** {row_v['XmlEstacion'] or 'No detectado'}")
                                        st.markdown(f"💰 **Monto Comprobante:** ${row_v['Monto'] or '0.00'}")
                        except Exception as query_err:
                            pass
                    else:
                        st.warning("⚠️ No se encontró ninguna factura enlazada a este folio del ticket en la bandeja de correo.")
                        
                    # If XML exists, parse and show Concept details table (just like the mail details view)
                    if matched_xml_content:
                        try:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(matched_xml_content)
                            ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                            
                            st.write("")
                            st.markdown("##### 🧾 Conceptos Facturados (Detalle CFDI)")
                            conceptos = root.findall('.//cfdi:Concepto', ns)
                            concept_data = []
                            for c in conceptos:
                                prod_serv = c.get('ClaveProdServ', 'N/A')
                                identificador = c.get('NoIdentificacion', 'N/A')
                                cantidad = c.get('Cantidad', 'N/A')
                                clave_unidad = c.get('ClaveUnidad', 'N/A')
                                unidad = c.get('Unidad', 'N/A')
                                descripcion = c.get('Descripcion', 'N/A')
                                valor_unitario = c.get('ValorUnitario', 'N/A')
                                importe = c.get('Importe', 'N/A')
                                
                                impuestos_c = c.find('.//cfdi:Traslados/cfdi:Traslado', ns)
                                tasa_cuota = 'N/A'
                                impuesto_tipo = 'N/A'
                                impuesto_importe = 'N/A'
                                if impuestos_c is not None:
                                    impuesto_tipo = impuestos_c.get('Impuesto', 'N/A')
                                    if impuesto_tipo == '002':
                                        impuesto_tipo = 'IVA'
                                    tasa_val = impuestos_c.get('TasaOCuota', '0.160000')
                                    try:
                                        tasa_cuota = f"{float(tasa_val)*100:.0f}%"
                                    except:
                                        tasa_cuota = tasa_val
                                    impuesto_importe = f"${float(impuestos_c.get('Importe', '0')):,.2f}"
                                    
                                concept_data.append({
                                    "Clave Prod": prod_serv,
                                    "Identificador (Folio Ticket)": identificador,
                                    "Cantidad": cantidad,
                                    "Unidad": f"{unidad} ({clave_unidad})",
                                    "Descripción": descripcion,
                                    "P. Unitario": f"${float(valor_unitario):,.2f}",
                                    "Importe Base": f"${float(importe):,.2f}",
                                    "Impuesto": f"{impuesto_tipo} ({tasa_cuota})",
                                    "Monto Imp.": impuesto_importe,
                                    "Total": f"${float(importe) + (float(impuestos_c.get('Importe', '0')) if impuestos_c is not None else 0):,.2f}"
                                })
                            if concept_data:
                                st.dataframe(pd.DataFrame(concept_data), use_container_width=True, hide_index=True)
                                
                            # Read Totals Block
                            st.write("")
                            st.markdown("##### 💵 Resumen de Totals")
                            col_tot1, col_tot2, col_tot3 = st.columns(3)
                            with col_tot1:
                                st.markdown(f"**SubTotal:** ${float(root.get('SubTotal', '0')):,.2f}")
                                st.markdown(f"**Descuento:** ${float(root.get('Descuento', '0')):,.2f}")
                            with col_tot2:
                                traslados = root.find('.//cfdi:Impuestos/cfdi:Traslados', ns)
                                total_traslado = 0.0
                                if traslados is not None:
                                    for tr in traslados.findall('cfdi:Traslado', ns):
                                        total_traslado += float(tr.get('Importe', '0'))
                                st.markdown(f"**Impuestos Trasladados:** ${total_traslado:,.2f}")
                            with col_tot3:
                                st.markdown(f"**Total Factura:** **${float(root.get('Total', '0')):,.2f}**")
                        except Exception as xml_err:
                            st.caption(f"No se pudieron cargar todos los detalles del CFDI: {xml_err}")

                with col_det2:
                    try:
                        img_bytes = None
                        img_name = None
                        with db.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT ImagenTicket, ImagenNombre FROM HUB_OxxoGasTickets WHERE Id = %s", (int(selected_ticket_details['Id']),))
                                img_row = cur.fetchone()
                                if img_row:
                                    img_bytes = img_row[0]
                                    img_name = img_row[1]
                        if img_bytes:
                            st.markdown("**📸 Foto del Ticket Físico:**")
                            st.image(img_bytes, width=280, caption=f"Foto del Ticket: {img_name}")
                            st.download_button(
                                label="⬇️ Descargar Foto del Ticket",
                                data=img_bytes,
                                file_name=img_name or "ticket.jpg",
                                mime="image/jpeg",
                                use_container_width=True,
                                key=f"dl_ticket_img_top_{selected_ticket_details['Id']}"
                            )
                    except Exception as img_err:
                        st.caption(f"Error al cargar imagen del ticket: {img_err}")
                
            elif sel_vale:
                
                st.write("---")
                st.markdown(f"### 📄 Detalles de Factura Seleccionada")
                
                col_det1, col_det2 = st.columns([1.5, 1.0])
                with col_det1:
                    st.markdown(f"**Asunto:** {sel_vale['Asunto']}")
                    st.markdown(f"**Fecha Correo:** {sel_vale['Fecha']}")
                    st.markdown(f"**Remitente:** {sel_vale['Remitente']}")
                    st.markdown(f"**Monto CFDI:** ${sel_vale['Monto'] or 'No detectado'}")
                    st.markdown(f"**Folio Factura (CFDI):** `{sel_vale['XmlFolio'] or 'N/A'}`")
                    st.markdown(f"**Estación de Gas:** {sel_vale['XmlEstacion'] or 'No detectado'}")
                    st.markdown(f"**Litros de Carga:** {f'{sel_vale[char_litros]:,.2f} Ltr' if 'XmlLitros' in sel_vale and (char_litros := 'XmlLitros') and sel_vale[char_litros] else 'No detectado'}")
                    st.markdown(f"**Producto:** {sel_vale['XmlConcepto'] or 'No detectado'}")
                    st.markdown(f"**Sincronizado Por:** {sel_vale['CreadoPor']}")
                    
                    # Match ticket section (retrieve info from HUB_OxxoGasTickets via db)
                    st.write("")
                    st.markdown("##### 🎫 Ticket Manual Enlazado")
                    
                    matched_ticket = None
                    if pd.notna(sel_vale['XmlFolio']) and sel_vale['XmlFolio']:
                        # Extract ticket ID number from full NoIdentificacion (e.g. 'PL/7653/EXP/ES/2015-6491730' -> '6491730')
                        raw_xml = sel_vale.get('XmlContent')
                        ticket_number_from_xml = None
                        if raw_xml:
                            try:
                                import xml.etree.ElementTree as ET
                                root = ET.fromstring(raw_xml)
                                ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                                concepto = root.find('.//cfdi:Concepto', ns)
                                if concepto is not None:
                                    no_ident = concepto.get('NoIdentificacion', '')
                                    if '-' in no_ident:
                                        ticket_number_from_xml = no_ident.split('-')[-1].strip()
                            except Exception as e:
                                pass
                        
                        try:
                            with db.get_connection() as conn:
                                with conn.cursor(as_dict=True) as cur:
                                    # Try exact match, partial match, or using the extracted ticket number
                                    query = """
                                        SELECT T.FolioTicket, T.Descripcion, T.FechaRegistro, 
                                               A.MarcaModelo AS Automovil, C.Cliente AS ClienteEmpresa, U.Nombre AS Conductor
                                        FROM HUB_OxxoGasTickets T
                                        LEFT JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
                                        LEFT JOIN clientes C ON T.IdCliente = C.IdCliente
                                        LEFT JOIN HUB_Users U ON T.IdUsuario = U.Id
                                        WHERE T.FolioTicket = %s 
                                           OR T.FolioTicket = %s
                                           OR %s LIKE '%' + T.FolioTicket + '%'
                                    """
                                    cur.execute(query, (str(sel_vale['XmlFolio']), str(ticket_number_from_xml), str(ticket_number_from_xml) if ticket_number_from_xml else ""))
                                    matched_ticket = cur.fetchone()
                        except Exception as db_err:
                            st.caption(f"Error al verificar enlace: {db_err}")
                            
                    if matched_ticket:
                        st.markdown(f"🟢 **Ticket Asociado:** `{matched_ticket['FolioTicket']}`")
                        st.markdown(f"👤 **Cargado Por:** {matched_ticket['Conductor']}")
                        st.markdown(f"🚗 **Automóvil:** {matched_ticket['Automovil']}")
                        st.markdown(f"👥 **Cliente / Empresa:** {matched_ticket['ClienteEmpresa']}")
                        st.markdown(f"📅 **Fecha Registro:** {matched_ticket['FechaRegistro']}")
                        st.markdown(f"📝 **Descripción:** {matched_ticket['Descripcion'] or 'Sin notas.'}")
                        
                        # Fetch and render the ticket manual photo if matching ticket exists
                        try:
                            img_bytes = None
                            img_name = None
                            with db.get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT ImagenTicket, ImagenNombre FROM HUB_OxxoGasTickets WHERE FolioTicket = %s", (str(matched_ticket['FolioTicket']),))
                                    img_row = cur.fetchone()
                                    if img_row:
                                        img_bytes = img_row[0]
                                        img_name = img_row[1]
                                        
                            if img_bytes:
                                st.markdown("**📸 Foto del Ticket Físico Enlazado:**")
                                st.image(img_bytes, width=280, caption=f"Foto del Ticket: {img_name}")
                                st.download_button(
                                    label="⬇️ Descargar Foto del Ticket",
                                    data=img_bytes,
                                    file_name=img_name or "ticket.jpg",
                                    mime="image/jpeg",
                                    use_container_width=True,
                                    key=f"dl_ticket_img_match_{sel_vale['Id']}"
                                )
                        except Exception as img_err:
                            st.caption(f"Error al cargar imagen del ticket: {img_err}")
                    else:
                        st.info("🟡 Ningún conductor ha registrado el ticket manual que coincida con esta factura aún.")
                        
                    # --- PARTE 3: DETALLE DETALLADO CFDI (XML) EXTRAÍDO SIN IA ---
                    if 'XmlContent' in sel_vale and sel_vale['XmlContent']:
                        try:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(sel_vale['XmlContent'])
                            ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                            
                            st.write("")
                            st.markdown("##### 🧾 Conceptos Facturados (Detalle CFDI)")
                            
                            # Read Conceptos
                            conceptos = root.findall('.//cfdi:Concepto', ns)
                            concept_data = []
                            for c in conceptos:
                                # Extract basic attributes safely
                                prod_serv = c.get('ClaveProdServ', 'N/A')
                                identificador = c.get('NoIdentificacion', 'N/A')
                                cantidad = c.get('Cantidad', 'N/A')
                                clave_unidad = c.get('ClaveUnidad', 'N/A')
                                unidad = c.get('Unidad', 'N/A')
                                descripcion = c.get('Descripcion', 'N/A')
                                valor_unitario = c.get('ValorUnitario', 'N/A')
                                importe = c.get('Importe', 'N/A')
                                descuento = c.get('Descuento', '0.00')
                                objeto_imp = c.get('ObjetoImp', 'N/A')
                                
                                # Extract inner taxes
                                impuestos_c = c.find('.//cfdi:Traslados/cfdi:Traslado', ns)
                                tasa_cuota = 'N/A'
                                impuesto_tipo = 'N/A'
                                impuesto_importe = 'N/A'
                                if impuestos_c is not None:
                                    impuesto_tipo = impuestos_c.get('Impuesto', 'N/A')
                                    if impuesto_tipo == '002':
                                        impuesto_tipo = 'IVA'
                                    tasa_val = impuestos_c.get('TasaOCuota', '0.160000')
                                    try:
                                        tasa_cuota = f"{float(tasa_val)*100:.0f}%"
                                    except:
                                        tasa_cuota = tasa_val
                                    impuesto_importe = f"${float(impuestos_c.get('Importe', '0')):,.2f}"
                                    
                                concept_data.append({
                                    "Clave Prod": prod_serv,
                                    "Identificador (Folio Ticket)": identificador,
                                    "Cantidad": cantidad,
                                    "Unidad": f"{unidad} ({clave_unidad})",
                                    "Descripción": descripcion,
                                    "P. Unitario": f"${float(valor_unitario):,.2f}",
                                    "Importe Base": f"${float(importe):,.2f}",
                                    "Impuesto": f"{impuesto_tipo} ({tasa_cuota})",
                                    "Monto Imp.": impuesto_importe,
                                    "Total": f"${float(importe) + (float(impuestos_c.get('Importe', '0')) if impuestos_c is not None else 0):,.2f}"
                                })
                                
                            if concept_data:
                                st.dataframe(pd.DataFrame(concept_data), use_container_width=True, hide_index=True)
                                
                            # Read Totals Block
                            st.write("")
                            st.markdown("##### 💵 Resumen de Totals")
                            col_tot1, col_tot2, col_tot3 = st.columns(3)
                            with col_tot1:
                                st.markdown(f"**SubTotal:** ${float(root.get('SubTotal', '0')):,.2f}")
                                st.markdown(f"**Descuento:** ${float(root.get('Descuento', '0')):,.2f}")
                            with col_tot2:
                                traslados = root.find('.//cfdi:Impuestos/cfdi:Traslados', ns)
                                total_traslado = 0.0
                                if traslados is not None:
                                    for tr in traslados.findall('cfdi:Traslado', ns):
                                        total_traslado += float(tr.get('Importe', '0'))
                                st.markdown(f"**Impuestos Trasladados:** ${total_traslado:,.2f}")
                            with col_tot3:
                                st.markdown(f"**Total Factura:** **${float(root.get('Total', '0')):,.2f}**")
                                
                        except Exception as xml_err:
                            st.caption(f"No se pudieron cargar todos los detalles del CFDI: {xml_err}")
                with col_det2:
                    st.markdown("**📁 Descargas Disponibles:**")
                    
                    # Download PDF trigger
                    if sel_vale['PdfName']:
                        try:
                            with db.get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT PdfContent FROM HUB_OxxoGasVales WHERE Id = %s", (int(sel_vale['Id']),))
                                    pdf_bytes = cur.fetchone()[0]
                            if pdf_bytes:
                                st.download_button(
                                    label=f"⬇️ Descargar PDF ({sel_vale['PdfName']})",
                                    data=pdf_bytes,
                                    file_name=sel_vale['PdfName'],
                                    mime="application/pdf",
                                    use_container_width=True,
                                    key=f"dl_pdf_{sel_vale['Id']}"
                                )
                            else:
                                st.caption("⚠️ PDF no disponible en base de datos.")
                        except Exception as e:
                            st.caption(f"Error al cargar PDF: {e}")
                    else:
                        st.caption("❌ No contiene archivo PDF adjunto.")

                    # Download XML trigger
                    if sel_vale['XmlName']:
                        try:
                            with db.get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT XmlContent FROM HUB_OxxoGasVales WHERE Id = %s", (int(sel_vale['Id']),))
                                    xml_bytes = cur.fetchone()[0]
                            if xml_bytes:
                                st.download_button(
                                    label=f"⬇️ Descargar XML ({sel_vale['XmlName']})",
                                    data=xml_bytes,
                                    file_name=sel_vale['XmlName'],
                                    mime="application/xml",
                                    use_container_width=True,
                                    key=f"dl_xml_{sel_vale['Id']}"
                                )
                            else:
                                st.caption("⚠️ XML no disponible en base de datos.")
                        except Exception as e:
                            st.caption(f"Error al cargar XML: {e}")
                    else:
                        st.caption("❌ No contiene XML.")

    # --- PART 4: CONSUMOS POR AUTOMÓVIL TAB VIEW ---
    with tab_consumos:
        st.markdown("### 📊 Consumos de Gasolina y Gasto por Automóvil")
        
        # Calculate default dates (current week: Monday to Sunday)
        today = datetime.date.today()
        default_start = today - datetime.timedelta(days=today.weekday())
        default_end = default_start + datetime.timedelta(days=6)
        
        # Date Filter Row
        col_f1, col_f2, col_f3 = st.columns([4.0, 4.0, 2.0])
        with col_f1:
            date_start = st.date_input("📅 Fecha de Inicio", value=default_start, key="consumos_start_date")
        with col_f2:
            date_end = st.date_input("📅 Fecha de Fin", value=default_end, key="consumos_end_date")
        with col_f3:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            btn_filter = st.button("🔍 Filtrar", key="btn_filter_consumos", use_container_width=True)

        # Convert to datetime format for sql query
        dt_start_str = datetime.datetime.combine(date_start, datetime.time.min).strftime("%Y-%m-%d %H:%M:%S")
        dt_end_str = datetime.datetime.combine(date_end, datetime.time.max).strftime("%Y-%m-%d %H:%M:%S")
        
        # Aggregate consumption metrics from database (joining tickets, automoviles and matched vales)
        consumo_records = []
        try:
            with db.get_connection() as conn:
                with conn.cursor(as_dict=True) as cur:
                    cur.execute("""
                        SELECT A.MarcaModelo AS Automovil, A.Placas,
                               COUNT(T.Id) AS TotalCargas,
                               SUM(CAST(V.XmlLitros AS FLOAT)) AS TotalLitros,
                               SUM(CAST(V.Monto AS FLOAT)) AS TotalMonto
                        FROM HUB_OxxoGasTickets T
                        INNER JOIN HUB_Automoviles A ON T.IdVehiculo = A.Id
                        INNER JOIN HUB_OxxoGasVales V ON (
                            T.FolioTicket = V.XmlFolio 
                            OR V.XmlContent LIKE '%' + T.FolioTicket + '%'
                        )
                        WHERE V.Fecha BETWEEN %s AND %s
                        GROUP BY A.MarcaModelo, A.Placas
                        ORDER BY TotalMonto DESC
                    """, (dt_start_str, dt_end_str))
                    consumo_records = cur.fetchall()
        except Exception as e:
            st.error(f"Error al cargar las estadísticas de consumos: {e}")
            
        if consumo_records:
            df_consumos = pd.DataFrame(consumo_records)
            df_display_c = df_consumos.copy()
            df_display_c.columns = ["Automóvil", "Placas", "Total de Cargas", "Total Litros (Ltr)", "Gasto Total ($)"]
            
            # Display Table Grid
            st.dataframe(
                df_display_c.style.format({
                    "Total Litros (Ltr)": "{:,.2f} L",
                    "Gasto Total ($)": "${:,.2f}"
                }),
                use_container_width=True,
                hide_index=True
            )
            
            st.write("---")
            st.markdown("#### 📈 Gráfica de Gasto y Litros por Vehículo")
            
            # Draw a beautiful streamlit double bar chart
            # We construct a dataframe index on Automovil
            df_chart = df_consumos.set_index("Automovil")[["TotalLitros", "TotalMonto"]]
            df_chart.columns = ["Total Litros (L)", "Gasto Total ($)"]
            
            st.bar_chart(df_chart, height=350, use_container_width=True)
            
        else:
            st.info(f"No se registraron consumos ni facturas enlazadas entre el {date_start} y el {date_end}.")
