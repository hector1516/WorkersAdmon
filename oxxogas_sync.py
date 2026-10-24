"""oxxogas_sync.py - sync de facturas OxxoGas desde Gmail.

Extraido de views/vales_oxxogas.py para que el worker no dependa de Streamlit.
"""

import eccsa_db as db
import imaplib
import email
from email.header import decode_header
import re
import datetime
import time

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None


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
