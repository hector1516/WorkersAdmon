"""
wa_envios.py - memoria de lo ya enviado hoy
===========================================
Antes de mandar cualquier WhatsApp se consulta esta capa: si el mismo texto ya
salió hoy a ese chat, no se repite. Es la última red contra el spam, además del
dedupe por tipo que tiene cada worker.

WhatsApp puede bloquear un número que manda lo mismo varias veces, y a la
persona le suena el teléfono por un aviso que ya vio.
"""
from datetime import date

try:
    from tz_mx import now_mx
except Exception:  # pragma: no cover - respaldo si falta el modulo
    def now_mx():
        from datetime import datetime
        return datetime.now()


def repetido(chat, texto, conn=None, dia=None):
    """¿Ya se envió exactamente este texto a este chat hoy?"""
    chat = str(chat or "").strip()
    texto = str(texto or "").strip()
    if not chat or not texto:
        return False
    if conn is None:
        # En pruebas o en un equipo sin credenciales: no intentar conectar
        # (get_connection tarda 30 s en rendirse y colgaría la suite).
        try:
            import config_db
            cfg = config_db.load_db_config() or {}
            if not str(cfg.get("password") or "").strip():
                return False
        except Exception:
            return False
    propio = conn is None
    c = conn
    if c is None:
        try:
            import eccsa_db
            c = eccsa_db.get_connection()
        except Exception as exc:
            print(f"[wa_envios] sin conexión para revisar enviados: {exc}")
            return False
    cur = None
    try:
        cur = c.cursor()
        cur.execute(
            "SELECT TOP 1 1 FROM HUB_WhatsappEntrante "
            "WHERE Direccion = 'OUT' AND ChatId = %s AND Texto = %s "
            "AND Fecha >= %s",
            (chat, texto, dia or now_mx().date()))
        return cur.fetchone() is not None
    except Exception as exc:
        print(f"[wa_envios] no se pudo revisar lo enviado: {exc}")
        return False
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
