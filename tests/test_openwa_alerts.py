"""
Pruebas del dispatcher de WhatsApp (openwa_alerts).

Lo que importa aqui es la regla de "a quien le llega": si el evento no tiene
telefonos, no sale nada y hay que decirlo (no fallar en silencio y que alguien
crea que si se aviso). Y si un evento pide adjunto y no llego, el texto sale
igualmente: un reporte sin foto es mil veces mejor que ningun reporte.

La base de datos y la generacion del PDF se doblan: estas pruebas no tocan ni la
una ni la otra.

    python3 -m unittest tests.test_openwa_alerts
"""

import unittest

import openwa_alerts as oa


EVENTO = {
    'IdEvento': 'KILOMETROS',
    'PlantillaMensaje': '🚗 {Auto}\n📊 {Kilometros} km\n👤 {Usuario}',
    'AdjuntarArchivo': 0,
    'Telefonos': '5218123211516, 5512345678',
    'Activo': 1,
}


class DbFalso:
    """Solo las tres llamadas que hace el dispatcher, y con memoria."""

    def __init__(self, evento=None, reporte=None):
        self.evento = evento
        self.reporte = reporte
        self.encolados = []

    def get_openwa_evento(self, id_evento):
        ev = self.evento
        if ev and ev.get('IdEvento') == id_evento:
            return ev
        return ev

    def queue_openwa_alerta(self, id_evento, chat, texto, adjunto=None,
                            nombre=None, tipo=None):
        self.encolados.append({'evento': id_evento, 'chat': chat, 'texto': texto,
                               'adjunto': adjunto, 'nombre': nombre, 'tipo': tipo})
        return True

    def get_service_report_by_id(self, id_reporte):
        return self.reporte

    def get_tecnicos_texto(self, id_reporte):
        return 'Miguel, Rosa'


def _con(módulo, db):
    original = getattr(módulo, 'db', None)
    módulo.db = db
    return original


class Encolar(unittest.TestCase):
    def test_un_numero_por_ves_que_hay_dos_telefonos(self):
        db = DbFalso(EVENTO)
        _con(oa, db)
        try:
            n = oa.alertar_kilometros(7, 123456, 3)
        finally:
            oa.db = None
        self.assertEqual(n, 2)
        self.assertEqual([e['chat'] for e in db.encolados],
                         ['5218123211516@c.us', '525512345678@c.us'])

    def test_el_texto_llega_renderizado_y_sin_llaves(self):
        db = DbFalso(EVENTO)
        _con(oa, db)
        try:
            oa.alertar_kilometros(7, 123456, 3)
        finally:
            oa.db = None
        texto = db.encolados[0]['texto']
        self.assertNotIn('{', texto)
        self.assertIn('123,456', texto)
        self.assertIn('7', texto)   # el auto no existe en el doble -> ID 7

    def test_sin_telefonos_no_sale_nada_y_no_revienta(self):
        db = DbFalso(dict(EVENTO, Telefonos=None))
        _con(oa, db)
        try:
            n = oa.alertar_kilometros(7, 1, 3)
        finally:
            oa.db = None
        self.assertEqual(n, 0)
        self.assertEqual(db.encolados, [])

    def test_un_numero_mal_escrito_se_salta_pero_los_buenos_salen(self):
        db = DbFalso(dict(EVENTO, Telefonos='5218123211516, hector'))
        _con(oa, db)
        try:
            n = oa.alertar_kilometros(7, 1, 3)
        finally:
            oa.db = None
        self.assertEqual(n, 1)
        self.assertEqual(db.encolados[0]['chat'], '5218123211516@c.us')

    def test_el_evento_apagado_no_manda_nada(self):
        db = DbFalso(dict(EVENTO, Activo=0))
        _con(oa, db)
        try:
            self.assertEqual(oa.alertar_kilometros(7, 1, 3), 0)
        finally:
            oa.db = None
        self.assertEqual(db.encolados, [])

    def test_sin_plantilla_no_manda_nada(self):
        db = DbFalso(dict(EVENTO, PlantillaMensaje='   '))
        _con(oa, db)
        try:
            self.assertEqual(oa.alertar_kilometros(7, 1, 3), 0)
        finally:
            oa.db = None


class Adjuntos(unittest.TestCase):
    EVENTO_TICKET = dict(EVENTO, IdEvento='OXXOGAS_TICKET', AdjuntarArchivo=1,
                         Telefonos='5218123211516',
                         PlantillaMensaje='Ticket {Folio} de {Nombre}')

    @staticmethod
    def _jpeg():
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (40, 30), (10, 20, 30)).save(buf, format='JPEG')
        return buf.getvalue()

    def test_la_foto_se_manda_como_imagen(self):
        db = DbFalso(self.EVENTO_TICKET)
        _con(oa, db)
        try:
            oa.alertar_ticket_oxxogas('F-1', foto_bytes=self._jpeg(), usuario_actual='Ana')
        finally:
            oa.db = None
        e = db.encolados[0]
        self.assertEqual(e['tipo'], 'imagen')
        self.assertEqual(e['nombre'], 'foto.jpg')
        self.assertTrue(e['adjunto'].startswith(b'\xff\xd8'))  # JPEG de verdad

    def test_una_foto_con_canal_alfa_se_recodifica_a_jpeg(self):
        # Las fotos de celular traen alfa o paleta; mandarlas tal cual Wha
        # tsApp las rechaza y el aviso se queda sin imagen.
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGBA', (40, 30), (10, 20, 30, 128)).save(buf, format='PNG')
        db = DbFalso(self.EVENTO_TICKET)
        _con(oa, db)
        try:
            oa.alertar_ticket_oxxogas('F-1', foto_bytes=buf.getvalue(), usuario_actual='Ana')
        finally:
            oa.db = None
        self.assertTrue(db.encolados[0]['adjunto'].startswith(b'\xff\xd8'))

    def test_una_foto_corrupta_no_tira_el_aviso(self):
        db = DbFalso(self.EVENTO_TICKET)
        _con(oa, db)
        try:
            n = oa.alertar_ticket_oxxogas('F-1', foto_bytes=b'no soy una imagen',
                                          usuario_actual='Ana')
        finally:
            oa.db = None
        self.assertEqual(n, 1)                       # el texto igual sale
        self.assertIsNone(db.encolados[0]['adjunto'])

    def test_sin_foto_manda_el_texto_y_no_una_imagen_vacia(self):
        db = DbFalso(self.EVENTO_TICKET)
        _con(oa, db)
        try:
            oa.alertar_ticket_oxxogas('F-1', usuario_actual='Ana')
        finally:
            oa.db = None
        e = db.encolados[0]
        self.assertIsNone(e['adjunto'])
        self.assertIsNone(e['tipo'])
        self.assertIn('F-1', e['texto'])

    def test_un_evento_sin_adjunto_nunca_carga_un_archivo(self):
        # El evento dice que no manda archivo: aunque le lleguen bytes, no se
        # adjuntan. Si no, un cambio de plantilla meteria un PDF de mas.
        db = DbFalso(dict(EVENTO, AdjuntarArchivo=0))
        _con(oa, db)
        try:
            oa.alertar_kilometros(7, 1, 3)
        finally:
            oa.db = None
        self.assertIsNone(db.encolados[0]['adjunto'])


class Saldo(unittest.TestCase):
    """El aviso de umbral y el diario son dos eventos, no uno con dos textos."""

    UN_SOLO = dict(EVENTO, Telefonos='5218123211516')

    def _db_un_numero(self):
        """Un evento por clave, con la plantilla de verdad de cada aviso."""
        import notif_messages as nm
        db = DbFalso(None)
        db.get_openwa_evento = lambda e: dict(
            self.UN_SOLO, IdEvento=e, Activo=1,
            PlantillaMensaje=nm.PLANTILLAS.get(e, 'datos: {Saldo} {Estado}'))
        return db

    def test_saldo_normal_solo_manda_el_diario(self):
        db = self._db_un_numero()
        _con(oa, db)
        try:
            n = oa.alertar_saldo_govale(9000, '28/09/2026 09:10')
        finally:
            oa.db = None
        eventos = [e['evento'] for e in db.encolados]
        self.assertEqual(eventos, ['GOVALE_SALDO'])
        self.assertEqual(n, 1)

    def test_saldo_bajo_manda_los_dos(self):
        db = self._db_un_numero()
        _con(oa, db)
        try:
            n = oa.alertar_saldo_govale(1500, '28/09/2026 09:10')
        finally:
            oa.db = None
        self.assertEqual([e['evento'] for e in db.encolados],
                         ['GOVALE_SALDO', 'GOVALE_SALDO_BAJO'])
        self.assertEqual(n, 2)
        self.assertIn('BAJO', db.encolados[1]['texto'])

    def test_en_modo_horario_solo_avisa_cuando_esta_bajo(self):
        # El worker revisa cada hora: si mandara el aviso diario cada vez, el
        # mismo aviso se repetiria 24 veces al dia.
        db = self._db_un_numero()
        _con(oa, db)
        try:
            oa.alertar_saldo_govale(9000, 'x', solo_si_bajo=True)
        finally:
            oa.db = None
        self.assertEqual(db.encolados, [])

    def test_el_umbral_se_puede_cambiar(self):
        db = self._db_un_numero()
        _con(oa, db)
        try:
            oa.alertar_saldo_govale(5000, 'x', umbral=6000, solo_si_bajo=True)
        finally:
            oa.db = None
        self.assertEqual([e['evento'] for e in db.encolados], ['GOVALE_SALDO_BAJO'])


class ReporteFirmado(unittest.TestCase):
    def test_sin_reporte_no_hace_nada(self):
        db = DbFalso(EVENTO, reporte=None)
        _con(oa, db)
        try:
            self.assertEqual(oa.alertar_reporte_firmado(999, usuario_firma='Ana'), 0)
        finally:
            oa.db = None
        self.assertEqual(db.encolados, [])

    def test_el_pdf_se_genera_y_se_marca_como_documento(self):
        import sys
        import types
        modulo = types.ModuleType('pdf_generator')
        modulo.generate_service_report_pdf = lambda reporte: b'%PDF-1.4 fake'
        previo = sys.modules.get('pdf_generator')
        sys.modules['pdf_generator'] = modulo
        db = DbFalso(dict(EVENTO, IdEvento='REPORTE_SERVICIO', AdjuntarArchivo=1,
                          PlantillaMensaje='{Folio} firmado por {UsuarioFirma}'),
                     reporte={'Folio': 'REP-1', 'Cliente': 'Soriana'})
        try:
            _con(oa, db)
            try:
                oa.alertar_reporte_firmado(1, usuario_firma='Hector')
            finally:
                oa.db = None
        finally:
            if previo is None:
                sys.modules.pop('pdf_generator', None)
            else:
                sys.modules['pdf_generator'] = previo
        e = db.encolados[0]
        self.assertEqual(e['adjunto'], b'%PDF-1.4 fake')
        self.assertEqual(e['tipo'], 'documento')
        self.assertEqual(e['nombre'], 'REP-1.pdf')
        self.assertIn('Hector', e['texto'])


if __name__ == '__main__':
    unittest.main()
