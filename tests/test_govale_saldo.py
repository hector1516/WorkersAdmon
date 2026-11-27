"""Aviso de saldo bajo de vales: umbral, transacción y dedupe.

Lo que se fija aquí (y por qué):

  · 2001 no avisa, 1999 sí: el umbral es estricto, no "menor o igual";
  · el mismo saldo bajo no se repite si no hubo transacción nueva;
  · un ticket nuevo sí vuelve a avisar: el aviso va por transacción;
  · el texto trae CLABE y referencia en negritas, que es lo que se copia;
  · con WhatsApp apagado no se encola ni se marca como avisado;
  · nunca más de un aviso cada 30 minutos.

La base y los canales se doblan en memoria; no se toca SQL Server ni OpenWA.

    python -m unittest tests.test_govale_saldo -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import canales  # noqa: E402
import cron_sync_govale_vouchers as cg  # noqa: E402
import notif_messages as nm  # noqa: E402
import openwa_alerts as oa  # noqa: E402

AHORA = datetime(2026, 11, 27, 10, 0, 0)

# `wlog` escribe en la BD real; en estas pruebas solo estorba (y cada intento
# sin credenciales se lleva 30 s de reintentos).
_WLOG_ORIGINAL = None


def setUpModule():
    global _WLOG_ORIGINAL
    _WLOG_ORIGINAL = cg.wlog
    cg.wlog = lambda *a, **k: None


def tearDownModule():
    cg.wlog = _WLOG_ORIGINAL


class DbFalso:
    """HUB_Config en memoria, con las claves que toca el worker."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.guardados = []

    def get_govale_config(self, clave):
        return self.config.get(clave)

    def set_govale_config(self, clave, valor):
        self.config[clave] = str(valor)
        self.guardados.append((clave, str(valor)))
        return True

    # Para el dispatcher de WhatsApp.
    def get_openwa_evento(self, id_evento):
        return self.evento

    def queue_openwa_alerta(self, id_evento, chat, texto, adjunto=None,
                            nombre=None, tipo=None):
        self.encolados.append({'evento': id_evento, 'chat': chat, 'texto': texto})
        return True


def _enviar_falso(llamadas, resultado=1):
    def enviar(saldo, umbral, fecha):
        llamadas.append((saldo, umbral, fecha))
        return resultado
    return enviar


class Umbral(unittest.TestCase):
    def _db(self, config=None):
        return DbFalso({'govale_umbral': '2000', **(config or {})})

    def test_saldo_2001_no_avisa(self):
        db = self._db()
        llamadas = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V0|T9'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo',
                                  _enviar_falso(llamadas)):
            n = cg.avisar_saldo_bajo(2001, ahora=AHORA)

        self.assertEqual(n, 0)
        self.assertEqual(llamadas, [])
        self.assertNotIn('govale_aviso_saldo_ticket', db.config)

    def test_saldo_1999_avisa(self):
        db = self._db()
        llamadas = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V0|T9'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo',
                                  _enviar_falso(llamadas)):
            n = cg.avisar_saldo_bajo(1999, ahora=AHORA)

        self.assertEqual(n, 1)
        self.assertEqual(llamadas[0][:2], (1999, 2000.0))
        self.assertEqual(db.config['govale_aviso_saldo_ticket'], 'V0|T9')
        self.assertEqual(db.config['govale_aviso_saldo_fecha'],
                         '2026-11-27 10:00:00')
        self.assertEqual(db.config['govale_aviso_saldo_estado'], 'BAJO')

    def test_umbral_se_puede_cambiar(self):
        db = self._db({'govale_umbral': '1500'})
        llamadas = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V0|T9'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo',
                                  _enviar_falso(llamadas)):
            self.assertEqual(cg.avisar_saldo_bajo(1600, ahora=AHORA), 0)
            self.assertEqual(cg.avisar_saldo_bajo(1400, ahora=AHORA), 1)

        self.assertEqual(len(llamadas), 1)


class Dedupe(unittest.TestCase):
    def _db(self, **extra):
        return DbFalso({'govale_umbral': '2000', **extra})

    def test_1999_otra_vez_sin_transaccion_nueva_no_avisa(self):
        db = self._db()
        llamadas = []
        enviar = _enviar_falso(llamadas)
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V0|T9'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo', enviar):
            primero = cg.avisar_saldo_bajo(1999, ahora=AHORA)
            segundo = cg.avisar_saldo_bajo(1999,
                                           ahora=AHORA + timedelta(hours=2))

        self.assertEqual(primero, 1)
        self.assertEqual(segundo, 0)
        self.assertEqual(len(llamadas), 1)

    def test_no_avisa_dos_veces_el_mismo_dia(self):
        db = self._db(govale_aviso_saldo_estado='BAJO',
                      govale_aviso_saldo_ticket='V9',
                      govale_aviso_saldo_fecha='2026-11-27 07:00:00')
        llamadas = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V10'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo',
                                  _enviar_falso(llamadas)):
            n = cg.avisar_saldo_bajo(1999, ahora=AHORA)

        self.assertEqual(n, 0)
        self.assertEqual(len(llamadas), 0)

    def test_tope_diario_aunque_pase_media_hora(self):
        db = self._db(govale_aviso_saldo_estado='BAJO',
                      govale_aviso_saldo_ticket='V9',
                      govale_aviso_saldo_fecha='2026-11-27 08:00:00')
        llamadas = []
        enviar = _enviar_falso(llamadas)
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V10'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo', enviar):
            manana = cg.avisar_saldo_bajo(1999, ahora=AHORA + timedelta(days=1))

        self.assertEqual(manana, 1)
        self.assertEqual(len(llamadas), 1)

    def test_cruce_sin_transaccion_no_avisa(self):
        db = self._db(govale_aviso_saldo_estado='OK',
                      govale_aviso_saldo_ticket='V9',
                      govale_aviso_saldo_fecha='2026-11-26 07:00:00')
        llamadas = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V9'), \
                mock.patch.object(cg, '_enviar_aviso_saldo_bajo',
                                  _enviar_falso(llamadas)):
            self.assertEqual(cg.avisar_saldo_bajo(2500, ahora=AHORA), 0)
            self.assertEqual(cg.avisar_saldo_bajo(1999, ahora=AHORA), 0)

        self.assertEqual(len(llamadas), 0)


class Canales(unittest.TestCase):
    def test_whatsapp_apagado_no_encola_ni_marca(self):
        db = DbFalso({'govale_umbral': '2000'})
        encolados = []
        with mock.patch.object(cg, 'db', db), \
                mock.patch.object(cg, '_marca_transacciones', lambda: 'V0|T9'), \
                mock.patch.object(canales, 'activo', lambda canal: False), \
                mock.patch.object(oa, 'alertar_saldo_govale',
                                  lambda *a, **k: encolados.append(a) or 1):
            n = cg.avisar_saldo_bajo(1999, ahora=AHORA)

        self.assertEqual(n, 0)
        self.assertEqual(encolados, [])
        self.assertNotIn('govale_aviso_saldo_fecha', db.config)


class Texto(unittest.TestCase):
    def test_trae_clabe_y_referencia_en_negritas(self):
        db = DbFalso()
        db.evento = {
            'IdEvento': 'GOVALE_SALDO_BAJO',
            'PlantillaMensaje': nm.PLANTILLAS['GOVALE_SALDO_BAJO'],
            'AdjuntarArchivo': 0,
            'Telefonos': '5218123211516',
            'Activo': 1,
        }
        db.encolados = []
        with mock.patch.object(oa, 'db', db):
            n = oa.alertar_saldo_govale(1850, '27/11/2026 10:00', 2000,
                                        solo_si_bajo=True)

        self.assertEqual(n, 1)
        texto = db.encolados[0]['texto']
        self.assertEqual(db.encolados[0]['evento'], 'GOVALE_SALDO_BAJO')
        self.assertIn('quedan $1,850.00', texto)
        self.assertIn('CLABE: *012580001208095515*', texto)
        self.assertIn('*VD003526*', texto)
        self.assertIn('BBVA Bancomer', texto)
        self.assertIn('SGM950714DC2', texto)
        self.assertIn('\n\nPara recargar', texto)
        self.assertNotIn('{', texto)

    def test_el_monto_lleva_separador_de_miles_y_dos_decimales(self):
        texto = nm.aplicar_plantilla(nm.PLANTILLAS['GOVALE_SALDO_BAJO'],
                                     nm.datos_saldo(1850, 'x'))
        self.assertIn('$1,850.00', texto)


if __name__ == '__main__':
    unittest.main()
