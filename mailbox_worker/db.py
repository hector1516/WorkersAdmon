"""
mailbox_worker/db.py — conexiones a SQL Server (pymssql).

Diferencias con el `db.py` del hubmail_worker, y por qué no se copió:

  · El otro es un POOL DE PyMySQL con un wrapper que finge ser pymssql. Acá se
    habla SQL Server de verdad, así que no hay nada que fingir.
  · Conexión por hilo con sonda de vida, igual que `eccsa_db.py` y que el
    `db.py` de Field. El worker corre N hilos de cuenta y cada uno atiende su
    cuenta; una conexión por hilo evita a la vez abrir una por consulta y
    compartir una entre hilos, que es donde pymssql se corrompe.
  · `autocommit=True`: no hay transacción multi-sentencia que valga la pena
    (el sync escribe mensaje por mensaje), y con autocommit no queda nada a medias.

REGLA DEL ECOSISTEMA: `%s` para los parámetros. pymssql es DB-Lib y **no
soporta `?`**. Y en un `IN (...)` hay que pasar `tuple()`, no `list()`: hay una
entrada que cuesta encontrar.
"""

import threading
from typing import Any, List, Optional

import pymssql

from .config import settings

_local = threading.local()

# Lo que Devolver NO es un dict: pymssql usa POSICIONALES salvo que pidas
# `as_dict=True`, y una fila posicional que se accede por nombre revienta con
# TypeError. El worker viejo tenía exactamente ese bug en `_push_new_mail`.


def get_connection():
    """Conexión del hilo actual, reconectando si se murió."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _local.conn = None

    conn = pymssql.connect(
        server=settings.db_server,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        login_timeout=15,
        timeout=60,
        autocommit=True,
    )
    _local.conn = conn
    return conn


def filas(sql: str, params: tuple = ()) -> List[dict]:
    cur = get_connection().cursor(as_dict=True)
    cur.execute(sql, params)
    out = cur.fetchall()
    cur.close()
    return out


def una(sql: str, params: tuple = ()) -> Optional[dict]:
    r = filas(sql, params)
    return r[0] if r else None


def ejecuta(sql: str, params: tuple = ()) -> int:
    """Ejecuta y devuelve rowcount. rowcount es -1 en un SELECT: por eso los
    UPDATE con `WHERE ... rowcount == 0` son la forma canónica de comprobar
    que una escritura affectó lo esperado (ver docs/ECONTRATO de Field)."""
    cur = get_connection().cursor()
    cur.execute(sql, params)
    n = cur.rowcount
    cur.close()
    return n


def ultimo_id() -> int:
    """SCOPE_IDENTITY() y no cur.lastrowid: pymssql no lo expone."""
    cur = get_connection().cursor(as_dict=True)
    cur.execute("SELECT SCOPE_IDENTITY() AS Id")
    fila = cur.fetchone()
    cur.close()
    return int(fila["Id"]) if fila and fila.get("Id") is not None else 0


def ping() -> bool:
    """El panel lo usa para el botón de "probar". No debe lanzar."""
    try:
        cur = get_connection().cursor()
        cur.execute("SELECT 1 AS Ok")
        fila = cur.fetchone()
        cur.close()
        return bool(fila)
    except Exception as exc:
        print(f"[db] ping falló: {exc}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# El candado de instancia única
# ═══════════════════════════════════════════════════════════════════════════════
# Dos workers de correo a la vez NO es redundancia: los dos drenarían la misma
# cola de operaciones, los dos aplicarían un APPEND a Enviados (correo duplicado
# en el buzón del cliente) y los dos abrirían conexiones IMAP sobre las mismas
# cuentas.
#
# El hubmail_worker usaba `GET_LOCK`, que es de MySQL. En SQL Server lo
# equivalente es `sp_getapplock`, que es un candado del SERVIDOR amarrado a la
# sesión: sobrevive a que maten el proceso a la fuerza y no depende de que /data
# sea un volumen compartido.
#
# Devuelve True si este proceso es el worker. False si ya hay otro.


def tomar_candado(nombre: str, timeout_s: int = 3) -> bool:
    """
    Toma el candado de instancia única. True = este proceso es el worker.

    `sp_getapplock` devuelve un entero: >= 0 es éxito, < 0 es error. En
    particular -1 es "timeout", o sea que hay otro worker vivo.

    El timeout es corto a propósito (3 s): si ya hay otro, hay que enterarse
    rápido y NO hacer el trabajo dos veces. El worker viejo lo tenía igual y el
    comentario explicaba por qué.
    """
    try:
        cur = get_connection().cursor()
        cur.execute(
            "DECLARE @r INT; EXEC @r = sp_getapplock "
            "@Resource = %s, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = %s; SELECT @r AS r",
            (nombre, timeout_s),
        )
        fila = cur.fetchone()
        cur.close()
        if not fila:
            return False
        res = int(fila[0]) if not isinstance(fila, dict) else int(list(fila.values())[0])
        if res >= 0:
            return True
        if res == -1:
            print(f"[candado] YA HAY UN WORKER DE CORREO CORRIENDO ({nombre} tomado).")
            print("[candado] Este proceso se sale para no duplicar la sincronización.")
            print("[candado] Apagá el otro antes de encender este.")
        else:
            print(f"[candado] sp_getapplock devolvió {res} (error)")
        return False
    except Exception as exc:
        print(f"[candado] no pude pedirlo: {exc}")
        return False


def soltar_candado(nombre: str) -> None:
    try:
        cur = get_connection().cursor()
        cur.execute("EXEC sp_releaseapplock @Resource = %s, @LockOwner = 'Session'", (nombre,))
        cur.close()
    except Exception:
        pass