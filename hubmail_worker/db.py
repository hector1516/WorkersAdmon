"""
hubmail_worker/db.py — Pool de conexiones a MySQL (HUBMAIL)
===========================================================
Adaptación de `HUBMail/app/db.py`.

Qué se quita y por qué:

* **`get_users_conn()` y el import de pymssql.** El worker no consulta SQL
  Server en ningún punto: `sync.py`, `filters.py`, `push.py` e
  `imap_client.py` solo hablan MySQL. Verificado con grep sobre los cuatro
  módulos. Sacar pymssql deja el paquete del worker sin ninguna dependencia de
  SQL Server.

* **`autocommit` como propiedad.** Se conserva porque `_MySQLPool` lo usa al
  reciclar una conexión (ver `_release`), pero se documenta que en MySQL el
  "autocommit" real lo aplica la conexiónunderlying, no el wrapper.

* **`time` sin usar.** El import estaba en el original y no se usaba.

Lo que NO se toca es el comportamiento del pool (10 conexiones, ping con
reconnect, espera de 15 s cuando está lleno) porque `sync_account()` abre
conexiones desde varios hilos a la vez: un pool más chico se traduce en
timeouts en el ciclo de sincronización.
"""
import queue
import threading

import pymysql

from .config import settings


class _MySQLConnection:
    """Envuelve una conexión PyMySQL con la misma API que pymssql.

    El código de HUBMail (traído sin cambios) usa `cursor(as_dict=True)`,
    `commit()`, `rollback()` y `close()`; esto le da lo mismo.
    """

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool
        self._autocommit = False
        self._in_use = True

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._autocommit = bool(value)
        self._conn.autocommit(bool(value))

    def cursor(self, as_dict=False):
        if as_dict:
            return self._conn.cursor(cursor=pymysql.cursors.DictCursor)
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        """Devuelve la conexión al pool en vez de cerrarla (compatible pymssql)."""
        if self._pool is not None:
            self._pool._release(self)
        else:
            self._conn.close()


class _MySQLPool:
    """Pool simple de conexiones PyMySQL, con recycled + ping."""

    def __init__(self, max_size=10, max_idle_sec=300):
        self._max_size = max_size
        self._max_idle = max_idle_sec
        self._pool = queue.Queue(maxsize=max_size)
        self._size = 0
        self._lock = threading.Lock()

    def _create_conn(self):
        return pymysql.connect(
            host=settings.db_server,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            charset="utf8mb4",
            connect_timeout=10,
            autocommit=False,
        )

    def _descartar(self, wrapper):
        """Cierra una conexión muerta y libera su lugar en el pool."""
        with self._lock:
            self._size -= 1
        try:
            wrapper._conn.close()
        except Exception:
            pass

    def _release(self, wrapper):
        wrapper._in_use = False
        try:
            # Ping para confirmar que sigue viva; reconnect=True la revive si
            # el servidor la cerró por wait_timeout (MySQL default 8 h).
            wrapper._conn.ping(reconnect=True)
            self._pool.put_nowait(wrapper)
        except Exception:
            self._descartar(wrapper)

    def get_conn(self):
        # 1) Intento reutilizar una ociosa.
        while True:
            try:
                wrapper = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                wrapper._conn.ping(reconnect=True)
                wrapper._in_use = True
                return wrapper
            except Exception:
                self._descartar(wrapper)

        # 2) Crear una nueva si aún hay lugar.
        with self._lock:
            if self._size < self._max_size:
                self._size += 1
                try:
                    return _MySQLConnection(self._create_conn(), pool=self)
                except Exception:
                    self._size -= 1
                    raise

        # 3) Pool lleno: esperar a que alguien libere.
        wrapper = self._pool.get(timeout=15)
        try:
            wrapper._conn.ping(reconnect=True)
            wrapper._in_use = True
            return wrapper
        except Exception:
            self._descartar(wrapper)
            # Reintento recursivo: ya se liberó el lugar que se acaba de cerrar.
            return self.get_conn()


_pool = _MySQLPool(max_size=10)


def get_conn():
    """Conexión MySQL prestada por el pool. Usar con `close()` siempre."""
    return _pool.get_conn()