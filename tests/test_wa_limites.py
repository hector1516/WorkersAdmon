"""
Pruebas del tope de respuestas del bot de WhatsApp (wa_limites.py).

Lo que se vigila:

  · que por debajo del tope el bot conteste;
  · que al llegar al tope del chat se calle ESE chat;
  · que al llegar al tope total se calle la sesión completa;
  · que una base caída PERMITA responder (mejor de más que mudo);
  · que la cuenta salga solo de `Direccion='OUT'` y de la hora en curso.

La base se dobla en memoria: no se toca SQL Server.

    python -m unittest tests.test_wa_limites -v
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_limites  # noqa: E402

CHAT = "5218123211516@c.us"


class BaseFalsa:
    """Config de HUB_Config y los dos conteos de la hora."""

    def __init__(self, config=None, del_chat=0, total=0):
        self.config = dict(config or {})
        self.del_chat = del_chat
        self.total = total
        self.consultas = []      # [(sql_normalizado, params)]


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.filas = []

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        self.base.consultas.append((q, params))
        if "FROM HUB_CONFIG" in q:
            self.filas = [(k, v) for k, v in self.base.config.items()
                          if k in params]
        elif "FROM HUB_WHATSAPPENTRANTE" in q:
            self.filas = [(self.base.total, self.base.del_chat)]
        else:
            raise AssertionError(f"SQL inesperado: {q}")

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorFalso(self.base)

    def close(self):
        raise AssertionError("la conexión del llamador no debe cerrarse")


def _con(config=None, **conteos):
    base = BaseFalsa(config, **conteos)
    return base, ConexionFalsa(base)


class Limites(unittest.TestCase):
    def setUp(self):
        self._conexion_original = wa_limites._conexion

    def tearDown(self):
        wa_limites._conexion = self._conexion_original

    # ── por debajo del tope ──────────────────────────────────────────────────
    def test_chat_por_debajo_del_limite_permite(self):
        _base, conn = _con({"wa_bot_max_hora_chat": "20",
                            "wa_bot_max_hora_total": "200"},
                           del_chat=19, total=40)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (True, ""))

    # ── topes ────────────────────────────────────────────────────────────────
    def test_chat_pasado_se_queda_en_chat(self):
        _base, conn = _con({"wa_bot_max_hora_chat": "20"}, del_chat=20,
                           total=40)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "chat"))

    def test_total_pasado_se_queda_en_total(self):
        _base, conn = _con({"wa_bot_max_hora_total": "200"}, del_chat=3,
                           total=200)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "total"))

    def test_chat_tiene_prioridad_sobre_total(self):
        _base, conn = _con({"wa_bot_max_hora_chat": "5",
                            "wa_bot_max_hora_total": "5"},
                           del_chat=5, total=9)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "chat"))

    # ── defaults ─────────────────────────────────────────────────────────────
    def test_sin_config_usa_20_y_200(self):
        _base, conn = _con(del_chat=19, total=199)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (True, ""))
        _base, conn = _con(del_chat=20, total=0)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "chat"))
        _base, conn = _con(del_chat=0, total=200)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "total"))

    def test_valor_ilegible_usa_el_default(self):
        _base, conn = _con({"wa_bot_max_hora_chat": "muchos"}, del_chat=20,
                           total=0)
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (False, "chat"))

    # ── base caída ───────────────────────────────────────────────────────────
    def test_bd_caida_permite_responder(self):
        def falla():
            raise RuntimeError("sin SQL Server")

        wa_limites._conexion = falla
        self.assertEqual(wa_limites.puede_responder(CHAT), (True, ""))

    def test_falla_al_contar_tambien_permite(self):
        _base, conn = _con()
        conn.cursor = lambda: (_ for _ in ()).throw(RuntimeError("cursor roto"))
        self.assertEqual(wa_limites.puede_responder(CHAT, conn), (True, ""))

    # ── qué se cuenta ────────────────────────────────────────────────────────
    def test_cuenta_solo_out_y_desde_la_hora_en_curso(self):
        base, conn = _con(del_chat=1, total=2)
        wa_limites.puede_responder(CHAT, conn)

        consultas = [q for q, _p in base.consultas
                     if "FROM HUB_WHATSAPPENTRANTE" in q]
        self.assertEqual(len(consultas), 1)
        self.assertIn("DIRECCION = 'OUT'", consultas[0])
        self.assertIn("FECHA >=", consultas[0])

        params = [p for q, p in base.consultas
                  if "FROM HUB_WHATSAPPENTRANTE" in q][0]
        self.assertEqual(params[0], CHAT)
        desde = params[1]
        self.assertEqual(desde.minute, 0)
        self.assertEqual(desde.second, 0)
        self.assertEqual(desde.microsecond, 0)


if __name__ == "__main__":
    unittest.main()
