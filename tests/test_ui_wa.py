"""
tests/test_ui_wa.py — Notificaciones, WhatsApp y bandeja con el sistema Apple
=============================================================================
Comprueba el contrato visual (panel/DESIGN.md) de las tres pantallas sin base de
datos: las funciones de `panel.views.notifications`, `panel.views.whatsapp` y
`panel.views.wa_conversaciones` devuelven HTML y los dobles de `panel.db` se
instalan con `unittest.mock` dentro de cada prueba (nunca al importar, porque
`unittest discover` importa todos los módulos antes de correr el primero).

Lo que se vigila, además de las clases:

  * los formularios siguen siendo los mismos (mismos `action` y `name`);
  * ningún `<form>` queda anidado (HTML lo invalida y el navegador lo parte);
  * el texto de la base se escapa;
  * no se pinta ningún secreto.

Ejecución:  python -m unittest tests.test_ui_wa -v
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Hay que fijarlo ANTES de importar panel.config: get_data_dir() crea carpetas.
_ROMPER = tempfile.mkdtemp(prefix="panel_ui_wa_")
os.environ.setdefault("WORKERS_DATA_DIR", os.path.join(_ROMPER, "data"))

from panel import db  # noqa: E402
from panel.templates import esc  # noqa: E402
from panel.views import notifications, wa_conversaciones, whatsapp  # noqa: E402

PERMS = {"AccesoTelegram": True, "AccesoWhatsapp": True,
         "AccesoConfigurarCorreo": True, "AccesoConfigAI": True}
ADMIN = {"id": 1, "nombre": "Admin", "email": "admin@ecc-sa.com.mx",
         "perms": PERMS}
AHORA = datetime.datetime(2026, 11, 26, 10, 30)
CHAT = "5218123211516@c.us"


def _formularios_anidados(html):
    """True si un <form> abre dentro de otro (HTML lo prohíbe)."""
    hondo = 0
    resto = html
    while True:
        abre = resto.find("<form")
        cierra = resto.find("</form>")
        if abre == -1 and cierra == -1:
            return False
        if cierra == -1 or (abre != -1 and abre < cierra):
            hondo += 1
            if hondo > 1:
                return True
            resto = resto[abre + 5:]
        else:
            hondo -= 1
            resto = resto[cierra + 7:]


def esc_url(url):
    """El enlace como lo pinta `esc()` en el HTML (con &amp; y %40)."""
    return esc(url)


_url_chat = wa_conversaciones._url_chat


class NotificacionesTest(unittest.TestCase):
    def render(self, **kw):
        with mock.patch.object(db, "telegram_metrics",
                               return_value={"vinculados": 3, "eventos": 5,
                                             "destinatarios": 9, "pendientes": 0,
                                             "fallados": 0, "enviados": 12}), \
             mock.patch.object(db, "get_push_subscriptions", return_value=[]), \
             mock.patch.object(db, "get_push_config", return_value={}), \
             mock.patch.object(db, "get_email_config", return_value={}), \
             mock.patch.object(db, "get_ai_config", return_value={}), \
             mock.patch.object(db, "get_config_values", return_value={}):
            return notifications.render(ADMIN, csrf="tok", **kw)

    def test_pinta_segmentado_y_tarjetas(self):
        html = self.render()
        self.assertIn('class="segmented"', html)
        self.assertIn('class="is-active"', html)
        self.assertIn('class="card ncard"', html)
        # Un solo botón primario por tarjeta: el de guardar.
        self.assertIn('class="btn btn--primary"', html)
        # El botón de prueba es discreto (no compite con el primario).
        self.assertIn('class="btn btn--quiet"', html)

    def test_telegram_tiene_cinco_sub_pestanas(self):
        html = self.render(vista_tg="eventos")
        self.assertEqual(html.count('href="/notificaciones?tg='), 5)

    def test_formularios_y_csrf_intactos(self):
        html = self.render()
        for accion in ("/notificaciones/telegram", "/notificaciones/push",
                       "/notificaciones/correo", "/notificaciones/ia"):
            self.assertIn(f'action="{accion}"', html)
        self.assertIn('name="csrf" value="tok"', html)
        self.assertIn('name="telegram_bot_token"', html)
        self.assertIn('name="VapidPublicKey"', html)
        self.assertIn('name="smtp_server"', html)
        # El secreto guardado jamás se pinta.
        self.assertNotIn("sk-secreta", html)

    def test_sin_formularios_anidados(self):
        for vista in ("conexion", "eventos", "destinatarios", "vinculados",
                      "historial"):
            html = self.render(vista_tg=vista)
            self.assertFalse(_formularios_anidados(html), vista)

    def test_emojis_fuera_del_titulo(self):
        html = self.render()
        self.assertIn("Bot de Telegram", html)
        self.assertNotIn("\U0001f50c", html)
        self.assertIn("<svg", html)


class WhatsAppTest(unittest.TestCase):
    def render(self, vista="conexion"):
        with mock.patch.object(db, "get_openwa_config",
                               return_value={"api_key": "sk-secreta",
                                             "base_url": "http://openwa:2785",
                                             "session_id": "20a37407"}):
            return whatsapp.render(ADMIN, csrf="tok", vista=vista)

    def test_cinco_segmentos(self):
        html = self.render()
        self.assertEqual(html.count('href="/whatsapp?wa='), 5)
        self.assertIn('class="segmented"', html)
        # La activa va marcada para lectores de pantalla.
        self.assertIn('aria-current="page"', html)

    def test_conexion_con_estado_y_api_key_en_blanco(self):
        html = self.render()
        self.assertIn("Sesión 20a37407", html)
        self.assertIn('name="openwa_api_key" value=""', html)
        self.assertNotIn("sk-secreta", html)
        self.assertIn('name="openwa_base_url"', html)
        self.assertIn('name="openwa_session_id"', html)

    def test_grupos_en_lista(self):
        grupo = {"id": "1203630@g.us", "nombre": "Cuadrilla Norte",
                 "participantes": 12}
        with mock.patch.object(notifications, "_grupos_de_la_sesion",
                               return_value=(True, [grupo])), \
             mock.patch.object(db, "get_openwa_eventos", return_value=[]):
            html = notifications._wa_grupos("/whatsapp")
        self.assertIn('class="list"', html)
        self.assertIn("Cuadrilla Norte", html)
        self.assertIn("Participantes: 12", html)

    def test_avisos_conservan_formulario(self):
        evento = {"IdEvento": "KILOMETROS", "Nombre": "Kilómetros",
                  "Descripcion": "", "PlantillaMensaje": "{Folio}",
                  "AdjuntarArchivo": 0, "Telefonos": "5218123211516",
                  "Activo": 1}
        with mock.patch.object(db, "get_openwa_eventos", return_value=[evento]), \
             mock.patch.object(db, "get_openwa_ultimos_envios", return_value={}), \
             mock.patch.object(db, "get_openwa_destinatarios_resueltos",
                               return_value=(["5218123211516@c.us"], [])):
            html = notifications._wa_eventos("/whatsapp")
        self.assertIn('action="/whatsapp/openwa/evento"', html)
        self.assertIn('name="IdEvento" value="KILOMETROS"', html)
        self.assertIn('name="PlantillaMensaje"', html)
        self.assertIn('name="Telefonos"', html)
        self.assertIn('action="/whatsapp/openwa/reenviar"', html)
        self.assertFalse(_formularios_anidados(html))


class ConversacionesTest(unittest.TestCase):
    CONV = {"ChatId": CHAT, "Estado": "HUMANO", "Asignado": "Ana",
            "AsignadoA": 1, "Ultima": AHORA}
    MENSAJES = [
        {"Id": 1, "Direccion": "IN", "Texto": "Buenos días",
         "Fecha": AHORA, "Nombre": "Cliente", "Telefono": "5218123211516",
         "Estado": "RECIBIDO"},
        {"Id": 2, "Direccion": "OUT", "Texto": "Con gusto",
         "Fecha": AHORA, "Nombre": "Ana", "Telefono": None, "Estado": "ENVIADO"},
    ]

    def test_lista_pinta_telefono_estado_y_lista_de_filas(self):
        fila = {"ChatId": CHAT, "Nombre": "", "Telefono": "5218123211516",
                "Texto": "Hola, ¿cómo va el reporte?", "Estado": "BOT",
                "Ultima": AHORA, "Asignado": ""}
        with mock.patch.object(db, "get_wa_conversaciones", return_value=[fila]):
            html = wa_conversaciones.lista()
        self.assertIn('class="list__row"', html)
        self.assertIn('class="wa-row"', html)
        self.assertIn("5218123211516", html)
        self.assertIn("Bot", html)
        self.assertIn("10:30", html)
        # El enlace abre ese chat, con el id escapado para la URL.
        self.assertIn(f'href="{esc_url(_url_chat(CHAT))}"', html)

    def test_detalle_pinta_burbujas_entrantes_y_salientes(self):
        with mock.patch.object(db, "get_wa_mensajes",
                               return_value=self.MENSAJES):
            html = wa_conversaciones.detalle(CHAT, self.CONV)
        self.assertIn('data-direccion="IN"', html)
        self.assertIn('data-direccion="OUT"', html)
        self.assertIn("wa-msg--in", html)
        self.assertIn("wa-msg--out", html)
        self.assertIn("Buenos días", html)
        self.assertIn("Con gusto", html)
        self.assertIn("Humano", html)

    def test_caja_de_respuesta_conserva_name_y_chat_oculto(self):
        with mock.patch.object(db, "get_wa_mensajes",
                               return_value=self.MENSAJES):
            html = wa_conversaciones.detalle(CHAT, self.CONV)
        self.assertIn('action="/whatsapp/conversaciones/enviar?', html)
        self.assertIn('name="texto"', html)
        self.assertIn(f'name="chat" value="{CHAT}"', html)
        self.assertIn('class="wa-compose"', html)
        self.assertIn('name="csrf"', html)
        # El botón de enviar es el icono del contrato.
        self.assertIn('aria-label="Enviar respuesta"', html)

    def test_acciones_del_chat(self):
        with mock.patch.object(db, "get_wa_mensajes",
                               return_value=self.MENSAJES):
            html = wa_conversaciones.detalle(CHAT, self.CONV)
        self.assertIn('action="/whatsapp/conversaciones/devolver?', html)
        self.assertIn('action="/whatsapp/conversaciones/cerrar?', html)
        self.assertFalse(_formularios_anidados(html))

    def test_pagina_de_dos_columnas(self):
        with mock.patch.object(db, "get_wa_conversaciones",
                               return_value=[self.CONV]), \
             mock.patch.object(db, "get_wa_conversacion",
                               return_value=self.CONV), \
             mock.patch.object(db, "get_wa_mensajes",
                               return_value=self.MENSAJES):
            html = wa_conversaciones.render(CHAT)
        self.assertIn('class="wa-inbox wa-inbox--detalle"', html)
        self.assertIn('class="wa-pane wa-pane--list"', html)
        self.assertIn('class="wa-pane wa-pane--detail"', html)
        self.assertIn("wa-back", html)
        # Alto propio y scroll interno, sin scroll de página.
        self.assertIn("min-height:0", html)
        self.assertFalse(_formularios_anidados(html))

    def test_chat_inexistente_avisa(self):
        with mock.patch.object(db, "get_wa_conversaciones", return_value=[]), \
             mock.patch.object(db, "get_wa_conversacion", return_value=None):
            html = wa_conversaciones.render("999@c.us")
        self.assertIn("Ese chat no existe", html)
        self.assertIn('href="/whatsapp?wa=conversaciones"', html)


if __name__ == "__main__":
    unittest.main()
