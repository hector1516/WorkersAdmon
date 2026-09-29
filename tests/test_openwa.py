"""
Pruebas del cliente de OpenWA (gateway de WhatsApp).

El modulo tiene dos cosas muy distintas y aqui se prueban por separado:

1. Lo **puro**: normalizar telefonos, recortar texto, armar los payloads. Sin red
   ni base de datos, corre en un segundo. Aqui esta el riesgo real: un numero mal
   normalizado manda el aviso al chat equivocado (o a nadie) y no se nota hasta
   que alguien pregunta "a quien le llego?".

2. Lo que **habla con OpenWA**: siempre contra una sesion HTTP falsa, para
   comprobar el header, el cuerpo, y sobre todo que la API key nunca se cuele en
   un mensaje de error (esa es la parte que si se puede filtrar a un log).

    python3 -m unittest tests.test_openwa
"""

import base64
import unittest

import openwa_client as ow

# Numero real de pruebas del handoff (Hector, la sesion `principal` de OpenWA).
NUMERO_REAL = '5218123211516'


# ── Normalizacion de telefonos ───────────────────────────────────────────────

class NormalizarChatId(unittest.TestCase):
    def test_un_numero_internacional_ya_trae_pais(self):
        self.assertEqual(ow.normalizar_chat_id(NUMERO_REAL), f'{NUMERO_REAL}@c.us')

    def test_diez_digitos_se_asume_mexico(self):
        # Es lo que va a pegar la gente: "8123211516".
        self.assertEqual(ow.normalizar_chat_id('8123211516'), '528123211516@c.us')

    def test_once_digitos_que_empiezan_con_uno_se_rechazan_por_ambiguos(self):
        # "15512345678" (formato antiguo de Mexico) y "+1 415 555 2671" (EUA)
        # son el mismo numero de digitos. Adivinar produce un numero con formato
        # valido que no es de nadie, y el aviso se pierde en silencio; rechazarlo
        # lo muestra en la interfaz para que se escriba el codigo de pais.
        self.assertIsNone(ow.normalizar_chat_id('15512345678'))
        self.assertIsNone(ow.normalizar_chat_id('14155552671'))

    def test_acepta_espacios_guiones_y_parentesis_al_pegarlo_de_un_pasto(self):
        self.assertEqual(ow.normalizar_chat_id('+52 1 (55) 1234-5678'), '5215512345678@c.us')

    def test_si_ya_venir_con_arroba_no_lo_roto(self):
        self.assertEqual(ow.normalizar_chat_id(f'{NUMERO_REAL}@c.us'), f'{NUMERO_REAL}@c.us')

    def test_un_grupo_no_es_un_telefono_y_no_se_trata_como_tal(self):
        # Los grupos se eligen a mano en otra parte; aqui no hay que inventarlos.
        self.assertIsNone(ow.normalizar_chat_id('1234567890-123456@g.us'))

    def test_lo_que_no_es_numero_se_rechaza_en_silencio(self):
        for malo in ('', '   ', 'abc', '123', '52', 'llamar a Hector', '5521@'):
            self.assertIsNone(ow.normalizar_chat_id(malo), f'debio rechazar: {malo!r}')

    def test_un_numero_de_otro_pais_con_el_codigo_completo_se_respeta(self):
        # 12+ digitos ya traen pais: no se toca el 52.
        self.assertEqual(ow.normalizar_chat_id('528123211516'), '528123211516@c.us')


class NormalizarListaDeTelefonos(unittest.TestCase):
    """El usuario pega los numeros como un solo texto, separados por comas."""

    def test_una_linea_de_comas_es_varios_numeros(self):
        ok, malos = ow.normalizar_chat_ids('5218123211516, 5512345678')
        self.assertEqual(ok, ['5218123211516@c.us', '525512345678@c.us'])
        self.assertEqual(malos, [])

    def test_acepta_comas_punto_y_coma_nuevas_lineas_y_espacios(self):
        ok, _ = ow.normalizar_chat_ids('5218123211516; 5512345678\n5512345679   ,')
        self.assertEqual(ok, ['5218123211516@c.us', '525512345678@c.us', '525512345679@c.us'])

    def test_el_texto_vacio_no_es_un_error_solo_no_hay_nadie(self):
        self.assertEqual(ow.normalizar_chat_ids(''), ([], []))
        self.assertEqual(ow.normalizar_chat_ids('   \n '), ([], []))

    def test_reporta_cuales_entradas_no_pudo_usar(self):
        # Importa: si alguien pego "8123211516, 555" hay que decirle cual fallo,
        # no mandarle medio aviso y dejarle pensar que funciono.
        ok, malos = ow.normalizar_chat_ids('8123211516, hector, 5512345678')
        self.assertEqual(ok, ['528123211516@c.us', '525512345678@c.us'])
        self.assertEqual(malos, ['hector'])

    def test_no_repite_el_mismo_numero_dos_veces(self):
        ok, _ = ow.normalizar_chat_ids(f'{NUMERO_REAL}, {NUMERO_REAL}, 8123211516')
        self.assertEqual(ok, ['5218123211516@c.us', '528123211516@c.us'])

    def test_ordena_y_deduplica_mas_alla_del_texto(self):
        ok, _ = ow.normalizar_chat_ids(f'5512345678, {NUMERO_REAL}, 5512345678')
        self.assertEqual(len(ok), 2)


# ── Secretos y limites ───────────────────────────────────────────────────────

class Enmascarar(unittest.TestCase):
    def test_no_deja_ver_la_key_entera(self):
        m = ow.enmascarar('sk-live-abcdefghijklmnop')
        self.assertNotIn('abcdefghijklmnop', m)
        self.assertIn('sk-l', m)            # 4 del inicio: se reconoce de que es
        self.assertTrue(m.endswith('mnop')) # 4 del final: se confirma cual es

    def test_una_key_corta_se_tapa_completamente(self):
        m = ow.enmascarar('123')
        self.assertNotIn('123', m)

    def test_vacio_sigue_vacio(self):
        self.assertEqual(ow.enmascarar(''), '')


class RecortarTexto(unittest.TestCase):
    def test_whatsapp_corta_a_4096_y_avisa_que_lo_corto(self):
        largo = 'a' * 5000
        out = ow.recortar_texto(largo)
        self.assertTrue(len(out) <= 4096)
        self.assertIn('recortado', out)

    def test_un_texto_normal_no_se_toca(self):
        self.assertEqual(ow.recortar_texto('Hola'), 'Hola')


# ── Payloads (lo que el handoff pidio leer del Swagger, no adivinar) ─────────

class Payloads(unittest.TestCase):
    def test_texto_lleva_chat_id_y_texto(self):
        p = ow.payload_texto('521@c.us', 'Hola')
        self.assertEqual(p, {'chatId': '521@c.us', 'text': 'Hola'})

    def test_el_documento_va_en_base64_con_su_mimetype(self):
        pdf = b'%PDF-1.7 hola'
        p = ow.payload_archivo('521@c.us', pdf, 'application/pdf', 'Folio-12.pdf', 'Reporte')
        self.assertEqual(p['chatId'], '521@c.us')
        self.assertEqual(base64.b64decode(p['base64']), pdf)
        self.assertEqual(p['mimetype'], 'application/pdf')  # sin esto OpenWA lo rechaza
        self.assertEqual(p['filename'], 'Folio-12.pdf')
        self.assertEqual(p['caption'], 'Reporte')
        self.assertNotIn('url', p)   # base64, no URL: el archivo nunca sale del server

    def test_sin_caption_no_manda_clave_vacia(self):
        p = ow.payload_archivo('521@c.us', b'x', 'image/jpeg', 'foto.jpg')
        self.assertNotIn('caption', p)


# ── Lo que habla con OpenWA ──────────────────────────────────────────────────

class SesionFalsa:
    """Doble de `requests`: registra la llamada y devuelve lo que se le pida."""

    def __init__(self, status=200, cuerpo=None, excepcion=None):
        self.status, self.cuerpo, self.excepcion = status, cuerpo, excepcion
        self.llamadas = []

    def _responder(self, metodo, url, **kw):
        self.llamadas.append({'metodo': metodo, 'url': url, **kw})
        if self.excepcion:
            raise self.excepcion
        r = type('R', (), {})()
        r.status_code = self.status
        r.text = self.cuerpo if self.cuerpo is not None else '{"ok":true}'
        return r

    def post(self, url, **kw):
        return self._responder('POST', url, **kw)

    def get(self, url, **kw):
        return self._responder('GET', url, **kw)


class Health(unittest.TestCase):
    def test_sin_key_responde_igual_porque_el_health_es_publico(self):
        s = SesionFalsa(cuerpo='{"status":"ok"}')
        ok, detalle = ow.health(base='http://openwa:2785', clave='', sesion=s)
        self.assertTrue(ok)
        self.assertIn('ok', detalle)

    def test_openwa_caido_no_es_excepcion_sino_fallo_reportado(self):
        s = SesionFalsa(excepcion=OSError('connection refused'))
        ok, detalle = ow.health(base='http://openwa:2785', clave='', sesion=s)
        self.assertFalse(ok)
        self.assertTrue(detalle)


class EnviarTexto(unittest.TestCase):
    def test_manda_la_key_en_el_header_y_no_en_el_cuerpo(self):
        s = SesionFalsa()
        ok, _ = ow.enviar_texto('521@c.us', 'Hola', base='http://openwa:2785',
                                clave='sk-secreta-9999', session_id='sid-1', sesion=s)
        self.assertTrue(ok)
        c = s.llamadas[0]
        self.assertEqual(c['headers']['X-API-Key'], 'sk-secreta-9999')
        self.assertNotIn('sk-secreta-9999', str(c['json']))
        self.assertIn('/api/sessions/sid-1/messages/send-text', c['url'])

    def test_sin_key_no_dispara_la_peticion(self):
        s = SesionFalsa()
        ok, detalle = ow.enviar_texto('521@c.us', 'Hola', base='http://openwa:2785',
                                      clave='', session_id='sid-1', sesion=s)
        self.assertFalse(ok)
        self.assertEqual(s.llamadas, [])   # ni se molesta: no hay a quienessor
        self.assertIn('key', detalle.lower())

    def test_texto_muy_largo_se_recorta_antes_de_mandarlo(self):
        s = SesionFalsa()
        ow.enviar_texto('521@c.us', 'a' * 9000, base='http://openwa:2785',
                        clave='k', session_id='sid-1', sesion=s)
        enviado = s.llamadas[0]['json']['text']
        self.assertLessEqual(len(enviado), 4096)

    def test_un_404_de_openwa_se_reporta_con_su_cuerpo(self):
        s = SesionFalsa(status=404, cuerpo='{"message":"session not found"}')
        ok, detalle = ow.enviar_texto('521@c.us', 'Hola', base='http://openwa:2785',
                                      clave='k', session_id='sid-1', sesion=s)
        self.assertFalse(ok)
        self.assertIn('404', detalle)
        self.assertIn('session not found', detalle)

    def test_la_key_jamas_aparece_en_el_error(self):
        s = SesionFalsa(status=401, cuerpo='{"message":"unauthorized"}')
        ok, detalle = ow.enviar_texto('521@c.us', 'Hola', base='http://openwa:2785',
                                      clave='sk-muy-secreta-1234', session_id='s', sesion=s)
        self.assertFalse(ok)
        self.assertNotIn('sk-muy-secreta-1234', detalle)

    def test_excepcion_de_red_tampoco_arrastra_la_key(self):
        s = SesionFalsa(excepcion=TimeoutError('timed out'))
        ok, detalle = ow.enviar_texto('521@c.us', 'Hola', base='http://openwa:2785',
                                      clave='sk-muy-secreta-1234', session_id='s', sesion=s)
        self.assertFalse(ok)
        self.assertNotIn('sk-muy-secreta-1234', detalle)


class EnviarAdjuntos(unittest.TestCase):
    def test_el_pdf_va_a_send_document(self):
        s = SesionFalsa()
        ok, _ = ow.enviar_documento('521@c.us', b'%PDF-1.4', 'reporte.pdf', 'Reporte',
                                    base='http://openwa:2785', clave='k',
                                    session_id='s1', sesion=s)
        self.assertTrue(ok)
        self.assertIn('/messages/send-document', s.llamadas[0]['url'])
        self.assertEqual(s.llamadas[0]['json']['mimetype'], 'application/pdf')

    def test_la_foto_va_a_send_image(self):
        s = SesionFalsa()
        ok, _ = ow.enviar_imagen('521@c.us', b'\xff\xd8\xff', 'ticket.jpg', 'Ticket',
                                 base='http://openwa:2785', clave='k',
                                 session_id='s1', sesion=s)
        self.assertTrue(ok)
        self.assertIn('/messages/send-image', s.llamadas[0]['url'])
        self.assertEqual(s.llamadas[0]['json']['mimetype'], 'image/jpeg')

    def test_el_archivo_vacio_no_se_manda(self):
        s = SesionFalsa()
        ok, detalle = ow.enviar_documento('521@c.us', b'', 'x.pdf', None,
                                          base='http://openwa:2785', clave='k',
                                          session_id='s1', sesion=s)
        self.assertFalse(ok)
        self.assertEqual(s.llamadas, [])
        self.assertIn('vac', detalle.lower())


if __name__ == '__main__':
    unittest.main()
