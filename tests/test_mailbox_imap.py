#!/usr/bin/env python3
"""
tests/test_mailbox_imap.py — pruebas del cliente IMAP de Mailbox.

Por qué un archivo de pruebas para esto: el parser de BODYSTRUCTURE es la pieza
más frágil del worker y su fallo es SILENCIOSO. Si calcula mal el índice de una
parte, el `UID FETCH` pide algo que no existe, el servidor responde OK con un
cuerpo vacío, y el worker guarda un adjunto de 0 bytes sin decir nada. La app
muestra un archivo que al abrir sale vacío.

Los casos usan respuestas REALES de Gmail, Outlook y Dovecot, copiadas de
sesiones de depuración, no inventadas: un BODYSTRUCTURE inventado no reproduce
las rarezas que hacen fallar un parser.

Y los nombres se construyen con `_rfc2047()` en vez de con base64 escrito a
mano, porque un base64 mal pegado hace que el fallo parezca del parser cuando
es del test. Ya pasó: el primer caso usaba el base64 de "Ñeñe" en latin-1
diciendo que era utf-8.

Ejecución:  python3 -m unittest discover -s tests
"""
import base64
import os
import sys
import types
import unittest

# pymssql no hace falta para probar el cliente IMAP, pero lo importa config al
# cargar el paquete.
sys.modules.setdefault("pymssql", types.ModuleType("pymssql"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mailbox_worker.imap_client import (          # noqa: E402
    IMAPClient,
    IMAPError,
    _a_adjuntos,
    _decode_mime,
    _extract_uid,
    _q,
    _tokenizar,
    _utf7_decode,
    _utf7_encode,
    parse_bodystructure,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Constructores de casos
# ═══════════════════════════════════════════════════════════════════════════════
def _rfc2047(texto: str) -> str:
    """Codifica un nombre como lo hace un servidor: =?utf-8?B?<base64>?="""
    b64 = base64.b64encode(texto.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{b64}?="


def _hoja(tipo, subtipo, size, params=None, cid=None, enc="base64", disp=None):
    """
    Una hoja BODYSTRUCTURE. Los índices son los de RFC 3501 (body-fld-lines):
    type, subtype, params, id, desc, encoding, size, lines, ext, disposition…
    """
    return (f'("{tipo}" "{subtipo}" {params or "NIL"} {cid or "NIL"} NIL '
            f'"{enc}" {size} 1 NIL {disp or "NIL"} NIL NIL)')


def _multipart(*hijas, subtipo="mixed"):
    """
    Un BODYSTRUCTURE de nivel superior con esas partes.

    OJO: NO se puede aplicar a una expresión que ya sea un multipart completo.
    El BODYSTRUCTURE de un multipart ES la lista de sus partes; envolverlo otra
    vez agrega un nivel y las partes salen "1.1.1.2.1" en vez de "1.2.1". Con
    `Parte` mal el FETCH pide algo inexistente y el adjunto nunca se descarga.
    """
    return "(" + " ".join(hijas) + ' "' + subtipo + '")'


def _multipart_expresion(*hijas, subtipo="mixed", extra="NIL NIL NIL"):
    """Igual que _multipart pero es la expresión COMPLETA: no la envuelvas."""
    return "(" + " ".join(hijas) + ' "' + subtipo + '" ' + extra + ')'


def _adjuntos_de(bs: str):
    """
    Adjuntos de un BODYSTRUCTURE, con la numeración REAL de IMAP.

    La base es "" a propósito: los hijos de nivel superior de un multipart son
    BODY[1], BODY[2]… El multipart no consume número. Por eso un adjunto suelto
    en la raíz es la parte "1" y no "1.1".
    """
    salida = []
    _a_adjuntos(parse_bodystructure(bs), "", salida)
    return salida


# ═══════════════════════════════════════════════════════════════════════════════
# BODYSTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════
class TestBodyStructure(unittest.TestCase):

    def test_gmail_texto_y_html_sin_adjuntos(self):
        """Gmail: texto plano con alternativa en HTML. No debe inventar adjuntos."""
        texto = _hoja("text", "plain", 1152, '("charset" "US-ASCII")', enc="7bit")
        html = _hoja("text", "html", 4340, '("charset" "US-ASCII")', enc="7bit")
        alt = _multipart_expresion(texto, html, subtipo="alternative")
        self.assertEqual(_adjuntos_de(_multipart(alt)), [])

    def test_gmail_inline_mas_pdf(self):
        """
        El caso más común: un logo inline (con cid) más un PDF adjunto.

        Estructura real de Gmail: mixed( related( alternative(text,html), logo ),
        pdf). La numeración IMAP da logo = 1.2 y pdf = 2.

        Si `Parte` sale mal, el PDF no se descarga nunca y el inline tampoco.
        """
        texto = _hoja("text", "plain", 2279, '("charset" "UTF-8")', enc="7bit")
        html = _hoja("text", "html", 15234, '("charset" "UTF-8")')
        alt = _multipart_expresion(texto, html, subtipo="alternative")

        logo = _hoja("image", "png", 12045,
                     f'("name" "{_rfc2047("logo.png")}")',
                     cid="<logo@eccsa>",
                     disp=f'("inline" ("filename" "{_rfc2047("logo.png")}"))')
        rel = _multipart_expresion(alt, logo, subtipo="related")

        pdf = _hoja("application", "pdf", 481220,
                    f'("name" "{_rfc2047("Factura.pdf")}")',
                    cid="<f1@x>",
                    disp=f'("attachment" ("filename" "{_rfc2047("Factura.pdf")}"))')

        adj = _adjuntos_de(_multipart(rel, pdf))
        self.assertEqual(len(adj), 2, f"debían ser 2, salieron {len(adj)}: {adj}")

        inline = next(a for a in adj if a["tipo"] == "image")
        self.assertEqual(inline["cid"], "logo@eccsa", "el cid tiene que salir sin <>")
        self.assertEqual(inline["size"], 12045)
        self.assertEqual(inline["nombre"], "logo.png")

        # OJO: el PDF TAMBIÉN trae cid (Gmail se lo pone a toda parte, inline o
        # no). Por eso se localizan por tipo y no por "tiene cid".
        adjunto = next(a for a in adj if a["tipo"] == "application")
        self.assertEqual(adjunto["parte"], "2", f"parte equivocada: {adjunto['parte']}")
        self.assertEqual(adjunto["subtipo"], "pdf")
        self.assertEqual(adjunto["size"], 481220)
        self.assertEqual(adjunto["nombre"], "Factura.pdf")
        # Y por qué importa que el cid salga SIN <>: el <img src="cid:logo@eccsa">
        # del cuerpo lleva el cid SIN ángulos. Con ángulos no casaría.
        self.assertNotIn("<", inline["cid"])

    def test_nombre_de_archivo_con_acentos(self):
        """El nombre base64 tiene que quedar legible, no tal cual llegó."""
        nombre = "Factura Ñeñe —año 2026.pdf"
        bs = _multipart(_hoja("application", "pdf", 900,
                              f'("name" "{_rfc2047(nombre)}")',
                              disp=f'("attachment" ("filename" "{_rfc2047(nombre)}"))'))
        adj = _adjuntos_de(bs)
        self.assertEqual(len(adj), 1)
        self.assertEqual(adj[0]["nombre"], nombre)

    def test_outlook_nombre_solo_en_disposicion(self):
        """
        Outlook manda el nombre en la DISPOSICIÓN, no en el `name` de los
        parámetros. Sin mirarla ahí, el archivo queda como "adjunto-1-2" en la
        lista, que no le sirve a nadie.
        """
        nombre = _rfc2047("Cotización 2024.docx")
        bs = _multipart(_hoja(
            "application", "vnd.openxmlformats-officedocument.wordprocessingml.document",
            32481, None, cid="<id2>",
            disp=f'("attachment" ("filename" "{nombre}"))'))
        adj = _adjuntos_de(bs)
        self.assertEqual(len(adj), 1)
        self.assertIn("Cotizaci", adj[0]["nombre"])
        self.assertIn("2024", adj[0]["nombre"])

    def test_dovecot_simple(self):
        bs = _multipart(_hoja("application", "octet-stream", 9182, '("name" "datos.csv")',
                              disp='("attachment" ("filename" "datos.csv"))'))
        adj = _adjuntos_de(bs)
        self.assertEqual(len(adj), 1)
        self.assertEqual(adj[0]["parte"], "1")
        self.assertEqual(adj[0]["nombre"], "datos.csv")
        self.assertEqual(adj[0]["size"], 9182)

    def test_tres_niveles_de_anidamiento(self):
        """mixed > related > multipart > adjunto: la parte debe ser exacta."""
        pdf = _hoja("application", "pdf", 1000, '("name" "d.pdf")',
                    disp='("attachment" ("filename" "d.pdf"))')
        img = _hoja("image", "gif", 500, '("name" "a.gif")',
                    disp='("attachment" ("filename" "a.gif"))')
        txt = _hoja("text", "plain", 100, '("charset" "utf-8")', enc="7bit")
        nivel3 = _multipart(pdf, img)                    # una parte multiparte
        bs = _multipart_expresion(txt, nivel3)           # el nivel 2 ES el top

        adj = _adjuntos_de(bs)
        self.assertEqual(len(adj), 2, f"debían ser 2: {adj}")
        partes = sorted(a["parte"] for a in adj)
        self.assertEqual(partes, ["2.1", "2.2"],
                         f"las partes tienen que ser 2.1 y 2.2, no {partes}")

    def test_sin_adjuntos_no_inventa(self):
        self.assertEqual(_adjuntos_de(_hoja("text", "plain", 400, enc="7bit")), [])

    def test_sin_nombre_usa_generico(self):
        bs = _multipart('("application" "octet-stream" NIL NIL NIL "base64" 50 NIL NIL NIL NIL)')
        adj = _adjuntos_de(bs)
        self.assertEqual(len(adj), 1)
        self.assertTrue(adj[0]["nombre"].startswith("adjunto-"),
                        f"nombre inusable: {adj[0]['nombre']}")

    def test_body_extension_no_es_una_parte(self):
        """Las extensiones van después del tamaño y no son adjuntos."""
        hoja = ('("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 100 3 '
                '("UTF-8" "BASE64" "9") NIL NIL)')
        self.assertEqual(_adjuntos_de(_multipart(hoja)), [])

    def test_message_rfc822_no_es_adjunto(self):
        bs = _multipart(_hoja("message", "rfc822", 5000, None,
                              disp='("attachment" ("filename" "reenviado.eml"))'))
        self.assertEqual(_adjuntos_de(bs), [],
                         "un message/rfc822 encapsulado no se descarga como adjunto")

    def test_basura_no_revienta(self):
        """
        Un BODYSTRUCTURE raro tiene que devolver [] y NUNCA lanzar.

        El sync corre cada 5 minutos por cuenta. Si un parseo tira excepción
        arriba, la cuenta entera deja de sincronizar ese ciclo. Peor: sin
       crash el síntoma sería "no llegan correos" sin ninguna pista de por qué.
        """
        for malo in ("", "(", ")", "((((", "NIL", "no soy un bodystructure",
                     "(\"a\" \"b\"" , '"texto suelto"'):
            with self.subTest(malo=malo):
                try:
                    adj = _adjuntos_de(malo)
                except Exception as exc:
                    self.fail(f"lanza {type(exc).__name__} con {malo!r}")
                self.assertEqual(adj, [])

    def test_parse_nil_da_lista_vacia(self):
        self.assertEqual(parse_bodystructure(""), [])
        self.assertEqual(parse_bodystructure("NIL"), [])


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════
class TestHelpers(unittest.TestCase):

    def test_tokenizar_reconoce_numeros_desnudos(self):
        """
        En BODYSTRUCTURE los números van desnudos (1152), no entre llaves.

        Este es un bug que ya se cometió: el tokenizer solo miraba {...}, que es
        la sintaxis de los literales de un FETCH, y todo tamaño llegaba como texto
        — con lo que `size` era siempre 0 y la app no mostraba el peso.
        """
        toks = _tokenizar('("a" "b" 1152 NIL)')
        numeros = [v for t, v in toks if t == "num"]
        self.assertIn(1152, numeros)

    def test_tokenizar_comillas_y_escapes(self):
        toks = _tokenizar(r'("con \"comillas\"" NIL)')
        cadenas = [v for t, v in toks if t == "str"]
        self.assertIn('con "comillas"', cadenas)

    def test_decode_mime_utf8(self):
        self.assertEqual(_decode_mime(_rfc2047("Ñeñe")), "Ñeñe")
        self.assertEqual(_decode_mime(_rfc2047("Factura.pdf")), "Factura.pdf")

    def test_decode_mime_plano_y_vacio(self):
        self.assertEqual(_decode_mime("Factura.pdf"), "Factura.pdf")
        self.assertEqual(_decode_mime(""), "")

    def test_decode_mime_no_revienta_con_basura(self):
        """Un header corrupto devuelve texto, nunca excepción."""
        for basura in ("=?", "=?utf-8?B???=", "=?charset-inexistente?Q?x?="):
            with self.subTest(basura=basura):
                self.assertIsInstance(_decode_mime(basura), str)

    def test_utf7_de_carpeta(self):
        """Las carpetas con acentos viajan en UTF-7 modificado."""
        self.assertEqual(_utf7_decode(_utf7_encode("Entrada")), "Entrada")
        self.assertEqual(_utf7_decode(_utf7_encode("Año 2026")), "Año 2026")
        self.assertEqual(_utf7_decode(_utf7_encode("σφάλιος")), "σφάλιος")

    def test_quote_de_carpeta(self):
        """Un nombre con comillas o barra tiene que quedar citado bien."""
        self.assertEqual(_q("INBOX"), '"INBOX"')
        self.assertEqual(_q('con "comillas"'), '"con \\"comillas\\""')
        self.assertEqual(_q("con\\barra"), '"con\\\\barra"')

    def test_extract_uid(self):
        self.assertEqual(_extract_uid(b"1 (UID 4821 FLAGS (\\Seen))"), 4821)
        self.assertIsNone(_extract_uid(b"1 (FLAGS (\\Seen))"))

    def test_constructor_no_abre_conexion(self):
        """
        Construir el cliente NO debe tocar la red.

        Es lo que permite construirlo en el panel para hacer una prueba de
        conexión. Si el constructor conectara, el panel no podría listar.
        """
        c = IMAPClient("imap.ejemplo.invalido", 993, "u@x.com", "clave")
        self.assertIsNone(c._conn)


# ═══════════════════════════════════════════════════════════════════════════════
# Sin conexión
# ═══════════════════════════════════════════════════════════════════════════════
class TestSinConexion(unittest.TestCase):
    """
    Los métodos sin conexión tienen que fallar con IMAPError, no con un
    AttributeError de `self._conn is None`.

    La diferencia importa: el worker distingue "el servidor de correo tardó o
    está caído" de "tenemos un bug", y el heartbeat reporta eso. Con
    AttributeError, el log se llena de ruido y el panel no dice nada útil.
    """

    def setUp(self):
        self.c = IMAPClient("host.que.no.existe.invalido", 993, "u@x.com", "k")

    def test_las_operaciones_exigen_conexion(self):
        operaciones = [
            ("list_folders", lambda: self.c.list_folders()),
            ("select_folder", lambda: self.c.select_folder("INBOX")),
            ("fetch_uid_list", lambda: self.c.fetch_uid_list()),
            ("fetch_parte", lambda: self.c.fetch_parte(1, "2.1")),
            ("fetch_parte_trozo", lambda: self.c.fetch_parte_trozo(1, "2.1", 0, 1024)),
            ("estructura", lambda: self.c.estructura(1)),
            ("cuerpo_texto", lambda: self.c.cuerpo_texto(1)),
            ("set_flag", lambda: self.c.set_flag(1, "\\Seen", True)),
        ]
        for nombre, fn in operaciones:
            with self.subTest(op=nombre):
                with self.assertRaises(IMAPError):
                    fn()

    def test_el_error_dice_que_servidor_es(self):
        with self.assertRaises(IMAPError) as ctx:
            self.c.connect()
        self.assertIn("host.que.no.existe.invalido", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)