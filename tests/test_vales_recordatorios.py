"""Recordatorios de ticket: hitos, dedupe y tope de edad.

La regla nueva: un vale con más de `govale_recordatorio_dias_max` días desde
`FechaSolicitud` ya no recibe recordatorios (default 15). Son tickets que no se
van a subir y el aviso solo molesta. El pendiente sigue contando en el
vigilante.

La base y Telegram se doblan: no se toca SQL Server ni se manda nada.

    python -m unittest tests.test_vales_recordatorios -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cron_vales_recordatorios as cr  # noqa: E402


class DbFalso:
    def __init__(self, config=None):
        self.config = dict(config or {})
        self.telegram = []

    def get_govale_config(self, clave):
        return self.config.get(clave)

    def set_govale_config(self, clave, valor):
        self.config[clave] = str(valor)
        return True

    def queue_telegram_alerta(self, id_evento, chat, texto):
        self.telegram.append((id_evento, chat, texto))
        return True


def _vale(dias, id_vale=12):
    return {
        'Id': id_vale,
        'IdSolicitante': 7,
        'FechaSolicitud': '2026-11-13',
        'Placa': 'ABC1234',
        'MontoUnit': 500,
        'MontoEmitido': 500,
        'IdValeGoVale': '856492448',
        'Solicitante': 'Héctor Peña',
        'dias': dias,
    }


class TopeDeEdad(unittest.TestCase):
    def _ciclo(self, dias, config=None):
        db = DbFalso(config)
        with mock.patch.object(cr, 'db', db), \
                mock.patch.object(cr, '_pendientes', return_value=[_vale(dias)]), \
                mock.patch.object(cr, '_chat_del_usuario', return_value=12345), \
                mock.patch.object(cr, 'wlog', lambda *a, **k: None):
            enviados = cr.ciclo()
        return enviados, db

    def test_vale_de_14_dias_recibe_recordatorio(self):
        enviados, db = self._ciclo(14)

        self.assertEqual(enviados, 1)
        self.assertEqual(len(db.telegram), 1)
        self.assertIn('856492448', db.telegram[0][2])

    def test_vale_de_16_dias_no_recibe(self):
        enviados, db = self._ciclo(16)

        self.assertEqual(enviados, 0)
        self.assertEqual(db.telegram, [])
        self.assertNotIn('vale_recordatorio_12', db.config)

    def test_el_tope_es_configurable(self):
        enviados, db = self._ciclo(
            16, {'govale_recordatorio_dias_max': '30'})

        self.assertEqual(enviados, 1)
        self.assertEqual(len(db.telegram), 1)

    def test_un_tope_ilegible_no_deja_mudo_al_worker(self):
        enviados, _ = self._ciclo(14, {'govale_recordatorio_dias_max': 'basura'})

        self.assertEqual(enviados, 1)

    def test_el_hito_de_7_dias_sigue_siendo_el_ultimo(self):
        enviados, db = self._ciclo(8)

        self.assertEqual(enviados, 1)
        self.assertEqual(db.config['vale_recordatorio_12'], '7')


if __name__ == '__main__':
    unittest.main()
