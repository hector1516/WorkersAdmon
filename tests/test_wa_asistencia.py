"""
Pruebas de los avisos de asistencia por WhatsApp.

Se doblan la base, el reloj y el envío: no se toca SQL Server ni OpenWA.

    python -m unittest tests.test_wa_asistencia -v
"""
import os
import sys
import unittest
from datetime import date, datetime, time, timedelta
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import wa_asistencia as wa  # noqa: E402

# Jueves 26 nov 2026.
JUEVES = datetime(2026, 11, 26, 9, 5)
# Sábado 28 nov 2026.
SABADO = datetime(2026, 11, 28, 10, 0)
# Viernes 27 nov 2026, hora del resumen.
VIERNES_RESUMEN = datetime(2026, 11, 27, 18, 35)

HECTOR = 7
ANA = 8
SIN_DEVICE = 9
ROSY = 11

# Turnos como los devuelve la consulta: (LV_Entrada, LV_Salida, Sab_Entrada,
# Sab_Salida, Dom_Entrada, Dom_Salida, Tol_Llegada_Min).
OPERATIVO = (time(9, 0), time(18, 30), time(9, 30), time(13, 30),
             None, None, 15)
ADMINISTRATIVO = (time(8, 0), time(18, 30), None, None, None, None, 15)


class BaseFalsa:
    def __init__(self):
        self.usuarios = []      # (id, nombre, telefono, wa_activo, device_activo)
        self.turnos = {}        # id_turno -> 7 columnas del turno
        self.turnos_usuario = []  # (uid, id_turno, desde, hasta, activo)
        self.escaneos = []      # (uid, datetime)
        self.avisos = []        # dict(tipo, uid, fecha, enviado, ok, detalle)
        self.config = {}
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *a, **k):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class CursorFalso:
    def __init__(self, base):
        self.base = base
        self.una = None
        self.todas = []

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).upper()
        p = tuple(params or ())
        b = self.base
        self.una = None
        self.todas = []

        if "INSERT INTO HUB_WAASISTENCIAAVISOS" in q:
            tipo, uid, dia, enviado, ok, detalle = p
            b.avisos.append({"tipo": tipo, "uid": uid, "fecha": dia,
                             "enviado": enviado, "ok": bool(ok),
                             "detalle": detalle})

        elif "FROM HUB_WAASISTENCIAAVISOS" in q:
            self._avisos(q, p)

        elif "FROM HUB_CONFIG" in q:
            self.todas = [(clave, b.config[clave]) for clave in p
                          if clave in b.config]

        elif "FROM HUB_USERS" in q:
            self.todas = [(uid, nombre, telefono)
                          for uid, nombre, telefono, wa_ok, device_ok in b.usuarios
                          if wa_ok and device_ok and telefono]

        elif "FROM HUB_USUARIOTURNO" in q:
            uid, hoy = p
            vigentes = sorted(
                ((desde, b.turnos.get(id_turno))
                 for u, id_turno, desde, hasta, activo in b.turnos_usuario
                 if u == uid and activo and (hasta is None or hasta >= hoy)),
                key=lambda par: par[0], reverse=True)
            self.una = vigentes[0][1] if vigentes else None

        elif "FROM HUB_NETWORKSCANRESULTS" in q:
            self._escaneos(q, p)

    def _avisos(self, q, p):
        b = self.base
        if "TIPO IN" in q:
            uid, dia = p
            fechas = sorted({a["fecha"] for a in b.avisos
                             if a["uid"] == uid
                             and a["tipo"] in ("AUSENCIA", "AUSENCIA2")
                             and a["fecha"] < dia and a["ok"]},
                            reverse=True)
            self.todas = [(f,) for f in fechas]
        elif "OK = 1 AND TIPO <> 'RESUMEN'" in q:
            uid, dia = p
            self.una = (sum(1 for a in b.avisos
                            if a["uid"] == uid and a["fecha"] == dia
                            and a["ok"] and a["tipo"] != "RESUMEN"),)
        elif "AND FECHA = %S" in q:
            tipo, uid, dia = p
            self.una = (sum(1 for a in b.avisos
                            if a["tipo"] == tipo and a["uid"] == uid
                            and a["fecha"] == dia),)
        else:
            tipo, uid = p
            self.una = (sum(1 for a in b.avisos
                            if a["tipo"] == tipo and a["uid"] == uid
                            and a["ok"]),)

    def _escaneos(self, q, p):
        b = self.base
        uid, desde, hasta = p
        filas = sorted(e for e in b.escaneos
                       if e[0] == uid and desde <= e[1] < hasta)
        if "GROUP BY" in q:
            por_dia = {}
            for _uid, cuando in filas:
                por_dia.setdefault(cuando.date(), cuando)
            self.todas = [(dia, por_dia[dia]) for dia in sorted(por_dia)]
        else:
            self.todas = [(cuando,) for _uid, cuando in filas]

    def fetchall(self):
        return self.todas

    def fetchone(self):
        return self.una

    def close(self):
        pass


class Envios:
    def __init__(self, ok=True, detalle="ok"):
        self.ok = ok
        self.detalle = detalle
        self.llamadas = []

    def __call__(self, chat_id, texto):
        self.llamadas.append((chat_id, texto))
        return self.ok, self.detalle


def base_con_hector():
    base = BaseFalsa()
    base.usuarios = [
        (HECTOR, "Héctor Peña", "5218123211516", True, True),
        (ANA, "Ana Ruiz", "528100000000", True, False),
        (SIN_DEVICE, "Sin Dispositivo", "528111111111", True, False),
    ]
    return base


def aviso(base, tipo, uid, dia, ok=True):
    base.avisos.append({"tipo": tipo, "uid": uid, "fecha": dia,
                        "enviado": datetime.combine(dia, time(9, 0)),
                        "ok": ok, "detalle": ""})


def con_turno(base, uid, turno, id_turno=1, desde=date(2026, 1, 1),
              hasta=None, activo=True):
    """Asigna un turno a una persona, como `HUB_UsuarioTurno`."""
    base.turnos[id_turno] = turno
    base.turnos_usuario.append((uid, id_turno, desde, hasta, activo))


def dia(momento):
    return momento.date()


class Entrada(unittest.TestCase):
    def test_puntual(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 2))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("Héctor", texto)
        self.assertIn("9:02", texto)
        self.assertIn("Puntual", texto)

    def test_tarde(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 12))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("Héctor", texto)
        self.assertIn("9:12", texto)

    def test_tarde_ayer_da_contexto(self):
        base = base_con_hector()
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 25, 9, 30)),
            (HECTOR, datetime(2026, 11, 26, 9, 12)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("Otra vez tarde como ayer", envios.llamadas[0][1])

    def test_tercera_tarde_de_la_semana(self):
        base = base_con_hector()
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 23, 9, 15)),   # lunes, tarde
            (HECTOR, datetime(2026, 11, 24, 9, 15)),   # martes, tarde
            (HECTOR, datetime(2026, 11, 25, 9, 0)),    # miércoles, puntual
            (HECTOR, datetime(2026, 11, 26, 9, 12)),   # jueves, tarde
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("Tercera tarde de la semana", envios.llamadas[0][1])

    def test_temprana(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 8, 31))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 8, 35),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("8:31", texto)
        self.assertIn("madrugaste", texto)


class Ausencia(unittest.TestCase):
    def test_primer_aviso_a_los_150_minutos(self):
        base = base_con_hector()
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 11, 35),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("Héctor", texto)
        self.assertIn("no te he visto hoy", texto)
        self.assertEqual(base.avisos[0]["tipo"], "AUSENCIA")

    def test_antes_de_los_150_minutos_no_avisa(self):
        base = base_con_hector()
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 11, 29),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)

    def test_segundo_aviso_a_las_13(self):
        base = base_con_hector()
        aviso(base, "AUSENCIA", HECTOR, dia(JUEVES))
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 13, 5),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("Segundo aviso", texto)
        self.assertNotIn("dormido", texto)
        self.assertEqual(base.avisos[-1]["tipo"], "AUSENCIA2")

    def test_tres_ausencias_seguidas_quitan_el_chiste(self):
        base = base_con_hector()
        for offset in (1, 2, 3):
            aviso(base, "AUSENCIA", HECTOR, (JUEVES - timedelta(days=offset)).date())
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 11, 35),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("no hay registro", texto)
        self.assertNotIn("dormido", texto)


class Horario(unittest.TestCase):
    def test_administrativo_entra_a_las_8(self):
        base = base_con_hector()
        con_turno(base, HECTOR, ADMINISTRATIVO)
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 8, 10))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 8, 15),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        # Con el horario por defecto (9:00) serían 50 minutos temprano.
        self.assertIn("Puntual", envios.llamadas[0][1])

    def test_administrativo_sabado_no_avisa(self):
        base = base_con_hector()
        con_turno(base, ROSY, ADMINISTRATIVO)
        base.usuarios = [(ROSY, "Rosy", "528100000001", True, True)]
        base.escaneos = [(ROSY, datetime(2026, 11, 28, 9, 31))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=SABADO, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_operativo_entrada_y_sabado(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO)

        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 26))[:2],
            (time(9, 0), time(18, 30)))
        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 28))[:2],
            (time(9, 30), time(13, 30)))

    def test_sabado_solo_a_quien_trabaja(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO, id_turno=2)
        con_turno(base, ANA, ADMINISTRATIVO, id_turno=1)
        base.usuarios = [(HECTOR, "Héctor Peña", "5218123211516", True, True),
                         (ANA, "Ana Ruiz", "528100000000", True, True)]
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 28, 9, 31)),
            (ANA, datetime(2026, 11, 28, 9, 31)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=SABADO, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertEqual(envios.llamadas[0][0], "5218123211516@c.us")

    def test_domingo_sin_horas_no_avisa(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO)
        base.escaneos = [(HECTOR, datetime(2026, 11, 29, 9, 31))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 29, 10, 0),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_sin_turno_usa_horario_de_oficina(self):
        base = base_con_hector()

        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 26))[:2],
            (time(9, 0), time(18, 30)))
        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 28))[:2],
            (time(9, 30), time(13, 30)))
        self.assertIsNone(wa.horario_dia(base, HECTOR, date(2026, 11, 29)))

    def test_vale_el_turno_vigente_mas_reciente(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO, id_turno=2,
                  desde=date(2026, 1, 1), hasta=date(2026, 9, 30))
        con_turno(base, HECTOR, ADMINISTRATIVO, id_turno=1,
                  desde=date(2026, 10, 1))

        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 26))[0], time(8, 0))

    def test_turno_vencido_cae_al_horario_de_oficina(self):
        base = base_con_hector()
        con_turno(base, HECTOR, ADMINISTRATIVO, hasta=date(2026, 10, 31))

        self.assertEqual(
            wa.horario_dia(base, HECTOR, date(2026, 11, 26))[0], time(9, 0))


class Tolerancia(unittest.TestCase):
    def test_tolerancia_del_turno(self):
        base = base_con_hector()
        con_turno(base, HECTOR, (time(9, 0), time(18, 30), time(9, 30),
                                 time(13, 30), None, None, 5))
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 6))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 9, 10),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        # Con la tolerancia por defecto (10) esta llegada sería puntual.
        self.assertNotIn("Puntual", envios.llamadas[0][1])

    def test_config_solo_si_el_turno_no_la_fija(self):
        base = base_con_hector()
        base.config["wa_asist_tolerancia_min"] = "5"
        con_turno(base, HECTOR, (time(9, 0), time(18, 30), time(9, 30),
                                 time(13, 30), None, None, None))
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 6))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 9, 10),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertNotIn("Puntual", envios.llamadas[0][1])

    def test_el_turno_manda_sobre_la_config(self):
        base = base_con_hector()
        base.config["wa_asist_tolerancia_min"] = "30"
        con_turno(base, HECTOR, (time(9, 0), time(18, 30), time(9, 30),
                                 time(13, 30), None, None, 5))
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 6))]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 9, 10),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertNotIn("Puntual", envios.llamadas[0][1])


class Salida(unittest.TestCase):
    def test_salida_normal(self):
        base = base_con_hector()
        aviso(base, "ENTRADA", HECTOR, dia(JUEVES))
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 26, 9, 0)),
            (HECTOR, datetime(2026, 11, 26, 18, 20)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 18, 50),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("18:20", texto)
        self.assertIn("Buen día", texto)

    def test_salida_temprana(self):
        base = base_con_hector()
        aviso(base, "ENTRADA", HECTOR, dia(JUEVES))
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 26, 9, 0)),
            (HECTOR, datetime(2026, 11, 26, 17, 10)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 17, 40),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("17:10", envios.llamadas[0][1])
        self.assertIn("apuraste", envios.llamadas[0][1])

    def test_antes_de_las_17_no_avisa_salida(self):
        base = base_con_hector()
        aviso(base, "ENTRADA", HECTOR, dia(JUEVES))
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 26, 9, 0)),
            (HECTOR, datetime(2026, 11, 26, 16, 0)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 16, 40),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)

    def test_sabado_salida_a_la_1_y_1(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO)
        aviso(base, "ENTRADA", HECTOR, date(2026, 11, 28))
        base.config["wa_asist_salida_gap_min"] = "10"
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 28, 9, 31)),
            (HECTOR, datetime(2026, 11, 28, 12, 50)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 28, 13, 1),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        self.assertIn("12:50", envios.llamadas[0][1])

    def test_sabado_antes_de_la_1_no_avisa_salida(self):
        base = base_con_hector()
        con_turno(base, HECTOR, OPERATIVO)
        aviso(base, "ENTRADA", HECTOR, date(2026, 11, 28))
        base.config["wa_asist_salida_gap_min"] = "10"
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 28, 9, 31)),
            (HECTOR, datetime(2026, 11, 28, 12, 20)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 28, 12, 59),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)


class Limites(unittest.TestCase):
    def test_tope_diario_de_tres(self):
        base = base_con_hector()
        for tipo in ("ENTRADA", "AUSENCIA", "AUSENCIA2"):
            aviso(base, tipo, HECTOR, dia(JUEVES))
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 26, 9, 0)),
            (HECTOR, datetime(2026, 11, 26, 18, 20)),
        ]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 18, 50),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_dedupe_dos_ciclos_el_mismo_dia(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 2))]

        _, primero = wa.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        _, segundo = wa.ciclo(ahora=JUEVES, conn=base, enviar=Envios())

        self.assertEqual(primero, 1)
        self.assertEqual(segundo, 0)
        self.assertEqual(len(base.avisos), 1)

    def test_whatsapp_apagado_no_envia(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 2))]
        envios = Envios()

        with mock.patch.object(wa.canales, "activo", lambda canal: False):
            detalle, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])
        self.assertIn("whatsapp apagado", detalle)

    def test_usuario_sin_dispositivo_se_salta(self):
        base = base_con_hector()
        base.usuarios = [(SIN_DEVICE, "Sin Dispositivo", "528111111111", True, False)]
        envios = Envios()

        _, enviados = wa.ciclo(ahora=datetime(2026, 11, 26, 11, 35),
                               conn=base, enviar=envios)

        self.assertEqual(enviados, 0)
        self.assertEqual(envios.llamadas, [])

    def test_fallo_de_openwa_se_registra_y_no_se_reintenta(self):
        base = base_con_hector()
        base.escaneos = [(HECTOR, datetime(2026, 11, 26, 9, 2))]
        falla = Envios(ok=False, detalle="HTTP 500: caido")

        _, enviados = wa.ciclo(ahora=JUEVES, conn=base, enviar=falla)

        self.assertEqual(enviados, 0)
        self.assertEqual(len(base.avisos), 1)
        self.assertFalse(base.avisos[0]["ok"])

        _, otra_vez = wa.ciclo(ahora=JUEVES, conn=base, enviar=Envios())
        self.assertEqual(otra_vez, 0)

    def test_fuera_de_ventana(self):
        base = base_con_hector()
        envios = Envios()
        for ahora in (datetime(2026, 11, 26, 7, 59),
                      datetime(2026, 11, 26, 19, 31),
                      datetime(2026, 11, 28, 14, 31)):
            detalle, enviados = wa.ciclo(ahora=ahora, conn=base, enviar=envios)
            self.assertEqual(enviados, 0)
            self.assertIn("fuera de ventana", detalle)
        self.assertEqual(envios.llamadas, [])


class Resumen(unittest.TestCase):
    def test_viernes_con_datos(self):
        base = base_con_hector()
        base.escaneos = [
            (HECTOR, datetime(2026, 11, 23, 9, 15)),   # lunes, tarde
            (HECTOR, datetime(2026, 11, 24, 9, 0)),
            (HECTOR, datetime(2026, 11, 25, 9, 0)),
            (HECTOR, datetime(2026, 11, 26, 9, 0)),
            (HECTOR, datetime(2026, 11, 27, 9, 0)),
        ]
        aviso(base, "ENTRADA", HECTOR, dia(VIERNES_RESUMEN))
        aviso(base, "SALIDA", HECTOR, dia(VIERNES_RESUMEN))
        envios = Envios()

        _, enviados = wa.ciclo(ahora=VIERNES_RESUMEN, conn=base, enviar=envios)

        self.assertEqual(enviados, 1)
        texto = envios.llamadas[0][1]
        self.assertIn("tu semana", texto)
        self.assertIn("5 días presente", texto)
        self.assertIn("1 llegada tarde", texto)
        self.assertIn("Racha de 5 días", texto)

    def test_viernes_sin_datos_no_manda_resumen(self):
        base = base_con_hector()
        envios = Envios()

        _, _ = wa.ciclo(ahora=VIERNES_RESUMEN, conn=base, enviar=envios)

        self.assertFalse(any("tu semana" in texto for _chat, texto in envios.llamadas))


if __name__ == "__main__":
    unittest.main()
