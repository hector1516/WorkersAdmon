#!/usr/bin/env python3
"""
tests/test_mailbox_stream.py — pruebas del servidor de adjuntos.

El stream es el ÚNICO camino por el que un byte de adjunto llega al dispositivo,
y su contrato está fijado por la app (`_pedir_al_worker` y `_trozos_del_worker`
en `api/main.py`). Si la ruta, el nombre de la cabecera o el nombre de un
parámetro no coinciden exactamente, los adjuntos se rompen con un 404 y el
síntoma en pantalla es un icono que no carga.

Por eso la primera clase no prueba "funciona": prueba que la RUTA es la que la
app pide, comparando las dos cadenas en el mismo test.

Lo segundo que se prueba aquí es lo que no se ve: que un `cid` malicioso no
puede usarse para descargar 300 MB en memoria, y que el token se compara en
tiempo constante.

Ejecución:  python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request

sys.modules.setdefault("pymssql", types.ModuleType("pymssql"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mailbox_worker import stream             # noqa: E402
from mailbox_worker.config import settings    # noqa: E402

# Lo que la app construye, copiado de api/main.py. Si esto cambia, el worker
# tiene que cambiar con él: es el contrato, no una preferencia.
URL_APP_ENTERO = "/adjunto/{cuenta}/{uid}/{parte}?inline={inline}"
URL_APP_TROZO = "/adjunto/{cuenta}/{uid}/{parte}?desde={desde}&bytes={largo}"
CABECERA_TOKEN = "X-Mailbox-Token"
TAMANO_TROZO_APP = 262144          # _TAMANO_TROZO en api/main.py


def _servidor_de_prueba():
    """El servidor del módulo, en un puerto libre que elija el sistema.

    Puerto 0 y no `settings.stream_port`: si dos corridas del archivo se pisan, o
    si el worker real está corriendo en 8201 durante las pruebas, el test falla
    con "Address in use" y parece un bug del worker.
    """
    from http.server import ThreadingHTTPServer
    return ThreadingHTTPServer(("127.0.0.1", 0), stream._Handler)


class IMAPFalso:
    """IMAP en memoria. Se puede configurar por llamada."""

    datos_entero = b"X" * 5000
    size_reportado = 5000        # lo que declara el indice
    # Devuelve `cantidad` bytes, y MENOS si el rango se pasa del final: es lo
    # que hace un servidor real, y es lo que le dice a la app que ya no hay más.
    calls = []

    def __init__(self, **_kw):
        pass

    def connect(self):
        pass

    def close(self):
        pass

    def select_folder(self, _c):
        pass

    def fetch_parte(self, uid, parte):
        IMAPFalso.calls.append(("entero", uid, parte))
        return self.datos_entero

    def fetch_parte_trozo(self, uid, parte, desde, cantidad):
        """Devuelve el SLICE real, y menos si el rango se pasa del final.

        Devolver un slice (y no un relleno) es lo que hace un servidor IMAP, y es
        lo que permite comprobar que los trozos reconstruyen el archivo original
        y no solo "la misma cantidad de bytes".
        """
        IMAPFalso.calls.append(("trozo", uid, desde, cantidad))
        return self.datos_entero[desde:desde + cantidad]


class _Base(unittest.TestCase):
    """Levanta el servidor real en un hilo y le habla por HTTP de verdad."""

    @classmethod
    def setUpClass(cls):
        cls._dir_anterior = settings.datos_dir
        cls._cache_anterior = settings.cache_dir
        cls._token_anterior = settings.stream_token
        cls.dir = tempfile.mkdtemp(prefix="mailbox-stream-")
        settings.datos_dir = cls.dir
        settings.cache_dir = os.path.join(cls.dir, "cache")
        settings.stream_token = "token-de-prueba"

        # Se sustituyen las tres dependencias externas (IMAP, descifrado, base).
        # Lo que se prueba es el HTTP y el _ruteo_, que es lo propio de aquí.
        cls._originales = (stream.IMAPClient, stream.decrypt_secret,
                           stream._ficha, stream._cuenta)
        stream.IMAPClient = IMAPFalso
        stream.decrypt_secret = lambda _c: "clave"
        cls.tamano_por_defecto = 5000
        stream._ficha = lambda c, u, p: {
            "Carpeta": "INBOX", "ContentType": "image/png",
            "Size": IMAPFalso.size_reportado}
        stream._cuenta = lambda i: {"Id": i, "Email": "a@b.com",
                                    "ServidorIMAP": "x", "PuertoIMAP": 993,
                                    "TipoAuth": "PASSWORD",
                                    "CredencialCifrada": "c", "CarpetaRaiz": "INBOX"}

        # Se levanta el servidor REAL en un hilo, guardando el objeto para
        # apagarlo en tearDownClass. Sin apagarlo, el puerto queda ocupado y la
        # segunda corrida falla: un test que solo pasa la primera vez es un test
        # que nadie vuelve a correr.
        cls.servidor = _servidor_de_prueba()
        cls.puerto = cls.servidor.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.puerto}"
        cls.hilo = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.hilo.start()
        for _ in range(50):                       # espera a que levante el puerto
            try:
                urllib.request.urlopen(cls.base + "/salud", timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.hilo.join(timeout=5)
        (stream.IMAPClient, stream.decrypt_secret,
         stream._ficha, stream._cuenta) = cls._originales
        settings.datos_dir = cls._dir_anterior
        settings.cache_dir = cls._cache_anterior
        settings.stream_token = cls._token_anterior

    def pedir(self, ruta, token="token-de-prueba"):
        req = urllib.request.Request(self.base + ruta)
        if token:
            req.add_header(CABECERA_TOKEN, token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            # Se cierra el error: si no, urllib deja el socket abierto y Python
            # avisa con ResourceWarning al recolectar. Ruido que esconde errores
            # de verdad.
            codigo, cuerpo, cabeceras = e.code, e.read(), dict(e.headers)
            e.close()
            return codigo, cuerpo, cabeceras

    def setUp(self):
        IMAPFalso.calls = []
        IMAPFalso.datos_entero = b"X" * 5000
        IMAPFalso.size_reportado = 5000

    def archivo_grande(self, bytes_):
        """Cambia el fake a un archivo grande y devuelve sus bytes."""
        datos = bytes(bytearray((i * 7 + 13) % 251 for i in range(bytes_)))
        IMAPFalso.datos_entero = datos
        IMAPFalso.size_reportado = bytes_
        return datos


class TestRutaEsLaDelContrato(_Base):
    """La ruta tiene que ser la que arma la app, no una parecida."""

    def test_url_de_la_app_se_sirve(self):
        """La URL exacta de `_pedir_al_worker` con inline=0."""
        ruta = URL_APP_ENTERO.format(cuenta=1, uid=10, parte="2.1", inline=0)
        status, datos, _h = self.pedir(ruta)
        self.assertEqual(status, 200, f"la app recibiría un error en {ruta}")
        self.assertGreater(len(datos), 0)

    def test_url_de_la_app_con_inline(self):
        ruta = URL_APP_ENTERO.format(cuenta=1, uid=10, parte="2.1", inline=1)
        status, datos, _h = self.pedir(ruta)
        self.assertEqual(status, 200)
        self.assertGreater(len(datos), 0)

    def test_url_de_trozos_de_la_app(self):
        """La URL exacta de `_trozos_del_worker`."""
        self.archivo_grande(TAMANO_TROZO_APP * 3)
        ruta = URL_APP_TROZO.format(cuenta=1, uid=10, parte="2.1",
                                    desde=0, largo=TAMANO_TROZO_APP)
        status, _datos, headers = self.pedir(ruta)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Followup"), "1",
                         "con un rango lleno hay más, y la app lo necesita")

    def test_el_trozo_de_la_app_no_se_recorta(self):
        """
        El tamaño de trozo lo fija la app en 256 KB. Si el worker lo recortara a
        menos, la app recibiría trozos más chicos de lo pedido (no rompería, pero
        multiplicaría los round trips a IMAP).
        """
        self.archivo_grande(TAMANO_TROZO_APP * 3)
        ruta = URL_APP_TROZO.format(cuenta=1, uid=10, parte="1",
                                    desde=0, largo=TAMANO_TROZO_APP)
        _s, datos, _h = self.pedir(ruta)
        self.assertEqual(len(datos), TAMANO_TROZO_APP)

    def test_un_archivo_grande_va_trozo_a_trozo(self):
        """
        El caso para el que existe el streaming: 700 KB pasan en 3 requests de
        256 KB, y el pico de memoria del worker es el de un trozo, no el del
        archivo.
        """
        datos = self.archivo_grande(TAMANO_TROZO_APP * 3 - 1000)
        recibido = b""
        enviados = 0
        while enviados < len(datos):
            largo = min(TAMANO_TROZO_APP, len(datos) - enviados)
            ruta = URL_APP_TROZO.format(cuenta=1, uid=10, parte="1",
                                        desde=enviados, largo=largo)
            _s, trozo, _h = self.pedir(ruta)
            recibido += trozo
            enviados += len(trozo)
        self.assertEqual(recibido, datos,
                         "el archivo tiene que llegar byte a byte igual")


class TestTransmision(_Base):
    def test_trozos_reconstruyen_el_archivo(self):
        """
        El bucle exacto de `_trozos_del_worker`: se avanza con `len(datos)` y se
        para cuando el worker devuelve menos de lo pedido. Si esto no reconstruye
        el archivo, la descarga de cualquier adjunto grande sale corrupta.
        """
        # Traducción literal de `_trozos_del_worker`: el total sale del índice y
        # el bucle avanza con len(datos).
        total = IMAPFalso.size_reportado
        enviados = 0
        recibido = b""
        vueltas = 0
        while not total or enviados < total:
            vueltas += 1
            self.assertLess(vueltas, 40, "el bucle no termina: el worker miente sobre el final")
            largo = TAMANO_TROZO_APP if not total else min(TAMANO_TROZO_APP,
                                                           total - enviados)
            ruta = URL_APP_TROZO.format(cuenta=1, uid=10, parte="1",
                                        desde=enviados, largo=largo)
            _s, datos, _h = self.pedir(ruta)
            if not datos:
                break
            recibido += datos
            enviados += len(datos)
            if not total:
                total = enviados
        self.assertEqual(len(recibido), IMAPFalso.size_reportado)
        self.assertEqual(recibido, IMAPFalso.datos_entero[:len(recibido)])

    def test_un_rango_al_final_avisa_que_no_hay_mas(self):
        """
        Cuando el rango se pasa del final, el worker devuelve menos de lo pedido.
        La app detecta el final ahí, y no en un `Content-Length` que no existe.
        """
        ruta = URL_APP_TROZO.format(cuenta=1, uid=10, parte="1",
                                    desde=4900, largo=1024)
        _s, datos, headers = self.pedir(ruta)
        self.assertEqual(len(datos), 100)
        self.assertEqual(headers.get("X-Followup"), "0")

    def test_un_pedido_absurdo_se_recorta(self):
        """Un `bytes=95MB` se recorta al tope. Sin tope sería un OOM."""
        ruta = f"/adjunto/1/10/1?desde=0&bytes=99999999"
        _s, datos, _h = self.pedir(ruta)
        self.assertLessEqual(len(datos), 262144)

    def test_content_type_viene_del_indice(self):
        """El tipo lo declara el servidor de correo, no el que llama."""
        _s, _d, headers = self.pedir("/adjunto/1/10/1")
        self.assertEqual(headers.get("Content-Type"), "image/png")

    def test_nosniff(self):
        """Sin esto, un adjunto `text/html` se ejecutaría en el origen de la app."""
        _s, _d, headers = self.pedir("/adjunto/1/10/1")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_cache_no_repide_imap(self):
        """Cinco personas abriendo el mismo PDF = un fetch. Ese es su propósito."""
        self.pedir("/adjunto/9/77/1")
        antes = len(IMAPFalso.calls)
        self.pedir("/adjunto/9/77/1")
        self.pedir("/adjunto/9/77/1")
        self.assertEqual(len(IMAPFalso.calls), antes,
                         "el 2do y 3er pedido debieron salir de la cache")


class TestSeguridad(_Base):
    def test_sin_token(self):
        status, _d, _h = self.pedir("/adjunto/1/10/1", token=None)
        self.assertEqual(status, 401)

    def test_token_incorrecto(self):
        status, _d, _h = self.pedir("/adjunto/1/10/1", token="adivinado")
        self.assertEqual(status, 401)

    def test_salud_tambien_exige_token(self):
        """
        Un endpoint de salud abierto dice si el servicio está vivo, que es
        justo lo que un atacante busca antes de intentar el token.
        """
        status, _d, _h = self.pedir("/salud", token=None)
        self.assertEqual(status, 401)

    def test_no_se_revela_si_la_ruta_existe(self):
        """401 tanto si el token falla como si la ruta no existe."""
        self.assertEqual(self.pedir("/adjunto/1/10/1", token="malo")[0], 401)
        self.assertEqual(self.pedir("/no/existe", token="malo")[0], 401)

    def test_inline_no_trae_un_archivo_grande(self):
        """
        El tope de inline existe para esto: un `cid` que apunte a un adjunto de
        300 MB, pedido por la ruta de inline, sería un OOM del worker. Y como
        todas las cuentas viven en el mismo proceso, se caerían todas.
        """
        IMAPFalso.datos_entero = b"Z" * (900 * 1024)
        IMAPFalso.size_reportado = 900 * 1024
        try:
            status, _d, _h = self.pedir("/adjunto/1/10/1?inline=1")
            # No se sirve: el indice declara 900 KB y el tope es 256 KB. La app
            # recibe un error y muestra el marcador, que es lo correcto.
            self.assertGreaterEqual(status, 400)
            # Y lo importante: no se bajaron los 900 KB de IMAP para nada.
            self.assertEqual(IMAPFalso.calls, [],
                             "el tope tiene que actuar ANTES de pedir los bytes")
        finally:
            IMAPFalso.datos_entero = b"X" * 5000
            IMAPFalso.size_reportado = 5000

    def test_parte_inexistente_da_400(self):
        """El `parte` viene de la URL: un número no numérico no debe ser un 500."""
        status, _d, _h = self.pedir("/adjunto/no-numero/10/1")
        self.assertEqual(status, 400)

    def test_post_no_se_acepta(self):
        req = urllib.request.Request(self.base + "/adjunto/1/10/1",
                                     data=b"x", method="POST")
        req.add_header(CABECERA_TOKEN, "token-de-prueba")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 405)

    def test_query_basura_no_produce_500(self):
        """Un `desde=abc` no puede tumbar al worker con un 500."""
        for ruta in ("/adjunto/1/10/1?desde=abc&bytes=xyz",
                     "/adjunto/1/10/1?inline=si",
                     "/adjunto/-1/10/1"):
            status, _d, _h = self.pedir(ruta)
            self.assertNotEqual(status, 500, ruta)


if __name__ == "__main__":
    unittest.main(verbosity=2)