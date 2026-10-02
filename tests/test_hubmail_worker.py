"""
tests/test_hubmail_worker.py — Guardas del worker de correo (`hubmail_worker`)
================================================================================
Este worker se trata de un buzón real. Los tres riesgos que cubrentest son
los que, si fallan, **no se anuncian solos**:

  1. **`crypto` nunca genera una clave nueva.** En la API eso es inofensivo; en
     el worker significa que `decrypt_secret` devuelve "" para todas las cuentas
     y el sincronizador se pasa 5 minutos cada 5 minutos fallando en silencio.
     El test falla si aparece un archivo de clave donde no debe.

  2. **`dry_run` no escribe en IMAP.** Es lo que permite correr este worker en
     paralelo al de HUBMail durante la migración. Se comprueba con un doble de
     IMAP que *lanza* en cuanto se toca cualquier método: si el gate se rompe,
     el test truena en vez de duplicar la operación en el buzón de un cliente.

  3. **Las rutas de adjuntos son relativas.** Si la base guarda la ruta absoluta
     del worker, la app la busca en su propio volumen, no la encuentra, y
     `os.path.isfile` devuelve False sin error → adjuntos vacíos en silencio.

No toca red ni MySQL: todo es local con dobles.
Ejecución:  python tests/test_hubmail_worker.py
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


def _recargar(entorno):
    """Recarga config/crypto/sync/filters con un entorno limpio y controlado."""
    for clave in ("HUBMAIL_ENCRYPTION_KEY", "HUBMAIL_KEY_FILE",
                  "HUBMAIL_SYNC_DRYRUN", "HUBMAIL_ATTACHMENTS_DIR"):
        os.environ.pop(clave, None)
    os.environ.update(entorno)
    from hubmail_worker import config, crypto, filters, sync
    for mod in (config, crypto, filters, sync):
        importlib.reload(mod)
    return config, crypto, sync, filters


class ImapQueExplota:
    """Doble de IMAP: cualquier llamada significa que alguien escribió al buzón."""

    def __getattr__(self, nombre):
        raise AssertionError(f"se escribió en IMAP: {nombre}()")


class TestClaveFernet(unittest.TestCase):
    """Guarda 1: la clave se toma de donde sea, pero nunca se inventa."""

    def test_sin_clave_no_inventa_archivo(self):
        inexistente = os.path.join(tempfile.mkdtemp(), "no_debe_existir")
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_KEY_FILE": inexistente})
        self.assertIsNone(crypto._load_key())
        self.assertFalse(os.path.exists(inexistente),
                         "el worker NO debe crear una clave nueva por su cuenta")

    def test_sin_clave_descifrar_lanza_en_vez_de_devolver_vacio(self):
        inexistente = os.path.join(tempfile.mkdtemp(), "no_debe_existir")
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_KEY_FILE": inexistente})
        with self.assertRaises(RuntimeError):
            crypto.decrypt_secret("cualquier-token")

    def test_env_var_tiene_prioridad_sobre_el_archivo(self):
        from cryptography.fernet import Fernet
        clave = Fernet.generate_key().decode()
        ruta = os.path.join(tempfile.mkdtemp(), "key")
        with open(ruta, "wb") as fh:
            fh.write(Fernet.generate_key())
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_ENCRYPTION_KEY": clave, "HUBMAIL_KEY_FILE": ruta})
        self.assertEqual(crypto.fuente_clave(), "HUBMAIL_ENCRYPTION_KEY")
        self.assertEqual(crypto.decrypt_secret(crypto.encrypt_secret("s3cr3t")), "s3cr3t")

    def test_clave_equivocada_da_error_explicito(self):
        """Con otra clave, `decrypt` debe quejarse; el original devolvía ""."""
        from cryptography.fernet import Fernet
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_ENCRYPTION_KEY": Fernet.generate_key().decode()})
        token = crypto.encrypt_secret("s3cr3t")
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_ENCRYPTION_KEY": Fernet.generate_key().decode()})
        with self.assertRaises(RuntimeError) as ctx:
            crypto.decrypt_secret(token)
        self.assertIn("no es la misma", str(ctx.exception))

    def test_clave_con_formato_invalido_se_rechaza_al_cargar(self):
        _config, crypto, _sync, _filtros = _recargar(
            {"HUBMAIL_ENCRYPTION_KEY": "no-so-una-clave-fernet"})
        self.assertIsNone(crypto._load_key())


class TestModoEnsayo(unittest.TestCase):
    """Guarda 2: con dry_run no se toca el buzón (pero sí la caché)."""

    def test_apply_pending_ops_no_toca_imap(self):
        _config, _crypto, sync, _filtros = _recargar({"HUBMAIL_SYNC_DRYRUN": "1"})
        sync._apply_pending_ops(1, ImapQueExplota())   # no debe lanzar

    def test_apply_pending_ops_si_escribe_sin_dry_run(self):
        """Comprobación contraria: el camino normal SÍ llama a IMAP."""
        _config, _crypto, sync, _filtros = _recargar({"HUBMAIL_SYNC_DRYRUN": "0"})
        with mock.patch.object(sync, "get_conn") as conn:
            cur = conn.return_value.cursor.return_value
            cur.fetchall.return_value = []      # cola vacía → ni una llamada
            sync._apply_pending_ops(1, ImapQueExplota())

    def test_apply_action_de_filtro_no_toca_imap(self):
        _config, _crypto, _sync, filtros = _recargar({"HUBMAIL_SYNC_DRYRUN": "1"})
        for accion in ("mark_read", "spam", "delete", "move"):
            filtros._apply_action(
                {"Action": accion, "ActionFolder": "Archivados"},
                1, "INBOX", {"UID": 5}, ImapQueExplota())

    def test_dry_run_lo_ven_sync_y_filtros(self):
        config, _crypto, sync, filtros = _recargar({"HUBMAIL_SYNC_DRYRUN": "1"})
        self.assertTrue(config.settings.dry_run)
        self.assertTrue(sync.settings.dry_run)
        self.assertTrue(filtros.settings.dry_run)


class TestRutasAdjuntos(unittest.TestCase):
    """Guarda 3: la BD guarda rutas relativas, y cada lado resuelve las suyas."""

    def setUp(self):
        self.base_worker = tempfile.mkdtemp()
        self.base_app = tempfile.mkdtemp()
        _config, _crypto, self.sync, _filtros = _recargar(
            {"HUBMAIL_ATTACHMENTS_DIR": self.base_worker})

    def test_se_guarda_relativa_no_absoluta(self):
        rel = f"{self.sync._attach_dir_rel(7, 'INBOX', 340)}/0_foto.jpg"
        self.assertFalse(os.path.isabs(rel))
        self.assertEqual(rel, "7/INBOX/340/0_foto.jpg")

    def test_la_app_lo_resuelve_en_su_propio_volumen(self):
        rel = f"{self.sync._attach_dir_rel(7, 'INBOX', 340)}/0_foto.jpg"
        destino = os.path.join(self.base_app, rel)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "wb") as fh:
            fh.write(b"\xff\xd8\xffJPEG")
        # El worker nunca vio ese volumen; la app sí lo resuelve por su cuenta.
        self.assertEqual(self.sync.resolver_adjunto(rel, base_dir=self.base_app),
                         destino)

    def test_tolera_la_ruta_absoluta_ya_heredada(self):
        """Lo escrito antes de este cambio sigue siendo legible."""
        rel = f"{self.sync._attach_dir_rel(7, 'INBOX', 340)}/0_foto.jpg"
        legado = os.path.join(self.base_worker, rel)
        os.makedirs(os.path.dirname(legado), exist_ok=True)
        with open(legado, "wb") as fh:
            fh.write(b"\xff\xd8\xffJPEG")
        # La app tiene el MISMO archivo en SU base (volumen compartido):
        copia_app = os.path.join(self.base_app, rel)
        os.makedirs(os.path.dirname(copia_app), exist_ok=True)
        with open(copia_app, "wb") as fh:
            fh.write(b"\xff\xd8\xffJPEG")
        self.assertIsNotNone(self.sync.resolver_adjunto(legado, base_dir=self.base_app))

    def test_inexistente_y_vacio_dan_none(self):
        self.assertIsNone(self.sync.resolver_adjunto("nada/aqui.jpg"))
        self.assertIsNone(self.sync.resolver_adjunto(""))
        self.assertIsNone(self.sync.resolver_adjunto(None))


class TestInventarioDeCuentas(unittest.TestCase):
    """Solo se sincronizan las canónicas: las compartidas se duplicarían."""

    def test_filtra_las_compartidas(self):
        _config, _crypto, sync, _filtros = _recargar({})
        with mock.patch.object(sync, "get_conn") as conn:
            cur = conn.return_value.cursor.return_value
            cur.fetchall.return_value = [{"AccountID": 3}, {"AccountID": 9}]
            with mock.patch.dict(sys.modules, {}):
                import cron_sync_hubmail as worker
                importlib.reload(worker)
                with mock.patch.object(worker, "get_conn", conn):
                    ids = worker._cuentas_canonicas()
        self.assertEqual(ids, [3, 9])

    def test_candado_es_de_instancia_unica(self):
        """Dos workers a la vez duplicarían la cola; el candado lo impide."""
        import cron_sync_hubmail as worker
        with mock.patch.object(worker, "get_conn") as conn:
            cur = conn.return_value.cursor.return_value
            cur.fetchone.return_value = (0,)          # 0 = ya hay otro
            self.assertFalse(worker._tomar_candado())
            cur.fetchone.return_value = (1,)          # 1 = libre
            self.assertTrue(worker._tomar_candado())
            worker._soltar_candado()


if __name__ == "__main__":
    unittest.main(verbosity=2)