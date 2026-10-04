"""
tests/test_almacen_adjuntos.py — Destino de los adjuntos (disco y SMB)
=======================================================================
Los adjuntos pasaron de un directorio local a `\\\\Fileserver\\HUB\\Mailbox`, que
es el share que ya usan `pdf_storage_worker` y `file_indexer`. Eso obliga a
tres cosas, y este archivo las cubre:

  1. **El destino no cambia lo que se guarda en la base.** `FilePath` sigue
     siendo la ruta RELATIVA con disco o con SMB. Si esto se rompe, cambiar de
     destino obliga a migrar 9.000+ filas y el problema reaparece cada vez que
     se mueve algo.
  2. **Las rutas absolutas que HUBMail dejó en la base siguen leyéndose.** Hay
     miles de filas con `/data/attachments/...`; si `normalizar_adjunto()` no
     las recorta, esos adjuntos desaparecen.
  3. **Si el share no responde, se reintenta y se avisa — nunca se cae al
     disco.** Caer "por si acaso" reintroduce los dos destinos, que es
     justamente lo que se vino a arreglar.

El SMB se prueba con un doble, no contra el share real: los tests tienen que
pisar sin red.
"""
import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("HUBMAIL_DB_PASSWORD", "prueba")

from hubmail_worker import almacen as mod  # noqa: E402


def _recargar(entorno):
    for k in list(os.environ):
        if k.startswith("HUBMAIL_") or k.startswith("HUB_SMB"):
            os.environ.pop(k, None)
    os.environ.setdefault("HUBMAIL_DB_PASSWORD", "prueba")
    os.environ.update(entorno)
    from hubmail_worker import config, sync
    for m in (config, sync):
        importlib.reload(m)
    mod.reiniciar_almacen()
    return config, sync


class TestDisco(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.a = mod.AlmacenDisco(self.base)

    def test_ida_y_vuelta(self):
        self.assertTrue(self.a.escribir("2/INBOX/7/0_foto.jpg", b"\xff\xd8\xffdatos"))
        self.assertTrue(self.a.existe("2/INBOX/7/0_foto.jpg"))
        self.assertEqual(self.a.leer("2/INBOX/7/0_foto.jpg"), b"\xff\xd8\xffdatos")

    def test_inexistente_devuelve_none(self):
        self.assertIsNone(self.a.leer("nada/aqui.jpg"))
        self.assertFalse(self.a.existe("nada/aqui.jpg"))

    def test_crea_directorios_intermedios(self):
        self.a.escribir("15/OXXOGas/1999/0_a.pdf", b"%PDF-")
        self.assertTrue(os.path.isfile(
            os.path.join(self.base, "15", "OXXOGas", "1999", "0_a.pdf")))

    def test_sobrescribe(self):
        self.a.escribir("a/b.txt", b"viejo")
        self.a.escribir("a/b.txt", b"nuevo")
        self.assertEqual(self.a.leer("a/b.txt"), b"nuevo")


class _SmbFalso:
    """Doble de pysmb: no red, pero con los mismos métodos y errores."""

    def __init__(self, fallar_en=None):
        self.escritos = {}
        self.creados = []
        self.fallar_en = fallar_en or set()
        self.escrituras = 0

    # -- la clase de error de pysmb no importa: lo que importa es que NO sea
    #    una excepción de negocio. Se usa Exception, que es lo que se captura.
    def storeFile(self, share, remoto, fh, timeout=30):
        self.escrituras += 1
        if remoto in self.fallar_en:
            raise RuntimeError("SMBTimeout simulado")
        self.escritos[remoto] = fh.read()
        return True

    def createDirectory(self, share, path, timeout=30):
        self.creados.append(path)

    def retrieveFile(self, share, remoto, destino, timeout=30):
        # pysmb toma un objeto de archivo abierto, no una ruta.
        if remoto not in self.escritos:
            raise RuntimeError("no existe")
        destino.write(self.escritos[remoto])

    def getSize(self, share, remoto, timeout=30):
        return len(self.escritos[remoto])

    def close(self):
        pass


class TestSmb(unittest.TestCase):
    def _almacen(self, falso=None):
        a = mod.AlmacenSmb("10.0.0.1", "HUB", "eccsa", "clave", base="Mailbox")
        if falso is not None:
            a._conexion = lambda: falso
            a._cerrar = lambda: None
        return a

    def test_la_base_del_share_se_antepone(self):
        a = self._almacen(_SmbFalso())
        a.escribir("2/INBOX/7/0_foto.jpg", b"x")
        self.assertIn("Mailbox/2/INBOX/7/0_foto.jpg", a._conexion().escritos)

    def test_crea_toda_la_cadena_de_directorios(self):
        falso = _SmbFalso()
        self._almacen(falso).escribir("15/OXXOGas/1999/0_a.pdf", b"x")
        self.assertEqual(falso.creados,
                         ["Mailbox", "Mailbox/15", "Mailbox/15/OXXOGas",
                          "Mailbox/15/OXXOGas/1999"])

    def test_ida_y_vuelta(self):
        falso = _SmbFalso()
        a = self._almacen(falso)
        a.escribir("2/INBOX/7/0_foto.jpg", b"\xff\xd8\xff")
        self.assertEqual(a.leer("2/INBOX/7/0_foto.jpg"), b"\xff\xd8\xff")
        self.assertTrue(a.existe("2/INBOX/7/0_foto.jpg"))

    def test_leer_inexistente_devuelve_none(self):
        a = self._almacen(_SmbFalso())
        self.assertIsNone(a.leer("no/existe.jpg"))

    def test_reintenta_ante_un_fallo(self):
        """El share corta sesiones; un fallo puntual no puede perder el adjunto."""
        falso = _SmbFalso()
        intentos = {"n": 0}
        original = falso.storeFile

        def intermitente(share, remoto, fh, timeout=30):
            intentos["n"] += 1
            if intentos["n"] <= 2:
                raise RuntimeError("Broken pipe")
            return original(share, remoto, fh, timeout)

        falso.storeFile = intermitente
        a = self._almacen(falso)
        a.escribir("a/b.txt", b"datos")
        self.assertEqual(intentos["n"], 3)
        self.assertEqual(falso.escritos["Mailbox/a/b.txt"], b"datos")

    def test_si_persiste_el_fallo_avisa_y_no_cae_al_disco(self):
        falso = _SmbFalso(fallar_en={"Mailbox/a/b.txt"})
        a = self._almacen(falso)
        a.reintentos = 2
        with self.assertRaises(mod.ErrorAlmacen):
            a.escribir("a/b.txt", b"datos")
        # NADA se escribió en disco: caer al disco sería el bug que se evita.
        for raiz in ("/data/attachments", tempfile.gettempdir()):
            p = os.path.join(raiz, "a", "b.txt")
            self.assertFalse(os.path.isfile(p), f"se escapó un archivo a {p}")


class TestFabrica(unittest.TestCase):
    def test_sin_configurar_usa_disco(self):
        config, _sync = _recargar({})
        self.assertIsInstance(mod.get_almacen(), mod.AlmacenDisco)

    def test_con_el_share_configurado_usa_smb(self):
        _config, _sync = _recargar({"HUBMAIL_ADJUNTOS_SMB": "1",
                                    "HUBMAIL_SMB_SERVER": "10.0.0.1",
                                    "HUBMAIL_SMB_SHARE": "HUB",
                                    "HUBMAIL_SMB_PASSWORD": "clave",
                                    "HUBMAIL_SMB_BASE": "Mailbox"})
        a = mod.get_almacen()
        self.assertIsInstance(a, mod.AlmacenSmb)
        self.assertEqual(a.servidor, "10.0.0.1")
        self.assertEqual(a.base, "Mailbox")

    def test_escribir_el_share_solo_ya_activa_smb(self):
        _config, _sync = _recargar({"HUBMAIL_SMB_SHARE": "HUB"})
        self.assertIsInstance(mod.get_almacen(), mod.AlmacenSmb)


class TestRutasDeLaBase(unittest.TestCase):
    """Lo que la base tiene hoy y lo que la app tiene que poder leer."""

    def setUp(self):
        _config, self.sync = _recargar({})

    def test_relativa_canonica_no_se_toca(self):
        self.assertEqual(self.sync.normalizar_adjunto("2/INBOX/7/0_foto.jpg"),
                         "2/INBOX/7/0_foto.jpg")

    def test_absoluta_heredada_de_hubmail_se_recorta(self):
        self.assertEqual(
            self.sync.normalizar_adjunto("/data/attachments/2/INBOX/7/0_foto.jpg"),
            "2/INBOX/7/0_foto.jpg")

    def test_absoluta_con_otro_prefijo_se_recorta_por_el_accountid(self):
        self.assertEqual(
            self.sync.normalizar_adjunto("/otro/lugar/15/OXXOGas/9/0_a.pdf"),
            "15/OXXOGas/9/0_a.pdf")

    def test_vacia_o_nula(self):
        self.assertIsNone(self.sync.normalizar_adjunto(""))
        self.assertIsNone(self.sync.normalizar_adjunto(None))

    def test_leer_adjunto_delega_en_el_almacen(self):
        base = tempfile.mkdtemp()
        mod.reiniciar_almacen()
        mod._almacen = mod.AlmacenDisco(base)
        mod.AlmacenDisco(base).escribir("2/INBOX/7/0_foto.jpg", b"\xff\xd8\xff")
        self.assertEqual(self.sync.leer_adjunto("/data/attachments/2/INBOX/7/0_foto.jpg"),
                         b"\xff\xd8\xff")

    def test_leer_adjunto_vacio_da_none(self):
        self.assertIsNone(self.sync.leer_adjunto(""))
        self.assertIsNone(self.sync.leer_adjunto(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)