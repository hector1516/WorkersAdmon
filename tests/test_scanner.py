"""
Pruebas del escáner de presencia: qué se escribe y en qué orden.

El escáner es la fuente de la asistencia, así que un ORDER BY equivocado o un
GETDATE() donde va el FechaScan no son detalles: son las 3-5 minutos de
desfase que se vieron en producción. Estas pruebas leen el CÓDIGO y ejecutan
las partes puras, sin base de datos ni red.

    python3 -m unittest tests.test_scanner
"""

import ast
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import network_scanner as sc

RAIZ = Path(__file__).resolve().parent.parent


def _codigo(ruta):
    """
    El archivo SIN comentarios ni docstrings.

    Hace falta porque las aserciones van sobre el SQL y sobre los argumentos que
    se pasan, no sobre la prosa: si el docstring de una función menciona el
    ORDER BY viejo, la prueba passes (o falla) por el comentario y no por el
    código, que es justo lo que uno quiere evitar.
    """
    fuente = (RAIZ / ruta).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(nodo):
            nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(arbol) if hasattr(ast, "unparse") else fuente


def _fuente_scanner():
    return _codigo("network_scanner.py")


def _fuente_db():
    return _codigo("eccsa_db.py")


class OrdenDeProcesamiento(unittest.TestCase):
    """Los escaneos pendientes se procesan del más ANTIGUO al más nuevo."""

    def test_pide_el_scan_mas_antiguo(self):
        fuente = _fuente_scanner()
        # El ORDER BY tiene que ser ASC: antes pedía el más nuevo (DESC) y el
        # bucle repetía hasta vaciar la cola, o sea que la secuencia llegaba
        # al revés y el ENTRADA de un escaneo podía quedar antes que el SALIDA
        # del mismo dispositivo en el mismo lote.
        self.assertIn("ORDER BY FechaScan ASC", fuente)
        self.assertNotIn("ORDER BY FechaScan DESC", fuente,
                         "el SQL sigue pidiendo el escaneo más nuevo")

    def test_ya_no_existe_el_nombre_viejo(self):
        """Que quede una sola forma de pedir el scan pendiente."""
        fuente = _fuente_scanner()
        self.assertNotIn("get_latest_unprocessed_macs", fuente)
        self.assertTrue(hasattr(sc, "get_proximo_scan_pendiente"))


class SelladoConElEscaneo(unittest.TestCase):
    """El instante del evento es el del ESCANEO, no el del procesamiento."""

    def test_el_insert_guarda_las_cuatro_columnas_de_evidencia(self):
        # El INSERT vive en eccsa_db (el worker delega); lo que se comprueba es
        # que la fila guarda la evidencia y no solo el momento de proceso.
        db = _fuente_db()
        self.assertIn("FechaDeteccion", db)
        self.assertIn("UltimaVezVisto", db)
        self.assertIn("VentanaMin", db)
        self.assertIn("Origen", db)

    def test_registra_fecha_de_deteccion(self):
        fuente = _fuente_scanner()
        self.assertIn("fecha_deteccion", fuente)
        self.assertIn("ultima_vez_visto", fuente)

    def test_el_evento_guarda_los_dos_extremos_de_la_ventana(self):
        """Una ENTRADA tiene que guardar desde cuándo se sabe que NO estaba."""
        fuente = _fuente_scanner()
        entrada = fuente[fuente.index("'ENTRADA', 95.0"):]
        entrada = entrada[:entrada.index("alertar_cambio_presencia")]
        self.assertIn("fecha_deteccion=momento", entrada)
        self.assertIn("ultima_vez_visto=ultimo_ok", entrada)
        # Y la SALIDA, con los dos extremos al revés: el piso es la última vez
        # que se vio PRESENTE.
        salida = fuente[fuente.index("'SALIDA', 95.0"):]
        salida = salida[:salida.index("alertar_cambio_presencia")]
        self.assertIn("ultima_vez_visto=ultimo_ok", salida)

    def test_el_estado_usa_el_momento_del_escaneo(self):
        """`update_device_state(device_id, 'AQUI', momento)`, no la hora de proceso."""
        fuente = _fuente_scanner()
        # Ninguna llamada del bucle puede quedarse con una hora de proceso.
        for llamada in re.findall(r"update_device_state\([^)]*\)", fuente):
            self.assertIn("momento", llamada, llamada)


class ToleranciasAsimetricas(unittest.TestCase):
    def test_entrada_en_cero_por_defecto(self):
        """La tolerancia de entrada era un retardo, no una tolerancia.

        Tiene que estar en 0 en los TRES lugares (default del código, fallback
        del bucle y migración): si uno se queda en 2, el desfase reaparece
       depending de si la fila de HUB_Config existe o no.
        """
        fuente = _fuente_scanner()
        # (a) el dict de defaults de get_network_config
        self.assertIn("'net_tolerance_entrada_min': '0'", fuente)
        # (b) el fallback del bucle, que se usa si HUB_Config no tiene la fila
        self.assertIn("net_tolerance_entrada_min', '0'", fuente)
        # Y que no quede ningún rastro del 2 en ningún sitio.
        self.assertNotIn("net_tolerance_entrada_min': '2'", fuente)
        self.assertNotIn("net_tolerance_entrada_min', '2'", fuente)

    def test_salida_nunca_baja_de_dos_intervalos(self):
        """Un debounce de salida más corto que dos escaneos convertiría un solo
        escaneo perdido en una salida falsa."""
        fuente = _fuente_scanner()
        self.assertIn("intervalo_min * 2", fuente)


class SinCodigoDuplicado(unittest.TestCase):
    def test_el_scanner_delega_en_la_capa_de_datos(self):
        """Dos copias del mismo MERGE divergen en silencio: lo que se arregló en
        una se quedó viejo en la otra."""
        fuente = _fuente_scanner()
        self.assertIn("return db.register_presence_event(", fuente)
        self.assertIn("return db.update_device_state(", fuente)
        # Y el worker no debe traer su propio INSERT de presencia.
        self.assertNotIn("INSERT INTO HUB_NetworkPresence", fuente)
        self.assertNotIn("MERGE HUB_NetworkState", fuente)


class ConfigDeAsistencia(unittest.TestCase):
    def test_los_defaults_son_los_de_la_migracion(self):
        """Si el default del código y el de la migración se separan, el
        comportamiento depende de si la migración corrió o no.

        Este repo (WorkersAdmon) no tiene migrations/: la migración vive en el
        repo de HUB, que comparte la base. Ahí el chequeo no se puede hacer, así
        que se salta en vez de fallar.
        """
        ruta = RAIZ / "migrations" / "0042_asistencia_precisa.sql"
        if not ruta.is_file():
            self.skipTest("la migración vive en el repo de HUB, no aquí")
        migracion = ruta.read_text(encoding="utf-8")
        fuente = _fuente_scanner()
        db_fuente = _fuente_db()
        for clave, valor in (("net_asistencia_ventana_min", "120"),
                             ("net_asistencia_escaneos_minimos", "20"),
                             ("net_tolerance_entrada_min", "0")):
            self.assertIn(f"'{clave}'", migracion, clave)
            with self.subTest(clave=clave, valor=valor):
                self.assertIn(valor, migracion, f"{clave}={valor} en la migración")
        self.assertIn("net_tolerance_entrada_min', '0'", fuente)
        self.assertIn("net_asistencia_escaneos_minimos", db_fuente)


if __name__ == "__main__":
    unittest.main(verbosity=2)
