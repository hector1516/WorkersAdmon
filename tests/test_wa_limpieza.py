"""
Pruebas de la purga de WhatsApp (wa_limpieza.py).

Lo que se vigila:

  · que el corte de días sea el de `wa_purga_dias` (con 91 se borra, con 89 no);
  · que NUNCA borre conversaciones en BOT o HUMANO, aunque sean viejas;
  · que `wa_purga_activa=0` apague la purga sin apagar el worker;
  · que borre en lotes de 500 (no un DELETE gigante);
  · que fuera de 03:00-03:30 no toque nada.

La base se dobla en memoria: no se toca SQL Server.

    python -m unittest tests.test_wa_limpieza -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_limpieza  # noqa: E402

AHORA = datetime(2026, 11, 26, 3, 10)      # dentro de la ventana
MEDIODIA = datetime(2026, 11, 26, 12, 0)   # fuera de la ventana


class BaseFalsa:
    def __init__(self, mensajes=None, conversaciones=None, config=None):
        self.mensajes = list(mensajes or [])
        # {chat: (Estado, Ultima)}
        self.conversaciones = dict(conversaciones or {})
        self.config = dict(config if config is not None else
                           {"wa_purga_dias": "90", "wa_purga_activa": "1"})
        self.borrados = []      # [(tabla, n)]
        self.sql = []
        self.commits = 0


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.filas = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        self.base.sql.append((q, params))
        self.filas = []
        self.rowcount = 0

        if "SELECT CLAVE, VALOR FROM HUB_CONFIG" in q:
            self.filas = [(k, v) for k, v in self.base.config.items()
                          if k in params]
            return
        if q.startswith("DELETE TOP (500) FROM HUB_WHATSAPPENTRANTE"):
            corte = params[0]
            self._borrar_mensajes(corte)
            return
        if q.startswith("DELETE TOP (500) FROM HUB_WHATSAPPCONVERSACIONES"):
            corte = params[0]
            self._borrar_conversaciones(corte)
            return
        raise AssertionError(f"SQL inesperado: {q}")

    def _borrar_mensajes(self, corte):
        viejos = sorted(f for f in self.base.mensajes if f < corte)[:500]
        for fecha in viejos:
            self.base.mensajes.remove(fecha)
        self.rowcount = len(viejos)
        self.base.borrados.append(("mensajes", len(viejos)))

    def _borrar_conversaciones(self, corte):
        objetivo = [chat for chat, (estado, ultima)
                    in self.base.conversaciones.items()
                    if str(estado).upper() == "CERRADA" and ultima < corte][:500]
        for chat in objetivo:
            del self.base.conversaciones[chat]
        self.rowcount = len(objetivo)
        self.base.borrados.append(("conversaciones", len(objetivo)))

    def fetchall(self):
        return self.filas

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorFalso(self.base)

    def commit(self):
        self.base.commits += 1


def _correr(base, ahora=AHORA):
    return wa_limpieza.turno(ahora=ahora, conn=ConexionFalsa(base))


class Purga(unittest.TestCase):
    # ── días de retención ────────────────────────────────────────────────────
    def test_con_91_dias_borra(self):
        base = BaseFalsa(
            mensajes=[AHORA - timedelta(days=91)],
            conversaciones={"viejo@c.us": ("CERRADA",
                                           AHORA - timedelta(days=100))})
        detalle = _correr(base)
        self.assertEqual(base.mensajes, [])
        self.assertEqual(base.conversaciones, {})
        self.assertIn("1 mensaje", detalle)
        self.assertIn("1 conversación", detalle)

    def test_con_89_dias_no_borra(self):
        base = BaseFalsa(
            mensajes=[AHORA - timedelta(days=89)],
            conversaciones={"reciente@c.us": ("CERRADA",
                                              AHORA - timedelta(days=89))})
        _correr(base)
        self.assertEqual(len(base.mensajes), 1)
        self.assertEqual(len(base.conversaciones), 1)

    # ── estados que nunca se tocan ───────────────────────────────────────────
    def test_no_borra_conversaciones_bot_ni_humano(self):
        base = BaseFalsa(conversaciones={
            "bot@c.us": ("BOT", AHORA - timedelta(days=300)),
            "humano@c.us": ("HUMANO", AHORA - timedelta(days=300)),
            "cerrada@c.us": ("CERRADA", AHORA - timedelta(days=300)),
        })
        _correr(base)
        self.assertEqual(set(base.conversaciones), {"bot@c.us", "humano@c.us"})

    # ── interruptor ──────────────────────────────────────────────────────────
    def test_purga_desactivada_no_borra(self):
        base = BaseFalsa(
            mensajes=[AHORA - timedelta(days=365)],
            conversaciones={"viejo@c.us": ("CERRADA",
                                           AHORA - timedelta(days=365))},
            config={"wa_purga_activa": "0", "wa_purga_dias": "90"})
        detalle = _correr(base)
        self.assertEqual(len(base.mensajes), 1)
        self.assertIn("desactivada", detalle)
        self.assertEqual(base.borrados, [])

    # ── lotes ────────────────────────────────────────────────────────────────
    def test_borra_en_lotes_de_500(self):
        base = BaseFalsa(mensajes=[AHORA - timedelta(days=100)] * 1200)
        _correr(base)
        self.assertEqual(base.mensajes, [])
        self.assertEqual([n for t, n in base.borrados if t == "mensajes"],
                         [500, 500, 200])

    # ── ventana horaria ──────────────────────────────────────────────────────
    def test_fuera_de_la_ventana_no_hace_nada(self):
        base = BaseFalsa(
            mensajes=[AHORA - timedelta(days=365)],
            conversaciones={"viejo@c.us": ("CERRADA",
                                           AHORA - timedelta(days=365))})
        detalle = _correr(base, ahora=MEDIODIA)
        self.assertIn("fuera de la ventana", detalle)
        self.assertEqual(len(base.mensajes), 1)
        self.assertEqual(len(base.conversaciones), 1)
        self.assertEqual(base.borrados, [])

    def test_ventana_incluye_las_tres_y_excluye_las_tres_y_media(self):
        self.assertTrue(wa_limpieza.en_ventana(datetime(2026, 11, 26, 3, 0)))
        self.assertTrue(wa_limpieza.en_ventana(datetime(2026, 11, 26, 3, 29)))
        self.assertFalse(wa_limpieza.en_ventana(datetime(2026, 11, 26, 3, 30)))
        self.assertFalse(wa_limpieza.en_ventana(datetime(2026, 11, 26, 2, 59)))


if __name__ == "__main__":
    unittest.main()
