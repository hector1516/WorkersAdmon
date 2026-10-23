"""Pruebas del worker de Telegram (sin BD y sin red).

Lo que se vigila aquí es el ritmo y la recuperación, que es justo lo que falló:
el worker viejo tomaba diez mensajes, los soltaba seguidos y no distinguía un
429 de un error definitivo. Estas pruebas fijan ese contrato sin levantar SQL
Server ni hablar con api.telegram.org.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cron_sync_telegram as cw


MENSAJE = {
    "Id": 7,
    "IdEvento": 1,
    "ChatId": 12345,
    "Texto": "Aviso de prueba",
    "Adjunto": None,
    "AdjuntoNombre": None,
    "Intentos": 0,
}


class RitmoTest(unittest.TestCase):
    """Cómo se reclama y cuánto se espera entre envíos."""

    def test_sin_cola_no_llama_a_telegram(self):
        with mock.patch.object(cw.db, "tomar_telegram_pendientes",
                               return_value=[]) as tomar, \
             mock.patch.object(cw.db, "send_telegram_message") as enviar:
            self.assertEqual(cw.process_queue(), (0, 0))
        tomar.assert_called_once_with(limite=1, reintento_seg=cw.REINTENTO_SEG)
        enviar.assert_not_called()

    def test_reclama_un_mensaje_y_lo_marca_enviado(self):
        with mock.patch.object(cw.db, "tomar_telegram_pendientes",
                               return_value=[dict(MENSAJE)]) as tomar, \
             mock.patch.object(cw.db, "send_telegram_message",
                               return_value=(True, "ok (42)")) as enviar, \
             mock.patch.object(cw.db, "marcar_telegram_enviado") as enviado, \
             mock.patch.object(cw.db, "marcar_telegram_fallado") as fallado:
            self.assertEqual(cw.process_queue(), (1, 1))
        tomar.assert_called_once_with(limite=1, reintento_seg=cw.REINTENTO_SEG)
        enviar.assert_called_once_with(12345, "Aviso de prueba", None, None)
        enviado.assert_called_once_with(7, "ok (42)")
        fallado.assert_not_called()

    def test_entre_dos_mensajes_hay_pausa(self):
        # Si algún día se sube MAX_POR_CICLO, no puede volver la ráfaga: la
        # pausa se aplica entre un mensaje y el siguiente, no después del lote.
        mensajes = [dict(MENSAJE, Id=1), dict(MENSAJE, Id=2)]
        original_max, original_pausa = cw.MAX_POR_CICLO, cw.SEND_DELAY
        cw.MAX_POR_CICLO, cw.SEND_DELAY = 2, 1.5
        try:
            with mock.patch.object(cw.db, "tomar_telegram_pendientes",
                                   return_value=mensajes), \
                 mock.patch.object(cw.db, "send_telegram_message",
                                   return_value=(True, "ok")), \
                 mock.patch.object(cw.db, "marcar_telegram_enviado"), \
                 mock.patch.object(cw.time, "sleep") as dormir:
                self.assertEqual(cw.process_queue(), (2, 2))
            dormir.assert_called_once_with(1.5)
        finally:
            cw.MAX_POR_CICLO, cw.SEND_DELAY = original_max, original_pausa


class ErroresTest(unittest.TestCase):
    """429, reintentos y el estado que queda en la cola."""

    def test_detecta_rate_limit(self):
        self.assertTrue(cw._es_rate_limit("API error: Too Many Requests: retry after 8"))
        self.assertTrue(cw._es_rate_limit("retry after 8"))
        self.assertFalse(cw._es_rate_limit("API error: chat not found"))

    def test_retry_after_extrae_y_acota(self):
        self.assertEqual(cw._retry_after("Too Many Requests: retry after 29"), 29)
        self.assertEqual(cw._retry_after("retry after 0"), 1)
        self.assertEqual(cw._retry_after("retry after 999"), 60)
        self.assertEqual(cw._retry_after("sin número"), 30)
        self.assertEqual(cw._retry_after("sin número", por_defecto=12), 12)

    def test_429_no_consume_intento(self):
        # El mensaje no está mal: solo hay que esperar. Si se contara como
        # intento, tres 429 seguidos lo marcarían FALLADO sin haberlo intentado.
        mensaje = dict(MENSAJE, Intentos=1)
        with mock.patch.object(cw.db, "tomar_telegram_pendientes",
                               return_value=[mensaje]), \
             mock.patch.object(cw.db, "send_telegram_message",
                               return_value=(False, "Too Many Requests: retry after 7")), \
             mock.patch.object(cw.db, "marcar_telegram_fallado") as fallado, \
             mock.patch.object(cw.time, "sleep") as dormir:
            self.assertEqual(cw.process_queue(), (0, 1))
        fallado.assert_called_once_with(
            7, "Too Many Requests: retry after 7", 1,
            max_intentos=cw.MAX_INTENTOS)
        dormir.assert_called_once_with(7)

    def test_error_normal_si_consume_intento(self):
        mensaje = dict(MENSAJE, Intentos=1)
        with mock.patch.object(cw.db, "tomar_telegram_pendientes",
                               return_value=[mensaje]), \
             mock.patch.object(cw.db, "send_telegram_message",
                               return_value=(False, "API error: chat not found")), \
             mock.patch.object(cw.db, "marcar_telegram_fallado") as fallado, \
             mock.patch.object(cw.time, "sleep") as dormir:
            self.assertEqual(cw.process_queue(), (0, 1))
        fallado.assert_called_once_with(
            7, "API error: chat not found", 2, max_intentos=cw.MAX_INTENTOS)
        dormir.assert_not_called()

    def test_un_fallo_de_bd_al_marcar_no_tumba_el_ciclo(self):
        # Si marcar falla, el mensaje queda ENVIANDO y la recuperación lo
        # devuelve; lo que no puede pasar es que el worker muera por eso.
        with mock.patch.object(cw.db, "marcar_telegram_enviado",
                               side_effect=RuntimeError("sin conexión")), \
             mock.patch.object(cw.db, "send_telegram_message",
                               return_value=(True, "ok")):
            cw._marcar_enviado(7, "ok")

    def test_recuperar_usa_el_maximo_configurado(self):
        with mock.patch.object(cw.db, "recuperar_telegram_atascados",
                               return_value=2) as recuperar:
            self.assertEqual(cw._recuperar_atascados(10), 2)
        recuperar.assert_called_once_with(minutos=10,
                                          max_intentos=cw.MAX_INTENTOS)

    def test_recuperar_no_tumba_el_ciclo_si_la_bd_falla(self):
        with mock.patch.object(cw.db, "recuperar_telegram_atascados",
                               side_effect=RuntimeError("sin conexión")):
            self.assertEqual(cw._recuperar_atascados(10), 0)


if __name__ == "__main__":
    unittest.main()
