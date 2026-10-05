"""
Pruebas del escaner de red del host (network_scanner_host).

Este script vive FUERA del contenedor, en el host, porque descubrir equipos
exige ver la capa 2 (la tabla ARP): un contenedor no ve la tabla ARP del switch.
Lo que encuentra lo mete a la base por el contenedor (que ya tiene pymssql).

Lo que se prueba son las partes puras, que es donde estan los errores
silenciosos: una MAC mal formada o una entrada de ARP a medio llenar se cuelan
en la base y el sistema de asistencia cuenta equipos que no existen.

    python3 -m unittest tests.test_scanner_host
"""

import os
import unittest

# Import por ruta explicita a proposito: este repo y el del HUB (retirado) tienen
# un `network_scanner_host.py` cada uno, y el del HUB se colaba en el sys.path.
# Si el import normal agarrara el otro, estas pruebas estarian approvebando el
# archivo equivocado sin quejarse. Mejor que falle aqui.
import importlib.util

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sh = importlib.util.spec_from_file_location(
    "_network_scanner_host_de_este_repo",
    os.path.join(_REPO, "network_scanner_host.py")).loader.load_module()


class EsteArchivo(unittest.TestCase):
    def test_se_prueba_el_de_este_repo_y_no_el_del_hub(self):
        self.assertTrue(sh.__file__.startswith(_REPO),
                        f"se cargo otro network_scanner_host: {sh.__file__}")


class Subred(unittest.TestCase):
    def test_una_ip_da_la_subred_sin_la_red_ni_el_broadcast(self):
        hosts = sh.subred_desde_ip('10.188.141.37')
        self.assertEqual(len(hosts), 254)          # toda la /24 menos 2
        self.assertEqual(hosts[0], '10.188.141.1')
        self.assertEqual(hosts[-1], '10.188.141.254')

    def test_no_incluye_la_direccion_de_red(self):
        hosts = sh.subred_desde_ip('10.188.141.37')
        self.assertNotIn('10.188.141.0', hosts)
        self.assertNotIn('10.188.141.255', hosts)
        self.assertIn('10.188.141.37', hosts)      # el host tambien se barre

    def test_una_ip_invalida_no_revienta(self):
        for mala in ('', None, 'no-es-ip', '999.999.999.999'):
            self.assertEqual(sh.subred_desde_ip(mala), [])


class TablaArp(unittest.TestCase):
    """Lee /proc/net/arp. Las entradas a medio llenar son la trampa."""

    ARP = """IP address       HW type     Flags       HW address            Mask     Device
10.188.141.1     0x1         0x2         e4:fa:c4:a3:21:40     *        ens192
10.188.141.31    0x1         0x2         00:0c:29:2a:a5:91     *        ens192
10.188.141.44    0x1         0x0         00:00:00:00:00:00     *        ens192
10.188.141.45    0x1         0x2                                 *        ens192
"""

    def test_solo_devuelve_las_entradas_completas(self):
        filas = sh.leer_tabla_arp(self.ARP)
        ips = [f['ip'] for f in filas]
        self.assertEqual(ips, ['10.188.141.1', '10.188.141.31'])

    def test_normaliza_las_mac_a_mayusculas_con_dos_puntos(self):
        filas = sh.leer_tabla_arp(self.ARP)
        self.assertEqual(filas[0]['mac'], 'E4:FA:C4:A3:21:40')

    def test_una_entrada_sin_mac_se_descarta(self):
        # flag 0x0 = entrada incompleta: si se cuela, el sistema cuenta un
        # equipo que no existe.
        macs = [f['mac'] for f in sh.leer_tabla_arp(self.ARP)]
        self.assertNotIn('00:00:00:00:00:00', macs)
        self.assertTrue(all(len(m.split(':')) == 6 for m in macs))

    def test_descarta_multicast_y_mac_cero(self):
        ARP = ("IP address       HW type     Flags       HW address            Mask     Device\n"
               "10.188.141.50    0x1         0x2         01:00:5e:00:00:01     *        ens192\n"
               "10.188.141.51    0x1         0x2         00:00:00:00:00:00     *        ens192\n"
               "10.188.141.52    0x1         0x2         aa:bb:cc:dd:ee:ff     *        ens192\n")
        filas = sh.leer_tabla_arp(ARP)
        self.assertEqual([f['ip'] for f in filas], ['10.188.141.52'])


class Filtros(unittest.TestCase):
    def test_una_mac_de_iPhone_con_direccion_privada_si_se_acepta(self):
        # Las MACs de telefono son "aleatorias" pero siguen siendo MACs validas
        # de 6 bytes. Si se filtraran por prefijo de fabricante, se perderia
        # justo lo que sirve para la asistencia.
        mac = 'EE:E2:FD:A3:43:EC'
        self.assertTrue(sh.mac_valida(mac))

    def test_rechaza_mac_mal_formada(self):
        for mala in ('', 'x', 'EE:E2:FD:A3:43', 'EE:E2:FD:A3:43:EC:FF', 'ZZ:E2:FD:A3:43:EC'):
            self.assertFalse(sh.mac_valida(mala), f'debio rechazar: {mala!r}')

    def test_el_ultimo_equipo_es_el_de_la_ip_que_pusimos(self):
        # Criterio para "este equipo es mio": que su IP sea la que acabamos de
        # detectar en el ping. Sin esto, un vecino que conteste al ARP entraria
        # como si estuviera en la oficina.
        filas = [{'ip': '10.188.141.9', 'mac': 'AABBCCDDEEFF'}]
        self.assertEqual(sh.es_mi_equipo('10.188.141.9', filas), True)
        self.assertEqual(sh.es_mi_equipo('10.188.141.77', filas), False)


class PresupuestoDeTiempo(unittest.TestCase):
    def test_el_presupuesto_se_acaba_para_no_colgar_el_ciclo(self):
        # Resolver 26 hostnames en serie tardo 186 s una vez (un cliente DNS
        # girando). El ciclo tiene que acabarse aunque el DNS no responda.
        import time as _t

        class Lento:
            """DNS que se tardaba: eso es lo que se midio en produccion."""

            def __init__(self):
                self.llamadas = 0

            def resolver(self, ip):
                self.llamadas += 1
                _t.sleep(0.02)          # ~0.6 s por hostname: lento de verdad
                raise TimeoutError('dns colgado')

        lento = Lento()
        nombres = sh.resolver_hostnames(['10.188.141.%d' % i for i in range(1, 30)],
                                        resolver=lento.resolver,
                                        presupuesto=0.05)
        self.assertEqual(nombres, {})
        self.assertLess(lento.llamadas, 29,
                        'debe dejar de intentar cuando se acaba el presupuesto')
        # Y con presupuesto holgado siRevisa todos.
        lento2 = Lento()
        sh.resolver_hostnames(['10.188.141.%d' % i for i in range(1, 30)],
                              resolver=lento2.resolver, presupuesto=30)
        self.assertEqual(lento2.llamadas, 29)

    def test_nombre_y_direccion_sin_resolver_no_torpes_el_resto(self):
        nombres = sh.resolver_hostnames(
            ['10.188.141.1', '10.188.141.2'],
            resolver=lambda ip: 'Oficina' if ip.endswith('.1') else None,
            presupuesto=5)
        self.assertEqual(nombres, {'10.188.141.1': 'Oficina'})


class FormatoDeSalida(unittest.TestCase):
    def test_lo_que_se_manda_al_contenedor_sobrevivir_sin_accentos(self):
        # El insert se hace por `docker exec ... python3 -c`, asi que un acento
        # mal codificado rompe el comando entero y se queda sin escanear.
        filas = [{'ip': '10.188.141.1', 'mac': 'E4:FA:C4:A3:21:40', 'hostname': 'Oficina Ñandú'}]
        salida = sh.a_json(filas)
        self.assertTrue(salida.isascii(), 'el JSON tiene que ir solo en ASCII')
        self.assertIn('\\u', salida)


if __name__ == '__main__':
    unittest.main()