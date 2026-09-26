"""
tests/test_pdf_worker.py — Worker de PDFs: 5º módulo (Remisiones)
==================================================================
El worker `cron_sync_pdf_storage.py` debe recorrer 5 módulos:
Cotizaciones Materiales, CSP, Reportes de Servicio, Órdenes de Compra
y **Remisiones** (RM-CM#####-NN → Remisiones_Materiales/<año>/<mes>/).

No necesita SQL Server ni SMB: se reemplazan las 5 consultas por rango,
`pdf_storage.pdf_exists`/`save_pdf` y `pdf_generator.generate_remision_pdf`
por dobles en memoria.

Ejecución:  python tests/test_pdf_worker.py
"""
import datetime
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import cron_sync_pdf_storage as worker  # noqa: E402


class TestGenerarPdfsRemisiones(unittest.TestCase):
    """Cubre generar_pdfs_dia() enfocado en el módulo de Remisiones."""

    def _run(self, remisiones, remision_ya_existe=False):
        """Ejecuta generar_pdfs_dia() con dobles para todo y devuelve (stats, guarda)."""
        fecha = datetime.date.today()
        guarda = mock.MagicMock(return_value="Remisiones_Materiales/x.pdf")
        genera = mock.MagicMock(return_value=b"%PDF-1.4 fake")
        nulos = mock.MagicMock(return_value=[])

        with mock.patch.object(worker.db, "get_folios_cotizaciones_materiales_by_range", nulos), \
             mock.patch.object(worker.db, "get_folios_cotizaciones_servproy_by_range", nulos), \
             mock.patch.object(worker.db, "get_folios_reportes_servicio_by_range", nulos), \
             mock.patch.object(worker.db, "get_folios_ordenes_compra_by_range", nulos), \
             mock.patch.object(worker.db, "get_remisiones_by_range",
                               mock.MagicMock(return_value=remisiones)) as consulta, \
             mock.patch.object(worker.pdf_storage, "pdf_exists",
                               mock.MagicMock(return_value=remision_ya_existe)), \
             mock.patch.object(worker.pdf_storage, "save_pdf", guarda), \
             mock.patch.object(worker.pdf_generator, "generate_remision_pdf", genera):
            stats = worker.generar_pdfs_dia(fecha)

        # La consulta de remisiones SIEMPRE debe pedirse para el día objetivo
        consulta.assert_called_once_with(fecha, fecha)
        return stats, genera, guarda

    def test_remision_nueva_se_genera_y_se_sube(self):
        """Una RM del día sin archivo en el share → se genera y se sube."""
        rems = [{"IdRemision": 42, "FolioRemision": "RM-CM00017-01",
                 "FechaCreacion": datetime.datetime(2026, 9, 25, 12, 0)}]
        stats, genera, guarda = self._run(rems, remision_ya_existe=False)

        self.assertEqual(stats["remisiones"], 1)
        self.assertEqual(stats["errores"], 0)
        genera.assert_called_once_with(42)
        guarda.assert_called_once_with("remisiones", "RM-CM00017-01",
                                       b"%PDF-1.4 fake",
                                       datetime.date.today())

    def test_remision_existente_se_salta(self):
        """Si el PDF ya está en el share no se regenera (idempotente)."""
        rems = [{"IdRemision": 42, "FolioRemision": "RM-CM00017-01"}]
        stats, genera, guarda = self._run(rems, remision_ya_existe=True)

        self.assertEqual(stats["remisiones"], 0)
        self.assertEqual(stats["saltados"], 1)
        genera.assert_not_called()
        guarda.assert_not_called()

    def test_cero_remisiones_otros_modulos_en_cero(self):
        """Sin remisiones el contador es 0 y los otros 4 módulos no se tocan."""
        stats, genera, guarda = self._run([], remision_ya_existe=False)

        self.assertEqual(stats["remisiones"], 0)
        for clave in ("materiales", "servproy", "reportes", "oc", "errores"):
            self.assertEqual(stats[clave], 0, clave)
        genera.assert_not_called()
        guarda.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
