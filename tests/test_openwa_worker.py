"""
Pruebas del worker de WhatsApp (cron_sync_openwa).

El worker es donde se decide si un aviso se entrego o se reintenta, asi que lo
que se prueba es el reparto: texto solo, con foto, con PDF, y que un fallo de
OpenWA no borre el aviso de la cola (lo deja pendiente) ni lo abandone a los
dos intentos (a los tres se rinde y lo marca FALLADO, pero con el error escrito
para poder entender que paso).

La API y la base estan dobladas: esto corre sin OpenWA y sin base de datos.

    python3 -m unittest tests.test_openwa_worker
"""

import unittest

import cron_sync_openwa as worker


class ColaFalsa:
    def __init__(self, filas):
        self.filas = filas
        self.entregados, self.fallados = [], []

    def dequeue_openwa_pendientes(self, lote=10):
        return self.filas

    def marcar_openwa_enviado(self, id_queue):
        self.entregados.append(id_queue)
        return True

    def marcar_openwa_fallido(self, id_queue, error, intentos, max_intentos=3):
        self.fallados.append({'id': id_queue, 'error': error, 'intentos': intentos,
                              'max': max_intentos})
        return True


class ClienteFalso:
    """Anota que se intento mandar y devuelve lo que se le indique."""

    def __init__(self, ok=True, detalle='ok', fallan=()):
        self.ok, self.detalle = ok, detalle
        self.fallan = set(fallan)
        self.enviados = []

    def _usar(self, metodo, chat, *args):
        self.enviados.append({'metodo': metodo, 'chat': chat, 'args': args})
        if chat in self.fallan:
            return False, self.detalle
        return self.ok, self.detalle

    def enviar_texto(self, chat, texto, **kw):
        return self._usar('texto', chat, texto)

    def enviar_documento(self, chat, datos, nombre, caption=None, **kw):
        return self._usar('documento', chat, datos, nombre, caption)

    def enviar_imagen(self, chat, datos, nombre, caption=None, **kw):
        return self._usar('imagen', chat, datos, nombre, caption)


def _fila(**kw):
    base = {'Id': 1, 'IdEvento': 'KILOMETROS', 'ChatId': '5218123211516@c.us',
            'Texto': 'hola', 'Adjunto': None, 'AdjuntoNombre': None,
            'AdjuntoTipo': None, 'Intentos': 0}
    base.update(kw)
    return base


def _con(cola, cliente):
    db_prev, ow_prev = worker.db, worker.ow
    worker.db, worker.ow = cola, cliente
    return db_prev, ow_prev


class Entregar(unittest.TestCase):
    def test_un_aviso_sin_adjunto_se_manda_como_texto(self):
        cola, cliente = ColaFalsa([_fila()]), ClienteFalso()
        prev = _con(cola, cliente)
        try:
            enviados = worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(enviados, 1)
        self.assertEqual(cliente.enviados[0]['metodo'], 'texto')
        self.assertEqual(cliente.enviados[0]['args'][0], 'hola')
        self.assertEqual(cola.entregados, [1])

    def test_el_reporte_firmado_se_manda_como_documento(self):
        cola = ColaFalsa([_fila(Adjunto=b'%PDF-1.4', AdjuntoNombre='REP-1.pdf',
                                 AdjuntoTipo='documento', Texto='REP-1 firmado')])
        cliente = ClienteFalso()
        prev = _con(cola, cliente)
        try:
            worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        e = cliente.enviados[0]
        self.assertEqual(e['metodo'], 'documento')
        self.assertEqual(e['args'][1], 'REP-1.pdf')
        # El texto va como caption, no como un segundo mensaje.
        self.assertEqual(e['args'][2], 'REP-1 firmado')

    def test_el_ticket_se_manda_como_imagen(self):
        cola = ColaFalsa([_fila(Adjunto=b'\xff\xd8\xff', AdjuntoNombre='foto.jpg',
                                 AdjuntoTipo='imagen')])
        cliente = ClienteFalso()
        prev = _con(cola, cliente)
        try:
            worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cliente.enviados[0]['metodo'], 'imagen')

    def test_un_adjunto_de_tipo_desconocido_cae_a_texto_y_no_se_pierde(self):
        # Si un tipo llega raro, el texto sale igual. Perder el aviso por un
        # campo de mas seria peor que mandarlo sin imagen.
        cola = ColaFalsa([_fila(Adjunto=b'x', AdjuntoTipo='raro')])
        cliente = ClienteFalso()
        prev = _con(cola, cliente)
        try:
            worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cliente.enviados[0]['metodo'], 'texto')

    def test_entrega_varios_del_lote(self):
        filas = [_fila(Id=i) for i in (1, 2, 3)]
        cola, cliente = ColaFalsa(filas), ClienteFalso()
        prev = _con(cola, cliente)
        try:
            self.assertEqual(worker.process_queue(), 3)
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cola.entregados, [1, 2, 3])


class Fallos(unittest.TestCase):
    def test_openwa_caido_deja_el_aviso_pendiente_para_reintentar(self):
        cola = ColaFalsa([_fila(Intentos=0)])
        cliente = ClienteFalso(ok=False, detalle='HTTP 503: gateway caido')
        prev = _con(cola, cliente)
        try:
            self.assertEqual(worker.process_queue(), 0)
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cola.entregados, [])
        self.assertEqual(cola.fallados[0]['intentos'], 1)
        self.assertIn('503', cola.fallados[0]['error'])

    def test_al_tercer_intento_se_rinde_y_lo_marca_fallado(self):
        cola = ColaFalsa([_fila(Intentos=2)])
        cliente = ClienteFalso(ok=False, detalle='HTTP 401: unauthorized')
        prev = _con(cola, cliente)
        try:
            worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cola.fallados[0]['intentos'], 3)
        self.assertEqual(cola.fallados[0]['max'], 3)

    def test_un_fallo_no_detiene_los_demas_del_lote(self):
        # Si a un destinatario le falla, los demas tienen que salir igual: de lo
        # contrario un solo numero malo bloquea el aviso entero.
        filas = [_fila(Id=1, ChatId='521@c.us'), _fila(Id=2, ChatId='522@c.us')]
        cola = ColaFalsa(filas)
        cliente = ClienteFalso(detalle='HTTP 503: caido', fallan={'521@c.us'})
        prev = _con(cola, cliente)
        try:
            enviados = worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(enviados, 1)
        self.assertEqual(cola.fallados[0]['id'], 1)
        self.assertEqual(cola.entregados, [2])

    def test_el_worker_guarda_el_error_tal_cual_lo_dio_el_cliente(self):
        # Quitar la API key del mensaje de error es trabajo del cliente
        # (openwa_client._limpiar_detalle, con su propia prueba). El worker solo
        # lo copia a la cola, sin reescribirlo ni inventarse otro texto.
        cola = ColaFalsa([_fila()])
        cliente = ClienteFalso(ok=False, detalle='HTTP 404: session not found')
        prev = _con(cola, cliente)
        try:
            worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        self.assertEqual(cola.fallados[0]['error'], 'HTTP 404: session not found')

    def test_el_worker_no_imprime_la_key_al_registrar_un_fallo(self):
        # El log del worker es lo primero que se lee cuando algo sale mal, asi
        # que no debe poder imprimir un secreto.
        cola = ColaFalsa([_fila()])
        cliente = ClienteFalso(ok=False, detalle='HTTP 401: unauthorized')
        import io
        import contextlib
        prev = _con(cola, cliente)
        captura = io.StringIO()
        try:
            with contextlib.redirect_stdout(captura):
                worker.process_queue()
        finally:
            worker.db, worker.ow = prev
        log = captura.getvalue()
        self.assertIn('401', log)
        # La key real (si la hay) no puede aparecer en el log.
        import openwa_client
        key = openwa_client.obtener_config()['api_key']
        self.assertFalse(key and key in log)


if __name__ == '__main__':
    unittest.main()
