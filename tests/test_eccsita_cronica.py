"""
tests/test_eccsita_cronica.py — crónica semanal con IA de ECCSita
================================================================
Pruebas con dobles: no tocan SQL Server, ni Gemini, ni la red.

Se vigila lo que duele si falla:

  · un JSON válido guarda resumen, temas, personajes y momentos con
    GeneradoPor='IA';
  · un JSON inválido se reintenta una vez y cae al resumen determinista;
  · las citas inventadas no llegan a la base (solo se guarda lo textual);
  · salud, dinero personal, familia e insultos graves no entran al prompt ni
    se guardan aunque la IA los cite;
  · con `wa_grupo_ia=0` no se llama a la IA (ni la API ni la función);
  · el worker solo trabaja el lunes dentro de la ventana y solo con la semana
    anterior; una semana RESUMIDA no se toca;
  · un timeout de la IA no rompe nada: la API cae a la básica y el worker
    deja la semana CAPTURADA.

Ejecución:  python -m unittest tests.test_eccsita_cronica -v
"""
import datetime
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_grupo_semana as worker  # noqa: E402
from panel import api as panel_api  # noqa: E402
from panel import api_eccsita, db, eccsita  # noqa: E402
from panel import eccsita_cronica as cronica  # noqa: E402

GRUPO = "120363407857907731@g.us"
USER = {"id": 7, "email": "ana@ecc-sa.com.mx", "nombre": "Ana Pruebas",
        "perms": {"AccesoConfiguracion": True}}

# Lunes 30 nov 2026 09:10: la semana anterior es la 48 de 2026.
LUNES = datetime.datetime(2026, 11, 30, 9, 10)
ANIO, SEMANA = 2026, 48

REAL = "yo llevo el café y los panes para la junta de hoy"
REAL2 = "buenos días a todos, hoy hay junta a las diez"
INVENTADA = "nadie dijo nunca esta frase en el grupo"

MENSAJES = [
    {"AutorId": "a", "AutorNombre": "Ana", "Tipo": "text",
     "Texto": REAL2, "Fecha": datetime.datetime(2026, 11, 24, 9, 0)},
    {"AutorId": "b", "AutorNombre": "Luis", "Tipo": "text",
     "Texto": REAL, "Fecha": datetime.datetime(2026, 11, 25, 10, 0)},
    {"AutorId": "c", "AutorNombre": "Ceci", "Tipo": "text",
     "Texto": "mi divorcio me tiene preocupada y no quiero hablarlo",
     "Fecha": datetime.datetime(2026, 11, 25, 11, 0)},
    {"AutorId": "d", "AutorNombre": "Dani", "Tipo": "text",
     "Texto": "eres un pendejo, no sirves para nada",
     "Fecha": datetime.datetime(2026, 11, 26, 12, 0)},
]

JSON_BUENO = {
    "resumen": "Semana de juntas y café.\n\nLuis trajo panes.\n\nAna puso "
               "orden.\n\nCeci puso el humor.\n\nY así pasó la semana.",
    "temas": ["el café de la junta", "las juntas de las diez"],
    "personajes": [{"nombre": "Luis", "que": "trae panes y no se le pide"}],
    "momentos": [
        {"cita": REAL, "por_que": "promesa de desayuno gratis"},
        {"cita": INVENTADA, "por_que": "no está en los mensajes"},
    ],
    "frases": [REAL2, "pendejo"],
}

NO_JSON = "Claro, aquí tienes la crónica: fue una gran semana."


# ─── dobles ──────────────────────────────────────────────────────────────────
class CursorFalso:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        if q.startswith("SELECT AUTORID"):
            self.rows = list(self.conn.mensajes)
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class ConexionFalsa:
    def __init__(self, mensajes=None):
        self.mensajes = MENSAJES if mensajes is None else mensajes
        self.consultas = []

    def cursor(self, as_dict=False):
        return CursorFalso(self)

    def close(self):
        pass


class HandlerFalso:
    def __init__(self, path="/", command="GET", cuerpo=None, csrf=False):
        self.path = path
        self.command = command
        self.cuerpo = cuerpo or {}
        self.headers = ({"Cookie": "panel_csrf=abc", "X-CSRF-Token": "abc"}
                        if csrf else {})
        self.code = None
        self.payload = None

    def _read_json(self):
        return self.cuerpo

    def _json(self, obj, code=200, extra=()):
        self.code = code
        self.payload = obj

    def _send(self, code, body, ctype, extra=()):
        self.code = code
        self.payload = {"body": body}


class BaseConDobles(unittest.TestCase):
    def setUp(self):
        self._previos = []

    def tearDown(self):
        for objeto, nombre, valor in reversed(self._previos):
            setattr(objeto, nombre, valor)

    def doblar(self, objeto, nombre, valor):
        self._previos.append((objeto, nombre, getattr(objeto, nombre)))
        setattr(objeto, nombre, valor)

    def guardar_ia(self, proveedor="google_gemini"):
        self.doblar(db, "get_ai_config", lambda: {
            "provider": proveedor, "api_key": "k-123", "model": "gemini-x"})

    def capturar_guardado(self):
        guardado = {}

        def guardar(chat, anio, semana, mensajes, participantes, resumen,
                    temas, personajes, momentos, generado_por="BASICO"):
            guardado.update({
                "chat": chat, "anio": anio, "semana": semana,
                "participantes": participantes, "resumen": resumen,
                "temas": temas, "personajes": personajes,
                "momentos": momentos, "generadoPor": generado_por})

        self.doblar(eccsita, "_guardar_semana", guardar)
        return guardado

    def ia_devuelve(self, valores):
        """
        Sustituye la llamada al modelo. `valores` es una lista de respuestas
        (str o Exception), una por llamada; la última se repite.
        """
        llamadas = []

        def llamar(prompt, api_key, modelo, timeout=60):
            llamadas.append(prompt)
            indice = min(len(llamadas) - 1, len(valores) - 1)
            valor = valores[indice]
            if isinstance(valor, Exception):
                raise valor
            return valor

        self.doblar(cronica, "_llamar", llamar)
        return llamadas


# ─── generar_semana ─────────────────────────────────────────────────────────
class Generar(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.guardar_ia()
        self.guardado = self.capturar_guardado()

    def test_json_valido_guarda_todo(self):
        self.ia_devuelve([json.dumps(JSON_BUENO, ensure_ascii=False)])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       chat_id=GRUPO)

        self.assertEqual(datos["generadoPor"], "IA")
        self.assertEqual(datos["estado"], "RESUMIDA")
        self.assertEqual(datos["temas"], JSON_BUENO["temas"])
        self.assertEqual(datos["personajes"][0]["nombre"], "Luis")
        self.assertIn("juntas y café", datos["resumen"])

        self.assertEqual(self.guardado["generadoPor"], "IA")
        self.assertEqual(self.guardado["semana"], SEMANA)
        self.assertEqual(self.guardado["temas"], JSON_BUENO["temas"])
        textos = [m["texto"] for m in self.guardado["momentos"]]
        self.assertIn(REAL, textos)
        momento = [m for m in self.guardado["momentos"]
                   if m["texto"] == REAL][0]
        self.assertEqual(momento["por_que"], "promesa de desayuno gratis")
        self.assertEqual(momento["autor"], "Luis")
        # La frase real entra marcada como frase; "pendejo" no entra.
        frases = [m for m in self.guardado["momentos"]
                  if m.get("tipo") == "frase"]
        self.assertEqual([f["texto"] for f in frases], [REAL2])

    def test_json_invalido_reintenta_y_cae_a_basico(self):
        llamadas = self.ia_devuelve([NO_JSON, NO_JSON])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       chat_id=GRUPO)

        self.assertEqual(len(llamadas), 2)          # un solo reintento
        self.assertIn("SOLO el JSON", llamadas[1])
        self.assertEqual(datos["generadoPor"], "BASICO")
        self.assertIn("Semana 48 de 2026", datos["resumen"])
        self.assertEqual(self.guardado["generadoPor"], "BASICO")
        self.assertIn("no devolvió JSON", datos.get("avisoIa", ""))

    def test_citas_inventadas_se_descartan(self):
        self.ia_devuelve([json.dumps(JSON_BUENO, ensure_ascii=False)])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       chat_id=GRUPO)

        textos = [m["texto"] for m in datos["momentos"]]
        self.assertIn(REAL, textos)
        self.assertNotIn(INVENTADA, textos)

    def test_filtro_insultos_y_sensibles(self):
        llamadas = self.ia_devuelve([json.dumps(JSON_BUENO, ensure_ascii=False)])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       chat_id=GRUPO)

        prompt = llamadas[0]
        self.assertNotIn("divorcio", prompt)
        self.assertNotIn("pendejo", prompt)
        self.assertIn(REAL, prompt)

        guardado = json.dumps(datos["momentos"], ensure_ascii=False)
        self.assertNotIn("divorcio", guardado)
        self.assertNotIn("pendejo", guardado)

    def test_sin_ia_solo_determinista(self):
        llamadas = self.ia_devuelve([AssertionError("no se debe llamar")])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       usar_ia=False, chat_id=GRUPO)

        self.assertEqual(llamadas, [])
        self.assertEqual(datos["generadoPor"], "BASICO")

    def test_timeout_cae_a_basico(self):
        self.ia_devuelve([TimeoutError("la IA tardó más de 60 s")])
        datos = cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                       chat_id=GRUPO)

        self.assertEqual(datos["generadoPor"], "BASICO")
        self.assertIn("TimeoutError", datos.get("avisoIa", ""))
        self.assertEqual(self.guardado["generadoPor"], "BASICO")

    def test_timeout_con_exigir_ia_no_guarda(self):
        self.ia_devuelve([TimeoutError("la IA tardó más de 60 s")])
        with self.assertRaises(cronica.CronicaIAError):
            cronica.generar_semana(ConexionFalsa(), ANIO, SEMANA,
                                   exigir_ia=True, chat_id=GRUPO)
        self.assertEqual(self.guardado, {})

    def test_sin_mensajes_es_none(self):
        self.assertIsNone(cronica.generar_semana(
            ConexionFalsa(mensajes=[]), ANIO, SEMANA, chat_id=GRUPO))

    def test_muestreo_parejo_por_dia(self):
        mensajes = []
        for dia in range(23, 30):           # 7 días × 100 mensajes
            for i in range(100):
                mensajes.append({
                    "AutorNombre": "Ana", "Texto": f"texto {dia} {i}",
                    "Fecha": datetime.datetime(2026, 11, dia, 10, 0)})
        muestra = cronica._muestrear(mensajes, 70)
        self.assertEqual(len(muestra), 70)
        dias = {m["Fecha"].day for m in muestra}
        self.assertEqual(len(dias), 7)


# ─── API ────────────────────────────────────────────────────────────────────
class Api(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.doblar(panel_api, "_sesion", lambda h: (dict(USER), None))
        self.doblar(api_eccsita, "_log", lambda *a: None)
        self.doblar(api_eccsita, "_grupo", lambda: GRUPO)

    def _post(self, path):
        h = HandlerFalso(path=path, command="POST", cuerpo={}, csrf=True)
        self.assertTrue(panel_api.atender(h))
        return h

    def test_ia_apagada_usa_el_determinista(self):
        self.doblar(eccsita, "config", lambda: {"grupo": GRUPO, "ia": False})
        self.doblar(eccsita, "generar_basico",
                    lambda chat, anio, semana: {"estado": "RESUMIDA",
                                                 "generadoPor": "BASICO"})

        def prohibido(*a, **k):
            raise AssertionError("con wa_grupo_ia=0 no se llama a la IA")

        self.doblar(cronica, "generar_semana", prohibido)
        h = self._post("/api/panel/eccsita/semanas/2026/48/generar")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["generadoPor"], "BASICO")

    def test_ia_encendida_usa_la_cronica_con_ia(self):
        self.doblar(eccsita, "config", lambda: {"grupo": GRUPO, "ia": True})
        self.doblar(api_eccsita, "_conexion", lambda: ConexionFalsa())
        vistas = []

        def generar(conn, anio, semana, usar_ia=True, **kw):
            vistas.append((anio, semana, usar_ia))
            return {"estado": "RESUMIDA", "generadoPor": "IA"}

        self.doblar(cronica, "generar_semana", generar)
        h = self._post("/api/panel/eccsita/semanas/2026/48/regenerar")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["generadoPor"], "IA")
        self.assertEqual(vistas, [(2026, 48, True)])

    def test_regenerar_con_ia_caida_no_truena(self):
        self.doblar(eccsita, "config", lambda: {"grupo": GRUPO, "ia": True})
        self.doblar(api_eccsita, "_conexion", lambda: ConexionFalsa())
        self.doblar(eccsita, "generar_basico",
                    lambda chat, anio, semana: {"generadoPor": "BASICO"})

        def rompe(*a, **k):
            raise RuntimeError("sin red")

        self.doblar(cronica, "generar_semana", rompe)
        h = self._post("/api/panel/eccsita/semanas/2026/48/generar")
        self.assertEqual(h.code, 200)
        self.assertEqual(h.payload["generadoPor"], "BASICO")


# ─── worker ─────────────────────────────────────────────────────────────────
class Worker(BaseConDobles):
    def setUp(self):
        super().setUp()
        self.llamadas = []
        self.cfg = {"grupo": GRUPO, "ia": True,
                    "semana_dia": "1", "semana_hora": "09:00"}
        self.doblar(eccsita, "_fila_semana", lambda *a: None)
        self.doblar(eccsita, "_mensajes_semana", lambda *a: list(MENSAJES))

    def _generar(self, conn, anio, semana, usar_ia=True, exigir_ia=False):
        self.llamadas.append((anio, semana, usar_ia, exigir_ia))
        return {"generadoPor": "IA"}

    def test_lunes_en_ventana_genera_la_semana_anterior(self):
        detalle, generadas = worker.ciclo(ahora=LUNES, conn=object(),
                                          generar=self._generar,
                                          cfg=dict(self.cfg))
        self.assertEqual(generadas, 1)
        self.assertIn("semana 48", detalle)
        self.assertEqual(self.llamadas, [(2026, 48, True, True)])

    def test_fuera_de_ventana_no_hace_nada(self):
        sabado = datetime.datetime(2026, 11, 28, 9, 10)
        detalle, generadas = worker.ciclo(ahora=sabado, conn=object(),
                                          generar=self._generar,
                                          cfg=dict(self.cfg))
        self.assertEqual((detalle, generadas), ("fuera de ventana", 0))

        tarde = datetime.datetime(2026, 11, 30, 10, 0)
        detalle, _ = worker.ciclo(ahora=tarde, conn=object(),
                                  generar=self._generar, cfg=dict(self.cfg))
        self.assertEqual(detalle, "fuera de ventana")
        self.assertEqual(self.llamadas, [])

    def test_semana_ya_resumida_no_se_toca(self):
        self.doblar(eccsita, "_fila_semana",
                    lambda *a: {"Estado": "RESUMIDA"})
        detalle, generadas = worker.ciclo(ahora=LUNES, conn=object(),
                                          generar=self._generar,
                                          cfg=dict(self.cfg))
        self.assertEqual(generadas, 0)
        self.assertIn("ya está resumida", detalle)
        self.assertEqual(self.llamadas, [])

    def test_sin_mensajes_no_genera(self):
        self.doblar(eccsita, "_mensajes_semana", lambda *a: [])
        detalle, generadas = worker.ciclo(ahora=LUNES, conn=object(),
                                          generar=self._generar,
                                          cfg=dict(self.cfg))
        self.assertEqual(generadas, 0)
        self.assertIn("no tiene mensajes", detalle)

    def test_ia_caida_deja_la_semana_capturada(self):
        def rompe(*a, **k):
            raise cronica.CronicaIAError("timeout")

        detalle, generadas = worker.ciclo(ahora=LUNES, conn=object(),
                                          generar=rompe, cfg=dict(self.cfg))
        self.assertEqual(generadas, 0)
        self.assertIn("CAPTURADA", detalle)

    def test_timeout_real_no_rompe_el_worker(self):
        self.guardar_ia()
        guardado = self.capturar_guardado()
        self.doblar(eccsita, "config", lambda: dict(self.cfg))
        self.ia_devuelve([TimeoutError("la IA tardó más de 60 s")])

        detalle, generadas = worker.ciclo(ahora=LUNES, conn=ConexionFalsa())
        self.assertEqual(generadas, 0)
        self.assertIn("CAPTURADA", detalle)
        self.assertEqual(guardado, {})

    def test_ia_apagada_genera_determinista(self):
        self.cfg["ia"] = False
        self.doblar(eccsita, "config", lambda: dict(self.cfg))
        self.doblar(cronica, "_llamar",
                    lambda *a, **k: self.fail("no se debe llamar"))
        self.guardar_ia()
        guardado = self.capturar_guardado()

        detalle, generadas = worker.ciclo(ahora=LUNES, conn=ConexionFalsa())
        self.assertEqual(generadas, 1)
        self.assertEqual(guardado["generadoPor"], "BASICO")
        self.assertIn("BASICO", detalle)


if __name__ == "__main__":
    unittest.main()
