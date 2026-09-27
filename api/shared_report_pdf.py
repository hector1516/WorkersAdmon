"""Piezas compartidas del PDF de Reporte de Servicio (HUB <-> Field).

FUENTE DE VERDAD: este archivo en el repo HUB.
COPIA EXACTA en: field/api/shared_report_pdf.py

REGLA: cualquier cambio aquí debe copiarse TAL CUAL al otro repo y subir
SHARED_REPORT_PDF_VERSION en ambos. El script tools/check_shared_pdf.py
verifica que las dos copias sean idénticas (mismo hash).

Alcance: clases/funciones complejas que ya divergieron una vez
(watermark CONFIDENCIAL, footer con paginación, placeholder de foto,
timestamp zona México). Los estilos simples y _txt se quedan duplicados
a propósito (son estables y de 1 línea).
"""

import datetime
import io

from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable
from reportlab.platypus import Image as RLImage

SHARED_REPORT_PDF_VERSION = 1


def now_mexico():
    """Fecha/hora actual en zona horaria de México (UTC-6)."""
    try:
        import pytz
        return datetime.datetime.now(pytz.timezone('America/Mexico_City'))
    except ImportError:
        return datetime.datetime.utcnow() - datetime.timedelta(hours=6)


def make_placeholder_photo(size=25):
    """Placeholder gris de foto de técnico para el footer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(size, size))
    c.setFillColor(colors.white)
    c.rect(0, 0, size, size, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#CBD5E1"))
    cx, cy = size / 2, size / 2
    c.circle(cx, cy + 4, 4, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.circle(cx, cy - 5, 7, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#CBD5E1"))
    c.circle(cx, cy + 4, 4, fill=1, stroke=0)
    c.save()
    buf.seek(0)
    return buf


class ServiceNumberedCanvas(canvas.Canvas):
    """Footer de reporte: timestamp, 'Página X de Y', aviso de
    confidencialidad y fotos de técnicos. Igual en HUB y Field."""
    def __init__(self, *args, tech_photos=None, timestamp_str=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self._tech_photos = tech_photos or []
        self._timestamp_str = timestamp_str or now_mexico().strftime("%d%m%Y%H%M%S")

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
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(20, 22, f"{self._timestamp_str} | Reporte de Servicio de Campo - ECCSA Automation")
        self.drawRightString(592, 22, f"Página {self._pageNumber} de {page_count}")
        self.setFont("Helvetica", 6)
        self.setFillColor(colors.HexColor("#94A3B8"))
        confidencial = "Este documento es confidencial y propiedad de ECCSA. Queda prohibida su reproducción, distribución o uso no autorizado. La firma del cliente autoriza su uso exclusivo por parte de ECCSA para los fines descritos en el presente reporte."
        self.drawString(20, 13, confidencial)
        if self._tech_photos:
            photo_size = 20
            gap = 3
            total_w = len(self._tech_photos) * photo_size + (len(self._tech_photos) - 1) * gap
            x_start = 592 - total_w
            y = 34
            for photo_buf in self._tech_photos:
                try:
                    from reportlab.lib.utils import ImageReader
                    img = ImageReader(photo_buf)
                    self.drawImage(img, x_start, y, width=photo_size, height=photo_size, mask='auto')
                except Exception:
                    pass
                x_start += photo_size + gap
        self.restoreState()


class ImageWithWatermark(Flowable):
    """Foto SIN marca de agua. Igual en HUB y Field.
    
    La marca de agua CONFIDENCIAL con transparencia (ExtGState/alpha)
    causaba crash OOM en WhatsApp iOS al aplanar el PDF.
    Ahora insertamos la imagen RGB normalizada directamente."""
    def __init__(self, img_buf, width, height, watermark_text=None):
        Flowable.__init__(self)
        self.img_buf = img_buf
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        self.canv.saveState()
        img = RLImage(self.img_buf, width=self.width, height=self.height)
        img.drawOn(self.canv, 0, 0)
        self.canv.restoreState()
