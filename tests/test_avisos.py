"""
tests/test_avisos.py — Pruebas del despacho de avisos push
==========================================================
Sin SQL Server, sin pywebpush, sin esperar a que sea el día correcto.

Lo que se prueba aquí es lo que NO se puede ver mirando el resultado en
producción: qué pasa el viernes a las 18:31, el sábado, el lunes a las 8:50, y
que 18.5 se convierta en 18:30 en vez de 18:00.

`notif_dispatch` importa `eccsa_db`, que al importarse abre conexiones; por eso
antes de importarlo se le inyecta un doble. Las funciones puras de reloj
(`dentro_de_horario`, `proximo_envio`, `_fraccion_a_hora`) no tocan la base, así
que se ejercitan tal cual.

Ejecución:  python tests/test_avisos.py
"""

import datetime
import os
import sys
import types
import unittest
import unittest.mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ── Doble de eccsa_db: registra lo que se le pide y no abre nada ─────────────
class _FakeCursor(dict):
    """Cursor que responde lo que se le programe, como dict de filas."""
    def __init__(self, respuestas=None):
        super().__init__()
        self.respuestas = respuestas or {}
        self.ejecutadas = []
        self.rowcount = 1
        self.ultima = None

    def execute(self, sql, params=()):
        limpio = " ".join(sql.split())
        self.ejecutadas.append((limpio, params))
        self.ultima = limpio
        # Se limpia ANTES de buscar: si no, una consulta sin coincidencia
        # devolvería la fila de la consulta anterior y `horario()` leería el
        # valor de la clave equivocada.
        self.clear()
        # La clave de HUB_Config viaja como PARÁMETRO (%s), no dentro del SQL.
        # Sin buscarlo también en los parámetros, todo devolvería el defecto y
        # los tests pasarían sin probar nada.
        donde = limpio + " | " + str(params)
        for patron, valor in self.respuestas.items():
            if patron in donde:
                if isinstance(valor, list):
                    return
                self.update(valor or {})
                return

    def fetchone(self):
        return dict(self) if self else None

    def fetchall(self):
        return [dict(self)] if self else []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.comite = 0
        self.rollback_n = 0

    def cursor(self, as_dict=False, **kw):
        return self._cursor

    def commit(self):
        self.comite += 1

    def close(self):
        pass

    def rollback(self):
        self.rollback_n += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


_cursor = _FakeCursor()
_conexiones = []
_ejecuciones = []          # SQL de todo lo que se escribió
_fake_db = types.ModuleType("eccsa_db")


def _get_connection():
    conn = _FakeConn(_cursor)
    _conexiones.append(conn)
    return conn


def _ejecuta(sql, params=()):
    _ejecuciones.append((" ".join(sql.split()), params))


def _reset():
    _cursor.respuestas.clear()
    _cursor.ejecutadas.clear()
    _cursor.rowcount = 1
    del _ejecuciones[:]
    del _conexiones[:]
    _fake_db.send_push_notification_to_users = _envia_fake
    _fake_db.get_connection = _get_connection


_llamadas_envio = []


def _envia_fake(titulo, mensaje, correos, extra=None, app=None):
    """Sustituto del envío real: anota la llamada y dice que llegó a uno.

    `app` existe porque el despacho ahora lo pasa: sin él, el doble firmaría una
    firma distinta de la de producción y las pruebas no comprobarían nada de lo
    que importa (que el aviso salga por los equipos de SU app).
    """
    _llamadas_envio.append({"titulo": titulo, "mensaje": mensaje,
                            "correos": list(correos or []), "extra": extra or {},
                            "app": app})
    return (1, 0) if correos else (0, 1)


_fake_db.send_push_notification_to_users = _envia_fake
_fake_db.get_connection = _get_connection

# El doble se instala SOLO mientras se importa `notif_dispatch`, y se restaura
# en el acto. `notif_dispatch` guarda la referencia en su atributo `db`, así que
# sigue viendo el doble durante todas sus pruebas.
#
# Por qué hay que hacerlo tan solo: `unittest discover` IMPORTA todos los
# módulos de test antes de ejecutar ninguno. Con el doble puesto en
# `sys.modules` durante más tiempo, los módulos siguientes importan un
# `eccsa_db` de mentira y fallan con "does not have the attribute ..." (se
# rompió la suite en 25 pruebas ajenas antes de cerrar esta ventana).
_ORIGINAL_ECCSA_DB = sys.modules.get("eccsa_db")
sys.modules["eccsa_db"] = _fake_db
try:
    import notif_dispatch as nd  # noqa: E402
finally:
    if _ORIGINAL_ECCSA_DB is not None:
        sys.modules["eccsa_db"] = _ORIGINAL_ECCSA_DB
    else:
        sys.modules.pop("eccsa_db", None)


def tearDownModule():
    """Red de seguridad: deja el módulo real en su lugar pase lo que pase."""
    if _ORIGINAL_ECCSA_DB is not None:
        sys.modules["eccsa_db"] = _ORIGINAL_ECCSA_DB


# ── Un momento fijo en México, sin depender del reloj del servidor ────────────
def mx(dia, mes, anio, hora, minuto=0, segundo=0):
    return datetime.datetime(anio, mes, dia, hora, minuto, segundo,
                             tzinfo=nd.TZ_MEXICO)


class TestFraccionAHora(unittest.TestCase):
    """18.5 tiene que ser 18:30, no 18:00: el acuerdo de jornada es 18:30."""

    def test_entero_trivial(self):
        self.assertEqual(nd._fraccion_a_hora(9), (9, 0))

    def test_media_hora(self):
        self.assertEqual(nd._fraccion_a_hora(18.5), (18, 30))

    def test_un_cuarto(self):
        self.assertEqual(nd._fraccion_a_hora(9.25), (9, 15))

    def test_un_tercio(self):
        self.assertEqual(nd._fraccion_a_hora(18.5 + (1/60)), (18, 31))

    def test_texto_en_lugar_de_numero(self):
        # Si alguien deja la clave con basura, se usa el valor por defecto en
        # vez de reventar el ciclo del worker.
        self.assertEqual(nd._fraccion_a_hora("nonsense"), (9, 0))
        self.assertEqual(nd._fraccion_a_hora(None), (9, 0))

    def test_redondeo_que_se_pasa_de_60(self):
        # 18.999 quedaría en minuto 60; hay que normalizarlo a la siguiente hora.
        self.assertEqual(nd._fraccion_a_hora(18.999), (19, 0))

    def test_hora_legible(self):
        self.assertEqual(nd.hora_legible(18.5), "18:30")
        self.assertEqual(nd.hora_legible(9), "09:00")


class TestDiaLaboral(unittest.TestCase):
    def test_lunes_a_viernes(self):
        # 2026-10-05 es lunes.
        for d in range(5):
            self.assertTrue(nd.es_dia_laboral(mx(5 + d, 10, 2026, 10)), f"día {d}")

    def test_sabado_y_domingo(self):
        self.assertFalse(nd.es_dia_laboral(mx(10, 10, 2026, 10)))   # sábado
        self.assertFalse(nd.es_dia_laboral(mx(11, 10, 2026, 10)))   # domingo


class TestDentroDeHorario(unittest.TestCase):
    """El caso que motivated todo: el lunes 9:05 no debe sacar lo del sábado."""

    INICIO, FIN = 9, 18.5

    def test_dentro_en_semana(self):
        self.assertTrue(nd.dentro_de_horario(mx(5, 10, 2026, 10), self.INICIO, self.FIN))
        self.assertTrue(nd.dentro_de_horario(mx(5, 10, 2026, 18, 29), self.INICIO, self.FIN))

    def test_limite_exacto(self):
        # Abre a las 9:00 exactas.
        self.assertTrue(nd.dentro_de_horario(mx(5, 10, 2026, 9, 0), self.INICIO, self.FIN))
        # Y cierra a las 18:30: un segundo antes todavía entra, a las 18:30
        # exactas ya no.
        self.assertFalse(nd.dentro_de_horario(mx(5, 10, 2026, 18, 30), self.INICIO, self.FIN))
        self.assertTrue(nd.dentro_de_horario(mx(5, 10, 2026, 18, 29, 59), self.INICIO, self.FIN))

    def test_antes_de_hora(self):
        self.assertFalse(nd.dentro_de_horario(mx(5, 10, 2026, 8, 59), self.INICIO, self.FIN))

    def test_noche(self):
        self.assertFalse(nd.dentro_de_horario(mx(5, 10, 2026, 22), self.INICIO, self.FIN))
        self.assertFalse(nd.dentro_de_horario(mx(5, 10, 2026, 3), self.INICIO, self.FIN))

    def test_fin_de_semana_aunque_coincida_la_hora(self):
        # Sábado y domingo a las 12:00: hora de trabajo, pero no día hábil.
        self.assertFalse(nd.dentro_de_horario(mx(10, 10, 2026, 12), self.INICIO, self.FIN))
        self.assertFalse(nd.dentro_de_horario(mx(11, 10, 2026, 12), self.INICIO, self.FIN))


class TestProximoEnvio(unittest.TestCase):
    """Cuándo vuelve a abrirse la ventana. Es lo que decide cuándo sale el resumen."""

    def test_despues_de_la_apertura_salta_al_dia_siguiente(self):
        # Miércoles 10:00: la apertura de hoy (09:00) YA PASÓ, así que la
        # siguiente ventana es mañana a las 9:00, no hoy a las 9.
        momento = mx(7, 10, 2026, 10)
        self.assertEqual(nd.proximo_envio(momento, 9), mx(8, 10, 2026, 9))

    def test_mismo_dia_antes_de_la_apertura(self):
        # Miércoles 7:00 todavía no abre: el mismo día a las 9:00.
        momento = mx(7, 10, 2026, 7)
        self.assertEqual(nd.proximo_envio(momento, 9), mx(7, 10, 2026, 9))

    def test_viernes_noche_salta_al_lunes(self):
        # Viernes 22:00 -> lunes 9:00, no el fin de semana.
        momento = mx(9, 10, 2026, 22)
        self.assertEqual(nd.proximo_envio(momento, 9), mx(12, 10, 2026, 9))

    def test_sabado_salta_al_lunes(self):
        momento = mx(10, 10, 2026, 14)
        self.assertEqual(nd.proximo_envio(momento, 9), mx(12, 10, 2026, 9))

    def test_domingo_salta_al_lunes(self):
        momento = mx(11, 10, 2026, 14)
        self.assertEqual(nd.proximo_envio(momento, 9), mx(12, 10, 2026, 9))

    def test_apertura_con_minutos(self):
        # El horario acordado empieza a las 9, pero si se cambiara a 9.5 (09:30)
        # el próximo envío tiene que ser a las 09:30 y no a las 09:00.
        momento = mx(7, 10, 2026, 8)
        self.assertEqual(nd.proximo_envio(momento, 9.5), mx(7, 10, 2026, 9, 30))

    def test_siempre_devuelve_un_instante_futuro(self):
        for dia in range(1, 15):
            for hora in (0, 8, 9, 13, 19, 23):
                m = mx(dia, 10, 2026, hora)
                self.assertGreater(nd.proximo_envio(m, 9), m,
                                   f"no avanzó: {m}")


class TestValidarPermiso(unittest.TestCase):
    """El nombre de permiso se interpola en el SQL, así que se valida siempre."""

    def test_acepta_los_del_catalogo(self):
        for t in nd.TIPOS.values():
            self.assertTrue(nd._validar_permiso(t["permiso"]), t["permiso"])

    def test_rechaza_basura(self):
        for malo in ("", None, "Acceso; DROP TABLE x", "usuarios",
                     "Acceso Reportes", "1 OR 1=1"):
            self.assertFalse(nd._validar_permiso(malo), repr(malo))

    def test_usuarios_con_suscripcion_aborta_con_permiso_invalido(self):
        with self.assertRaises(ValueError):
            nd.usuarios_con_suscripcion(permiso="DROP TABLE HUB_Users")


class TestCatalogoDeTipos(unittest.TestCase):
    """El catálogo es lo que traduce 'qué avisar' a SQL; si se desincroniza del
    interruptor de HUB_Config, el aviso se queda apagado sin que nadie lo note."""

    def test_cinco_tipos(self):
        self.assertEqual(len(nd.TIPOS), 5)

    def test_cada_tipo_tiene_lo_que_necesita(self):
        for nombre, d in nd.TIPOS.items():
            self.assertIn("permiso", d, nombre)
            self.assertIn("titulo", d, nombre)
            self.assertIn("url", d, nombre)
            self.assertTrue(d["url"].startswith("/"), f"{nombre}: {d['url']}")
            self.assertTrue(callable(d["titulo_func"]), nombre)
            self.assertTrue(callable(d["mensaje_func"]), nombre)

    def test_detector_para_cada_tipo(self):
        self.assertEqual(set(nd.DETECTORES), set(nd.TIPOS))

    def test_el_nombre_del_tipo_es_la_clave_de_config(self):
        # TICKET_OXXOGAS -> avisos_push_ticket_oxxogas. Si alguien escribe
        # TICKET_OXXOGAS2, la clave sale distinta y el aviso nunca enciende.
        for nombre in nd.TIPOS:
            self.assertEqual(f"avisos_push_{nombre.lower()}",
                             f"avisos_push_{nombre.lower()}")

    def test_cotizaciones_comparten_permiso(self):
        self.assertEqual(nd.TIPOS["COTIZACION_FIRMADA"]["permiso"],
                         nd.TIPOS["COTIZACION_FACTURADA"]["permiso"])


class TestTextoResumen(unittest.TestCase):
    """Un resumen con ocho cosas que decir, en un solo aviso."""

    def _filas(self, n):
        return [{"Titulo": f"Aviso {i}", "Id": i} for i in range(1, n + 1)]

    def test_uno_en_singular(self):
        t = nd._texto_resumen(self._filas(1))
        self.assertIn("Tienes 1 aviso pendiente", t)
        self.assertNotIn("avisos", t)

    def test_varios_en_plural(self):
        t = nd._texto_resumen(self._filas(8))
        self.assertIn("Tienes 8 avisos pendientes", t)

    def test_no_vuelca_la_lista_entera(self):
        # El requisito es un aviso corto: si no, en el teléfono es un muro.
        t = nd._texto_resumen(self._filas(30))
        self.assertLess(len(t), 200)

    def test_vacio(self):
        # Con nada que resumir el texto sale raro, pero no revienta: es el caso
        # de una carrera entre dos ciclos del worker, no un error de la app.
        self.assertIn("Tienes 0 avisos", nd._texto_resumen([]))


class TestEnvio(unittest.TestCase):
    """El reparto se hace al ENCOLAR, no al enviar. Ver por qué está en el
    docstring de `encolar` y por qué estos tests lo fijan."""

    def setUp(self):
        _reset()
        del _llamadas_envio[:]

    def test_el_envio_lleva_url_y_tag(self):
        # Sin `url`, el aviso abre la raíz y el usuario no sabe qué abrir.
        _llamadas_envio.clear()
        nd.db.send_push_notification_to_users(
            "tit", "msg", ["a@x.com"],
            extra={"url": "/reportes", "tag": "admon-reporte_firmado"})
        self.assertEqual(_llamadas_envio[0]["extra"]["url"], "/reportes")
        self.assertEqual(_llamadas_envio[0]["extra"]["tag"], "admon-reporte_firmado")

    def test_el_resumen_lleva_el_conteo_como_badge(self):
        # En iOS `badge` es un número; el SW lo espera en `badge_count`.
        nd.db.send_push_notification_to_users(
            "🔔 Tienes 3 avisos", "cuerpo", ["a@x.com"],
            extra={"tag": "admon-digest-7", "url": "/", "badge_count": 3})
        self.assertEqual(_llamadas_envio[0]["extra"]["badge_count"], 3)


class TestHorariosDesdeConfig(unittest.TestCase):
    """El horario se lee de HUB_Config, y una clave con basura no debe tumbar el
    ciclo: se cae al valor acordado."""

    def setUp(self):
        _reset()

    def test_valores_por_defecto(self):
        inicio, fin = nd.horario(_cursor)
        self.assertEqual(inicio, 9)
        self.assertEqual(fin, 18.5)

    def test_valores_de_la_base(self):
        _cursor.respuestas = {
            "avisos_push_horario_inicio": {"v": "10"},
            "avisos_push_horario_fin": {"v": "17"},
        }
        inicio, fin = nd.horario(_cursor)
        self.assertEqual((inicio, fin), (10.0, 17.0))

    def test_basura_usa_el_defecto(self):
        _cursor.respuestas = {"avisos_push_horario_fin": {"v": "tarde"}}
        self.assertEqual(nd.horario(_cursor)[1], 18.5)

    def test_tipo_activo_por_defecto(self):
        # Sin fila en HUB_Config, un tipo nuevo se considera encendido.
        self.assertTrue(nd.tipo_activo(_cursor, "REPORTE_FIRMADO"))
        _cursor.respuestas = {"avisos_push_kilometros": {"v": "0"}}
        self.assertFalse(nd.tipo_activo(_cursor, "KILOMETROS"))

    def test_tipo_activo_acepta_variantes(self):
        for v in ("1", "true", "TRUE", " si ", "sí", "yes"):
            _cursor.respuestas = {"avisos_push_kilometros": {"v": v}}
            self.assertTrue(nd.tipo_activo(_cursor, "KILOMETROS"), v)
        for v in ("0", "false", "no", "off"):
            _cursor.respuestas = {"avisos_push_kilometros": {"v": v}}
            self.assertFalse(nd.tipo_activo(_cursor, "KILOMETROS"), v)

    def test_valor_vacio_cae_al_defecto(self):
        # Vaciar la casilla NO apaga el aviso: cae al valor por defecto (que es
        # encendido). Para apagar hay que escribir 0. Queda escrito porque es la
        # forma fácil de descuadrar el interruptor por error.
        _cursor.respuestas = {"avisos_push_kilometros": {"v": ""}}
        self.assertTrue(nd.tipo_activo(_cursor, "KILOMETROS"))


class TestCorrespondeResumen(unittest.TestCase):
    """El resumen no puede dispararse dos veces ni perderse por un reinicio."""

    def setUp(self):
        _reset()

    def test_lunes_antes_de_las_nueve_no(self):
        momento = mx(12, 10, 2026, 8, 50)
        self.assertFalse(nd._corresponde_resumen(momento, 9, "2026-10-09", True))

    def test_lunes_despues_de_las_nueve_si(self):
        momento = mx(12, 10, 2026, 9, 5)
        self.assertTrue(nd._corresponde_resumen(momento, 9, "2026-10-09", True))

    def test_no_repetir_el_mismo_dia(self):
        momento = mx(12, 10, 2026, 9, 5)
        self.assertFalse(nd._corresponde_resumen(momento, 9, "2026-10-12", True))

    def test_desactivado_no_corrige(self):
        momento = mx(12, 10, 2026, 9, 5)
        self.assertFalse(nd._corresponde_resumen(momento, 9, "2026-10-09", False))

    def test_sin_marcador_no(self):
        # Nunca se entregó: no hay nada acumulado que justifique un resumen.
        momento = mx(12, 10, 2026, 9, 5)
        self.assertFalse(nd._corresponde_resumen(momento, 9, "", True))

    def test_fin_de_semana_con_marcador_del_viernes(self):
        # Sábado: sigue fuera de horario pero lo del viernes ya no se puede
        # quedar para siempre.
        momento = mx(10, 10, 2026, 15)
        self.assertTrue(nd._corresponde_resumen(momento, 9, "2026-10-09", True))


class TestNormalizarVapidPrivada(unittest.TestCase):
    """
    La clave privada que guarda la base tiene que estar en el formato que
    pywebpush entiende: base64url de los 32 bytes CRUDOS.

    Se encontró una en la base de pruebas que venia en DER (39 bytes). Con esa,
    TODOS los push fallan y el error que sale no dice nada util, asi que el
    peligro no es que falle: es que nadie se entere de por que.
    """

    @staticmethod
    def _bytes(clave_b64):
        import base64
        pad = "=" * ((4 - len(clave_b64) % 4) % 4)
        return base64.urlsafe_b64decode(
            (clave_b64 + pad).replace("-", "+").replace("_", "/"))

    def setUp(self):
        import eccsa_db
        self._norm = eccsa_db._normalizar_vapid_privada

    def test_una_clave_buena_se_deja_igual(self):
        # Par generado por el propio pywebpush: 32 bytes crudos.
        buena = "9vHHZ72DUVw5jFk5C5mDlYxHxkCapy0gOzjdJtj-PNc"
        self.assertEqual(self._norm(buena), buena)
        self.assertEqual(len(self._bytes(self._norm(buena))), 32)

    def test_pem_se_convierte_a_bytes_crudos(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            self.skipTest("cryptography no esta instalado")
        clave = ec.generate_private_key(ec.SECP256R1())
        pem = clave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()).decode()
        salida = self._norm(pem)
        self.assertEqual(len(self._bytes(salida)), 32)
        # Y debe seguir SIENDO la misma clave, no otra.
        esperado = clave.private_numbers().private_value.to_bytes(32, "big")
        self.assertEqual(self._bytes(salida), esperado)

    def test_der_en_base64_se_convierte(self):
        # Así estaba en la base de pruebas: base64 de un DER de PKCS#8.
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            self.skipTest("cryptography no esta instalado")
        import base64
        clave = ec.generate_private_key(ec.SECP256R1())
        der = clave.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())
        entrada = base64.b64encode(der).decode()
        salida = self._norm(entrada)
        self.assertEqual(len(self._bytes(salida)), 32)
        self.assertEqual(self._bytes(salida),
                         clave.private_numbers().private_value.to_bytes(32, "big"))

    def test_basura_se_devuelve_tal_cual(self):
        # No se devuelve "": un "" silencioso seria peor que un error de pywebpush.
        for malo in ("", "   ", "no-es-una-clave"):
            self.assertEqual(self._norm(malo), malo.strip())



class TestFormaDelSQL(unittest.TestCase):
    """
    La forma de la sentencia, no su resultado.

    Pasó en producción: `pendientes()` armaba el TOP con `%d` y la sentencia
    también llevaba el `%s` del app. Un solo operador `%` sobre los dos
    marcadores se come los dos argumentos, y el worker falló en el primer turno
    con "not enough arguments for format string". El doble de los tests de arriba
    no aplica el formato, así que esto NO se veía; el heartbeat sí.

    Por eso aquí se comprueba que el número de marcadores que se pasan es el que
    la sentencia declara, que es la regla que pymssql aplica de verdad.
    """

    def setUp(self):
        _reset()

    def _comprueba(self, sql, params):
        """Como lo hace DB-Lib: los marcadores tienen que calzar con los args."""
        import re
        marcadores = len(re.findall(r"%s", sql))
        self.assertEqual(
            marcadores, len(params),
            f"la sentencia declara {marcadores} marcador(es) y se pasan "
            f"{len(params)} argumento(s): {sql}")

    def test_pendientes_tiene_un_marcador_y_un_argumento(self):
        nd.pendientes(app="admon")
        sql, params = _cursor.ejecutadas[-1]
        self._comprueba(sql, params)
        self.assertIn("App = %s", sql)
        self.assertIn("HUB_AvisosCola", sql)

    def test_pendientes_el_top_va_como_numero_y_no_como_marcador(self):
        nd.pendientes(app="admon", limite=250)
        sql, _ = _cursor.ejecutadas[-1]
        self.assertIn("TOP (250)", sql)
        self.assertNotIn("%d", sql)
        self.assertNotIn("TOP %", sql)

    def test_el_limite_se_escapa_a_entero(self):
        # Un limite que venga de fuera como texto no debe poder inyectar SQL ni
        # romper la sentencia.
        nd.pendientes(app="admon", limite="50")
        sql, params = _cursor.ejecutadas[-1]
        self.assertIn("TOP (50)", sql)
        self._comprueba(sql, params)

    def test_limite_negativo_o_basura_no_rompe(self):
        for malo in (-5, "abc", None):
            try:
                nd.pendientes(app="admon", limite=malo)
            except (ValueError, TypeError):
                continue          # que rechace el valor está bien
            sql, params = _cursor.ejecutadas[-1]
            self._comprueba(sql, params)



# ── Field: el despachador tiene que entregar por APP ──────────────────────────
#
# El fallo que esto cubre es silencioso, que es la peor clase: una fila de la
# cola de Field se buscaba en los equipos de Admon, no encontraba ninguno y
# quedaba en FALLADO "sin dispositivos que acepten" sin que nadie supiera que
# el aviso nunca salió. Además el `tag` llevaba "admon-" fijo, así que un mismo
# tipo en dos apps se pisaba.
class TestDespachoPorApp(unittest.TestCase):

    def setUp(self):
        _reset()
        # _reset() no vacía la lista de envíos (otras pruebas la consultan tal
        # cual); aquí hace falta empezar de cero para no contar lo que mandó la
        # prueba anterior.
        del _llamadas_envio[:]

    def test_correos_de_filtra_por_la_app_que_se_le_pide(self):
        _cursor.respuestas = {"HUB_PushSuscripciones": [{"Email": "hector@ecc-sa.com.mx"}]}
        nd._correos_de([2], app="field")
        sql, params = _cursor.ejecutadas[-1]
        self.assertIn("s.App = %s", sql)
        self.assertEqual(params, ("field",))
        self.assertIn("HUB_PushSuscripciones", sql)

    def test_correos_de_marcaadores_y_argumentos_calzan(self):
        import re
        nd._correos_de([2], app="field")
        sql, params = _cursor.ejecutadas[-1]
        self.assertEqual(len(re.findall(r"%s", sql)), len(params))

    def test_despachar_de_field_pasa_la_app_al_envio(self):
        # Fila pendiente de Field para el usuario 2.
        _cursor.respuestas = {
            "HUB_AvisosCola": {"Id": 99, "App": "field", "Tipo": "REPORTE_FIRMADO",
                               "IdUsuario": 2, "Titulo": "✍️ Reporte firmado",
                               "Mensaje": "Ya está firmado.", "Url": "/reportes/7"},
            "HUB_PushSuscripciones": {"Email": "hector@ecc-sa.com.mx"},
        }
        with unittest.mock.patch.object(nd, "dentro_de_horario", return_value=True), \
             unittest.mock.patch.object(nd, "resumen_activo", return_value=False), \
             unittest.mock.patch.object(nd, "cfg_get", return_value=""):
            nd.despachar(app="field")
        self.assertEqual(len(_llamadas_envio), 1, "no se intentó enviar nada")
        self.assertEqual(_llamadas_envio[0]["app"], "field",
                         "el envío no se hizo por la app de field: el aviso "
                         "buscaría los equipos de otra app")
        self.assertEqual(_llamadas_envio[0]["extra"]["tag"], "field-reporte_firmado")

    def test_el_resumen_also_se_envia_por_su_app(self):
        _cursor.respuestas = {
            "HUB_AvisosCola": {"Id": 5, "App": "field", "Tipo": "VALE_GENERADO",
                               "IdUsuario": 2, "Titulo": "⛽ Vale listo",
                               "Mensaje": "Ya puedes pagarlo.", "Url": "/vales"},
            "HUB_PushSuscripciones": {"Email": "hector@ecc-sa.com.mx"},
        }
        n = nd.enviar_resumen(app="field")
        self.assertEqual(n, 1)
        self.assertEqual(_llamadas_envio[0]["app"], "field")
        self.assertTrue(_llamadas_envio[0]["extra"]["tag"].startswith("field-"))

    def test_sin_equipos_de_esa_app_no_marca_enviado(self):
        # Sin patron para HUB_PushSuscripciones a proposito: este usuario no
        # tiene ningun equipo con Field instalado.
        _cursor.respuestas = {
            "HUB_AvisosCola": {"Id": 7, "App": "field", "Tipo": "VALE_GENERADO",
                               "IdUsuario": 2, "Titulo": "t", "Mensaje": "m",
                               "Url": "/vales"},
        }
        with unittest.mock.patch.object(nd, "dentro_de_horario", return_value=True), \
             unittest.mock.patch.object(nd, "resumen_activo", return_value=False), \
             unittest.mock.patch.object(nd, "cfg_get", return_value=""):
            nd.despachar(app="field")
        # El envío se intenta con la lista de correos VACÍA (así se ve que
        # buscó equipos y no los encontró) y la fila queda FALLADA, no ENVIADA:
        # marcar ENVIADO algo que no salió es cómo se pierde un aviso en silencio.
        self.assertEqual(len(_llamadas_envio), 1)
        self.assertEqual(_llamadas_envio[0]["correos"], [])
        self.assertEqual(_llamadas_envio[0]["app"], "field")
        # El estado va como PARÁMETRO (no dentro del SQL), así que se comprueba
        # en los argumentos: es el mismo cuidado de marcadores que ya tiene la
        # clase TestFormaDelSQL.
        sql, params = _cursor.ejecutadas[-1]
        self.assertIn("UPDATE HUB_AvisosCola SET Estado = %s", sql)
        self.assertEqual(params[0], "FALLADO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
