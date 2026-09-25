import io
import datetime
import os
import base64
import time
import eccsa_db as db
import pdf_storage
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Register DejaVu Sans fonts for better readability ---
_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
try:
    pdfmetrics.registerFont(TTFont('DejaVuSans', os.path(_FONTS_DIR, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', os.path(_FONTS_DIR, 'DejaVuSans-Bold.ttf')))
    _PDF_FONT = 'DejaVuSans'
    _PDF_FONT_BOLD = 'DejaVuSans-Bold'
except Exception:
    _PDF_FONT = 'Helvetica'
    _PDF_FONT_BOLD = 'Helvetica-Bold'

# --- PDF Cache (TTL 10 min) ---
_pdf_cache = {}
_PDF_CACHE_TTL = 600

def invalidate_service_report_cache(folio):
    """Invalida el cache del PDF de un reporte de servicio específico."""
    key = f"srv_{folio}"
    if key in _pdf_cache:
        del _pdf_cache[key]

def _get_cached_pdf(cache_key):
    if cache_key in _pdf_cache:
        data, ts = _pdf_cache[cache_key]
        if time.time() - ts < _PDF_CACHE_TTL:
            return data
        del _pdf_cache[cache_key]
    return None

def _set_cached_pdf(cache_key, data):
    _pdf_cache[cache_key] = (data, time.time())
    if len(_pdf_cache) > 50:
        oldest = min(_pdf_cache, key=lambda k: _pdf_cache[k][1])
        del _pdf_cache[oldest]

def invalidate_remision_pdf_cache(id_remision):
    """Invalida el cache del PDF de una remisión (p. ej. tras firmar en Field)."""
    key = f"rem_{id_remision}"
    if key in _pdf_cache:
        del _pdf_cache[key]


def _normalize_image_for_pdf(raw_bytes, max_dim=250, quality=None):
    """Normaliza imagen para ReportLab: RGB, PNG (FlateDecode/zlib).
    
    JPEG (/DCTDecode) crashea WhatsApp iOS al procesar el PDF en el ShareSheet.
    PNG usa compresión zlib nativa (/FlateDecode) que iOS maneja sin crash.
    
    Resuelve problemas de WhatsApp iOS:
    - JPEG progresivo (SOF2 0xFFC2) / DCTDecode
    - Perfiles /ICCBased
    - Espacios /DeviceCMYK
    - Diccionarios /SMask de transparencia (alpha eliminado al convertir RGB)
    """
    from PIL import Image as PILImage
    img_buf = io.BytesIO(raw_bytes)
    pil_img = PILImage.open(img_buf)
    
    # Convertir a RGB (elimina Alpha → evita /SMask en ReportLab)
    if pil_img.mode == 'RGBA':
        background = PILImage.new('RGB', pil_img.size, (255, 255, 255))
        background.paste(pil_img, mask=pil_img.split()[3])
        pil_img = background
    elif pil_img.mode != 'RGB':
        pil_img = pil_img.convert('RGB')
    
    # Thumbnail a max_dim manteniendo aspect ratio
    pil_img.thumbnail((max_dim, max_dim), PILImage.LANCZOS)
    
    # Guardar como PNG: compresión zlib nativa (FlateDecode en PDF)
    # optimize=True usa zlib level 9
    out = io.BytesIO()
    pil_img.save(out, format='PNG', optimize=True)
    out.seek(0)
    return out


def _build_photo_grid_composite(fotos, max_photos=6):
    """Construye UNA imagen compuesta con grid 2x3 de las fotos del reporte.
    
    Reduce de 6 XObjects independientes a 1 solo XObject por página.
    Evita crash WhatsApp iOS al procesar múltiples imágenes en ShareSheet.
    
    Grid: 2 columnas x 3 filas máx = 6 fotos
    Cada celda: 250x250 px + caption 30px = 250x280
    Total canvas: 540px ancho x 650px alto (con márgenes)
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont
    
    # Configuración del grid
    CELL_W, CELL_H = 250, 250
    CAPTION_H = 30
    MARGIN = 20
    GAP = 10
    COLS = 2
    ROWS = 3
    
    canvas_w = MARGIN * 2 + COLS * CELL_W + (COLS - 1) * GAP
    canvas_h = MARGIN * 2 + ROWS * (CELL_H + CAPTION_H) + (ROWS - 1) * GAP
    
    # Canvas blanco
    canvas = PILImage.new('RGB', (canvas_w, canvas_h), 'white')
    draw = ImageDraw.Draw(canvas)
    
    # Fuente para captions
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    
    # Procesar máximo 6 fotos
    for idx, foto in enumerate(fotos[:max_photos]):
        try:
            # Normalizar imagen (PNG, 250px max)
            normalized = _normalize_image_for_pdf(foto['FotoComprimida'], max_dim=CELL_W)
            pil_img = PILImage.open(normalized)
            
            # Calcular posición en grid
            col = idx % COLS
            row = idx // COLS
            x = MARGIN + col * (CELL_W + GAP)
            y = MARGIN + row * (CELL_H + CAPTION_H + GAP)
            
            # Centrar imagen en celda (mantener aspect ratio)
            img_x = x + (CELL_W - pil_img.width) // 2
            img_y = y + (CELL_H - pil_img.height) // 2
            
            # Pegar imagen
            canvas.paste(pil_img, (img_x, img_y))
            
            # Dibujar caption "Foto N" centrado
            caption = f"Foto {foto['Orden']}"
            bbox = draw.textbbox((0, 0), caption, font=font)
            text_w = bbox[2] - bbox[0]
            text_x = x + (CELL_W - text_w) // 2
            text_y = y + CELL_H + 5
            draw.text((text_x, text_y), caption, fill='#475569', font=font)
            
        except Exception:
            # Si falla una foto, dibujar placeholder
            col = idx % COLS
            row = idx // COLS
            x = MARGIN + col * (CELL_W + GAP)
            y = MARGIN + row * (CELL_H + CAPTION_H + GAP)
            draw.rectangle([x, y, x + CELL_W, y + CELL_H], outline='#CBD5E1', width=1)
            draw.text((x + 10, y + CELL_H // 2), f"Foto {foto['Orden']} (error)", fill='#EF4444', font=font)
    
    # Guardar como JPEG baseline (más pequeño que PNG para canvas grande)
    out = io.BytesIO()
    canvas.save(out, format='JPEG', quality=80, progressive=False, optimize=True)
    out.seek(0)
    return out


def clear_quotation_pdf_cache(folio):
    """Limpia el caché del PDF para un folio específico."""
    cache_key = f"quot_{folio}"
    if cache_key in _pdf_cache:
        del _pdf_cache[cache_key]
        return True
    return False


def clear_all_pdf_cache():
    """Limpia todo el caché de PDFs."""
    _pdf_cache.clear()
    return True


# --- SPANISH NUMBER TO WORDS CONVERTER ---
UNIDADES = ["", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
DECENAS = ["", "DIEZ", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
ESPECIALES = {
    11: "ONCE", 12: "DOCE", 13: "TRECE", 14: "CATORCE", 15: "QUINCE",
    16: "DIECISEIS", 17: "DIECISIETE", 18: "DIECIOCHO", 19: "DIECINUEVE",
    21: "VEINTIUN", 22: "VEINTIDOS", 23: "VEINTITRES", 24: "VEINTICUATRO",
    25: "VEINTICINCO", 26: "VEINTISEIS", 27: "VEINTISIETE", 28: "VEINTIOCHO", 29: "VEINTINUEVE"
}
CENTENAS = ["", "CIEN", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

def _convert_group(n):
    if n == 0:
        return ""
    res = []
    c = n // 100
    d = (n % 100) // 10
    u = n % 10
    
    if c > 0:
        if c == 1 and (d > 0 or u > 0):
            res.append("CIENTO")
        else:
            res.append(CENTENAS[c])
            
    du = n % 100
    if du > 0:
        if du in ESPECIALES:
            res.append(ESPECIALES[du])
        else:
            if d > 0:
                if u > 0:
                    res.append(f"{DECENAS[d]} Y {UNIDADES[u]}")
                else:
                    res.append(DECENAS[d])
            elif u > 0:
                res.append(UNIDADES[u])
    return " ".join(res)

def numero_a_letras(num):
    entero = int(num)
    cents = int(round((num - entero) * 100))
    cents_str = f"{cents:02d}/100 M. N. Pesos."
    
    if entero == 0:
        return f"CERO {cents_str}"
        
    parts = []
    # Millions
    millones = entero // 1000000
    resto = entero % 1000000
    if millones > 0:
        if millones == 1:
            parts.append("UN MILLON")
        else:
            parts.append(f"{_convert_group(millones)} MILLONES")
            
    # Thousands
    miles = resto // 1000
    unidades = resto % 1000
    if miles > 0:
        if miles == 1:
            parts.append("MIL")
        else:
            parts.append(f"{_convert_group(miles)} MIL")
            
    # Unidades
    if unidades > 0:
        parts.append(_convert_group(unidades))
        
    text = " ".join(parts)
    text = text.replace("UN MIL", "MIL")
    text = text.replace("  ", " ").strip()
    return f"{text} {cents_str}"


# --- NUMBERED CANVAS FOR DYNAMIC PAGES ---
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 6.5)
        self.setFillColor(colors.HexColor("#475569"))
        # Left side footer: company address & website
        address_text = "Fray Luis de Leon 1713, Jardin Español, Monterrey, Nuevo Leon, Cp. 64820 | www.ecc-sa.com.mx"
        self.drawString(30, 12, address_text)
        # Right side footer: Pagina X de Y
        page_text = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(582, 12, page_text)
        self.restoreState()


# --- PDF GENERATOR FUNCTION ---
def generate_quotation_pdf(folio):
    cached = _get_cached_pdf(f"quot_{folio}")
    if cached:
        return cached
    # 1. Fetch data
    details = db.get_quotation_full_details(folio)
    items = db.get_quotation_items(folio)
    
    if not details:
        return None
        
    # 2. Setup document buffer & template
    # Printable area: 612 - 30 = 582 points wide (margin 15 points left/right)
    # Page size: Letter (612 x 792 points)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=15,
        rightMargin=15,
        topMargin=15,
        bottomMargin=25
    )
    
    # 3. Setup styles
    styles = getSampleStyleSheet()
    
    style_normal = ParagraphStyle('Norm', fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.black)
    style_bold = ParagraphStyle('Bld', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    
    style_meta_label = ParagraphStyle('MLbl', fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    style_meta_value = ParagraphStyle('MVal', fontName='Helvetica', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    
    style_item_desc = ParagraphStyle('ItemDesc', fontName='Helvetica', fontSize=8, leading=9.5, textColor=colors.black)
    style_item_header = ParagraphStyle('ItemHdr', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    
    style_words = ParagraphStyle('Words', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    
    story = []
    
    # 4. HEADER SECTION (Logo + Metadata Table)
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    logo_container = []
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_norm = _normalize_image_for_pdf(f.read(), max_dim=300, quality=50)
        logo_container.append(RLImage(logo_norm, width=175, height=52))
        logo_container.append(Spacer(1, 4))
    
    style_logo_subtext = ParagraphStyle('LogoSub', fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#334155"))
    logo_container.append(Paragraph("Oscar Noe Castillo Zavala - CAZO670914BK8", style_logo_subtext))
        
    # Format metadata right side
    # e.g. "Cotizacion: CM01201"
    cot_number = f"CM{str(details['Folio']).zfill(5)}"
    
    date_val = details['Fecha']
    if isinstance(date_val, (datetime.date, datetime.datetime)):
        date_str = date_val.strftime("%d/%m/%Y")
    else:
        date_str = str(date_val)
        
    # Build Metadata text table (Right aligned)
    # We lay this out in key-value pairs
    meta_data = [
        [Paragraph(f"Cotizacion: {cot_number}", style_meta_label)],
        [Paragraph(f"Fecha: {date_str}", style_meta_value)],
        [Paragraph(f"Cliente: {details['Contacto'] or ''}", style_meta_value)],
        [Paragraph(f"{details['ClienteNombre'] or ''}", style_meta_value)],
        [Paragraph(f"Elaboro: {details['Autor']}", style_meta_value)],
        [Paragraph(f"Telefono: {details['Telefono']}", style_meta_value)]
    ]
    meta_table = Table(meta_data, colWidths=[380])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    
    # Header container (Logo on left, Metadata on right)
    header_data = [[logo_container, meta_table]]
    header_table = Table(header_data, colWidths=[202, 380])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))
    
    # 5. MAIN ITEMS TABLE (Part., Cant., Descripcion, Sub., Total)
    # Printable width: 582 points
    # Col widths: Part (30), Cant (30), Descripcion (402), Sub (60), Total (60)
    items_data = [
        [
            Paragraph("Part.", style_item_header),
            Paragraph("Cant.", style_item_header),
            Paragraph("Descripcion", style_item_header),
            Paragraph("Sub.", style_item_header),
            Paragraph("Total", style_item_header)
        ]
    ]
    
    subtotal_general = 0.0
    max_delivery_days = 1
    
    for idx, item in enumerate(items):
        partida_num = str(item['Partida']).zfill(3)
        cantidad_num = str(item['Cantidad']).zfill(3)
        
        # Calculate sale prices
        cant = float(item['Cantidad'])
        p_compra = float(item['PrecioCompraUnitario'])
        factor = float(item['Factor'])
        flete = float(item['Flete'])
        delivery_days = int(item['TiempoEntregaDias'])
        
        if delivery_days > max_delivery_days:
            max_delivery_days = delivery_days
            
        subtotal_temp = p_compra * (1.0 + factor)
        total_item = (subtotal_temp * cant) + flete
        unit_price_item = total_item / cant if cant > 0 else 0.0
        
        subtotal_general += total_item
        
        # Clean description (remove tabs/newlines)
        desc_clean = str(item['Descripcion']).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        diass_suffix = " Dia Laboral" if delivery_days == 1 else " Dias Laborales"
        desc_full = f"{desc_clean}<br/><font color='#64748B'>Tiempo entrega: {delivery_days}{diass_suffix}</font>"
        
        items_data.append([
            Paragraph(partida_num, style_item_desc),
            Paragraph(cantidad_num, style_item_desc),
            Paragraph(desc_full, style_item_desc),
            Paragraph(f"${unit_price_item:,.2f}", style_item_desc),
            Paragraph(f"${total_item:,.2f}", style_item_desc)
        ])
        
    # Render main items table
    items_table = Table(items_data, colWidths=[30, 30, 402, 60, 60])
    items_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F8FAFC")),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('ALIGN', (0,0), (1,-1), 'CENTER'),
        ('ALIGN', (3,0), (4,-1), 'RIGHT'),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 10))
    
    # 6. TOTALS SECTION & WORDS
    iva_general = subtotal_general * 0.16
    total_general = subtotal_general + iva_general
    total_letras = numero_a_letras(total_general).upper()
    
    # Right-aligned totals table (width = 120)
    totals_rows = [
        [Paragraph("Subtotal", style_bold), Paragraph(f"${subtotal_general:,.2f}", style_bold)],
        [Paragraph("I.V.A. (16%)", style_bold), Paragraph(f"${iva_general:,.2f}", style_bold)],
        [Paragraph("Total", style_bold), Paragraph(f"${total_general:,.2f}", style_bold)]
    ]
    totals_table = Table(totals_rows, colWidths=[70, 70])
    totals_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
    ]))
    
    # Wrap totals to align right
    totals_wrapper = Table([[Spacer(1,1), totals_table]], colWidths=[442, 140])
    totals_wrapper.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(totals_wrapper)
    story.append(Spacer(1, 10))
    
    # Total in words paragraph
    words_table = Table([[Paragraph(total_letras, style_words)]], colWidths=[582])
    words_table.setStyle(TableStyle([
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(words_table)
    story.append(Spacer(1, 10))
    
    # 7. TERMS & CORPORATE CONDITIONS (Table C in VB)
    cond_pago_days = details.get('CondicionesPagoDias', 30) or 30
    te_suffix = " Dia Laboral" if max_delivery_days == 1 else " Dias Laborales"
    
    cond_col1_data = [
        [Paragraph(f"T.E. Max. {str(max_delivery_days).zfill(2)}{te_suffix}", style_bold)],
        [Paragraph(f"Condiciones de Pago: {str(cond_pago_days).zfill(2)} dias", style_normal)],
        [Paragraph("Precios en Moneda Nacional", style_normal)],
        [Paragraph("Stock salvo previa venta", style_normal)]
    ]
    cond_col1_table = Table(cond_col1_data, colWidths=[200])
    cond_col1_table.setStyle(TableStyle([
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    
    # Col 2 is corporate information
    author_email = str(details['Autor']).lower().replace(' ', '.') + "@ecc-sa.com.mx"
    cond_col2_data = [
        [Paragraph("ECCSA Automation, RFC: CAZO670914BK8", style_bold)],
        [Paragraph(f"Telefono: 8183589075, eccsa@ecc-sa.com.mx {author_email}", style_normal)],
        [Paragraph("Fray Luis de Leon 1713, Jardin Español, Monterrey, Nuevo Leon, Cp. 64820", style_normal)],
        [Paragraph("La cotizacion tiene una vigencia de 15 dias naturales a partir de su fecha de emisión", style_normal)]
    ]
    cond_col2_table = Table(cond_col2_data, colWidths=[382])
    cond_col2_table.setStyle(TableStyle([
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    
    # Combine conditions in a KeepTogether group
    cond_table = Table([[cond_col1_table, cond_col2_table]], colWidths=[200, 382])
    cond_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    
    story.append(KeepTogether([cond_table]))
    
    # 8. Build Document using NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    _set_cached_pdf(f"quot_{folio}", pdf_bytes)
    pdf_storage.save_pdf("materiales", f"CM{int(folio):05d}", pdf_bytes, details.get('Fecha'))
    return pdf_bytes


# --- REMISION PDF (desde Cotizaciones de Materiales) ---
# Mismo encabezado que la cotización pero etiquetado "Remisión",
# solo Part./Cant./Descripción (sin precios) y línea de firma física.
# El documento es inmutable: se genera desde el snapshot en BD.

def generate_remision_pdf(id_remision):
    """Genera el PDF de una remisión ya creada. Retorna bytes o None."""
    cached = _get_cached_pdf(f"rem_{id_remision}")
    if cached:
        return cached

    details = db.get_remision_full_details(id_remision)
    items = db.get_remision_partidas(id_remision)
    if not details:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=15,
        rightMargin=15,
        topMargin=15,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('RemNorm', fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.black)
    style_bold = ParagraphStyle('RemBld', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    style_meta_label = ParagraphStyle('RemMLbl', fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    style_meta_value = ParagraphStyle('RemMVal', fontName='Helvetica', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    style_item_desc = ParagraphStyle('RemItemDesc', fontName='Helvetica', fontSize=8, leading=9.5, textColor=colors.black)
    style_item_header = ParagraphStyle('RemItemHdr', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    style_firma_label = ParagraphStyle('RemFirma', fontName='Helvetica', fontSize=8, leading=11, textColor=colors.HexColor("#334155"))

    story = []

    # HEADER (logo + metadata) — mismo layout que generate_quotation_pdf
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    logo_container = []
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_norm = _normalize_image_for_pdf(f.read(), max_dim=300, quality=50)
        logo_container.append(RLImage(logo_norm, width=175, height=52))
        logo_container.append(Spacer(1, 4))
    style_logo_subtext = ParagraphStyle('RemLogoSub', fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#334155"))
    logo_container.append(Paragraph("Oscar Noe Castillo Zavala - CAZO670914BK8", style_logo_subtext))

    fecha_val = details['FechaCreacion']
    if isinstance(fecha_val, (datetime.date, datetime.datetime)):
        fecha_str = fecha_val.strftime("%d/%m/%Y %H:%M")
    else:
        fecha_str = str(fecha_val)

    folio_rm = details['FolioRemision']
    folio_cm = f"CM{int(details['FolioCotizacion']):05d}"

    meta_data = [
        [Paragraph(f"Remision: {folio_rm}", style_meta_label)],
        [Paragraph(f"Cotizacion origen: {folio_cm}", style_meta_value)],
        [Paragraph(f"Fecha: {fecha_str}", style_meta_value)],
        [Paragraph(f"Cliente: {details['Contacto'] or ''}", style_meta_value)],
        [Paragraph(f"{details['ClienteNombre'] or ''}", style_meta_value)],
        [Paragraph(f"Elaboro: {details['Autor']}", style_meta_value)],
        [Paragraph(f"Creado por: {details['CreadoPor']}", style_meta_value)],
        [Paragraph(f"Telefono: {details['Telefono']}", style_meta_value)],
    ]
    meta_table = Table(meta_data, colWidths=[380])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))

    header_data = [[logo_container, meta_table]]
    header_table = Table(header_data, colWidths=[202, 380])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6))

    # Descripción general de la cotización origen
    if (details.get('Descripcion') or '').strip():
        story.append(Paragraph(f"<b>Descripcion:</b> {details['Descripcion'].strip()}", style_normal))
        story.append(Spacer(1, 8))

    # TABLA: solo Part. / Cant. / Descripcion (sin precios)
    items_data = [
        [
            Paragraph("Part.", style_item_header),
            Paragraph("Cant.", style_item_header),
            Paragraph("Descripcion", style_item_header),
        ]
    ]
    for item in items:
        partida_num = str(item['Partida']).zfill(3)
        cantidad_num = str(item['Cantidad']).zfill(3)
        desc_clean = str(item['Descripcion']).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        items_data.append([
            Paragraph(partida_num, style_item_desc),
            Paragraph(cantidad_num, style_item_desc),
            Paragraph(desc_clean, style_item_desc),
        ])

    items_table = Table(items_data, colWidths=[40, 40, 502])
    items_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (0, 0), (1, -1), 'CENTER'),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 40))

    # Firma digital (Field/HUB) si ya existe; si no, línea física en blanco
    firma_img = None
    firma_raw = details.get('FirmaConformidad')
    if firma_raw and isinstance(firma_raw, str) and firma_raw.strip():
        try:
            import base64 as _b64
            sig_payload = firma_raw.split(',', 1)[1] if ',' in firma_raw else firma_raw
            sig_bytes = _b64.b64decode(sig_payload)
            sig_norm = _normalize_image_for_pdf(sig_bytes, max_dim=400, quality=50)
            firma_img = RLImage(sig_norm, width=150, height=60)
        except Exception as e:
            print("Error rendering remision signature on PDF:", e)

    if firma_img:
        # Bloque con firma capturada + quién firmó / fecha
        firmo_txt = details.get('UsuarioAsignadoNombre') or details.get('CreadoPor') or ''
        fecha_firma = details.get('FechaFirma') or details.get('FechaCreacion')
        if isinstance(fecha_firma, (datetime.date, datetime.datetime)):
            fecha_firma_str = fecha_firma.strftime("%d/%m/%Y %H:%M")
        else:
            fecha_firma_str = str(fecha_firma or '')
        sig_inner = [
            [firma_img],
            [Paragraph(
                f"<b>Firmó:</b> {firmo_txt} &nbsp;&nbsp; <b>Fecha:</b> {fecha_firma_str}",
                style_firma_label,
            )],
        ]
        sig_inner_table = Table(sig_inner, colWidths=[300])
        sig_inner_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        firma_data = [
            [sig_inner_table, Paragraph("Fecha de recepcion: ____ / ____ / ______", style_firma_label)],
        ]
    else:
        # Línea de firma física (recibí conforme) — sin captura aún
        firma_data = [
            [
                Paragraph("_______________________________________", style_firma_label),
                Paragraph("", style_firma_label),
            ],
            [
                Paragraph("Nombre y firma de quien recibe", style_firma_label),
                Paragraph("Fecha de recepcion: ____ / ____ / ______", style_firma_label),
            ],
        ]
    firma_table = Table(firma_data, colWidths=[300, 282])
    firma_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(firma_table)

    doc.build(story, canvasmaker=NumberedCanvas)

    pdf_bytes = buffer.getvalue()
    buffer.close()
    _set_cached_pdf(f"rem_{id_remision}", pdf_bytes)

    # Guardar en share: Remisiones_Materiales/{Año}/{MM Mes}/RM-CM#####-NN.pdf
    fecha_creacion = details.get('FechaCreacion')
    pdf_storage.save_pdf("remisiones", folio_rm, pdf_bytes, fecha_creacion)
    return pdf_bytes


from shared_report_pdf import (
    ServiceNumberedCanvas,
    ImageWithWatermark,
    make_placeholder_photo as _make_placeholder_photo,
)

def generate_service_report_pdf(report):
    folio = report.get('Folio', '') if isinstance(report, dict) else ''
    cached = _get_cached_pdf(f"srv_{folio}")
    if cached:
        return cached
    # Los reportes pueden llegar desde un DataFrame (pandas convierte NULL en float NaN);
    # _txt convierte None/NaN a un texto seguro para reportlab Paragraph.
    def _txt(v, fallback="N/A"):
        if v is None:
            return fallback
        if isinstance(v, float) and v != v:  # NaN
            return fallback
        s = str(v)
        # Escapar para reportlab Paragraph (mini-HTML): evita crash con & < >
        return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=30
    )
    
    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('Norm', fontName=_PDF_FONT, fontSize=10.5, leading=14, textColor=colors.black)
    style_bold = ParagraphStyle('Bld', fontName=_PDF_FONT_BOLD, fontSize=10.5, leading=14, textColor=colors.black)
    style_title = ParagraphStyle('Title', fontName=_PDF_FONT_BOLD, fontSize=16, leading=18, textColor=colors.HexColor('#1E293B'))
    style_subtitle = ParagraphStyle('SubTitle', fontName=_PDF_FONT_BOLD, fontSize=11.5, leading=14, textColor=colors.HexColor('#475569'))
    
    story = []
    
    # 1. Header (Logo + Title) — RLImage (normalizado)
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_norm = _normalize_image_for_pdf(f.read(), max_dim=300, quality=50)
        logo_img = RLImage(logo_norm, width=120, height=45)
    else:
        logo_img = Paragraph("<b>ECCSA</b>", style_title)
    
    header_data = [
        [logo_img, Paragraph(f"REPORTE DE SERVICIO DE CAMPO<br/><font size=9.5 color='#64748B'>FIELD SERVICE REPORT</font>", style_title), Paragraph(f"<b>FOLIO:</b><br/><font color='#EF4444' size=12.5><b>{report['Folio']}</b></font>", style_bold)]
    ]
    header_table = Table(header_data, colWidths=[130, 310, 132])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # 2. Secciones de datos
    _ACCENT = colors.HexColor('#FF6B00')
    _BG_LIGHT = colors.HexColor('#F8FAFC')
    _BG_HEADER = colors.HexColor('#1E293B')
    _BORDER = colors.HexColor('#E2E8F0')
    style_section = ParagraphStyle('Section', fontName=_PDF_FONT_BOLD, fontSize=11, leading=14, textColor=colors.white)
    style_field_label = ParagraphStyle('FieldLabel', fontName=_PDF_FONT_BOLD, fontSize=9, leading=11, textColor=colors.HexColor('#475569'))
    style_field_value = ParagraphStyle('FieldValue', fontName=_PDF_FONT, fontSize=10, leading=13, textColor=colors.black)

    t_ini = report['FechaHoraInicio'].strftime('%d/%m/%Y %H:%M') if isinstance(report['FechaHoraInicio'], datetime.datetime) else str(report['FechaHoraInicio'] or 'N/A')
    t_fin = report['FechaHoraFin'].strftime('%d/%m/%Y %H:%M') if isinstance(report['FechaHoraFin'], datetime.datetime) else str(report['FechaHoraFin'] or 'N/A')
    t_traslado = f"{report['TiempoTraslado']} horas" if report['TiempoTraslado'] is not None else "0.0 horas"
    t_comida = "Sí" if report['TiempoComida'] else "No"

    _tecnicos_adic = []
    _id_rep = report.get('IdReporte')
    if _id_rep:
        try:
            _tecnicos_adic = db.get_report_tecnicos(int(_id_rep))
        except Exception:
            pass

    _cliente_id = _txt(report['Cliente'])
    _cliente_nombre = ''
    if _cliente_id and _cliente_id != 'N/A':
        try:
            _cliente_nombre = db.get_client_name(_cliente_id) or ''
        except Exception:
            pass
    _cliente_display = f"{_cliente_id} — {_cliente_nombre}" if _cliente_nombre else _cliente_id

    def _build_section(title, data_rows, col_widths, spans=None):
        sec_data = [[Paragraph(f"<b>{title}</b>", style_section)] + [Paragraph("", style_section)] * (len(col_widths) - 1)]
        for row in data_rows:
            sec_data.append(row)
        sec_table = Table(sec_data, colWidths=col_widths)
        sec_style = [
            ('SPAN', (0,0), (len(col_widths)-1, 0)),
            ('BACKGROUND', (0,0), (-1,0), _BG_HEADER),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('BACKGROUND', (0,1), (-1,-1), _BG_LIGHT),
            ('BOX', (0,0), (-1,-1), 0.5, _BORDER),
            ('INNERGRID', (0,1), (-1,-1), 0.25, colors.HexColor('#CBD5E1')),
            ('LINEBELOW', (0,0), (-1,0), 1.5, _ACCENT),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,0), 2),
            ('BOTTOMPADDING', (0,0), (-1,0), 2),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,1), (-1,-1), 1),
            ('BOTTOMPADDING', (0,1), (-1,-1), 1),
        ]
        if spans:
            sec_style += spans
        sec_table.setStyle(TableStyle(sec_style))
        return sec_table

    _eq_text = ', '.join(_tecnicos_adic) if _tecnicos_adic else '—'
    _correo = report['CorreoContacto'] or '—'

    # --- DATOS DEL CLIENTE (1 col cliente / 2 cols contacto+correo) ---
    story.append(_build_section('DATOS DEL CLIENTE', [
        [Paragraph("<b>Cliente:</b>", style_field_label), Paragraph("", style_field_label)],
        [Paragraph(_txt(_cliente_display), style_field_value), Paragraph("", style_field_value)],
        [Paragraph("<b>Contacto:</b>", style_field_label), Paragraph("<b>Correo:</b>", style_field_label)],
        [Paragraph(_txt(report['Contacto']), style_field_value), Paragraph(_txt(_correo), style_field_value)],
    ], [286, 286], [
        ('SPAN', (0,1), (1,1)),
        ('SPAN', (0,2), (1,2)),
    ]))
    story.append(Spacer(1, 2))

    # --- DATOS DEL SERVICIO (30% / 70%) ---
    _w_servicio = [172, 400]
    story.append(_build_section('DATOS DEL SERVICIO', [
        [Paragraph("<b>Ingeniero:</b>", style_field_label), Paragraph("<b>Equipo:</b>", style_field_label)],
        [Paragraph(_txt(report['Tecnico']), style_field_value), Paragraph(_txt(_eq_text), style_field_value)],
    ], _w_servicio))
    story.append(Spacer(1, 2))

    # --- TIEMPOS (30% / 30% / 20% / 20%) ---
    _w_tiempos = [172, 172, 114, 114]
    story.append(_build_section('TIEMPOS', [
        [Paragraph("<b>Inicio:</b>", style_field_label), Paragraph("<b>Fin:</b>", style_field_label),
         Paragraph("<b>Traslado:</b>", style_field_label), Paragraph("<b>Comida:</b>", style_field_label)],
        [Paragraph(_txt(t_ini), style_field_value), Paragraph(_txt(t_fin), style_field_value),
         Paragraph(_txt(t_traslado), style_field_value), Paragraph(_txt(t_comida), style_field_value)],
    ], _w_tiempos))
    story.append(Spacer(1, 2))

    # --- MÁQUINA / LÍNEA (100%) ---
    story.append(_build_section('MÁQUINA / LÍNEA', [
        [Paragraph("<b>Línea / Máquina:</b>", style_field_label)],
        [Paragraph(_txt(report.get('MaquinaLinea', ''), '—'), style_field_value)],
    ], [572]))
    story.append(Spacer(1, 4))
    
    # 3. Service Description Block
    story.append(Paragraph("DESCRIPCIÓN DEL SERVICIO REALIZADO / SERVICE DESCRIPTION", style_subtitle))
    story.append(Spacer(1, 6))
    
    desc_p = Paragraph(_txt(report['DescripcionServicio'], "Sin descripción del servicio.").replace(chr(10), '<br/>'), style_normal)
    desc_table = Table([[desc_p]], colWidths=[572])
    desc_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('MINHEIGHT', (0,0), (-1,-1), 150),
    ]))
    story.append(desc_table)
    story.append(Spacer(1, 20))
    
    # 5. Signatures Block (Technician + Client Signature image) — RLImage
    import base64
    client_sig_img = None
    if report.get('FirmaConformidad') and isinstance(report['FirmaConformidad'], str):
        try:
            sig_data = report['FirmaConformidad'].split(',')[1]
            sig_bytes = base64.b64decode(sig_data)
            sig_norm = _normalize_image_for_pdf(sig_bytes, max_dim=400, quality=50)
            client_sig_img = RLImage(sig_norm, width=150, height=60)
        except Exception as e:
            print("Error rendering signature on PDF:", e)
            
    sig_story = []
    if client_sig_img:
        sig_img_table = Table([[client_sig_img]], colWidths=[572])
        sig_img_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('TOPPADDING', (0,0), (-1,-1), 0),
        ]))
        sig_story.append(sig_img_table)
    else:
        sig_story.append(Spacer(1, 45))
        
    sig_story.append(Paragraph("________________________________________<br/><b>Firma de Aceptación del Cliente</b>", ParagraphStyle('Sig', fontName=_PDF_FONT, fontSize=10, alignment=1)))
    story.append(KeepTogether(sig_story))
    
    # 6. Photos Page (if any)
    id_reporte = report.get('IdReporte')
    if id_reporte:
        try:
            fotos = db.get_report_fotos(int(id_reporte))
        except Exception:
            fotos = []
        if fotos:
            try:
                from reportlab.platypus import PageBreak
                story.append(PageBreak())
                story.append(Paragraph("EVIDENCIA FOTOGRÁFICA / PHOTO EVIDENCE", style_subtitle))
                story.append(Spacer(1, 10))
                # Imagen compuesta (1 XObject) — RLImage
                composite_buf = _build_photo_grid_composite(fotos)
                if composite_buf:
                    img = RLImage(composite_buf, width=540, height=650)
                    story.append(img)
                    story.append(Spacer(1, 10))
            except Exception as e:
                # Un fallo en la página de fotos no debe abortar todo el PDF
                print(f"generate_service_report_pdf: fotos omitidas para {folio}: {e}")
    
    # Build PDF - collect technician photos for footer (normalizadas para iOS)
    _tech_photos = []
    _all_techs = [report.get('Tecnico', '')] + _tecnicos_adic
    for _tech_name in _all_techs:
        if not _tech_name or not _tech_name.strip():
            continue
        try:
            _foto = db.get_hub_user_foto(_tech_name.strip())
            if _foto and isinstance(_foto, str) and 'base64' in _foto:
                import base64 as _b64
                _data = _foto.split(',')[1] if ',' in _foto else _foto
                _bytes = _b64.b64decode(_data)
                _tech_photos.append(_normalize_image_for_pdf(_bytes, max_dim=100, quality=50))
            else:
                _tech_photos.append(_make_placeholder_photo())
        except Exception:
            _tech_photos.append(_make_placeholder_photo())
    from functools import partial
    doc.build(story, canvasmaker=partial(ServiceNumberedCanvas, tech_photos=_tech_photos))
    pdf_bytes = buffer.getvalue()
    buffer.close()
    _set_cached_pdf(f"srv_{folio}", pdf_bytes)
    fecha_rep = report.get('FechaHoraInicio') if isinstance(report, dict) else None
    if fecha_rep and isinstance(fecha_rep, datetime.datetime):
        fecha_rep = fecha_rep.date()
    try:
        pdf_storage.save_pdf("reportes", folio, pdf_bytes, fecha_rep)
    except Exception as e:
        # Un fallo de SMB no debe impedir devolver el PDF (p. ej. alerta Telegram)
        print(f"generate_service_report_pdf: save_pdf omitido para {folio}: {e}")
    return pdf_bytes

def generate_cotizacion_servproy_pdf(folio):
    """Genera el PDF de una Cotización de Servicios y Proyectos (encabezado + partidas).
    Recibe el Folio (ej. 'CSP-2026-0001'). Devuelve bytes o None."""
    cached = _get_cached_pdf(f"sproy_{folio}")
    if cached:
        return cached
    details = db.get_cotizacion_servproy_by_folio(folio)
    partidas = db.get_partidas_cotizacion_servproy(folio)
    if not details:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    style_titulo = ParagraphStyle('Titulo', fontName='Helvetica-Bold', fontSize=13,
                                  leading=16, textColor=colors.HexColor('#0F172A'), alignment=2)
    style_sub = ParagraphStyle('Sub', fontName='Helvetica', fontSize=8, leading=10,
                               textColor=colors.HexColor('#475569'), alignment=2)
    style_meta_value = ParagraphStyle('MVal', fontName='Helvetica', fontSize=9, leading=11,
                                      alignment=2, textColor=colors.black)
    style_campo = ParagraphStyle('Campo', fontName='Helvetica-Bold', fontSize=8.5, leading=10,
                                 textColor=colors.HexColor('#1E293B'))
    style_valor = ParagraphStyle('Valor', fontName='Helvetica', fontSize=8.5, leading=10,
                                 textColor=colors.HexColor('#334155'))
    style_item_desc = ParagraphStyle('ItemDesc', fontName='Helvetica', fontSize=8, leading=9.5,
                                     textColor=colors.black)
    style_item_header = ParagraphStyle('ItemHdr', fontName='Helvetica-Bold', fontSize=8.5,
                                       leading=10, textColor=colors.white)

    story = []

    # HEADER
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    logo_container = []
    if os.path.exists(logo_path):
        try:
            with open(logo_path, 'rb') as f:
                logo_norm = _normalize_image_for_pdf(f.read(), max_dim=300, quality=50)
            logo_container.append(RLImage(logo_norm, width=175, height=52))
        except Exception:
            pass
        logo_container.append(Spacer(1, 3))
    style_logo_subtext = ParagraphStyle('LogoSub', fontName='Helvetica-Bold', fontSize=6.5,
                                        leading=8, textColor=colors.HexColor("#334155"))
    logo_container.append(Paragraph("Oscar Noe Castillo Zavala - CAZO670914BK8", style_logo_subtext))

    fecha_val = details.get('FechaCreacion')
    fecha_str = fecha_val.strftime("%d/%m/%Y") if isinstance(fecha_val, (datetime.date, datetime.datetime)) else str(fecha_val or '')

    titulo_cell = [
        Paragraph("<b>COTIZACIÓN DE SERVICIOS Y PROYECTOS</b>", style_titulo),
        Paragraph(f"Folio: {folio}", style_meta_value),
        Paragraph(f"Fecha: {fecha_str}", style_meta_value),
    ]
    header_data = [[logo_container, titulo_cell]]
    header_table = Table(header_data, colWidths=[202, 350])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)

    divider = Table([[""]], colWidths=[552])
    divider.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#FF6B00')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 10))

    # INFORMACIÓN DEL CLIENTE Y METADATA
    cliente_id = details.get('IdCliente') or ''
    cliente_nombre = db.get_client_name(cliente_id) if cliente_id else ''
    info_rows = [
        [Paragraph('Cliente:', style_campo), Paragraph(f"{cliente_nombre or cliente_id or 'N/A'} ({cliente_id})", style_valor)],
        [Paragraph('Contacto:', style_campo), Paragraph(str(details.get('Contacto') or 'N/A'), style_valor)],
    ]
    info_table = Table(info_rows, colWidths=[120, 432])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6))

    # TABLA DE PARTIDAS
    items_data = [
        [
            Paragraph('Part.', style_item_header),
            Paragraph('Tipo', style_item_header),
            Paragraph('Cant.', style_item_header),
            Paragraph('Descripción', style_item_header),
            Paragraph('Modelo', style_item_header),
            Paragraph('P. Unitario', style_item_header),
            Paragraph('Total', style_item_header),
        ]
    ]

    subtotal = 0.0
    for item in partidas:
        cant = float(item['Cantidad'] or 0)
        pu = float(item['PrecioVentaUnit'] or 0)
        total_item = float(item['PrecioVentaTotal'] or 0)
        subtotal += total_item
        desc_clean = str(item['Descripcion'] or '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        entrega = str(item.get('TiempoEntrega') or '').strip()
        entrega_txt = f"<br/><font color='#64748B'>Entrega: {entrega}</font>" if entrega else ""
        proveedor_txt = f"<br/><font color='#64748B'>Prov: {item.get('Proveedor')}</font>" if item.get('Proveedor') else ""
        items_data.append([
            Paragraph(str(item['Partida']).zfill(3), style_item_desc),
            Paragraph(str(item['Tipo'] or ''), style_item_desc),
            Paragraph(f"{cant:g}", style_item_desc),
            Paragraph(desc_clean + entrega_txt + proveedor_txt, style_item_desc),
            Paragraph(str(item['Modelo'] or ''), style_item_desc),
            Paragraph(f"${pu:,.2f}", style_item_desc),
            Paragraph(f"${total_item:,.2f}", style_item_desc),
        ])

    items_table = Table(items_data, colWidths=[30, 55, 35, 215, 85, 70, 70])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 10))

    subtotal_final = float(details.get('Subtotal') or subtotal)
    iva = float(details.get('IVA') or round(subtotal_final * 0.16, 2))
    total = float(details.get('Total') or round(subtotal_final + iva, 2))
    total_letras = numero_a_letras(total)
    totales = Table([
        [Paragraph('Subtotal:', style_campo), Paragraph(f"${subtotal_final:,.2f}", style_meta_value)],
        [Paragraph('I.V.A. (16%):', style_campo), Paragraph(f"${iva:,.2f}", style_meta_value)],
        [Paragraph('Total:', style_campo), Paragraph(f"${total:,.2f}", style_meta_value)],
        [Paragraph('Cantidad con letra:', style_campo), Paragraph(total_letras, style_valor)],
    ], colWidths=[180, 372])
    totales.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,-2), 'RIGHT'),
        ('LINEBELOW', (0,-2), (-1,-2), 1.2, colors.HexColor('#FF6B00')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(totales)
    story.append(Spacer(1, 12))

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    _set_cached_pdf(f"oc_{folio}", pdf_bytes)
    fecha_csp = details.get('FechaCreacion')
    if fecha_csp and isinstance(fecha_csp, datetime.datetime):
        fecha_csp = fecha_csp.date()
    pdf_storage.save_pdf("servproy", folio, pdf_bytes, fecha_csp)
    return pdf_bytes

def generate_comprobante_vacaciones_pdf(nombre, dias_ley, dias_a_tomar, dias_restantes, fechas_list, periodo_str, texto, firma_base64=None, fecha_generacion=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=35
    )
    
    styles = getSampleStyleSheet()
    
    style_title = ParagraphStyle('VacTitle', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=colors.HexColor('#EB6C24'), alignment=1)
    style_body = ParagraphStyle('VacBody', fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.black)
    style_bold = ParagraphStyle('VacBold', fontName='Helvetica-Bold', fontSize=9.5, leading=13, textColor=colors.black)
    style_th = ParagraphStyle('VacTH', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.HexColor('#1E293B'))
    style_td = ParagraphStyle('VacTD', fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.HexColor('#334155'))
    style_sig = ParagraphStyle('VacSig', fontName='Helvetica', fontSize=8.5, leading=11, alignment=1)
    
    story = []
    
    # 1. HEADER (Logo + Owner + Title)
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    left_cell = []
    if os.path.exists(logo_path):
        try:
            left_cell.append(RLImage(logo_path, width=175, height=52))
            left_cell.append(Spacer(1, 2))
        except Exception:
            pass
    left_cell.append(Paragraph("<b>Oscar Noe Castillo Zavala - CAZO670914BK8</b>", ParagraphStyle('OwnerVac', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor('#1E293B'))))
    
    title_cell = Paragraph("COMPROBANTE DE VACACIONES<br/><font size=9 color='#64748B'>ECCSA Automation</font>", style_title)
    
    header_table = Table([[left_cell, title_cell]], colWidths=[220, 332])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(header_table)
    
    divider = Table([[""]], colWidths=[552])
    divider.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#EB6C24')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 12))
    
    # 2. Employee + generation info
    gen_ts = fecha_generacion if isinstance(fecha_generacion, (datetime.date, datetime.datetime)) else db.now_mexico()
    gen_str = gen_ts.strftime("%d/%m/%Y %H:%M:%S")
    
    info_data = [
        [Paragraph("<b>Colaborador:</b>", style_bold), Paragraph(nombre, style_body)],
        [Paragraph("<b>Periodo de vacaciones:</b>", style_bold), Paragraph(periodo_str, style_body)],
        [Paragraph("<b>Fecha y hora de generación:</b>", style_bold), Paragraph(gen_str, style_body)],
    ]
    info_table = Table(info_data, colWidths=[170, 382])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12))
    
    # 3. Suggested legal text
    texto_p = Paragraph(str(texto).replace("\n", "<br/>") if texto else "*Sin texto.*", style_body)
    texto_table = Table([[texto_p]], colWidths=[552])
    texto_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFF7ED')),
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#EB6C24')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(texto_table)
    story.append(Spacer(1, 14))
    
    # 4. Days table
    dias_data = [
        [Paragraph("Fecha", style_th), Paragraph("Periodo Aplicado", style_th), Paragraph("Concepto / Detalle", style_th)]
    ]
    for d in fechas_list:
        dias_data.append([
            Paragraph(str(d.get('fecha') or ''), style_td),
            Paragraph(str(d.get('tipo') or ''), style_td),
            Paragraph(str(d.get('notas') or '-'), style_td),
        ])
    dias_table = Table(dias_data, colWidths=[100, 170, 282])
    dias_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(dias_table)
    story.append(Spacer(1, 14))
    
    # 5. Summary
    resumen_data = [
        [Paragraph("<b>Días de vacaciones por año (LFT):</b>", style_bold), Paragraph(str(dias_ley), style_body)],
        [Paragraph("<b>Días a tomar en este comprobante:</b>", style_bold), Paragraph(str(dias_a_tomar), style_body)],
        [Paragraph("<b>Días restantes disponibles:</b>", style_bold), Paragraph(str(dias_restantes), style_body)],
    ]
    resumen_table = Table(resumen_data, colWidths=[330, 222])
    resumen_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFF7ED')),
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#EB6C24')),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.HexColor('#FDBA74')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(resumen_table)
    story.append(Spacer(1, 30))
    
    # 6. Signature block
    sig_story = []
    if firma_base64 and isinstance(firma_base64, str):
        try:
            sig_data = firma_base64.split(',')[1] if ',' in firma_base64 else firma_base64
            sig_bytes = base64.b64decode(sig_data)
            sig_buf = io.BytesIO(sig_bytes)
            sig_img = RLImage(sig_buf, width=170, height=65)
            sig_img_table = Table([[sig_img]], colWidths=[552])
            sig_img_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                ('TOPPADDING', (0,0), (-1,-1), 0),
            ]))
            sig_story.append(sig_img_table)
        except Exception as e:
            print(f"Error rendering vacation signature on PDF: {e}")
    
    sig_story.append(Paragraph("________________________________________<br/><b>Firma del Colaborador</b><br/><font size=8 color='#475569'>Nombre: {}</font>".format(nombre), style_sig))
    story.append(KeepTogether(sig_story))
    story.append(Spacer(1, 30))
    
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_recibo_horas_extras_pdf(nombre, registros, total_horas, texto, firma_base64=None, fecha_generacion=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=35
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle('HETitle', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=colors.HexColor('#EB6C24'), alignment=1)
    style_body = ParagraphStyle('HEBody', fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.black)
    style_bold = ParagraphStyle('HEBold', fontName='Helvetica-Bold', fontSize=9.5, leading=13, textColor=colors.black)
    style_th = ParagraphStyle('HETH', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1E293B'))
    style_td = ParagraphStyle('HETD', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'))
    style_sig = ParagraphStyle('HESig', fontName='Helvetica', fontSize=8.5, leading=11, alignment=1)

    story = []

    # 1. HEADER (Logo + Owner + Title)
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    left_cell = []
    if os.path.exists(logo_path):
        try:
            left_cell.append(RLImage(logo_path, width=175, height=52))
            left_cell.append(Spacer(1, 2))
        except Exception:
            pass
    left_cell.append(Paragraph("<b>Oscar Noe Castillo Zavala - CAZO670914BK8</b>", ParagraphStyle('OwnerHE', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor('#1E293B'))))

    title_cell = Paragraph("RECIBO DE HORAS EXTRAS<br/><font size=9 color='#64748B'>ECCSA Automation</font>", style_title)

    header_table = Table([[left_cell, title_cell]], colWidths=[220, 332])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(header_table)

    divider = Table([[""]], colWidths=[552])
    divider.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#EB6C24')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 12))

    # 2. Employee + generation info
    gen_ts = fecha_generacion if isinstance(fecha_generacion, (datetime.date, datetime.datetime)) else db.now_mexico()
    gen_str = gen_ts.strftime("%d/%m/%Y %H:%M:%S")

    info_data = [
        [Paragraph("<b>Colaborador:</b>", style_bold), Paragraph(nombre, style_body)],
        [Paragraph("<b>Total de horas extras:</b>", style_bold), Paragraph(f"{total_horas:.2f} horas", style_body)],
        [Paragraph("<b>Fecha y hora de generación:</b>", style_bold), Paragraph(gen_str, style_body)],
    ]
    info_table = Table(info_data, colWidths=[170, 382])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12))

    # 3. Suggested descriptive text
    texto_p = Paragraph(str(texto).replace("\n", "<br/>") if texto else "*Sin texto.*", style_body)
    texto_table = Table([[texto_p]], colWidths=[552])
    texto_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFF7ED')),
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#EB6C24')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(texto_table)
    story.append(Spacer(1, 14))

    # 4. Records table
    def _fmt_fecha_he(f):
        try:
            if isinstance(f, datetime.datetime):
                return f.strftime("%d/%m/%Y")
            return datetime.datetime.strptime(str(f)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            return str(f)

    def _fmt_hora_he(t):
        if t is None:
            return "-"
        try:
            if isinstance(t, str):
                return t[:5]
            return t.strftime("%H:%M")
        except Exception:
            return str(t)

    def _get_detalle_he(r, key, default=0.0):
        val = r.get(key)
        try:
            return float(val or default)
        except Exception:
            return default

    # Desglose individual por registro (doble/triple/cuádruple/traslado/equivalente)
    reg = []
    tot_d = tot_t = tot_c = tot_tr = tot_eq = 0.0
    for r in registros:
        dbl = _get_detalle_he(r, "Doble")
        trp = _get_detalle_he(r, "Triple")
        cuad = _get_detalle_he(r, "Cuadruple")
        tras = _get_detalle_he(r, "TrasladoExtra")
        # Equivalente: si viene calculado se usa, si no se reconstruye con multiplicadores
        eq = _get_detalle_he(r, "Equivalente")
        if eq == 0.0 and not r.get("Equivalente"):
            eq = dbl * 2 + trp * 3 + cuad * 4 + tras
        tot_d += dbl
        tot_t += trp
        tot_c += cuad
        tot_tr += tras
        tot_eq += eq
        reg.append([
            Paragraph(_fmt_fecha_he(r.get('Fecha')), style_td),
            Paragraph(_fmt_hora_he(r.get('HoraEntrada')), style_td),
            Paragraph(_fmt_hora_he(r.get('HoraSalida')), style_td),
            Paragraph(f"{dbl:.2f}", style_td),
            Paragraph(f"{trp:.2f}", style_td),
            Paragraph(f"{cuad:.2f}", style_td),
            Paragraph(f"{tras:.2f}", style_td),
            Paragraph(f"{eq:.2f}", style_td),
            Paragraph(str(r.get('Cliente') or '-'), style_td),
        ])
    # Fila de totales
    reg.append([
        Paragraph("<b>TOTAL</b>", ParagraphStyle('HETot', parent=style_td, fontName='Helvetica-Bold')),
        Paragraph("", style_td), Paragraph("", style_td),
        Paragraph(f"<b>{tot_d:.2f}</b>", style_td),
        Paragraph(f"<b>{tot_t:.2f}</b>", style_td),
        Paragraph(f"<b>{tot_c:.2f}</b>", style_td),
        Paragraph(f"<b>{tot_tr:.2f}</b>", style_td),
        Paragraph(f"<b>{tot_eq:.2f}</b>", style_td),
        Paragraph("", style_td),
    ])

    reg_data = [
        [Paragraph("Fecha", style_th), Paragraph("Entrada", style_th), Paragraph("Salida", style_th),
         Paragraph("Doble (h)", style_th), Paragraph("Triple (h)", style_th), Paragraph("Cuádruple (h)", style_th),
         Paragraph("Traslado (h)", style_th), Paragraph("Total (h)", style_th),
         Paragraph("Cliente", style_th)]
    ] + reg
    reg_table = Table(reg_data, colWidths=[60, 40, 40, 42, 42, 46, 44, 48, 190])
    reg_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(reg_table)
    story.append(Spacer(1, 14))

    # 5. Summary (detalle de totales por tipo)
    resumen_data = [
        [Paragraph("<b>Número de registros:</b>", style_bold), Paragraph(str(len(registros)), style_body)],
        [Paragraph("<b>Horas dobles (2×):</b>", style_bold), Paragraph(f"{tot_d:.2f} horas", style_body)],
        [Paragraph("<b>Horas triples (3×):</b>", style_bold), Paragraph(f"{tot_t:.2f} horas", style_body)],
        [Paragraph("<b>Horas cuádruples (4×):</b>", style_bold), Paragraph(f"{tot_c:.2f} horas", style_body)],
        [Paragraph("<b>Traslado:</b>", style_bold), Paragraph(f"{tot_tr:.2f} horas", style_body)],
        [Paragraph("<b>Total horas extras equivalentes:</b>", style_bold), Paragraph(f"{tot_eq:.2f} horas", style_body)],
    ]
    resumen_table = Table(resumen_data, colWidths=[330, 222])
    resumen_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFF7ED')),
        ('BOX', (0,0), (-1,-1), 0.75, colors.HexColor('#EB6C24')),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.HexColor('#FDBA74')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(resumen_table)
    story.append(Spacer(1, 30))

    # 6. Signature block
    sig_story = []
    if firma_base64 and isinstance(firma_base64, str):
        try:
            sig_data = firma_base64.split(',')[1] if ',' in firma_base64 else firma_base64
            sig_bytes = base64.b64decode(sig_data)
            sig_buf = io.BytesIO(sig_bytes)
            sig_img = RLImage(sig_buf, width=170, height=65)
            sig_img_table = Table([[sig_img]], colWidths=[552])
            sig_img_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                ('TOPPADDING', (0,0), (-1,-1), 0),
            ]))
            sig_story.append(sig_img_table)
        except Exception as e:
            print(f"Error rendering overtime signature on PDF: {e}")

    sig_story.append(Paragraph("________________________________________<br/><b>Firma del Colaborador</b><br/><font size=8 color='#475569'>Nombre: {}</font>".format(nombre), style_sig))
    story.append(KeepTogether(sig_story))
    story.append(Spacer(1, 30))

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_orden_compra_pdf(folio):
    """Genera el PDF de una Orden de Compra (encabezado + partidas).
    Recibe el FolioOC (ej. 'OC-2026-0001'). Devuelve bytes o None."""
    cached = _get_cached_pdf(f"oc_{folio}")
    if cached:
        return cached
    details = db.get_oc_by_folio(folio)
    partidas = db.get_oc_partidas(folio)
    if not details:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=15,
        rightMargin=15,
        topMargin=15,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('Norm', fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.black)
    style_meta_label = ParagraphStyle('MLbl', fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    style_meta_value = ParagraphStyle('MVal', fontName='Helvetica', fontSize=9, leading=11, alignment=2, textColor=colors.black)
    style_item_desc = ParagraphStyle('ItemDesc', fontName='Helvetica', fontSize=8, leading=9.5, textColor=colors.black)
    style_item_header = ParagraphStyle('ItemHdr', fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)

    story = []

    # HEADER
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    logo_container = []
    if os.path.exists(logo_path):
        logo_container.append(RLImage(logo_path, width=175, height=52))
        logo_container.append(Spacer(1, 4))
    style_logo_subtext = ParagraphStyle('LogoSub', fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#334155"))
    logo_container.append(Paragraph("Oscar Noe Castillo Zavala - CAZO670914BK8", style_logo_subtext))

    fecha_val = details['Fecha']
    if isinstance(fecha_val, (datetime.date, datetime.datetime)):
        fecha_str = fecha_val.strftime("%d/%m/%Y")
    else:
        fecha_str = str(fecha_val)

    meta_data = [
        [Paragraph(f"Orden de Compra: {folio}", style_meta_label)],
        [Paragraph(f"Fecha: {fecha_str}", style_meta_value)],
        [Paragraph(f"Proveedor: {details.get('ProveedorNombre') or ''}", style_meta_value)],
        [Paragraph(f"Moneda: {details.get('Moneda') or 'MXN'}", style_meta_value)],
        [Paragraph(f"Elaboró: {details.get('Autor') or ''}", style_meta_value)],
        [Paragraph(f"Condiciones: {details.get('Condicion') or ''}", style_meta_value)]
    ]
    meta_table = Table(meta_data, colWidths=[380])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    header_data = [[logo_container, meta_table]]
    header_table = Table(header_data, colWidths=[202, 380])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # ITEMS TABLE
    items_data = [
        [
            Paragraph("Part.", style_item_header),
            Paragraph("Cant.", style_item_header),
            Paragraph("Descripcion", style_item_header),
            Paragraph("Modelo", style_item_header),
            Paragraph("P. Unitario", style_item_header),
            Paragraph("Total", style_item_header)
        ]
    ]

    subtotal = 0.0
    for item in partidas:
        cant = float(item['Cantidad'] or 0)
        pu = float(item['PrecioUnitario'] or 0)
        total_item = float(item['PrecioTotal'] or 0)
        subtotal += total_item
        desc_clean = str(item['Descripcion'] or '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        entrega = int(item['TiempoEntregaDias'] or 0)
        entrega_txt = f"<br/><font color='#64748B'>Tiempo entrega: {entrega} día(s)</font>" if entrega else ""
        items_data.append([
            Paragraph(str(item['Partida']).zfill(3), style_item_desc),
            Paragraph(f"{cant:g}", style_item_desc),
            Paragraph(desc_clean + entrega_txt, style_item_desc),
            Paragraph(str(item['Modelo'] or ''), style_item_desc),
            Paragraph(f"${pu:,.2f}", style_item_desc),
            Paragraph(f"${total_item:,.2f}", style_item_desc)
        ])

    items_table = Table(items_data, colWidths=[35, 40, 200, 105, 100, 100])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 10))

    iva = float(details.get('IVA') or 0)
    total_mxn = float(details.get('TotalMXN') or 0)
    totales = Table([
        [Paragraph('Subtotal:', style_meta_value), Paragraph(f"${subtotal:,.2f}", style_meta_value)],
        [Paragraph('I.V.A. (16%):', style_meta_value), Paragraph(f"${iva:,.2f}", style_meta_value)],
        [Paragraph('Total:', style_meta_value), Paragraph(f"${total_mxn:,.2f}", style_meta_value)],
    ], colWidths=[482, 100])
    totales.setStyle(TableStyle([
        ('ALIGN', (0,0), (0,-1), 'RIGHT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('LINEBELOW', (0,-1), (-1,-1), 1.2, colors.black),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(totales)
    story.append(Spacer(1, 12))

    notas = details.get('Notas') or ''
    if notas.strip():
        style_notas = ParagraphStyle('Notas', fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.HexColor('#475569'))
        story.append(Paragraph(f"<b>Notas:</b> {notas}", style_notas))

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    fecha_oc = details.get('Fecha')
    if fecha_oc and isinstance(fecha_oc, datetime.datetime):
        fecha_oc = fecha_oc.date()
    pdf_storage.save_pdf("oc", folio, pdf_bytes, fecha_oc)
    return pdf_bytes
