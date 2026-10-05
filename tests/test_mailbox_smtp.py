#!/usr/bin/env python3
"""
tests/test_mailbox_smtp.py — pruebas de la salida de correo y del almacén en disco.

Por qué estos casos son los que están:

**El MIME con firma es la parte que más falla en silencio.** Si el `Content-ID`
de la imagen no coincide con el `cid:` del `src`, el correo LLEGA, se abre, y
se ve con el espacio de la imagen en blanco. No hay error, no hay log, no hay
queja inmediata: el usuario asume que el que lo mandó no puso su logo. Por eso
se verifica la estructura completa (`related` → `alternative` → `text/*` →
`image/*`), no solo que "no lance excepción".

**`add_related` después de `add_alternative` es un ValueError en Python.** Se
probó lo contrario al escribir esto: la firma se perdía entera. El test fija la
estructura correcta para que nadie "simplifique" de vuelta a la API de azúcar.

**Las direcciones se parsean con `getaddresses`, no con split(",")**, porque los
nombres legítimos llevan comas. Un split manda `"Pérez` y `Juan" <j@x.com>` como
dos destinatarios y el servidor remoto rechaza el correo entero.

El PNG de los tests se genera con `zlib`/`struct` en vez de pegarse en base64 a
mano, por la misma razón que en test_mailbox_imap.py: un base64 mal escrito hace
que el fallo parezca del código cuando es del test.

Ejecución:  python3 -m unittest discover -s tests
"""
import base64
import email
import os
import struct
import sys
import tempfile
import types
import unittest
import zlib

sys.modules.setdefault("pymssql", types.ModuleType("pymssql"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mailbox_worker import almacen, smtp          # noqa: E402
from mailbox_worker.config import settings        # noqa: E402


def _png_1x1() -> bytes:
    """Un PNG válido y mínimo, construido (no copiado)."""
    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", ihdr) + trozo(b"IDAT", idat)
            + trozo(b"IEND", b""))


class _Base(unittest.TestCase):
    """Redirige el volumen a un temporal para no tocar /data."""

    def setUp(self):
        self._dir_anterior = settings.datos_dir
        self._cache_anterior = settings.cache_dir
        self._inline_kb = settings.inline_max_kb
        self.dir = tempfile.mkdtemp(prefix="mailbox-test-")
        settings.datos_dir = self.dir
        settings.cache_dir = os.path.join(self.dir, "cache")

    def tearDown(self):
        settings.datos_dir = self._dir_anterior
        settings.cache_dir = self._cache_anterior
        settings.inline_max_kb = self._inline_kb

    def _dejar_firma(self, firma_id: int, clave: str, datos: bytes):
        base = os.path.join(self.dir, "firmas", str(firma_id))
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, clave), "wb") as fh:
            fh.write(datos)
        return datos


# ═══════════════════════════════════════════════════════════════════════════════
# Direcciones
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirecciones(_Base):
    def test_separa_varios(self):
        self.assertEqual(smtp.separar_direcciones('"Juan" <juan@x.com>, maria@y.com'),
                         ["juan@x.com", "maria@y.com"])

    def test_nombre_con_coma_no_se_parte(self):
        """`"Pérez, Juan" <j@x.com>` es UNA dirección, no dos.

        Es el caso que rompe el `split(",")` y manda basura al servidor remoto.
        """
        self.assertEqual(smtp.separar_direcciones('"Pérez, Juan" <j@x.com>'), ["j@x.com"])

    def test_vacios(self):
        self.assertEqual(smtp.separar_direcciones(""), [])
        self.assertEqual(smtp.separar_direcciones(None), [])
        self.assertEqual(smtp.separar_direcciones("a@b.com,,c@d.com"), ["a@b.com", "c@d.com"])

    def test_validas(self):
        for d in ("a@b.com", "nombre.apellido@dominio.co.uk", "a+etiqueta@b.com"):
            self.assertTrue(smtp.es_correo_valido(d), d)

    def test_invalidas(self):
        for d in ("a@b", "a@@b.com", "a b@c.com", "<a@b.com>", "a@b..com",
                  "a@.com", "a@com.", "", None, "@b.com", "a@"):
            self.assertFalse(smtp.es_correo_valido(d), d)

    def test_el_filtro_rechaza_la_basura(self):
        """El filtro es la frontera de seguridad, no `separar_direcciones`."""
        with self.assertRaises(smtp.SMTPError):
            smtp.solo_permitidos(["esto no es un correo"], "a@b.com")

    def test_el_filtro_conserva_las_buenas(self):
        self.assertEqual(
            smtp.solo_permitidos(["bueno@x.com", "basura", "@x.com", "otro@y.com"], "a@b.com"),
            ["bueno@x.com", "otro@y.com"])

    def test_tope_de_destinatarios(self):
        with self.assertRaises(smtp.SMTPError):
            smtp.solo_permitidos([f"u{i}@x.com" for i in range(60)], "a@b.com")


# ═══════════════════════════════════════════════════════════════════════════════
# cid
# ═══════════════════════════════════════════════════════════════════════════════

class TestCid(_Base):
    def test_cipura(self):
        self.assertEqual(smtp.cids_en_html('<img src="cid:logo">'), ["logo"])

    def test_repetido_solo_una_vez(self):
        self.assertEqual(smtp.cids_en_html('<img src="cid:a"><img src="cid:a">'), ["a"])

    def test_sin_imagenes(self):
        self.assertEqual(smtp.cids_en_html("<p>texto</p>"), [])

    def test_quita_los_angulos(self):
        """Un HTML con `cid:<logo>` y otro con `cid:logo` son la misma imagen."""
        self.assertEqual(smtp.cids_en_html('<img src="cid:<logo>">'), ["logo"])

    def test_admite_el_token_real_de_la_app(self):
        """El formato que la app genera de verdad."""
        self.assertEqual(smtp.cids_en_html('<img src="cid:logo@eccsa">'), ["logo@eccsa"])

    def test_no_se_confunde_con_una_palabra(self):
        """`cid:` suelto en un texto no es una referencia a imagen."""
        self.assertEqual(smtp.cids_en_html("escribe cid: algo"), [])


class TestTextoPlano(_Base):
    """`_html_a_texto` es lo que impide un correo en blanco."""

    def test_basico(self):
        self.assertEqual(smtp._html_a_texto("<p>Hola</p>"), "Hola")

    def test_saltos_de_parrafo(self):
        self.assertEqual(smtp._html_a_texto("<p>Uno</p><p>Dos</p>"), "Uno\nDos")

    def test_br(self):
        self.assertEqual(smtp._html_a_texto("Uno<br>Dos"), "Uno\nDos")

    def test_borra_el_css(self):
        """Si no, el recipient lee el CSS del correo al principio."""
        got = smtp._html_a_texto("<style>p{color:red}</style><p>Hola</p>")
        self.assertEqual(got, "Hola")
        self.assertNotIn("color:red", got)

    def test_borra_el_script(self):
        got = smtp._html_a_texto("<script>alert(1)</script><p>Hola</p>")
        self.assertEqual(got, "Hola")
        self.assertNotIn("alert", got)

    def test_entidades(self):
        self.assertEqual(smtp._html_a_texto("<p>Jos&eacute; &amp;&ntilde;o</p>"),
                         "José &ño")

    def test_vacio(self):
        self.assertEqual(smtp._html_a_texto(""), "")
        self.assertEqual(smtp._html_a_texto(None), "")

    def test_un_solo_html_aun_así_tiene_texto_plano(self):
        """
        El caso que motivó `_html_a_texto`: si el usuario escribió en el editor
        rico y no hay texto plano guardado, el correo igual debe llevar la parte
        `text/plain` o el Outlook de escritorio lo muestra vacío.
        """
        smtp.filas = lambda sql, params=(): []
        msg, _ = smtp.construir_mensaje(
            {"Id": 1, "IdFirma": None, "Para": "a@b.com",
             "HtmlSnapshot": "<p>Hola</p>", "TextoSnapshot": "   ",
             "Asunto": "x"},
            {"Email": "yo@x.com"})
        tipos = [p.get_content_type()
                 for p in email.message_from_bytes(msg.as_bytes()).walk()]
        self.assertIn("text/plain", tipos)


# ═══════════════════════════════════════════════════════════════════════════════
# El MIME, que es donde el fallo es invisible
# ═══════════════════════════════════════════════════════════════════════════════

class TestMimeConFirma(_Base):
    def setUp(self):
        super().setUp()
        self.png = self._dejar_firma(7, "logo.png", _png_1x1())
        self._filas_anterior = smtp.filas
        smtp.filas = lambda sql, params=(): [
            {"Id": 1, "Nombre": "logo.png", "ContentType": "image/png",
             "Cid": "logo", "Clave": "logo.png"},
        ]

    def tearDown(self):
        smtp.filas = self._filas_anterior
        super().tearDown()

    def _fila(self, **cambios):
        base = {
            "Id": 1, "IdFirma": 7,
            "HtmlSnapshot": '<p>Hola</p><img src="cid:logo">',
            "TextoSnapshot": "Hola\n\n-- \nSaludos",
            "Asunto": "Prueba", "Para": "destino@otro.com",
            "Cc": "copia@x.com", "InResponderA": "<orig@otro.com>",
        }
        base.update(cambios)
        return base

    def _construir(self, **cambios):
        return smtp.construir_mensaje(self._fila(**cambios), {"Email": "yo@x.com"})

    def test_estructura_anidada(self):
        """
        related → alternative → text/plain + text/html, con la imagen colgando
        del `related` y NO del `alternative` (RFC 2046: alternative solo lleva
        texto).
        """
        msg, _ = self._construir()
        self.assertEqual(msg.get_content_type(), "multipart/related")
        msg = email.message_from_bytes(msg.as_bytes())
        tipos = [p.get_content_type() for p in msg.walk()]
        self.assertIn("multipart/alternative", tipos)
        self.assertIn("text/plain", tipos)
        self.assertIn("text/html", tipos)
        self.assertIn("image/png", tipos)

    def test_el_content_id_coincide_con_el_html(self):
        """El fallo clásico: el correo llega con la imagen en blanco."""
        msg, _ = self._construir()
        parseado = email.message_from_bytes(msg.as_bytes())
        pedidos = smtp.cids_en_html(self._fila()["HtmlSnapshot"])
        transportados = {p["Content-ID"] for p in parseado.walk() if p["Content-ID"]}
        for cid in pedidos:
            self.assertIn(f"<{cid}>", transportados,
                          f"el HTML pide cid:{cid} pero ninguna parte lo trae")

    def test_los_bytes_de_la_imagen_viajan_intactos(self):
        msg, _ = self._construir()
        for parte in email.message_from_bytes(msg.as_bytes()).walk():
            if parte.get_content_maintype() == "image":
                self.assertEqual(parte.get_payload(decode=True), self.png)
                self.assertTrue(parte["Content-Disposition"].startswith("inline"))

    def test_el_html_conserva_su_cid(self):
        """El HTML NO se reescribe: el `cid:` tiene que seguir apuntando al
        Content-ID. Reescribirlo aquí rompería el mensaje."""
        msg, _ = self._construir()
        for parte in email.message_from_bytes(msg.as_bytes()).walk():
            if parte.get_content_type() == "text/html":
                self.assertIn('src="cid:logo"',
                              parte.get_payload(decode=True).decode("utf-8"))

    def test_always_text_plano(self):
        """Sin texto plano, el Outlook de escritorio muestra el correo EN BLANCO."""
        msg, _ = self._construir(TextoSnapshot="   ")
        tipos = [p.get_content_type()
                 for p in email.message_from_bytes(msg.as_bytes()).walk()]
        self.assertIn("text/plain", tipos)
        self.assertIn("text/html", tipos)

    def test_solo_texto_no_parte_imagenes(self):
        msg, faltan = self._construir(
            HtmlSnapshot="<p>Hola</p>", TextoSnapshot="Hola")
        self.assertEqual(faltan, [])
        self.assertNotEqual(msg.get_content_type(), "multipart/related")

    def test_cid_sin_resolver_se_reporta_pero_no_tumba(self):
        """Una imagen que falta se avisa; el correo igual sale."""
        msg, faltan = self._construir(
            HtmlSnapshot='<img src="cid:logo"><img src="cid:fantasma">')
        self.assertEqual(faltan, ["fantasma"])
        tipos = [p.get_content_type()
                 for p in email.message_from_bytes(msg.as_bytes()).walk()]
        self.assertIn("image/png", tipos)

    def test_firma_sin_imagenes_en_el_volumen(self):
        """Si la imagen no está, el correo sale igual y se reporta."""
        self._filas_anterior2 = smtp.filas
        smtp.filas = lambda sql, params=(): [
            {"Id": 1, "Nombre": "logo.png", "ContentType": "image/png",
             "Cid": "logo", "Clave": "no-existe.png"},
        ]
        msg, faltan = self._construir()
        self.assertEqual(faltan, ["logo"])
        self.assertNotEqual(msg.get_content_type(), "multipart/related")

    def test_bcc_no_aparece_en_cabeceras(self):
        """El Bcc viaja en el sobre SMTP, nunca en las cabeceras del mensaje."""
        msg, _ = self._construir(Bcc="oculto@x.com")
        self.assertIsNone(msg["Bcc"])

    def test_hilo(self):
        msg, _ = self._construir()
        self.assertEqual(msg["In-Reply-To"], "<orig@otro.com>")
        self.assertEqual(msg["References"], "<orig@otro.com>")

    def test_sin_message_id_de_otro_no_rompe(self):
        """Un Message-ID de más de 500 chars se ignora en vez de propagarse."""
        msg, _ = self._construir(InResponderA="x" * 900)
        self.assertIsNone(msg["In-Reply-To"])


# ═══════════════════════════════════════════════════════════════════════════════
# Almacén
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlmacen(_Base):
    def test_cuerpo_roundtrip(self):
        html = "<html><body>Hola señor ñandú ☕</body></html>"
        self.assertTrue(almacen.guardar_cuerpo(3, "abc123", html))
        self.assertEqual(almacen.leer_cuerpo(3, "abc123"), html)
        self.assertTrue(almacen.existe_cuerpo(3, "abc123"))

    def test_cuerpo_inexistente(self):
        self.assertEqual(almacen.leer_cuerpo(3, "no-existe"), "")
        self.assertFalse(almacen.existe_cuerpo(3, "no-existe"))

    def test_cuerpo_vacio_no_se_guarda(self):
        self.assertFalse(almacen.guardar_cuerpo(3, "vacio", ""))
        self.assertFalse(almacen.existe_cuerpo(3, "vacio"))

    def test_borrar_cuerpo(self):
        almacen.guardar_cuerpo(3, "k", "x")
        self.assertTrue(almacen.borrar_cuerpo(3, "k"))
        self.assertFalse(almacen.existe_cuerpo(3, "k"))
        self.assertFalse(almacen.borrar_cuerpo(3, "k"))

    def test_leer_firma(self):
        datos = self._dejar_firma(7, "logo.png", b"PNG")
        self.assertEqual(almacen.leer_firma(7, "logo.png"), datos)

    def test_firma_no_existe(self):
        self.assertEqual(almacen.leer_firma(7, "nada.png"), b"")

    def test_firma_path_traversal_bloqueado(self):
        """
        Sin este chequeo, un token con `../` leería cualquier archivo del
        contenedor desde la API de firmas.
        """
        with open(os.path.join(self.dir, "secreto.txt"), "w") as fh:
            fh.write("no leer")
        self.assertEqual(almacen.leer_firma(7, "../../../secreto.txt"), b"")
        self.assertEqual(almacen.leer_firma(7, "a/b"), b"")

    def test_digest_estable_y_sensible_al_offset(self):
        a = almacen.digest_de(3, 10, "2.1", 0, 262144)
        b = almacen.digest_de(3, 10, "2.1", 0, 262144)
        c = almacen.digest_de(3, 10, "2.1", 262144, 262144)
        d = almacen.digest_de(4, 10, "2.1", 0, 262144)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c, "el offset tiene que cambiar el digest")
        self.assertNotEqual(a, d, "la cuenta tiene que cambiar el digest")

    def test_cache_roundtrip_y_ttl(self):
        self.assertTrue(almacen.cache_escribir("ab" + "0" * 38, b"datos"))
        leidos, edad = almacen.cache_leer("ab" + "0" * 38)
        self.assertEqual(leidos, b"datos")
        self.assertGreaterEqual(edad, 0)

    def test_cache_vencida_no_se_sirve(self):
        digest = "cd" + "0" * 38
        almacen.cache_escribir(digest, b"viejo")
        ruta = almacen._cache_ruta(digest)
        os.utime(ruta, (0, 0))            # 1970: vencido hace años
        leidos, _ = almacen.cache_leer(digest)
        self.assertIsNone(leidos, "una cache vencida no se debe servir")
        self.assertFalse(os.path.exists(ruta), "y se debe borrar al detectarla")

    def test_cache_limpia_lo_vencido(self):
        digest = "ef" + "0" * 38
        almacen.cache_escribir(digest, b"x")
        os.utime(almacen._cache_ruta(digest), (0, 0))
        self.assertGreaterEqual(almacen.cache_limpiar(), 1)

    def test_cache_limpia_por_tamano(self):
        settings.cache_max_mb = 0.001
        for i in range(6):
            almacen.cache_escribir(f"{i:02d}" + "0" * 38, os.urandom(400))
        almacen.cache_limpiar()
        self.assertLessEqual(almacen.cache_tamano_mb(), 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)