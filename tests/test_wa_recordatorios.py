"""
Pruebas de los recordatorios proactivos del bot de WhatsApp.

Lo que se vigila (y por qué):

  · fuera de la ventana no sale NADA: un aviso a las 22:00 molesta y se paga;
  · kilómetros: texto distinto si hay lectura vieja o si nunca hubo;
  · vales: a las 47 h no molesta, a las 49 h sí; un vale de 14 días avisa y
    uno de 16 ya no; un solo mensaje por persona y por día, y nunca más de
    `wa_rec_vale_max` veces por persona;
  · el resumen del lunes solo sale con datos (sin datos es spam);
  · el índice único del dedupe: dos ciclos el mismo día mandan un solo mensaje;
  · el tope diario por usuario (3) y
  · un fallo de OpenWA se registra con Ok=0 y no se reintenta en el mismo día.

La base y OpenWA se doblan en memoria; la hora se pasa a `ciclo()`. No se toca
SQL Server ni WhatsApp.

    python -m unittest tests.test_wa_recordatorios -v
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_recordatorios as wr  # noqa: E402

# Jueves 26 nov 2026, dentro de la ventana (09:00-18:30).
JUEVES = datetime(2026, 11, 26, 10, 0)
# Sábado 28: la ventana está cerrada.
SABADO = datetime(2026, 11, 28, 10, 0)
# Lunes 30: además de la ventana, es el día del resumen.
LUNES = datetime(2026, 11, 30, 9, 10)

HECTOR = 7
ANA = 8
SIN_WA = 9


class BaseFalsa:
    """Conexión en memoria con las tablas que toca el worker."""

    def __init__(self):
        self.autos = []        # (id, MarcaModelo, Placas, IdUsuarioAsignado)
        self.usuarios = {}     # id -> (Nombre, Telefono, Activo)
        self.km = []           # (IdAutomovil, Kilometros, FechaHora, IdUsuario)
        self.vales = []        # (Id, IdSolicitante, MontoUnit, FechaSolicitud, Estatus)
        self.tickets = set()   # IdSolicitudVale con comprobante
        self.reportes = []     # (IdUsuario, Fecha, Eliminado)
        self.config = {}       # Clave -> Valor
        self.recordatorios = []  # dict(tipo, clave, uid, dia, enviado, ok)
        self.sqls = []
        self.commits = 0
        self.rollbacks = 0
        self.cerrada = False

    def cursor(self, *a, **k):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        p = tuple(params or ())
        b = self.base
        b.sqls.append((q, p))
        self.una = None
        self.todas = []

        if "INSERT INTO HUB_WARECORDATORIOS" in q:
            tipo, clave, uid, dia, enviado, ok, detalle = p
            b.recordatorios.append({"tipo": tipo, "clave": clave, "uid": uid,
                                    "dia": dia, "enviado": enviado,
                                    "ok": bool(ok), "detalle": detalle})

        elif "FROM HUB_WARECORDATORIOS" in q:
            self._recordatorios(q, p)

        elif "FROM HUB_CONFIG" in q:
            self.todas = [(clave, b.config[clave]) for clave in p
                          if clave in b.config]

        elif "FROM HUB_AUTOMOVILES" in q:
            self.todas = []
            for auto_id, marca, placas, uid in b.autos:
                nombre, telefono, activo = b.usuarios.get(uid, ("", "", False))
                if not activo or not telefono:
                    continue
                self.todas.append((auto_id, marca, placas, uid, nombre, telefono))

        elif "FROM HUB_REGISTROKILOMETROS" in q:
            self._kilometros(q, p)

        elif "FROM HUB_SOLICITUDVALES" in q:
            self._vales(q, p)

        elif "FROM HUB_USUARIOSWHATSAPP" in q:
            self.todas = [(uid, nombre, telefono)
                          for uid, (nombre, telefono, activo) in sorted(b.usuarios.items())
                          if activo and telefono]

        elif "FROM REPORTESSERVICIO R" in q:
            uid, ini, fin = p
            n = sum(1 for r in b.reportes
                    if r[0] == uid and not r[2] and ini <= r[1] < fin)
            self.una = (n,)

    # ── Tablas ──────────────────────────────────────────────────────────────

    def _recordatorios(self, q, p):
        b = self.base
        if "GROUP BY IDUSUARIO" in q:
            dia = p[0]
            conteo = {}
            for r in b.recordatorios:
                # Un mensaje suelto de Volt no gasta el tope de recordatorios:
                # viaja dentro de una respuesta normal.
                if r["dia"] == dia and r["ok"] and r["tipo"] != "VOLT":
                    conteo[r["uid"]] = conteo.get(r["uid"], 0) + 1
            self.todas = sorted(conteo.items())

        elif "TIPO = 'VALE'" in q:
            clave, uid = p
            filas = [r for r in b.recordatorios
                     if r["tipo"] == "VALE" and r["clave"] == clave
                     and r["uid"] == uid and r["ok"]]
            ultimo = max((r["enviado"] for r in filas), default=None)
            self.una = (len(filas), ultimo)

        elif "TIPO = 'KM'" in q:
            clave, uid, desde = p
            self.una = (sum(1 for r in b.recordatorios
                            if r["tipo"] == "KM" and r["clave"] == clave
                            and r["uid"] == uid and r["dia"] > desde),)

        else:
            tipo, clave, uid, dia = p
            self.una = (sum(1 for r in b.recordatorios
                            if r["tipo"] == tipo and r["clave"] == clave
                            and r["uid"] == uid and r["dia"] == dia),)

    def _kilometros(self, q, p):
        b = self.base
        if "GROUP BY IDAUTOMOVIL" in q:
            uid, desde = p
            autos = {}
            for auto, km, cuando, _usuario in b.km:
                if _usuario == uid and cuando >= desde:
                    autos.setdefault(auto, []).append(km)
            self.todas = [(auto, min(v), max(v), len(v))
                          for auto, v in sorted(autos.items())]

        elif "AS RECIENTES" in q:
            desde, auto = p
            filas = sorted((k for k in b.km if k[0] == auto),
                           key=lambda k: k[2], reverse=True)
            if filas:
                km, cuando = filas[0][1], filas[0][2]
                recientes = sum(1 for k in filas if k[2] >= desde)
                self.una = (km, cuando, recientes)

        elif "COUNT(*)" in q:
            uid, desde = p
            self.una = (sum(1 for k in b.km if k[3] == uid and k[2] >= desde),)

        else:  # TOP 1 del mes por usuario (wa_consultas._respuesta_km)
            uid, desde = p
            filas = sorted((k for k in b.km if k[3] == uid and k[2] >= desde),
                           key=lambda k: k[2], reverse=True)
            self.una = (filas[0][1], filas[0][2]) if filas else None

    def _vales(self, q, p):
        b = self.base
        if q.startswith("SELECT COUNT(*) FROM HUB_SOLICITUDVALES"):
            uid = p[0]
            self.una = (sum(1 for v in b.vales
                            if v[1] == uid and v[4] == "GENERADO"
                            and v[0] not in b.tickets),)
            return

        corte = p[0]
        desde = p[1] if len(p) > 1 else datetime.min
        self.todas = []
        for vale_id, uid, monto, fecha, estatus in b.vales:
            nombre, telefono, activo = b.usuarios.get(uid, ("", "", False))
            if estatus != "GENERADO" or fecha > corte or fecha < desde:
                continue
            if vale_id in b.tickets or not activo or not telefono:
                continue
            self.todas.append((vale_id, uid, monto, fecha, nombre, telefono))

    def fetchall(self):
        return self.todas

    def fetchone(self):
        return self.una

    def close(self):
        pass


class Envios:
    """Doble de `openwa_client.enviar_texto`."""

    def __init__(self, ok=True, detalle="ok"):
        self.ok = ok
        self.detalle = detalle
        self.llamadas = []

    def __call__(self, chat_id, texto):
        self.llamadas.append((chat_id, texto))
        return self.ok, self.detalle


def _base_con_hector():
    base = BaseFalsa()
    base.usuarios = {
        HECTOR: ("Héctor Peña", "5218123211516", True),
        ANA: ("Ana Ruiz", "528100000000", True),
        SIN_WA: ("Sin WhatsApp", "", True),
    }
    return base


class Ventana(unittest.TestCase):
    def test_sabado_no_envia_nada(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        envios = Envios()

        detalle, enviados = wr.ciclo(ahora=SABADO, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])
        self.assertEqual(base.recordatorios, [])
        self.assertIn("fuera de ventana", detalle)

    def test_despues_de_las_seis_y_media_no_envia(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        tarde = datetime(2026, 11, 26, 18, 31)
        detalle, enviados = wr.ciclo(ahora=tarde, conn=base, enviar=Envios())
        self.assertEqual(enviados, 0)
        self.assertIn("fuera de ventana", detalle)

    def test_ventana_es_de_lunes_a_viernes(self):
        self.assertTrue(wr.en_ventana(datetime(2026, 11, 27, 9, 0)))    # viernes
        self.assertFalse(wr.en_ventana(datetime(2026, 11, 28, 9, 0)))   # sábado
        self.assertFalse(wr.en_ventana(datetime(2026, 11, 29, 9, 0)))   # domingo
        self.assertFalse(wr.en_ventana(datetime(2026, 11, 26, 8, 59)))
        self.assertTrue(wr.en_ventana(datetime(2026, 11, 26, 18, 30)))


class Kilometros(unittest.TestCase):
    def test_avisa_con_la_ultima_lectura(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        base.km = [(1, 1000, JUEVES - timedelta(days=5), HECTOR)]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        chat, texto = envios.llamadas[0]
        self.assertEqual(chat, "5218123211516@c.us")
        self.assertIn("Héctor Peña", texto)
        self.assertIn("Toyota Hilux", texto)
        self.assertIn("ABC1234", texto)
        self.assertIn("no veo kilómetros", texto)
        self.assertIn("21 nov 2026", texto)
        self.assertIn("odómetro", texto)

    def test_avisa_distinto_si_nunca_hubo_lectura(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("no tienes lecturas registradas", envios.llamadas[0][1])

    def test_con_lectura_reciente_no_avisa(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        base.km = [(1, 1000, JUEVES - timedelta(days=1), HECTOR)]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_auto_sin_conductor_con_whatsapp_no_entra(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", SIN_WA)]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)


class Vales(unittest.TestCase):
    def _base(self):
        base = _base_con_hector()
        return base

    def test_vale_de_14_dias_si_avisa(self):
        # La regla del dueño: un vale de hasta 15 días todavía se recuerda.
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(days=14), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("vale #12", envios.llamadas[0][1])

    def test_vale_de_16_dias_no_avisa(self):
        # Más viejo que el tope: ya no se va a comprobar, insistir solo molesta.
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(days=16), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_el_corte_de_edad_se_configura(self):
        base = self._base()
        base.config = {"govale_recordatorio_dias_max": "30"}
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(days=20), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)

    def test_segundo_ciclo_el_mismo_dia_no_repite(self):
        # Un solo mensaje de vales por persona y por día (antes eran 48 h).
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(days=3), "GENERADO")]

        _, primero = wr.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        _, segundo = wr.ciclo(ahora=JUEVES.replace(hour=16), conn=base,
                              enviar=Envios())

        self.assertEqual(primero, 1)
        self.assertEqual(segundo, 0)
        self.assertEqual(len(base.recordatorios), 1)

    def test_al_dia_siguiente_si_puede_repetir(self):
        # Sigue pendiente y dentro del tope: mañana toca otra vez (antes las
        # 48 h lo bloqueaban).
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(days=13), "GENERADO")]
        viernes = JUEVES + timedelta(days=1)

        _, primero = wr.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        _, segundo = wr.ciclo(ahora=viernes, conn=base, enviar=Envios())

        self.assertEqual(primero, 1)
        self.assertEqual(segundo, 1)
        self.assertEqual([r["dia"] for r in base.recordatorios],
                         [JUEVES.date(), viernes.date()])

    def test_a_las_47_horas_no_avisa(self):
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(hours=47), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)

    def test_a_las_49_horas_si_avisa(self):
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(hours=49), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("vale #12", texto)
        self.assertIn("$500.00", texto)
        self.assertIn("sigue sin comprobante", texto)

    def test_con_comprobante_no_avisa(self):
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(hours=49), "GENERADO")]
        base.tickets = {12}
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)

    def test_mas_de_tres_avisos_por_vale_no(self):
        base = self._base()
        base.vales = [(12, HECTOR, 500, JUEVES - timedelta(hours=49), "GENERADO")]
        for dias in (11, 9, 7):
            base.recordatorios.append({
                "tipo": "VALE", "clave": f"u{HECTOR}", "uid": HECTOR,
                "dia": (JUEVES - timedelta(days=dias)).date(),
                "enviado": JUEVES - timedelta(days=dias),
                "ok": True, "detalle": "",
            })
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)


class Resumen(unittest.TestCase):
    def _base(self):
        base = _base_con_hector()
        # Sin esto `_respuesta_km` usaría el mes real, que puede no ser el de
        # la prueba; con el parche, el "mes" es el de `ahora`.
        import wa_consultas
        self._original = wa_consultas.now_mx
        wa_consultas.now_mx = lambda: LUNES
        return base

    def tearDown(self):
        original = getattr(self, "_original", None)
        if original is not None:
            import wa_consultas
            wa_consultas.now_mx = original

    def test_lunes_con_datos_manda_resumen(self):
        base = self._base()
        base.vales = [(12, HECTOR, 500, LUNES - timedelta(hours=10), "GENERADO")]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=LUNES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("resumen de la semana", texto)
        self.assertIn("Vales pendientes de comprobante: 1", texto)

    def test_lunes_sin_datos_no_manda_nada(self):
        base = self._base()
        envios = Envios()

        _, enviados = wr.ciclo(ahora=LUNES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_resumen_solo_el_lunes(self):
        self.assertTrue(wr.es_resumen(LUNES))
        self.assertFalse(wr.es_resumen(datetime(2026, 11, 30, 9, 31)))
        self.assertFalse(wr.es_resumen(JUEVES))


class Dedupe(unittest.TestCase):
    def test_dos_ciclos_el_mismo_dia_mandan_uno(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]

        _, primero = wr.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        _, segundo = wr.ciclo(ahora=JUEVES, conn=base, enviar=Envios())

        self.assertEqual(primero, 1)
        self.assertEqual(segundo, 0)
        self.assertEqual(len(base.recordatorios), 1)

    def test_km_se_recuerda_cada_dia_hasta_registrar(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        base.recordatorios.append({
            "tipo": "KM", "clave": "1", "uid": HECTOR,
            "dia": (JUEVES - timedelta(days=2)).date(),
            "enviado": JUEVES - timedelta(days=2), "ok": True, "detalle": "",
        })
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)

    def test_km_no_se_repite_el_mismo_dia(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        base.recordatorios.append({
            "tipo": "KM", "clave": "1", "uid": HECTOR,
            "dia": JUEVES.date(),
            "enviado": JUEVES, "ok": True, "detalle": "",
        })
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)

    def test_tope_diario_de_tres_por_usuario(self):
        base = _base_con_hector()
        base.autos = [(n, f"Auto {n}", f"P{n}", HECTOR) for n in range(1, 5)]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 3)
        self.assertEqual(len(envios.llamadas), 3)
        self.assertEqual(len(base.recordatorios), 3)

    def test_volt_no_gasta_el_tope_diario(self):
        # La frase suelta de Volt viaja dentro de una respuesta normal: no es
        # un recordatorio proactivo y no debe comerle cupo al tope de 3.
        base = _base_con_hector()
        base.autos = [(n, f"Auto {n}", f"P{n}", HECTOR) for n in range(1, 5)]
        base.recordatorios = [
            {"tipo": "VOLT", "clave": "volt", "uid": HECTOR,
             "dia": JUEVES.date(), "enviado": JUEVES, "ok": True, "detalle": "x"}
            for _ in range(3)
        ]
        envios = Envios()

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 3)

    def test_fallo_de_openwa_se_registra_y_no_se_reintenta(self):
        base = _base_con_hector()
        base.autos = [(1, "Toyota Hilux", "ABC1234", HECTOR)]
        falla = Envios(ok=False, detalle="HTTP 500: caido")

        _, enviados = wr.ciclo(ahora=JUEVES, conn=base, enviar=falla)

        self.assertEqual(enviados, 0)
        self.assertEqual(len(base.recordatorios), 1)
        self.assertFalse(base.recordatorios[0]["ok"])
        self.assertIn("km auto 1", base.recordatorios[0]["detalle"])

        # El siguiente ciclo del mismo día no vuelve a intentarlo.
        _, otra_vez = wr.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        self.assertEqual(otra_vez, 0)


if __name__ == "__main__":
    unittest.main()
