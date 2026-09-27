import pymssql
import threading
from config import load_db_config

DB_CONFIG = load_db_config()
_local = threading.local()

def get_connection():
    conn = getattr(_local, 'conn', None)
    try:
        if conn is not None:
            conn.cursor().execute("SELECT 1")
            return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
        conn = None
    if conn is None:
        conn = pymssql.connect(**DB_CONFIG, login_timeout=10, timeout=30)
        _local.conn = conn
    return conn
