# Informe — Módulo «WhatsApp» con bandeja de conversaciones

- **Commit**: `5076cef feat(panel): modulo WhatsApp con bandeja de conversaciones` (rama `main`, **sin push**).
- **Fecha**: 2026-11-26.

## Qué se hizo

| Archivo | Cambio |
|---|---|
| `panel/config.py` | Pestaña `whatsapp` (`/whatsapp`, permiso `AccesoWhatsapp`) y `PANEL_PERMISSIONS["whatsapp"]`. Notificaciones queda con Telegram/push/correo/IA. |
| `panel/views/whatsapp.py` | Módulo con barra `.tnav` y 5 sub-pestañas: `conexion`, `avisos`, `grupos`, `historial`, `conversaciones`. Reusa los bloques de `views/notifications.py` con `base="/whatsapp"` (no duplica guardado). Alias `?wa=eventos` → `avisos`. |
| `panel/views/wa_conversaciones.py` | Bandeja: lista (TOP 100 por `Ultima` desc, badges Bot/Humano/Cerrada) y detalle con historial por `Direccion`, Tomar / Devolver al bot / Cerrar y caja de respuesta. |
| `panel/views/notifications.py` | Ya no pinta WhatsApp (`_nav_wa`, `_bloque_openwa` y `VISTAS_WA` fuera). Los bloques `_wa_*` reciben `base` para sus formularios. |
| `panel/server.py` | `GET /whatsapp` (403 sin permiso), `POST /whatsapp/<bloque>[/...]` (openwa delega a `_notif_action`; conversaciones tiene su propio handler). `POST /notificaciones/openwa/...` sigue vivo y sus avisos redirigen al módulo. `GET /notificaciones?wa=` redirige a `/whatsapp?wa=`. |
| `panel/db.py` | `get_wa_conversaciones`, `get_wa_conversacion`, `get_wa_mensajes`, `set_wa_estado`, `guardar_wa_salida` (INSERT `Direccion='OUT'`, `Estado='ENVIADO'` + `Ultima` en la misma transacción). |
| `panel/auth.py` | `has_perm_whatsapp` y `has_perm` reconoce `AccesoWhatsapp`. |
| `panel/views/home.py` | Ícono `💬` del módulo. |
| Tests | `tests/test_wa_panel.py` (nuevo, 18 pruebas: TABS, 403, lista, detalle, Tomar/Devolver/Cerrar, Enviar con `openwa_client.enviar_texto` mockeado, fallo sin insertar). `tests/test_panel.py`: sus pruebas de WhatsApp apuntan al módulo y se conserva una de ruta vieja. |

## Verificación

- `python -m py_compile panel/views/whatsapp.py panel/views/wa_conversaciones.py panel/server.py panel/config.py panel/views/notifications.py panel/db.py panel/auth.py panel/views/home.py` → OK.
- `python -m unittest tests.test_wa_panel -v` → **18 pruebas, OK**.
- `python -m unittest tests.test_panel` → solo fallan las preexistentes: `test_12`, `test_60`, `test_66` (fallan también sin estos cambios) y los errores de entorno Windows (`test_06/07/08/10/16/22`, supervisorctl POSIX) y `test_36` (intenta resolver nombres contra la BD; falla igual sin estos cambios).
- `python -m unittest discover -s tests` → **615 pruebas**, 5 fallas y 6 errores, **todos preexistentes** en `main` (`test_12`, `test_60`, `test_66`, `test_avisos.TestDespachoPorApp.test_sin_equipos...`, `test_wa_firmados...` y los errores de entorno citados). Sin fallas nuevas.

## Dudas / notas

1. **`AccesoWhatsapp` no existe como columna en `HUB_Users`** (ninguna migración la crea). Para no romper el SELECT de login (y porque no se toca BD), `auth.has_perm_whatsapp` acepta `AccesoWhatsapp` **o** `AccesoTelegram`, que es como funcionaba WhatsApp dentro de Notificaciones. Cuando se agregue la columna (y se sume a `_PERM_COLS` en `panel/db.py`), el permiso queda separado sin dejar a nadie fuera.
2. Se incluyó `migrations/0066_wa_direccion.sql` en el commit: estaba sin trackear y es prerrequisito de la bandeja. **No se aplicó** a ninguna BD.
3. Si 0066 no está aplicada, el detalle muestra el chat sin historial y `guardar_wa_salida` falla: el panel dice «Respuesta enviada, pero no se pudo guardar en el historial» en vez de mentir.
4. Otro agente venía commiteando en `main` (`f5eb140`, `a690b0b`); este commit solo toca los archivos del módulo.
5. La tabla de módulos de `AGENTS.md` no se actualizó (el trabajo era código + tests).
