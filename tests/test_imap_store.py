"""
tests/test_imap_store.py — El bug de `UID STORE` (marcar como leído)
====================================================================
Bug encontrado en producción al migrar el worker de correo, y reproducido con
un servidor IMAP real y mínimo.

Estos casos vienen de los 3 ops `failed` que había en la cola de la app de
correo (cuenta 34, 2026-09-01 y
2026-09-09) tenían el mismo error:

    UID command error: BAD [b'UID STORE extra parameters supplied "\\Seen"']

Es decir: **marcar un correo como leído (o con bandera) NUNCA funcionó contra
ese servidor**, desde el 2026-09-01. La UI loreflecte al instante (escribe en
la caché) y el usuario ve el correo marcado; lo que falla es el `STORE`
contra IMAP, que el log deja quite enterrado. Por eso hay 5 ops `seen`
atascadas en la cola desde el 2026-08-26 que nunca se ejecutaron.

`imap_client.set_flag()` usaba `conn.uid("store", uid, "+FLAGS", flag)`, que
depende de cómo imaplib interprete el flag según la versión de Python: unas
versiones lo citan y otras no, y el resultado cambia. Este test fija la forma
explícita y verificable `UID STORE <uid> +FLAGS.SILENT (\\Seen)`, que es la
del RFC 3501 y la que aceptan los servidores.

No necesita red externa: levanta un IMAP de mentira en localhost que solo
registra lo que le llega.

Ejecución:  python tests/test_imap_store.py
"""
import os
import socket
import socketserver
import sys
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import imaplib  # noqa: E402

# Lo que el servidor recibio, en orden.
RECIBIDO = []


class _ImapFalso(socketserver.StreamRequestHandler):
    """IMAP mínimo: responde lo justo para que imaplib entre en SELECTED y
    anota la línea de cada comando. No implementa nada: solo registramos."""

    def handle(self):
        self.wfile.write(b"* OK [CAPABILITY IMAP4rev1 UIDPLUS] listo\r\n")
        self.wfile.flush()
        while True:
            linea = self.rfile.readline()
            if not linea:
                return
            cruda = linea.decode("ascii", "replace").rstrip("\r\n")
            RECIBIDO.append(cruda)
            partes = cruda.split(" ")
            tag = partes[0]
            # imaplib manda continuaciones con el prefijo "+": las ignoramos.
            if tag.startswith("+"):
                continue
            verbo = (partes[1] if len(partes) > 1 else "").upper()

            # Respuestas sin etiqueta que imaplib necesita para avanzar de estado.
            if verbo == "CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1 UIDPLUS\r\n")
            elif verbo == "SELECT":
                self.wfile.write(b"* 3 EXISTS\r\n* 0 RECENT\r\n"
                                 b"* FLAGS (\\Seen \\Flagged \\Deleted)\r\n"
                                 b"* OK [UIDVALIDITY 1] UIDs validos\r\n")
            elif verbo == "LIST":
                self.wfile.write(b"* LIST (\\HasNoChildren) \"/\" \"INBOX\"\r\n")
            elif verbo == "NAMESPACE":
                self.wfile.write(b'* NAMESPACE (("" "/")) NIL NIL\r\n')
            elif verbo == "ID":
                self.wfile.write(b'* ID ("name" "prueba")\r\n')

            self.wfile.write(f"{tag} OK listo\r\n".encode())
            self.wfile.flush()


class ServidorLocal:
    def __enter__(self):
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ImapFalso)
        self.srv.daemon_threads = True
        self.hilo = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.hilo.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def host(self):
        return "127.0.0.1"

    @property
    def puerto(self):
        return self.srv.server_address[1]


def _conectar(puerto):
    c = imaplib.IMAP4("127.0.0.1", puerto)
    c.login("x", "x")
    c.select("INBOX")
    return c


def _uid_store(conn, uid, flag, valor):
    """Asi debe quedar la orden de STORE. Mismo criterio que
    `mailbox_worker.imap_client.IMAPClient.set_flag`."""
    prefijo = "+" if valor else "-"
    return conn._simple_command("UID", "STORE", str(uid),
                                f"{prefijo}FLAGS.SILENT", f"({flag})")


class TestUidStore(unittest.TestCase):
    def setUp(self):
        RECIBIDO.clear()

    def test_marca_leido_usa_la_forma_del_rfc(self):
        """`\\Seen` tiene que ir DENTRO de parentesis y con .SILENT."""
        with ServidorLocal() as srv:
            c = _conectar(srv.puerto)
            typ, _ = _uid_store(c, 1474, "\\Seen", True)
            c.logout()
        self.assertEqual(typ, "OK")
        store = [l for l in RECIBIDO if "STORE" in l]
        self.assertTrue(store, f"no llego ningun STORE; se recibio: {RECIBIDO}")
        linea = store[-1]
        self.assertRegex(linea, r"UID STORE 1474 \+FLAGS\.SILENT \(\\Seen\)")

    def test_quitar_bandera_usa_el_menos(self):
        with ServidorLocal() as srv:
            c = _conectar(srv.puerto)
            _uid_store(c, 9, "\\Flagged", False)
            c.logout()
        linea = [l for l in RECIBIDO if "STORE" in l][-1]
        self.assertRegex(linea, r"UID STORE 9 -FLAGS\.SILENT \(\\Flagged\)")

    def test_la_forma_vieja_manda_el_flag_sin_parentesis(self):
        """Documenta POR QUÉ se cambió, contra la lógica de Python 3.11.

        `imaplib.uid()` de 3.11 pasa los argumentos a `_command()` tal cual
        (`data = data + b' ' + arg`, sin comillas ni paréntesis), así que
        `uid('store', uid, '+FLAGS', flag)` deja el flag-list SUELTO. El RFC
        3501 lo exige entre paréntesis.

        Ojo: este test corre en la versión de Python de la máquina, y en 3.14
        `uid()` ya añade los paréntesis por su cuenta. Lo que se verifica aquí
        es lo que importa y no depende de la versión: la forma NUEVA sale
        siempre igual (test de arriba), mientras la VIEJA depende.
        """
        with ServidorLocal() as srv:
            c = _conectar(srv.puerto)
            c.uid("store", "1474", "+FLAGS", "\\Seen")
            c.logout()
        linea = [l for l in RECIBIDO if "STORE" in l][-1]
        # En 3.14 imaplib lo envuelve; en 3.11 lo deja suelto. Cualquiera de las
        # dos formas documenta el problema, pero solo la segunda es la que se
        # vio en produccion.
        self.assertTrue(
            linea.endswith("+FLAGS (\\Seen)") or linea.endswith("+FLAGS \\Seen"),
            f"forma inesperada, hay que revisar el comentario: {linea}")


class TestSetFlagDelWorker(unittest.TestCase):
    """Verifica el método REAL del worker, no una reimplementación."""

    def _cliente_con_conn(self, srv):
        """IMAPClient con la conexión ya puesta, para no dialar por SSL.

        `IMAPClient._connect()` usa IMAP4_SSL fijo (GoDaddy es 993), así que en
        el test se le inyecta la conexión en claro ya conectada.
        """
        from mailbox_worker.imap_client import IMAPClient
        cliente = IMAPClient(srv.host, srv.puerto, "x", "x")
        cliente._conn = _conectar(srv.puerto)
        return cliente

    def test_set_flag_emite_store_valido(self):
        with ServidorLocal() as srv:
            cliente = self._cliente_con_conn(srv)
            RECIBIDO.clear()
            cliente.select_folder("INBOX")
            cliente.set_flag(1474, "\\Seen", True)
            cliente.close()
        store = [l for l in RECIBIDO if "STORE" in l]
        self.assertTrue(store, f"no llego STORE; se recibio: {RECIBIDO}")
        self.assertRegex(store[-1], r"UID STORE 1474 \+FLAGS\.SILENT \(\\Seen\)")

    def test_no_hay_envio_en_lote_pero_cada_uid_sigue_saliendo(self):
        """
        `mailbox_worker` no tiene `set_flags` (en lote): hace un STORE por UID.
        La carpeta se selecciona antes, no se pasa en cada llamada.
        """
        with ServidorLocal() as srv:
            cliente = self._cliente_con_conn(srv)
            RECIBIDO.clear()
            cliente.select_folder("INBOX")
            for uid in (1, 2, 3):
                cliente.set_flag(uid, "\\Seen", True)
            cliente.close()
        store = [l for l in RECIBIDO if "STORE" in l]
        self.assertEqual(len(store), 3, f"esperaba 3 STORE; se recibio: {RECIBIDO}")
        self.assertRegex(store[-1], r"UID STORE 3 \+FLAGS\.SILENT \(\\Seen\)")


def _linea_imaplib_311(tag, nombre, args):
    """Reproduce EXACTAMENTE cómo Python 3.11 arma la línea que va al socket.

    Copiado de `Lib/imaplib.py` de CPython 3.11 (`IMAP4._command`):

        data = tag + b' ' + name
        for arg in args:
            if arg is None: continue
            if isinstance(arg, str):
                arg = bytes(arg, self._encoding)
            data = data + b' ' + arg

    Sin comillas, sin escapes, sin paréntesis. `_simple_command` es
    `_command_complete(nombre, _command(nombre, *args))`, o sea que también
    concatena verbatim.

    Se replica aquí porque el CI y esta máquina pueden correr 3.13/3.14, donde
    `uid()` ya envuelve el flag por su cuenta, y el test contra el IMAP local
    pasaría sin decir nada del problema real de 3.11.
    """
    data = tag.encode() + b" " + nombre.encode()
    for arg in args:
        if arg is None:
            continue
        data = data + b" " + (arg.encode() if isinstance(arg, str) else arg)
    return data.decode("ascii")


class TestLinea311(unittest.TestCase):
    """El formato de la línea bajo la lógica de 3.11, que es la de producción."""

    def test_la_forma_nueva_es_valida_en_311(self):
        linea = _linea_imaplib_311("A001", "UID",
                                   ("STORE", "1474", "+FLAGS.SILENT", "(\\Seen)"))
        self.assertEqual(linea, "A001 UID STORE 1474 +FLAGS.SILENT (\\Seen)")

    def test_la_forma_vieja_es_invalida_en_311(self):
        """Esto es exactamente lo que hizo fallar a la cuenta 34."""
        linea = _linea_imaplib_311("A001", "UID",
                                   ("store", "1474", "+FLAGS", "\\Seen"))
        self.assertEqual(linea, "A001 UID store 1474 +FLAGS \\Seen")
        # El RFC 3501: flag-list = "(" [flag *(SP flag)] ")"
        self.assertNotIn("(", linea.split("+FLAGS")[1],
                         "el flag-list tiene que ir entre paréntesis")

    def test_borrar_en_lote_en_311(self):
        linea = _linea_imaplib_311("A002", "UID",
                                   ("STORE", "1,2,3", "+FLAGS.SILENT", "(\\Deleted)"))
        self.assertEqual(linea, "A002 UID STORE 1,2,3 +FLAGS.SILENT (\\Deleted)")

    def test_quitar_bandera_en_311(self):
        linea = _linea_imaplib_311("A003", "UID",
                                   ("STORE", "9", "-FLAGS.SILENT", "(\\Flagged)"))
        self.assertEqual(linea, "A003 UID STORE 9 -FLAGS.SILENT (\\Flagged)")


class TestArgumentosQuePasan(unittest.TestCase):
    """Lo que el cliente entrega a `_simple_command`, que es lo que 3.11
    concatena verbatim. Sin esto, un cambio future podría volver a colar el
    flag suelto aunque los tests contra el IMAP local siguieran en verde en 3.14.
    """

    def _spy(self, metodo, args, selec="INBOX"):
        """
        Captura las ordenes STORE que salen por la conexion.

        `mailbox_worker` no tiene el helper `_uid_store` que tenia
        `hubmail_worker`: arma el STORE con `conn._simple_command`. Por eso el
        espia va sobre `_simple_command`, que es donde de verdad sale la linea.
        """
        from unittest import mock
        from mailbox_worker.imap_client import IMAPClient
        cli = IMAPClient("h", 993, "u", "p")
        conn = mock.MagicMock()
        conn.select.return_value = ("OK", [b""])
        conn.expunge.return_value = ("OK", [b""])
        conn._simple_command.return_value = ("OK", [b""])
        cli._conn = conn
        vistos = []

        def _spy_command(_name, *args):
            if _name == "UID" and args and args[0] == "STORE":
                vistos.append((args[1], args[2], args[3]))
            return ("OK", [b""])

        conn._simple_command.side_effect = _spy_command
        if selec:
            cli.select_folder(selec)
        getattr(cli, metodo)(*args)
        return vistos

    def test_cada_metodo_manda_el_flag_entre_parentesis(self):
        casos = [
            ("set_flag", (1474, "\\Seen", True),
             ("1474", "+FLAGS.SILENT", "(\\Seen)")),
            ("set_flag", (1474, "\\Seen", False),
             ("1474", "-FLAGS.SILENT", "(\\Seen)")),
            ("delete_message", ("9",),
             ("9", "+FLAGS.SILENT", "(\\Deleted)")),
            ("delete_messages", (["3", "4"],),
             ("3,4", "+FLAGS.SILENT", "(\\Deleted)")),
        ]
        for metodo, args, esperado in casos:
            with self.subTest(metodo=metodo):
                self.assertEqual(self._spy(metodo, args), [esperado])

    def test_lista_vacia_no_manda_nada(self):
        self.assertEqual(self._spy("delete_messages", ([],), selec=None), [])


class TestErroresDelServidor(unittest.TestCase):
    """Qué propaga cada método cuando el servidor contesta BAD.

    Importa por un caso que sin esto sería una bomba de duplicados: en
    `move_message` el `UID COPY` ya salió bien cuando falla el `\\Deleted`. Si
    ahí se levantara el error, la cola marcaría la op como `failed`, el
    siguiente ciclo reharía el COPY completo y dejaría **una copia nueva en el
    destino cada 5 minutos**, sin límite. Por eso `move` avisa y no lanza, y los
    métodos idempotentes sí lanzan.
    """

    def _cliente(self, copy_ty="OK", store_ty="OK"):
        from unittest import mock
        from mailbox_worker.imap_client import IMAPClient
        cli = IMAPClient("h", 993, "u", "p")
        conn = mock.MagicMock()
        conn.select.return_value = ("OK", [b""])
        conn.uid.return_value = (copy_ty, [b""])
        conn.expunge.return_value = ("OK", [b""])
        conn._simple_command.return_value = (store_ty, [b""])
        cli._conn = conn
        return cli

    def test_move_no_lanza_si_falla_el_store_tras_un_copy_bueno(self):
        # Si este test empezara a fallar con IMAPError, alguien volvió a hacer
        # que move levante: revisar la nota del método antes de "arreglarlo".
        self._cliente("OK", "BAD").move_message("1474", "Archivados")

    def test_move_lanza_si_falla_el_copy(self):
        from mailbox_worker.imap_client import IMAPError
        with self.assertRaises(IMAPError):
            self._cliente("NO", "OK").move_message("1474", "Archivados")

    def test_delete_lanza_si_falla_el_store(self):
        from mailbox_worker.imap_client import IMAPError
        with self.assertRaises(IMAPError):
            self._cliente("OK", "BAD").delete_message("9")

    def test_set_flag_lanza_si_falla_el_store(self):
        from mailbox_worker.imap_client import IMAPError
        with self.assertRaises(IMAPError):
            self._cliente("OK", "BAD").set_flag(9, "\\Seen", True)


if __name__ == "__main__":
    unittest.main(verbosity=2)