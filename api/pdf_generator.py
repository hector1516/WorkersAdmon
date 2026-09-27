import io
import os
import datetime
import base64
from functools import partial
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether, PageBreak, Image as RLImage
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from db import get_connection

# Piezas compartidas con el HUB (ver shared_report_pdf.py: copia exacta,
# cualquier cambio va en ambos lados + subir SHARED_REPORT_PDF_VERSION)
from shared_report_pdf import (
    ServiceNumberedCanvas,
    ImageWithWatermark,
    make_placeholder_photo as _make_placeholder_photo,
)

# --- Register DejaVu Sans fonts (same as HUB) ---
_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts")
_PDF_FONT = 'Helvetica'
_PDF_FONT_BOLD = 'Helvetica-Bold'
try:
    pdfmetrics.registerFont(TTFont('DejaVuSans', os.path.join(_FONTS_DIR, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', os.path.join(_FONTS_DIR, 'DejaVuSans-Bold.ttf')))
    _PDF_FONT = 'DejaVuSans'
    _PDF_FONT_BOLD = 'DejaVuSans-Bold'
except Exception:
    pass

_ACCENT = colors.HexColor('#FF6B00')
_BG_LIGHT = colors.HexColor('#F8FAFC')
_BG_HEADER = colors.HexColor('#1E293B')
_BORDER = colors.HexColor('#E2E8F0')

def _txt(v, fallback="N/A"):
    if v is None: return fallback
    if isinstance(v, float) and v != v: return fallback
    return str(v)


def _normalize_image_for_pdf(raw_bytes, max_dim=250, quality=50):
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
    
    # Convertir a RGB sobre fondo blanco (para PNGs con transparencia como firmas)
    if pil_img.mode in ('RGBA', 'LA', 'PA'):
        background = PILImage.new('RGB', pil_img.size, (255, 255, 255))
        if pil_img.mode == 'LA':
            pil_img = pil_img.convert('RGBA')
        if pil_img.mode == 'PA':
            pil_img = pil_img.convert('RGBA')
        background.paste(pil_img, mask=pil_img.split()[-1])
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
    
    # Fuente para captions (usar default si no hay DejaVu)
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


def _get_tech_photo(nombre):
    """Foto de perfil del técnico normalizada para PDF (o placeholder)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT Foto FROM HUB_Users WHERE Email = %s", (nombre.strip().lower(),))
            row = cur.fetchone()
        foto = row[0] if row else None
        if foto and isinstance(foto, str) and 'base64' in foto:
            data = foto.split(',')[1] if ',' in foto else foto
            raw = base64.b64decode(data)
            return _normalize_image_for_pdf(raw, max_dim=100, quality=50)
    except Exception:
        pass
    return _make_placeholder_photo()


def _generate_synthetic_test_image():
    """Genera imagen sintética simple (cuadrado rojo) para test de aislamiento."""
    from PIL import Image
    img = Image.new('RGB', (100, 100), color='red')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', progressive=False)
    buf.seek(0)
    return buf


def generate_service_report_pdf(report):
    """Generate PDF matching HUB format exactly."""
    folio = report.get('Folio', '')
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=30)

    style_normal = ParagraphStyle('Norm', fontName=_PDF_FONT, fontSize=10.5, leading=14, textColor=colors.black)
    style_bold = ParagraphStyle('Bld', fontName=_PDF_FONT_BOLD, fontSize=10.5, leading=14, textColor=colors.black)
    style_title = ParagraphStyle('Title', fontName=_PDF_FONT_BOLD, fontSize=16, leading=18, textColor=colors.HexColor('#1E293B'))
    style_section = ParagraphStyle('Section', fontName=_PDF_FONT_BOLD, fontSize=11, leading=14, textColor=colors.white)
    style_field_label = ParagraphStyle('FieldLabel', fontName=_PDF_FONT_BOLD, fontSize=9, leading=11, textColor=colors.HexColor('#475569'))
    style_field_value = ParagraphStyle('FieldValue', fontName=_PDF_FONT, fontSize=10, leading=13, textColor=colors.black)
    style_subtitle = ParagraphStyle('SubTitle', fontName=_PDF_FONT_BOLD, fontSize=11.5, leading=14, textColor=colors.HexColor('#475569'))
    style_sig = ParagraphStyle('Sig', fontName=_PDF_FONT, fontSize=10, alignment=TA_CENTER)

    story = []

    # 1. Header (Logo + Title + Folio) — RLImage (normalizado)
    logo_path = "/eccsa_logo.png" if os.path.exists("/eccsa_logo.png") else "eccsa_logo.png"
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_norm = _normalize_image_for_pdf(f.read(), max_dim=300, quality=50)
        logo_img = RLImage(logo_norm, width=120, height=45)
    else:
        logo_img = Paragraph("<b>ECCSA</b>", style_title)
    header_data = [[
        logo_img,
        Paragraph(f"REPORTE DE SERVICIO DE CAMPO<br/><font size=9.5 color='#64748B'>FIELD SERVICE REPORT</font>", style_title),
        Paragraph(f"<b>FOLIO:</b><br/><font color='#EF4444' size=12.5><b>{folio}</b></font>", style_bold)
    ]]
    header_table = Table(header_data, colWidths=[130, 310, 132])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('ALIGN', (2,0), (2,0), 'RIGHT')]))
    story.append(header_table)
    story.append(Spacer(1, 15))

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
            ('TOPPADDING', (0,0), (-1,0), 2), ('BOTTOMPADDING', (0,0), (-1,0), 2),
            ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,1), (-1,-1), 1), ('BOTTOMPADDING', (0,1), (-1,-1), 1),
        ]
        if spans:
            for s in spans:
                sec_style.append(('SPAN', s[0], s[1]))
        sec_table.setStyle(TableStyle(sec_style))
        return sec_table

    # Resolve cliente name
    cliente_id = _txt(report.get('Cliente'))
    cliente_nombre = report.get('ClienteNombre', '')
    cliente_display = f"{cliente_id} — {cliente_nombre}" if cliente_nombre else cliente_id

    # Resolve additional technicians
    tecnicos_adic = report.get('TecnicosAdicionales', [])
    if isinstance(tecnicos_adic, str):
        try:
            import json
            tecnicos_adic = json.loads(tecnicos_adic)
        except:
            tecnicos_adic = [t.strip() for t in tecnicos_adic.split(',') if t.strip()] if tecnicos_adic else []
    eq_text = ', '.join(tecnicos_adic) if tecnicos_adic else '—'

    # Format times — same as HUB
    t_ini = report['FechaHoraInicio'].strftime('%d/%m/%Y %H:%M') if isinstance(report.get('FechaHoraInicio'), datetime.datetime) else str(report.get('FechaHoraInicio') or 'N/A')
    t_fin = report['FechaHoraFin'].strftime('%d/%m/%Y %H:%M') if isinstance(report.get('FechaHoraFin'), datetime.datetime) else str(report.get('FechaHoraFin') or 'N/A')
    t_traslado = f"{report.get('TiempoTraslado', 0)} horas"
    t_comida = "Sí" if report.get('TiempoComida') else "No"

    # 2. DATOS DEL CLIENTE — same as HUB
    story.append(_build_section("DATOS DEL CLIENTE", [
        [Paragraph("<b>Cliente:</b>", style_field_label), Paragraph("", style_field_label)],
        [Paragraph(_txt(cliente_display), style_field_value), Paragraph("", style_field_value)],
        [Paragraph("<b>Contacto:</b>", style_field_label), Paragraph("<b>Correo:</b>", style_field_label)],
        [Paragraph(_txt(report.get('Contacto')), style_field_value), Paragraph(_txt(report.get('CorreoContacto')), style_field_value)],
    ], [286, 286], spans=[((0,1), (1,1)), ((0,2), (1,2))]))
    story.append(Spacer(1, 2))

    # 3. DATOS DEL SERVICIO — same as HUB
    story.append(_build_section("DATOS DEL SERVICIO", [
        [Paragraph("<b>Ingeniero:</b>", style_field_label), Paragraph("<b>Equipo:</b>", style_field_label)],
        [Paragraph(_txt(report.get('Tecnico')), style_field_value), Paragraph(eq_text, style_field_value)],
    ], [172, 400]))
    story.append(Spacer(1, 2))

    # 4. TIEMPOS — same as HUB
    story.append(_build_section("TIEMPOS", [
        [Paragraph("<b>Inicio:</b>", style_field_label), Paragraph("<b>Fin:</b>", style_field_label),
         Paragraph("<b>Traslado:</b>", style_field_label), Paragraph("<b>Comida:</b>", style_field_label)],
        [Paragraph(t_ini, style_field_value), Paragraph(t_fin, style_field_value),
         Paragraph(t_traslado, style_field_value), Paragraph(t_comida, style_field_value)],
    ], [172, 172, 114, 114]))
    story.append(Spacer(1, 2))

    # 5. MÁQUINA / LÍNEA — same as HUB
    story.append(_build_section("MÁQUINA / LÍNEA", [
        [Paragraph("<b>Línea / Máquina:</b>", style_field_label)],
        [Paragraph(_txt(report.get('MaquinaLinea'), '—'), style_field_value)],
    ], [572]))
    story.append(Spacer(1, 4))

    # 6. DESCRIPCIÓN DEL SERVICIO — same as HUB
    desc = _txt(report.get('DescripcionServicio'), 'Sin descripción del servicio.')
    desc_p = Paragraph(desc.replace('\n', '<br/>'), style_normal)
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
    story.append(Paragraph("DESCRIPCIÓN DEL SERVICIO REALIZADO / SERVICE DESCRIPTION", style_subtitle))
    story.append(Spacer(1, 6))
    story.append(desc_table)
    story.append(Spacer(1, 20))

    # 7. Firma — RLImage (normalizada)
    firma = report.get('FirmaConformidad')
    sig_story = []
    if firma and isinstance(firma, str) and firma.strip():
        try:
            sig_data = firma.split(',')[1] if ',' in firma else firma
            sig_bytes = base64.b64decode(sig_data)
            sig_norm = _normalize_image_for_pdf(sig_bytes, max_dim=400, quality=50)
            sig_img = RLImage(sig_norm, width=150, height=60)
            sig_img_table = Table([[sig_img]], colWidths=[572])
            sig_img_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                ('TOPPADDING', (0,0), (-1,-1), 0),
            ]))
            sig_story.append(sig_img_table)
        except Exception:
            sig_story.append(Spacer(1, 45))
    else:
        sig_story.append(Spacer(1, 45))

    sig_story.append(Paragraph("________________________________________<br/><b>Firma de Aceptación del Cliente</b>", style_sig))
    story.append(KeepTogether(sig_story))

    # 8. Fotos — composite grid (1 XObject) — RLImage (PNG FlateDecode)
    id_reporte = report.get('IdReporte')
    if id_reporte:
        try:
            conn = get_connection()
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Orden, FotoComprimida FROM ReportesServicioFotos WHERE IdReporte = %s ORDER BY Orden", (id_reporte,))
                fotos = cur.fetchall()
            if fotos:
                composite_buf = _build_photo_grid_composite(fotos)
                if composite_buf:
                    story.append(PageBreak())
                    story.append(Paragraph("EVIDENCIA FOTOGRÁFICA / PHOTO EVIDENCE", style_subtitle))
                    story.append(Spacer(1, 10))
                    # Insertar imagen compuesta como un solo RLImage
                    img = RLImage(composite_buf, width=540, height=650)
                    story.append(img)
                    story.append(Spacer(1, 10))
        except Exception:
            pass

    # Build con footer del HUB (timestamp, paginación, confidencialidad, fotos de técnicos)
    tech_names = [report.get('Tecnico', '')] + (tecnicos_adic if isinstance(tecnicos_adic, list) else [])
    tech_photos = [_get_tech_photo(n) for n in tech_names if n and str(n).strip()]
    doc.build(story, canvasmaker=partial(ServiceNumberedCanvas, tech_photos=tech_photos))
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
