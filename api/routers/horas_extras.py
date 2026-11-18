"""api/routers/horas_extras.py - Endpoints de horas extras (PDF)."""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import Response
from auth import require_user
from db import get_connection
from typing import Optional
import io, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pdf_generator import generate_horas_extras_reporte_pdf

router = APIRouter()

def sanitize_pdf(raw_pdf_bytes: bytes) -> bytes:
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(raw_pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return out.getvalue()
    except Exception:
        return raw_pdf_bytes


@router.get("/reporte/{id_reporte}/pdf")
def generar_pdf_horas_extras(id_reporte: int, user=Depends(require_user)):
    """Genera el PDF de un reporte de horas extras."""
    conn = get_connection()
    cur = conn.cursor(as_dict=True)
    try:
        # Obtener reporte
        cur.execute("""
            SELECT r.*, u.Nombre AS NombreUsuario
            FROM HUB_HorasExtrasReportes r
            LEFT JOIN HUB_Users u ON u.Id = r.IdUsuario
            WHERE r.Id = %s
        """, (id_reporte,))
        reporte = cur.fetchone()
        if not reporte:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")

        # Obtener registros de horas extras vinculados
        cur.execute("""
            SELECT Fecha, HoraEntrada, HoraSalida, HorasComida, HorasTraslado,
                   HorasExtrasCalculadas, Descripcion, Cliente, Calificacion, Nota
            FROM HUB_HorasExtrasRegistros
            WHERE IdReporte = %s
            ORDER BY Fecha ASC
        """, (id_reporte,))
        registros_raw = cur.fetchall()

        # Convertir a dict para el PDF
        registros = []
        for r in registros_raw:
            registros.append({
                'Fecha': r.get('Fecha'),
                'HoraEntrada': r.get('HoraEntrada'),
                'HoraSalida': r.get('HoraSalida'),
                'HorasComida': r.get('HorasComida'),
                'HorasTraslado': r.get('HorasTraslado'),
                'HorasExtrasCalculadas': r.get('HorasExtrasCalculadas'),
                'Descripcion': r.get('Descripcion'),
                'Cliente': r.get('Cliente'),
                'Calificacion': r.get('Calificacion'),
                'Nota': r.get('Nota'),
            })

        nombre = reporte.get('NombreUsuario') or 'Empleado'
        total_horas = float(reporte.get('TotalHorasExtras') or 0)
        sueldo_semana = float(reporte.get('SueldoSemana') or 0)
        periodo_inicio = reporte.get('PeriodoInicio')
        periodo_fin = reporte.get('PeriodoFin')
        notas = reporte.get('Notas')
        folio = reporte.get('Folio')

        # Generar PDF
        pdf_bytes = generate_horas_extras_reporte_pdf(
            nombre=nombre,
            registros=registros,
            total_horas=total_horas,
            sueldo_semana=sueldo_semana,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            notas=notas,
            folio=folio
        )

        if not pdf_bytes:
            raise HTTPException(status_code=500, detail="Error generando PDF")

        pdf_bytes = sanitize_pdf(pdf_bytes)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={folio or 'reporte_horas_extras'}.pdf"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {e}")
    finally:
        cur.close()
        conn.close()
