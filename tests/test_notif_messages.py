"""
Pruebas del modulo de mensajes compartidos (notif_messages).

Aqui vive la informacion de los avisos: la plantilla y los datos que se le
sustituyen. Lo usan los dos canales (Telegram y WhatsApp) a proposito, para que
un aviso no diga una cosa en un canal y otra en el otro.

Lo que se prueba es el texto que sale, porque es lo que lee una persona: que no
le queden {llaves} sueltas, que no salga una linea en blanco donde no hay dato, y
que el saldo se formatee como dinero.

    python3 -m unittest tests.test_notif_messages
"""

import unittest

import notif_messages as nm


# ── Doble de la base de datos ────────────────────────────────────────────────

class CurFalso:
    """Cursor que responde a lo que el modulo consulta, con respuestas fijas."""

    def __init__(self, respuestas=None):
        self.respuestas = respuestas or {}
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((' '.join(sql.split()), params))
        for patron, fila in self.respuestas.items():
            if patron in ' '.join(sql.split()):
                self.siguiente = fila
                return
        self.siguiente = None

    def fetchone(self):
        return getattr(self, 'siguiente', None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConnFalsa:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self, as_dict=False):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DbFalso:
    def __init__(self, respuestas=None):
        self.cur = CurFalso(respuestas)

    def get_connection(self):
        return ConnFalsa(self.cur)


def _con_fake(módulo, respuestas):
    """Pone una base falsa en el modulo y la quita al terminar la prueba."""
    original = getattr(módulo, 'db', None)
    módulo.db = DbFalso(respuestas)
    return original


# ── Plantillas ───────────────────────────────────────────────────────────────

class AplicarPlantilla(unittest.TestCase):
    def test_sustituye_los_datos(self):
        txt = nm.aplicar_plantilla('Hola {Nombre}, tu folio es {Folio}',
                                   {'Nombre': 'Hector', 'Folio': 'TC-9'})
        self.assertEqual(txt, 'Hola Hector, tu folio es TC-9')

    def test_un_dato_que_falta_no_deja_la_llave_visible(self):
        txt = nm.aplicar_plantilla('Cliente: {Cliente}\nOtro: 1', {'Cliente': ''})
        self.assertNotIn('{', txt)
        # La etiqueta sin valor se va completa, no queda colgada.
        self.assertEqual(txt, 'Otro: 1')

    def test_el_alias_resuelve_desde_el_nombre_interno(self):
        # El productor manda los nombres internos (Usuario, Automovil) y la
        # plantilla usa los de pantalla (Nombre, Auto).
        txt = nm.aplicar_plantilla('{Nombre} en {Auto}',
                                   {'Usuario': 'Hector', 'Automovil': 'NP300 (JKM-123)'})
        self.assertEqual(txt, 'Hector en NP300 (JKM-123)')

    def test_el_alias_no_pisa_un_dato_que_ya_venia_puesto(self):
        txt = nm.aplicar_plantilla('{Nombre}', {'Nombre': 'El correcto', 'Usuario': 'Otro'})
        self.assertEqual(txt, 'El correcto')

    def test_borra_las_lineas_que_solo_eran_negrita_vacia(self):
        # Una plantilla con un campo opcional deja lineas como "* *" si no se
        # limpian, y eso se ve roto en el telefono.
        txt = nm.aplicar_plantilla('Titulo\n*\nDato: 1', {'Dato': 1})
        self.assertNotIn('*', txt)

    def test_una_etiqueta_sin_valor_no_llega_con_el_colon_colgando(self):
        # "👤 Firmado:" sin nombre (p.ej. la firma remota, donde no hay usuario)
        # se ve como un mensaje roto. La linea se omite y ya.
        txt = nm.aplicar_plantilla('👤 Firmado: {UsuarioFirma}\n📅 {FechaFirma}',
                                   {'UsuarioFirma': '', 'FechaFirma': '29/09'})
        self.assertEqual(txt, '📅 29/09')

    def test_una_etiqueta_con_valor_se_queda(self):
        txt = nm.aplicar_plantilla('👤 Firmado: {UsuarioFirma}',
                                   {'UsuarioFirma': 'Hector'})
        self.assertEqual(txt, '👤 Firmado: Hector')

    def test_una_linea_que_no_es_etiqueta_no_se_borra(self):
        # "Reportes de Servicio: 3" es un dato, no una etiqueta vacia.
        txt = nm.aplicar_plantilla('Aviso: algo importante', {})
        self.assertEqual(txt, 'Aviso: algo importante')

    def test_conserva_los_saltos_de_linea_que_organizan_el_mensaje(self):
        txt = nm.aplicar_plantilla('🚗 {Auto}\n📊 {Km} km', {'Auto': 'NP300', 'Km': 100})
        self.assertEqual(txt, '🚗 NP300\n📊 100 km')


class QuitarBloque(unittest.TestCase):
    def test_se_lleva_la_seccion_completa_cuando_no_hay_factura(self):
        p = ('Antes\nFactura vinculada: {FacturaFolio}\nTicket: {Folio}\n'
             '\nFinal: {Hora}')
        txt = nm.quitar_bloque(p, 'Factura vinculada')
        self.assertNotIn('Factura vinculada', txt)
        self.assertNotIn('Ticket:', txt)
        self.assertIn('Antes', txt)
        self.assertIn('Final: {Hora}', txt)

    def test_una_seccion_sin_linea_vacia_al_final_se_comeria_el_resto(self):
        # Por eso las plantillas separan cada bloque con una linea en blanco:
        # el bloque se come lo que sigue hasta el primer hueco. Es el
        # comportamiento de siempre (lo usaba Telegram), no un cambio, pero
        # conviene que quede dicho porque es facil de romper sin querer.
        p = 'Factura vinculada: {FacturaFolio}\nFinal: {Hora}'
        self.assertNotIn('Final', nm.quitar_bloque(p, 'Factura vinculada'))

    def test_si_el_marcador_no_esta_no_altera_la_plantilla(self):
        p = 'linea 1\nlinea 2'
        self.assertEqual(nm.quitar_bloque(p, 'Factura vinculada'), p)


# ── Datos de cada aviso ──────────────────────────────────────────────────────

class DatosSaldo(unittest.TestCase):
    def test_el_saldo_se_formatea_como_dinero(self):
        d = nm.datos_saldo(1234.5, '28/09/2026 09:10', 2000.0)
        self.assertEqual(d['Saldo'], '1,234.50')
        self.assertIn('2,000', d['Umbral'])
        self.assertEqual(d['UltimaRevision'], '28/09/2026 09:10')

    def test_avisa_cuando_el_saldo_esta_bajo_el_umbral(self):
        self.assertIn('BAJO', nm.datos_saldo(1500, 'x', 2000.0)['Estado'])
        self.assertIn('OK', nm.datos_saldo(5000, 'x', 2000.0)['Estado'])

    def test_sin_fecha_de_revision_dice_que_no_se_ha_revisado(self):
        self.assertEqual(nm.datos_saldo(10, None, 2000.0)['UltimaRevision'], 'sin revisar')


class DatosKilometros(unittest.TestCase):
    def _con_auto_y_usuario(self):
        return {'FROM HUB_Automoviles': {'MarcaModelo': 'NP300', 'Placas': 'JKM-123'},
                'FROM HUB_Users': {'Nombre': 'Hector Medina'}}

    def test_trae_el_auto_y_quien_registro(self):
        _con_fake(nm, self._con_auto_y_usuario())
        try:
            d = nm.datos_kilometros(7, 123456, 3, '29/09/2026', '10:15')
        finally:
            nm.db = None
        self.assertEqual(d['Automovil'], 'NP300 (JKM-123)')
        self.assertEqual(d['Usuario'], 'Hector Medina')
        self.assertEqual(d['Kilometros'], '123,456')
        self.assertEqual(d['Fecha'], '29/09/2026')

    def test_si_el_auto_no_existe_no_inventa_nada(self):
        _con_fake(nm, {'FROM HUB_Automoviles': None, 'FROM HUB_Users': {'Nombre': 'Ana'}})
        try:
            d = nm.datos_kilometros(99, 10, 3, 'f', 'h')
        finally:
            nm.db = None
        self.assertIn('99', d['Automovil'])

    def test_sin_id_de_usuario_usa_el_nombre_que_manda_el_caller(self):
        # El sync desde Field no manda id de usuario, solo el nombre. Sin esto
        # el mensaje de kilometros llegaba diciendo "Usuario None".
        _con_fake(nm, {'FROM HUB_Automoviles': {'MarcaModelo': 'NP300', 'Placas': 'JKM-123'},
                       'FROM HUB_Users': None})
        try:
            d = nm.datos_kilometros(7, 10, None, 'f', 'h', usuario_actual='Rosa Ramirez')
        finally:
            nm.db = None
        self.assertEqual(d['Usuario'], 'Rosa Ramirez')

    def test_sin_id_y_sin_nombre_no_inventa_un_usuario_none(self):
        _con_fake(nm, {'FROM HUB_Automoviles': None, 'FROM HUB_Users': None})
        try:
            d = nm.datos_kilometros(7, 10, None, 'f', 'h')
        finally:
            nm.db = None
        self.assertNotIn('None', d['Usuario'])

    def test_si_el_usuario_desaparo_usa_el_id_para_que_se_note(self):
        _con_fake(nm, {'FROM HUB_Automoviles': None, 'FROM HUB_Users': None})
        try:
            d = nm.datos_kilometros(1, 10, 42, 'f', 'h')
        finally:
            nm.db = None
        self.assertIn('42', d['Usuario'])


class DatosReporte(unittest.TestCase):
    def test_arma_el_resumen_del_reporte_firmado(self):
        reporte = {'Folio': 'REP-0012', 'Cliente': 'Soriana', 'Contacto': 'Juan',
                   'Tecnico': 'Miguel', 'Fecha': '28/09/2026',
                   'DescripcionServicio': 'Cambio de balata y aceite'}
        d = nm.datos_reporte(reporte, 'Miguel, Rosa', 'Hector', '29/09/2026', '11:00')
        self.assertEqual(d['Folio'], 'REP-0012')
        self.assertEqual(d['UsuarioFirma'], 'Hector')
        self.assertEqual(d['Tecnicos'], 'Miguel, Rosa')
        self.assertIn('11:00', d['HoraFirma'])

    def test_la_descripcion_se_corta_para_que_el_mensaje_no_se_vaya_de_largo(self):
        d = nm.datos_reporte({'Folio': 'F', 'DescripcionServicio': 'x' * 400},
                             '', 'H', 'f', 'h')
        self.assertLessEqual(len(d['DescripcionServicio']), 100)


class DatosTicket(unittest.TestCase):
    def _fake(self, vale=None):
        return {'FROM HUB_Automoviles': {'MarcaModelo': 'NP300', 'Placas': 'JKM-123'},
                'FROM clientes': {'Cliente': 'CEMEX'},
                'FROM HUB_Users': {'Nombre': 'Hector'},
                'FROM HUB_OxxoGasVales': vale}

    def test_une_ticket_auto_cliente_y_vale(self):
        vale = {'XmlFolio': 'F-99', 'XmlEstacion': 'Oxxo Guadalajara',
                'XmlLitros': 45.5, 'XmlConcepto': 'Magna', 'Monto': 520.5}
        _con_fake(nm, self._fake(vale))
        try:
            d = nm.datos_ticket('F-99', 7, 3, 5, 'Viaje a Heidelberg', '29/09/2026', '12:00')
        finally:
            nm.db = None
        self.assertEqual(d['Automovil'], 'NP300 (JKM-123)')
        self.assertEqual(d['Cliente'], 'CEMEX')
        self.assertEqual(d['Estacion'], 'Oxxo Guadalajara')
        self.assertEqual(d['Folio'], 'F-99')
        self.assertEqual(d['Cantidad'], '$520.50')

    def test_sin_vale_ligado_no_inventa_factura(self):
        _con_fake(nm, self._fake(None))
        try:
            d = nm.datos_ticket('F-100', None, None, None, 'Carga', '29/09/2026', '12:00')
        finally:
            nm.db = None
        self.assertEqual(d['FacturaFolio'], '')
        self.assertEqual(d['Cantidad'], '')

    def test_sin_usuario_usa_el_que_llego_al_sistema(self):
        # En el panel no hay sesion de Streamlit: sin esto el mensaje sale con
        # el nombre vacio aunque sepamos quien registro el ticket.
        _con_fake(nm, self._fake(None))
        try:
            d = nm.datos_ticket('F-101', None, None, None, 'Carga', '29/09/2026', '12:00',
                                usuario_actual='Rosa Ramirez')
        finally:
            nm.db = None
        self.assertEqual(d['Usuario'], 'Rosa Ramirez')
        self.assertEqual(d['Nombre'], 'Rosa Ramirez')


# ── Las cinco plantillas que se siembran para WhatsApp ───────────────────────

class RecuperarJpeg(unittest.TestCase):
    """
    Las fotos del flujo de Vale QR traen 16 bytes de basura antes del JPEG. PIL
    no las abre, y con eso el aviso se quedaba sin foto.
    """

    BASURA = bytes.fromhex('75ab5a8a66a07bf8e97a06dab1eeb8ff')

    def _jpeg(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (30, 20), (200, 30, 30)).save(buf, format='JPEG')
        return buf.getvalue()

    def test_una_foto_con_basura_adelante_se_recupera(self):
        datos = self.BASURA + self._jpeg()
        out = nm.normalizar_foto(datos)
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith(b'\xff\xd8\xff'))
        from PIL import Image
        import io
        self.assertEqual(Image.open(io.BytesIO(out)).size, (30, 20))

    def test_una_foto_limpia_se_deja_como_esta(self):
        out = nm.normalizar_foto(self._jpeg())
        self.assertTrue(out.startswith(b'\xff\xd8\xff'))

    def test_basura_sin_jpeg_dentro_no_inventa_una_foto(self):
        self.assertIsNone(nm.normalizar_foto(b'no soy nada'))
        self.assertIsNone(nm.normalizar_foto(self.BASURA + b'tampoco soy un jpeg'))

    def test_el_registro_real_de_produccion_si_se_recupera(self):
        # Los bytes tal cual como estan en HUB_OxxoGasTickets.Id=19
        from PIL import Image
        import io
        real = bytes.fromhex('75ab5a8a66a07bf8e97a06dab1eeb8ffd8ffe000104a4649460001010048')
        real += b'\x00' * 20 + b'\xff\xd9'
        out = nm._recuperar_jpeg(real)
        self.assertTrue(out.startswith(b'\xff\xd8\xff'))


class Plantillas(unittest.TestCase):
    def test_hay_una_plantilla_para_cada_aviso_pedido(self):
        for evento in ('GOVALE_SALDO', 'GOVALE_SALDO_BAJO', 'KILOMETROS',
                       'REPORTE_SERVICIO', 'OXXOGAS_TICKET'):
            self.assertIn(evento, nm.PLANTILLAS, f'falta la plantilla de {evento}')
            self.assertTrue(nm.PLANTILLAS[evento].strip())

    def test_ninguna_plantilla_trae_una_llave_que_no_conozcamos(self):
        import re
        for evento, p in nm.PLANTILLAS.items():
            llaves = set(re.findall(r'\{(\w+)\}', p))
            desconocidas = llaves - set(nm.CAMPOS_CONOCIDOS)
            self.assertEqual(desconocidas, set(),
                             f'{evento} usa campos que el productor no llena: {desconocidas}')

    def test_el_aviso_de_saldo_bajo_dice_que_hay_que_refactoriar(self):
        self.assertIn('refactor', nm.PLANTILLAS['GOVALE_SALDO_BAJO'].lower())


if __name__ == '__main__':
    unittest.main()
