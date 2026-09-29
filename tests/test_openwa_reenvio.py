"""
Pruebas del boton "reenviar el ultimo evento".

Este boton existe para probar un aviso sin esperar a que pase algo de verdad, y
su peligro es el contrario de lo habitual: que diga "reenviado" sin haber
mandado nada. Por eso lo que se prueba es sobre todo que NO mienta:

  · si no hay ningun registro, lo dice (no inventa un evento);
  · si el aviso no tiene telefonos o esta apagado, avisa que no se encolo nada;
  · el adjunto sale si existia: el ticket lleva su foto y el reporte su PDF.

La base y la generacion del PDF estan dobladas.

    python3 -m unittest tests.test_openwa_reenvio
"""

import unittest

import openwa_alerts as oa


KM = {'Id': 1, 'IdAutomovil': 4, 'Kilometros': 324107,
      'FechaHora': '2026-07-23 16:15:31', 'IdUsuario': 2}
def _jpeg():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), (10, 20, 30)).save(buf, format='JPEG')
    return buf.getvalue()


TICKET = {'FolioTicket': '6491730', 'IdVehiculo': 4, 'IdCliente': 'HUSA',
          'Descripcion': 'PC vtech', 'IdUsuario': 2, 'ImagenTicket': _jpeg(),
          'ImagenNombre': 'camara_ticket.jpg'}
REPORTE = {'IdReporte': 10, 'Folio': 'RS-00010', 'Cliente': 'Hussmann',
           'Estatus': 'Firmado'}


class DbFalso:
    def __init__(self, km=KM, ticket=TICKET, reporte=REPORTE, evento=None,
                 saldo='9709.00', saldo_fecha='2026-09-29 14:47:17'):
        self.km, self.ticket, self.reporte = km, ticket, reporte
        self.evento = evento
        self.saldo, self.saldo_fecha = saldo, saldo_fecha
        self.encolados = []
        self._eventos = {}

    def get_ultimo_registro_kilometros(self):
        return self.km

    def get_ultimo_ticket_oxxogas(self, con_foto=True):
        return self.ticket

    def get_ultimo_reporte_firmado(self):
        return self.reporte

    def get_govale_config(self, clave):
        return {'govale_saldo': self.saldo, 'govale_saldo_fecha': self.saldo_fecha}.get(clave)

    def get_service_report_by_id(self, id_reporte):
        return dict(self.reporte, Tecnico='IT Support', Fecha='2026-07-21',
                    DescripcionServicio='cambio de balata')

    def get_openwa_evento(self, id_evento):
        if id_evento in self._eventos:
            return self._eventos[id_evento]
        ev = self.evento or {
            'IdEvento': id_evento, 'Activo': 1, 'AdjuntarArchivo': 1,
            'PlantillaMensaje': 'Folio {Folio} Nombre {Nombre} Saldo {Saldo} '
                                'Estado {Estado} Km {Kilometros} Usuario {Usuario}',
            'Telefonos': '5218123211516'}
        ev = dict(ev, IdEvento=id_evento)
        self._eventos[id_evento] = ev
        return ev

    def queue_openwa_alerta(self, id_evento, chat, texto, adjunto=None,
                            nombre=None, tipo=None):
        self.encolados.append({'evento': id_evento, 'chat': chat, 'texto': texto,
                               'adjunto': adjunto, 'tipo': tipo})
        return True


def _con(db):
    previo = oa.db
    oa.db = db
    return previo


def _sin_pdf(módulo):
    """Pone un pdf_generator que devuelve bytes (o None, para probar el fallo)."""
    import sys
    import types
    mod = types.ModuleType('pdf_generator')
    mod.generate_service_report_pdf = lambda reporte: (b'%PDF-1.4' if módulo else None)
    previo = sys.modules.get('pdf_generator')
    sys.modules['pdf_generator'] = mod
    return previo


class Kilometros(unittest.TestCase):
    def test_reenvia_el_ultimo_odometro(self):
        db = DbFalso()
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('KILOMETROS')
        finally:
            oa.db = previo
        self.assertTrue(ok, msg)
        self.assertIn('324,107', db.encolados[0]['texto'])
        self.assertIn('destinatario', msg)

    def test_sin_registros_no_inventa_uno(self):
        db = DbFalso(km=None)
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('KILOMETROS')
        finally:
            oa.db = previo
        self.assertFalse(ok)
        self.assertIn('No hay', msg)
        self.assertEqual(db.encolados, [])

    def test_sin_telefonos_avisa_que_no_se_envio(self):
        db = DbFalso(evento={'IdEvento': 'KILOMETROS', 'Activo': 1,
                             'AdjuntarArchivo': 0, 'PlantillaMensaje': 'x',
                             'Telefonos': ''})
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('KILOMETROS')
        finally:
            oa.db = previo
        self.assertFalse(ok)
        self.assertIn('teléfonos', msg.lower())


class Ticket(unittest.TestCase):
    def test_reenvia_el_ticket_con_su_foto(self):
        db = DbFalso()
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('OXXOGAS_TICKET')
        finally:
            oa.db = previo
        self.assertTrue(ok, msg)
        e = db.encolados[0]
        self.assertEqual(e['tipo'], 'imagen')
        self.assertIn('6491730', e['texto'])

    def test_si_la_foto_no_es_una_imagen_el_texto_sigue_saliendo(self):
        db = DbFalso(ticket=dict(TICKET, ImagenTicket=b'no soy una foto'))
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('OXXOGAS_TICKET')
        finally:
            oa.db = previo
        self.assertTrue(ok, msg)
        self.assertIsNone(db.encolados[0]['adjunto'])
        self.assertIn('sin foto', msg)

    def test_sin_tickets_no_inventa_uno(self):
        db = DbFalso(ticket=None)
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('OXXOGAS_TICKET')
        finally:
            oa.db = previo
        self.assertFalse(ok)
        self.assertEqual(db.encolados, [])


class Reporte(unittest.TestCase):
    def test_reenvia_el_reporte_con_su_pdf(self):
        db = DbFalso()
        previo = _con(db)
        pdf_previo = _sin_pdf(True)
        try:
            ok, msg = oa.reenviar_ultimo('REPORTE_SERVICIO')
        finally:
            oa.db = previo
            import sys
            if pdf_previo is None:
                sys.modules.pop('pdf_generator', None)
            else:
                sys.modules['pdf_generator'] = pdf_previo
        self.assertTrue(ok, msg)
        e = db.encolados[0]
        self.assertEqual(e['tipo'], 'documento')
        self.assertEqual(e['adjunto'], b'%PDF-1.4')
        self.assertIn('RS-00010', e['texto'])

    def test_si_el_pdf_no_se_genera_lo_dice_pero_manda_el_texto(self):
        db = DbFalso()
        previo = _con(db)
        pdf_previo = _sin_pdf(False)
        try:
            ok, msg = oa.reenviar_ultimo('REPORTE_SERVICIO')
        finally:
            oa.db = previo
            import sys
            if pdf_previo is None:
                sys.modules.pop('pdf_generator', None)
            else:
                sys.modules['pdf_generator'] = pdf_previo
        self.assertTrue(ok, msg)
        self.assertIsNone(db.encolados[0]['adjunto'])
        self.assertIn('sin PDF', msg)

    def test_sin_reportes_firmados_no_inventa_uno(self):
        db = DbFalso(reporte=None)
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('REPORTE_SERVICIO')
        finally:
            oa.db = previo
        self.assertFalse(ok)
        self.assertIn('No hay', msg)


class Saldo(unittest.TestCase):
    def test_manda_el_saldo_actual(self):
        db = DbFalso()
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('GOVALE_SALDO')
        finally:
            oa.db = previo
        self.assertTrue(ok, msg)
        self.assertIn('9,709.00', db.encolados[0]['texto'])
        # y dice que es el estado de ahora, no un evento
        self.assertIn('saldo actual', msg.lower())

    def test_pide_solo_el_aviso_que_se_entrego(self):
        # Si estas en 9,709 y pides el diario, no tiene que salir tambien el de
        # "saldo bajo": cada boton prueba lo suyo.
        db = DbFalso()
        previo = _con(db)
        try:
            oa.reenviar_ultimo('GOVALE_SALDO')
        finally:
            oa.db = previo
        self.assertEqual(len(db.encolados), 1)
        self.assertEqual(db.encolados[0]['evento'], 'GOVALE_SALDO')

    def test_el_saldo_bajo_solo_aparece_si_realmente_esta_bajo(self):
        db = DbFalso(saldo='1500')
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('GOVALE_SALDO_BAJO')
        finally:
            oa.db = previo
        self.assertTrue(ok)
        self.assertIn('BAJO', db.encolados[0]['texto'])


class Desconocido(unittest.TestCase):
    def test_un_aviso_que_no_sabe_reproducir_lo_dice_en_vez_de_fallar(self):
        db = DbFalso()
        previo = _con(db)
        try:
            ok, msg = oa.reenviar_ultimo('DISPOSITIVO_RED_NUEVO')
        finally:
            oa.db = previo
        self.assertFalse(ok)
        self.assertIn('DISPOSITIVO_RED_NUEVO', msg)


if __name__ == '__main__':
    unittest.main()
