"""
tests/test_eccsita_perfiles.py — el roast por persona y su API
==============================================================

Pruebas con dobles: no tocan SQL Server, ni OpenWA, ni la red.

Se vigila lo que duele si falla:

  · el alias se detecta con acentos y mayúsculas, y como palabra completa
    («hectoreo» no cuenta como «hector»);
  · los alias extra de `HUB_Config.wa_grupo_alias` también cuentan;
  · no se guarda lo que la persona escribe de sí misma;
  · la misma mención no se guarda dos veces (Hash + índice único);
  · APODO / INSISTENCIA / BROMA se clasifican en ese orden;
  · una oración con insulto grave no se guarda;
  · `wa_persona.hechos` trae `roast_grupo` y los interruptores lo apagan;
  · `sarcasmo` usa la mención sin citar al autor;
  · la API lista, aprueba, oculta y borra.

Ejecución:  python -m unittest tests.test_eccsita_perfiles -v
"""
import datetime
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import eccsita_perfiles  # noqa: E402
from panel import api as panel_api  # noqa: E402
from panel import api_eccsita  # noqa: E402
import wa_asistencia  # noqa: E402
import wa_persona  # noqa: E402

YO = 2
OTRO = 5
CHAT = "120363407857907731@g.us"
AHORA = datetime.datetime(2026, 11, 26, 10, 0)
USER = {"id": 7, "email": "ana@ecc-sa.com.mx", "nombre": "Ana Pruebas",
        "perms": {"AccesoConfiguracion": True}}


# ─── dobles de base ──────────────────────────────────────────────────────────
class BaseFalsa:
    def __init__(self):
        self.usuarios = [(YO, "Héctor Salas"), (OTRO, "Luis Pérez")]
        self.alias_cfg = None          # texto de HUB_Config.wa_grupo_alias
        self.perfiles = []             # dicts como en HUB_GrupoPerfiles
        self.config = {}               # otras claves de HUB_Config
        self.commits = 0
        self.rollbacks = 0

    def config_valor(self, clave):
        if clave == eccsita_perfiles.CLAVE_ALIAS:
            return self.alias_cfg
        return self.config.get(clave)


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        base = self.base
        q = " ".join(str(sql).split()).upper()
        self.una, self.todas, self.rowcount = None, [], 0

        if q.startswith("SELECT ID, NOMBRE FROM HUB_USERS"):
            self.todas = list(base.usuarios)

        elif q.startswith("SELECT VALOR FROM HUB_CONFIG"):
            clave = params[0] if params else eccsita_perfiles.CLAVE_ALIAS
            valor = base.config_valor(clave)
            self.una = (valor,) if valor is not None else None

        elif q.startswith("SELECT 1 FROM HUB_GRUPOPERFILES"):
            uid, digest = params
            self.una = (1,) if any(
                p["IdUsuario"] == uid and p["Hash"] == digest
                for p in base.perfiles) else None

        elif q.startswith("INSERT INTO HUB_GRUPOPERFILES"):
            (uid, texto, autor, chat, fecha, anio, semana, tipo,
             aprobado, digest) = params
            base.perfiles.append({
                "Id": len(base.perfiles) + 1, "IdUsuario": uid,
                "Texto": texto, "Autor": autor, "ChatId": chat,
                "Fecha": fecha, "Anio": anio, "Semana": semana,
                "Tipo": tipo, "Aprobado": bool(aprobado), "Hash": digest})
            self.rowcount = 1

        elif q.startswith("SELECT P.IDUSUARIO"):
            cuentas = {}
            for p in base.perfiles:
                fila = cuentas.setdefault(p["IdUsuario"], [0, None])
                fila[0] += 1
                if fila[1] is None or p["Fecha"] > fila[1]:
                    fila[1] = p["Fecha"]
            nombres = dict(base.usuarios)
            self.todas = [(uid, nombres.get(uid, ""), datos[0], datos[1])
                          for uid, datos in cuentas.items()]

        elif q.startswith("SELECT ID, TEXTO, AUTOR"):
            uid = params[0]
            filas = [p for p in base.perfiles if p["IdUsuario"] == uid]
            filas.sort(key=lambda p: (p["Fecha"], p["Id"]), reverse=True)
            self.todas = [(p["Id"], p["Texto"], p["Autor"], p["Fecha"],
                           p["Tipo"], p["Aprobado"]) for p in filas]

        elif q.startswith("UPDATE HUB_GRUPOPERFILES"):
            valor, pid = params
            for p in base.perfiles:
                if p["Id"] == pid:
                    p["Aprobado"] = bool(valor)
                    self.rowcount = 1

        elif q.startswith("DELETE FROM HUB_GRUPOPERFILES"):
            antes = len(base.perfiles)
            base.perfiles = [p for p in base.perfiles if p["Id"] != params[0]]
            self.rowcount = antes - len(base.perfiles)

        elif q.startswith("SELECT TOP 50 TEXTO"):
            uid, desde = params
            filas = [p for p in base.perfiles
                     if p["IdUsuario"] == uid and p["Aprobado"]
                     and p["Fecha"] >= desde]
            self.todas = [(p["Texto"], p["Tipo"]) for p in filas]

        else:
            raise AssertionError(f"SQL inesperado: {q}")

    def fetchone(self):
        return self.una

    def fetchall(self):
        return self.todas

    def close(self):
        return None


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, *args, **kwargs):
        return CursorFalso(self.base)

    def commit(self):
        self.base.commits += 1

    def rollback(self):
        self.base.rollbacks += 1

    def close(self):
        return None


class HandlerFalso:
    def __init__(self, path="/", command="GET", cuerpo=None, csrf=True):
        self.path = path
        self.command = command
        self.cuerpo = cuerpo or {}
        self.headers = {}
        if csrf and command == "POST":
            self.headers = {"Cookie": "panel_csrf=abc", "X-CSRF-Token": "abc"}
        self.code = None
        self.payload = None

    def _read_json(self):
        return self.cuerpo

    def _json(self, obj, code=200, extra=()):
        self.code = code
        self.payload = obj

    def _send(self, code, body, ctype, extra=()):
        self.code = code
        self.payload = body


class BaseConDobles(unittest.TestCase):
    def setUp(self):
        self._previos = []

    def tearDown(self):
        for objeto, nombre, valor in reversed(self._previos):
            setattr(objeto, nombre, valor)

    def doblar(self, objeto, nombre, valor):
        self._previos.append((objeto, nombre, getattr(objeto, nombre)))
        setattr(objeto, nombre, valor)


def mensaje(texto, autor="Luis Pérez", autor_id="5218000000000@c.us",
            chat=CHAT, fecha=AHORA):
    return {"chat_id": chat, "autor_id": autor_id, "autor_nombre": autor,
            "texto": texto, "fecha": fecha}


# ─── extracción ──────────────────────────────────────────────────────────────
class Extraccion(unittest.TestCase):
    def setUp(self):
        self.base = BaseFalsa()
        self.conn = ConexionFalsa(self.base)

    def extraer(self, texto, **extra):
        return eccsita_perfiles.extraer(self.conn, mensaje(texto, **extra))

    def test_detecta_alias_con_acentos_y_mayusculas(self):
        self.assertEqual(self.extraer("HÉCTOR llegó tarde hoy."), 1)
        self.assertEqual(self.base.perfiles[0]["IdUsuario"], YO)
        self.assertEqual(self.base.perfiles[0]["Texto"], "HÉCTOR llegó tarde hoy.")

    def test_detecta_el_nombre_completo(self):
        self.assertEqual(self.extraer("hoy hector salas no vino."), 1)
        self.assertEqual(self.base.perfiles[0]["IdUsuario"], YO)

    def test_no_detecta_subcadenas(self):
        self.assertEqual(self.extraer("hectoreo es una palabra inventada."), 0)
        self.assertEqual(self.extraer("luisa tampoco es luis."), 0)
        self.assertEqual(self.base.perfiles, [])

    def test_no_guarda_si_el_autor_es_el_mencionado(self):
        self.assertEqual(
            self.extraer("Héctor otra vez llegó tarde.",
                         autor="Héctor Salas"), 0)
        self.assertEqual(self.base.perfiles, [])

    def test_dedupe_por_hash(self):
        self.extraer("Héctor llegó tarde otra vez.")
        self.extraer("Héctor llegó tarde otra vez.")
        self.assertEqual(len(self.base.perfiles), 1)

    def test_clasifica_apodo_insistencia_broma(self):
        self.extraer("A Héctor le dicen el jefe.")
        self.extraer("Héctor siempre trae café.")
        self.extraer("Héctor se quedó sin batería.")
        tipos = [p["Tipo"] for p in self.base.perfiles]
        self.assertEqual(tipos, ["APODO", "INSISTENCIA", "BROMA"])

    def test_filtra_insultos_graves(self):
        self.assertEqual(self.extraer("Héctor es un pendejo."), 0)
        self.assertEqual(self.base.perfiles, [])

    def test_alias_extra_de_la_config(self):
        self.base.alias_cfg = '{"2": ["hech", "El Compa"]}'
        self.assertEqual(self.extraer("el compa no llegó."), 1)
        self.assertEqual(self.base.perfiles[0]["IdUsuario"], YO)

    def test_config_de_alias_rota_no_revienta(self):
        self.base.alias_cfg = "{esto no es json"
        self.assertEqual(self.extraer("Héctor llegó tarde."), 1)

    def test_texto_vacio_o_sin_alias_no_consulta_demasiado(self):
        self.assertEqual(self.extraer(""), 0)
        self.assertEqual(self.extraer("nadie del equipo vino."), 0)
        self.assertEqual(self.base.perfiles, [])


# ─── hechos y sarcasmo del bot ───────────────────────────────────────────────
class ConHechos(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.base = BaseFalsa()
        self.base.perfiles = [
            {"Id": 1, "IdUsuario": YO, "Texto": "A Héctor le dicen el jefe.",
             "Autor": "Luis", "ChatId": CHAT,
             "Fecha": AHORA - datetime.timedelta(days=2),
             "Anio": 2026, "Semana": 47, "Tipo": "APODO",
             "Aprobado": True, "Hash": "a" * 64},
            {"Id": 2, "IdUsuario": YO, "Texto": "Héctor siempre trae café.",
             "Autor": "Luis", "ChatId": CHAT,
             "Fecha": AHORA - datetime.timedelta(days=3),
             "Anio": 2026, "Semana": 47, "Tipo": "INSISTENCIA",
             "Aprobado": True, "Hash": "b" * 64},
        ]
        self.conn = ConexionFalsa(self.base)
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()

        self.doblar(wa_persona, "_nombre", lambda conn, uid: "Héctor Salas")
        self.doblar(wa_persona, "_vales",
                    lambda conn, uid: {"total": 0, "mas_viejo": None})
        self.doblar(wa_persona, "_reporte", lambda conn, uid: None)
        self.doblar(wa_persona, "_tickets",
                    lambda conn, uid, desde: 0)
        self.doblar(wa_persona, "_cotizaciones",
                    lambda conn, uid, desde: None)
        self.doblar(wa_persona, "_kilometros", lambda conn, uid: {
            "km_mes": 0, "km_registros": 0, "odometro": None,
            "odometro_fecha": None})
        self.doblar(wa_persona, "_llegadas_tarde",
                    lambda conn, uid, ahora: (0, 0))
        self.doblar(wa_asistencia, "_ausencias_seguidas",
                    lambda conn, uid, hoy: False)

    def tearDown(self):
        super().tearDown()
        wa_persona.olvidar()
        wa_persona._ULTIMA.clear()

    def hechos(self):
        return wa_persona.hechos(YO, conn=self.conn, ahora=AHORA)


class Hechos(ConHechos):
    def test_hechos_trae_roast_grupo(self):
        datos = self.hechos()
        self.assertEqual(len(datos["roast_grupo"]), 2)
        self.assertEqual(datos["roast_grupo"][0]["tipo"], "APODO")
        self.assertIn("texto", datos["roast_grupo"][0])

    def test_solo_menciones_aprobadas_y_recientes(self):
        self.base.perfiles.append(
            {"Id": 3, "IdUsuario": YO, "Texto": "Héctor es un mito.",
             "Autor": "Luis", "ChatId": CHAT,
             "Fecha": AHORA - datetime.timedelta(days=200),
             "Anio": 2026, "Semana": 1, "Tipo": "BROMA",
             "Aprobado": True, "Hash": "c" * 64})
        self.base.perfiles.append(
            {"Id": 4, "IdUsuario": YO, "Texto": "Héctor no vino.",
             "Autor": "Luis", "ChatId": CHAT,
             "Fecha": AHORA - datetime.timedelta(days=1),
             "Anio": 2026, "Semana": 48, "Tipo": "BROMA",
             "Aprobado": False, "Hash": "d" * 64})
        self.base.perfiles.append(
            {"Id": 5, "IdUsuario": OTRO, "Texto": "Luis no vino.",
             "Autor": "Héctor", "ChatId": CHAT,
             "Fecha": AHORA - datetime.timedelta(days=1),
             "Anio": 2026, "Semana": 48, "Tipo": "BROMA",
             "Aprobado": True, "Hash": "e" * 64})
        textos = [r["texto"] for r in self.hechos()["roast_grupo"]]
        self.assertEqual(len(textos), 2)
        self.assertNotIn("Héctor no vino.", textos)      # oculto
        self.assertNotIn("Héctor es un mito.", textos)   # viejo
        self.assertNotIn("Luis no vino.", textos)        # de otro usuario

    def test_interruptor_apaga_el_roast(self):
        self.base.config["wa_roast_grupo"] = "0"
        self.assertEqual(self.hechos()["roast_grupo"], [])

    def test_respeta_wa_bot_sarcasmo(self):
        self.base.config["wa_bot_sarcasmo"] = "0"
        self.assertEqual(self.hechos()["roast_grupo"], [])

    def test_insulto_grave_no_sale_ni_estando_aprobado(self):
        self.base.perfiles[0]["Texto"] = "Héctor es un culero."
        textos = [r["texto"] for r in self.hechos()["roast_grupo"]]
        self.assertNotIn("Héctor es un culero.", textos)

    def test_sarcasmo_usa_el_roast_sin_citar_al_autor(self):
        frase = wa_persona.sarcasmo(YO, hechos=self.hechos())
        self.assertTrue(frase)
        self.assertNotIn("Luis", frase)
        self.assertEqual(len(frase.splitlines()), 1)


# ─── API del panel ───────────────────────────────────────────────────────────
class Api(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.base = BaseFalsa()
        self.llamadas = []
        self.modulo = types.SimpleNamespace(
            perfiles=lambda conn, uid=None: (
                self.llamadas.append(("perfiles", uid)) or []),
            aprobar=lambda conn, pid, valor: (
                self.llamadas.append(("aprobar", pid, valor)) or True),
            borrar=lambda conn, pid: (
                self.llamadas.append(("borrar", pid)) or True))

        self.doblar(panel_api, "_sesion", lambda h: (dict(USER), None))
        self.doblar(api_eccsita, "_log", lambda *a: None)
        self.doblar(api_eccsita, "_modulo_perfiles", lambda: self.modulo)
        self.doblar(api_eccsita, "_conexion",
                    lambda: ConexionFalsa(self.base))
        self.doblar(api_eccsita.db, "get_user_by_id",
                    lambda uid: {"id": uid, "nombre": "Héctor Salas"}
                    if uid == YO else None)

    def _get(self, path):
        h = HandlerFalso(path=path)
        self.assertTrue(panel_api.atender(h))
        return h

    def _post(self, path, cuerpo):
        h = HandlerFalso(path=path, command="POST", cuerpo=cuerpo)
        self.assertTrue(panel_api.atender(h))
        return h

    def test_lista_personas(self):
        self.modulo.perfiles = lambda conn, uid=None: [
            {"idUsuario": YO, "nombre": "Héctor Salas", "perfiles": 3,
             "ultimo": "2026-11-24T09:00:00"}]
        h = self._get("/api/panel/eccsita/personas")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["total"], 1)
        self.assertEqual(h.payload["items"][0]["idUsuario"], YO)

    def test_detalle_de_persona(self):
        self.modulo.perfiles = lambda conn, uid=None: [
            {"id": 1, "texto": "A Héctor le dicen el jefe.",
             "autor": "Luis", "fecha": "2026-11-24T09:00:00",
             "tipo": "APODO", "aprobado": True}]
        h = self._get(f"/api/panel/eccsita/personas/{YO}")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["usuario"]["nombre"], "Héctor Salas")
        self.assertEqual(h.payload["items"][0]["tipo"], "APODO")

    def test_detalle_de_usuario_inexistente_es_404(self):
        h = self._get("/api/panel/eccsita/personas/99")
        self.assertEqual(h.code, 404)

    def test_aprobar_y_ocultar(self):
        h = self._post(f"/api/panel/eccsita/perfiles/3",
                       {"accion": "aprobar"})
        self.assertEqual(h.code, 200)
        self.assertEqual(self.llamadas[-1], ("aprobar", 3, 1))

        h = self._post(f"/api/panel/eccsita/perfiles/3",
                       {"accion": "ocultar"})
        self.assertEqual(h.code, 200)
        self.assertEqual(self.llamadas[-1], ("aprobar", 3, 0))

    def test_borrar(self):
        h = self._post("/api/panel/eccsita/perfiles/4", {"accion": "borrar"})
        self.assertEqual(h.code, 200)
        self.assertEqual(self.llamadas[-1], ("borrar", 4))

    def test_accion_invalida(self):
        h = self._post("/api/panel/eccsita/perfiles/4", {"accion": "publicar"})
        self.assertEqual(h.code, 400)
        self.assertEqual(self.llamadas, [])

    def test_post_sin_csrf_no_pasa(self):
        h = HandlerFalso(path="/api/panel/eccsita/perfiles/4",
                         command="POST", cuerpo={"accion": "aprobar"},
                         csrf=False)
        self.assertTrue(panel_api.atender(h))
        self.assertEqual(h.code, 403)


if __name__ == "__main__":
    unittest.main()
