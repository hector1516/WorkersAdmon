"""
Pruebas del worker de reportes firmados al grupo «Servicios».

Lo que se vigila aquí no es que «mande», sino las dos cosas que duelen si
fallan:

  · que SOLO hable con el grupo (un PDF de cliente a un teléfono equivocado no
    se puede deshacer);
  · que el cursor avance únicamente con el envío bueno, para no repetir ni
    saltarse un reporte.

La base y el cliente de OpenWA están doblados: no se toca SQL Server ni se
manda nada.

    python3 -m unittest tests.test_wa_firmados
"""

import unittest

import cron_whatsapp_firmados as wf
import openwa_alerts as oa
from openwa_client import normalizar_chat_id


GRUPO = '120363407857907731@g.us'


class ClienteFalso:
    # El worker valida el destino con el normalizador real: es justo la parte
    # que se prueba (que no acepte un teléfono), así que no se dobla.
    normalizar_chat_id = staticmethod(normalizar_chat_id)

    def __init__(self, falla_en=None):
        self.falla_en = falla_en or set()
        self.documentos = []
        self.textos = []

    def enviar_documento(self, chat, datos, nombre, caption=None, **kw):
        if chat in self.falla_en or nombre in self.falla_en:
            return False, 'HTTP 500: rechazado'
        self.documentos.append({'chat': chat, 'nombre': nombre,
                                'caption': caption, 'bytes': len(datos)})
        return True, 'ok'

    def enviar_texto(self, chat, texto, **kw):
        if chat in self.falla_en:
            return False, 'HTTP 500: rechazado'
        self.textos.append({'chat': chat, 'texto': texto})
        return True, 'ok'


class WorkerFirmados(unittest.TestCase):
    def setUp(self):
        self.cfg = {'whatsapp_grupo_servicios': GRUPO, 'wa_firmados_ultimo_id': '10'}
        self.rows = [
            {'IdReporte': 11, 'Folio': 'RS-00011', 'Cliente': 'Hussmann'},
            {'IdReporte': 12, 'Folio': 'RS-00012', 'Cliente': 'Bimbo'},
        ]
        self.ultimo = 12
        self.cursor_guardado = []
        self.cliente = ClienteFalso()
        self.pdf = {11: b'%PDF-11', 12: b'%PDF-12'}

        self._orig = (wf._cfg_get, wf._cfg_set, wf._reportes_firmados_desde,
                      wf._ultimo_id_firmado, wf.ow, oa._datos_reporte_para_reenvio)
        wf._cfg_get = lambda clave: self.cfg.get(clave)
        wf._cfg_set = self._cfg_set
        wf._reportes_firmados_desde = lambda cursor, limite=wf.LOTE: [
            r for r in self.rows if r['IdReporte'] > cursor][:limite]
        wf._ultimo_id_firmado = lambda: self.ultimo
        wf.ow = self.cliente
        oa._datos_reporte_para_reenvio = self._datos

    def tearDown(self):
        (wf._cfg_get, wf._cfg_set, wf._reportes_firmados_desde,
         wf._ultimo_id_firmado, wf.ow, oa._datos_reporte_para_reenvio) = self._orig

    def _cfg_set(self, clave, valor):
        self.cursor_guardado.append((clave, valor))
        self.cfg[clave] = valor
        return True

    def _datos(self, fila):
        datos = {'Folio': fila['Folio'], 'Cliente': fila['Cliente'],
                 'Tecnicos': 'IT Support', 'FechaFirma': '26/11/2026',
                 'HoraFirma': '09:30'}
        pdf = self.pdf.get(fila['IdReporte'])
        return datos, pdf, (f"{fila['Folio']}.pdf" if pdf else None)

    # ── destino ──────────────────────────────────────────────────────────────
    def test_sin_grupo_valido_no_manda_nada(self):
        self.cfg['whatsapp_grupo_servicios'] = '5218123211516'
        enviados, detalle = wf.procesar_ciclo()
        self.assertEqual(enviados, 0)
        self.assertEqual(self.cliente.documentos, [])
        self.assertEqual(self.cliente.textos, [])
        self.assertIn('no se manda nada', detalle)

    def test_manda_solo_al_grupo(self):
        wf.procesar_ciclo()
        self.assertEqual(len(self.cliente.documentos), 2)
        for envio in self.cliente.documentos:
            self.assertEqual(envio['chat'], GRUPO)
            self.assertTrue(envio['chat'].endswith('@g.us'))

    # ── cursor ───────────────────────────────────────────────────────────────
    def test_cursor_avanza_solo_con_envio_bueno(self):
        self.cursor_guardado.clear()
        enviados, _ = wf.procesar_ciclo()
        self.assertEqual(enviados, 2)
        self.assertEqual(self.cursor_guardado,
                         [('wa_firmados_ultimo_id', '11'),
                          ('wa_firmados_ultimo_id', '12')])

    def test_si_falla_se_queda_el_cursor_y_no_avanza_al_siguiente(self):
        self.cliente.falla_en = {'RS-00012.pdf'}
        enviados, detalle = wf.procesar_ciclo()
        self.assertEqual(enviados, 1)
        self.assertEqual(self.cursor_guardado, [('wa_firmados_ultimo_id', '11')])
        self.assertIn('se reintenta', detalle)
        self.assertEqual(len(self.cliente.documentos), 1)

    def test_primer_arranque_siembra_el_cursor_sin_mandar_historial(self):
        self.cfg.pop('wa_firmados_ultimo_id')
        self.cursor_guardado.clear()
        enviados, detalle = wf.procesar_ciclo()
        self.assertEqual(enviados, 0)
        self.assertEqual(self.cursor_guardado, [('wa_firmados_ultimo_id', '12')])
        self.assertEqual(self.cliente.documentos, [])
        self.assertIn('sembrado', detalle)

    def test_reintento_no_repite_lo_ya_enviado(self):
        wf.procesar_ciclo()                     # 11 y 12 salen
        self.cliente.documentos.clear()
        enviados, _ = wf.procesar_ciclo()       # ya no hay nada nuevo
        self.assertEqual(enviados, 0)
        self.assertEqual(self.cliente.documentos, [])

    def test_sin_pdf_manda_el_texto_y_avanza(self):
        self.pdf.pop(11)
        enviados, _ = wf.procesar_ciclo()
        self.assertEqual(enviados, 2)
        self.assertEqual(len(self.cliente.textos), 1)
        self.assertIn('RS-00011', self.cliente.textos[0]['texto'])
        self.assertEqual(self.cliente.textos[0]['chat'], GRUPO)

    # ── lo que se lee ────────────────────────────────────────────────────────
    def test_texto_lleva_folio_cliente_tecnico_fecha_y_firma(self):
        texto = wf.texto_reporte({'Folio': 'RS-00011', 'Cliente': 'Hussmann',
                                  'Tecnicos': 'IT Support',
                                  'FechaFirma': '26/11/2026', 'HoraFirma': '09:30'})
        for dato in ('RS-00011', 'Hussmann', 'IT Support', '26/11/2026', '09:30',
                     'PDF adjunto', 'The Penguin'):
            self.assertIn(dato, texto)


if __name__ == '__main__':
    unittest.main()
