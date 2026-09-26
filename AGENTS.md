# AGENTS.md — WorkersAdmon

Repo del contenedor **`workersadmon`**: los workers de fondo de ECCSA + la
página de estado de los mismos. **Independiente de `field`, `admon` y `HUB`.**

## Arquitectura

| Capa | Archivo(s) | Rol |
|---|---|---|
| Imagen | `Dockerfile` | python:3.11-slim + freetds + smbclient + supervisor. Playwright **solo** con `--build-arg WITH_PLAYWRIGHT=1` |
| Arranque | `docker/entrypoint.sh` | genera `secretos_local.py` desde env vars → `/etc/hosts` Fileserver → activa los workers listados en `/data/workers_enabled.txt` → `supervisord` |
| Programas activos | `docker/conf.d/*.conf` | **hoy solo `status_web`** (la página de estado) |
| Programas en standby | `docker/conf.d.available/*.conf` | 9 workers + `mcp_server`. **Ninguno corre hasta que se active** |
| Helpers | `docker/bin/` | `enable_worker`, `disable_worker`, `workers_list` |
| Panel | `status_server.py` → `panel/` | **Interfaz de control** en `STATUS_PORT` (8080): login HUB, pestañas Workers (activar/desactivar/reiniciar/logs), **Configuración** (`panel/spec.py` + `panel/envconf.py`), **Notificaciones** (`panel/views/notifications.py` + `panel/probes.py`) y **Apps** (`panel/views/apps.py`), `GET /api/status` (JSON) |
| Heartbeats | `worker_heartbeat.py` | escribe `/data/heartbeats/<worker>.json` con la última ejecución |
| Datos | `eccsa_db.py`, `config_db.py` | snapshot de la capa de datos de HUB (misma BD `ECCSA_Admon`) |

## Regla de oro: un worker = 3 cosas

1. **Código**: `<worker>.py` en la raíz (loop infinito con `try/except` + `time.sleep`).
2. **Conf**: `docker/conf.d.available/<worker>.conf` (patrón idéntico a los existentes).
3. **Heartbeat**: llamar `heartbeat("<worker>", detail=..., count=...)` **cada ciclo**
   para que la página de estado muestre la última ejecución.

Nada corre solo por existir: hay que ejecutar
`docker exec workersadmon enable_worker <worker>` (agrega el nombre a
`/data/workers_enabled.txt`, que vive en el **volumen** y sobrevive a recrear
el contenedor).

## Panel de control (`panel/`)

Es el **centro de configuración** del ecosistema (workers + notificaciones +
config de apps de HUB/admon/Field/futuras). Fases:

| Fase | Contenido | Estado |
|---|---|---|
| **A** | `panel/` + login HUB + pestaña Workers (acciones y logs) | ✅ |
| **B** | pestaña **Configuración por worker**: `panel/spec.py` (catálogo), `panel/envconf.py` (overrides de entorno persistidos en `/data/worker_env.json` y reaplicados por el entrypoint), edición de `HUB_Config`, constantes solo lectura, bitácora sin valores secretos | ✅ |
| **C** | pestaña Notificaciones (Telegram `HUB_Telegram*` con **5 sub-pestañas** Conexión/Eventos/Destinatarios/Vinculados/Historial iguales a `views/telegram.py` del HUB, Push `HUB_PushConfig`, SMTP `HUB_EmailConfig`, IA `HUB_AiConfig`) con botón de prueba | ✅ |
| **D** | pestaña Apps: catálogo `HUB_ConfigCatalogo` (metadatos) + valores en `HUB_Config`, agrupados por app; permiso `AccesoAppConfig` | ✅ |
| **E** | subdominio `worker.ecc-sa.com.mx` vía Cloudflare (dashboard Zero Trust → Add public hostname → `http://10.188.141.31:8200`) | ✅ |

Reglas del panel:

- **Auth**: cookie `ecsa_token` + tabla `HUB_Sessions` (mismo mecanismo que el
  HUB; `HUB_Users.Password` está en texto plano y se compara igual). Permiso
  requerido: `AccesoConfiguracion`. CSRF de doble envío en **todo** POST.
  Nunca guardar passwords ni tokens en el repo: se leen de BD/env.
- **Sin dependencias**: solo stdlib + `pymssql`. HTML generado en Python,
  formularios POST + Post/Redirect/Get (sin JS obligatorio).
- **Fuente de verdad de la config**: reusar las tablas existentes
  (`HUB_Config`, `HUB_EmailConfig`, `HUB_AIConfig`, `HUB_PushConfig`,
  `HUB_Telegram*`); **no duplicar valores** en archivos del contenedor. La
  única tabla nueva del panel es `HUB_ConfigCatalogo` (migración `0036` del
  HUB): solo guarda **metadatos**, los valores siempre viven en `HUB_Config`.
- Lee 4 fuentes de estado: `supervisorctl status`, `/data/workers_enabled.txt`,
  `docker/conf.d.available/` y `/data/heartbeats/*.json`.
- `/api/status` debe seguir devolviendo el mismo JSON (contrato de monitoreo).
- UI: tema oscuro ECCSA (`#0F172A`, `#1E293B`, acento `#FF6B00`/`#FFAE00`).
- Config por worker: agregar/editar campos en **`panel/spec.py`** (origen
  `env` / `hub_config` / `const` / `info`). **NO** editar el código del worker
  para hacerlo configurable: el worker debe seguir siendo idéntico al HUB.
  - Los secretos **nunca** se pintan en el HTML ni se loguean (blanco = no cambiar).
  - `HUB_Config` se escribe con el mismo `MERGE` del HUB y `Valor` admite 500 chars.
  - Los overrides de entorno viven en `/data/worker_env.json` y el entrypoint los
    reaplica antes de arrancar supervisord (`python3 -m panel.envconf --apply`).
- Descripción de cada worker: párrafo en **`panel/workers.py` →
  `CATALOGO[nombre]["descripcion"]`** (se muestra plegado con «ℹ️ Qué hace» en
  la tabla y completo en su tarjeta de Configuración). Mantenerla sincronizada
  con el docstring del script.
- **Telegram del panel = `views/telegram.py` del HUB**: mismas 5 sub-pestañas
  (pestañas `st.tabs` allá, barra `?tg=` aquí) — Conexión, Eventos, Destinatarios
  (resumen con chips por evento + checklist), Vinculados e Historial.
  Si cambia la UX del HUB, reflejarla aquí.
- Notificaciones (Fase C): bloque propio en `panel/views/notifications.py` +
  rutas `POST /notificaciones/<bloque>`; sondas en `panel/probes.py`. Mismas
  tablas que el HUB, secretos nunca pintados, permiso por bloque.
- Apps (Fase D): `panel/views/apps.py` + `GET/POST /apps`, permiso
  `AccesoAppConfig`. Catálogo en `HUB_ConfigCatalogo` (app, título, tipo,
  unidad, orden, descripción) y **valores en `HUB_Config`** (nunca duplicados).
  - **Un formulario por bloque** (uno por app, uno de clasificar y uno de alta):
    los botones se distinguen por su `name` (`save_all`/`edit`/`del`/`add`/
    `clasificar`); no anidar `<form>` (HTML lo invalida).
  - Tipos: `text`/`secret`/`number`/`bool`/`readonly`. El secreto en blanco =
    no cambiar y su valor **jamás se pinta**; las claves sin clasificar tampoco
    muestran valor (pueden ser secretas).
  - `HUB_Config.Clave` es PK global → **una clave pertenece a UNA sola app**;
    para validar se reusa `panel/db._validar_item()` (`_APP_RE`, `_CLAVE_RE`).
  - Borrar una fila solo quita el catálogo: **`HUB_Config` conserva el valor**.
  - Bitácora solo con nombres de clave, nunca con valores.
  - La migración se aplica en el **repo HUB** (`migrations/0036_*.sql`); si
    falta, `/apps` muestra el aviso y el listado de solo lectura.
- Login con passkey: **verificación solamente** en `panel/webauthn.py` (las
  passkeys se crean en HUB/Field). Rutas `POST /passkey/begin|finish` en
  `panel/server.py` (JSON, con su propio CSRF, antes del gate genérico).
  - RP raíz **`ecc-sa.com.mx`**: sirve para `worker.ecc-sa.com.mx`; las
    passkeys con `RpId` `field.`/`hub.` se rechazan con mensaje explícito.
  - Challenge firmado con **HMAC-SHA256 (stdlib)**, TTL 5 min, sin `pyjwt`;
    `_verify_assertion()` envuelve `webauthn.verify_authentication_response`
    (única parte con crypto; los tests la sustituyen).
  - Origen permitido: `https://ecc-sa.com.mx` + subdominios y `localhost`
    (`http://127.0.0.1` para pruebas). Si el proxy tira el `Origin`, se
    reconstruye con `X-Forwarded-Proto` + `Host`.
  - El éxito es idéntico a `_post_login`: cookie `ecsa_token` + `panel_csrf`
    y bitácora; el botón del login solo aparece si hay `isSecureContext` +
    `PublicKeyCredential`.
- PWA (igual que Field y Admon): assets en `static/` (`manifest.webmanifest`,
  `sw.js`, `icons/*`) servidos en la raíz por `panel/server.py`
  (`/manifest.webmanifest`, `/sw.js`, `/offline`, `/icons/<whitelist>`).
  - Meta/registro en `panel/templates.py` → `PWA_HEAD` + `PWA_JS` (se inyectan
    en `page()`, `login_page()` y `offline_page()`); `forbidden_page` hereda de
    `page()`. Si se agrega una página nueva, usar `page()` (no HTML suelto).
  - `sw.js`: **SOLO GET** (nunca responder a POST con la petición de red: se
    pierde el body). Navegaciones = red siempre + fallback `/offline`
    (no cachear HTML autenticado); `/api/status`, `/healthz`, `/login`,
    `/logout` y `/passkey/*` network-only; estáticos caché+revalidación.
  - Los iconos se generan desde `workers/worker.png` con Pillow (fondo
    `#0F172A`); al cambiar el logo, regenerarlos y subir el `CACHE` de `sw.js`.
  - Instalable solo con HTTPS (`https://worker.ecc-sa.com.mx`).
- UI responsive (móvil): reglas en `panel/templates.py → CSS` con cortes en
  **820px** y **430px**. Al agregar vistas:
  - **Toda tabla** va dentro de `<div class="tscroll">…</div>` (scroll
    horizontal en celular); columnas prescindibles con `hide-sm`.
  - No usar anchos fijos (`width:NNNpx`) ni `white-space:nowrap` fuera de
    `.actions-cell`; los botones nuevos reusan `.btn` (ya tiene 40px táctiles).
  - Si se agregan campos de formulario, respetar el bloque de `font-size:16px`
    (evita el zoom automático de iOS).
  - HTML vía `page()` (no suelto): hereda head PWA, viewport y el CSS móvil.
- Tests: `python -m unittest discover -s tests` (58 pruebas; dobles de BD y
  supervisorctl, no requieren SQL Server ni supervisord). Los casos puntuales
  se corren con `python tests/test_panel.py` (55) o `python tests/test_pdf_worker.py` (3).

## Credenciales (CRÍTICO)

- `secretos_local.py` **nunca se sube a Git** (está en `.gitignore` y
  `.dockerignore`); lo genera el entrypoint con `HUB_DB_*` / `HUB_SMTP_PASSWORD`.
- Pasar las credenciales como env vars en `docker run` (mismas que usa HUB).

## Convenciones

- Español en UI y comentarios; comentarios explicativos en el código.
- Emojis en títulos/tarjetas de UI.
- Estructura de un worker: docstring → imports → constantes con env var →
  `main()` con loop → `if __name__ == "__main__": main()`.
- Sin placeholders `?` en SQL (pymssql/DB-Lib no los soporta) → interpolar `%s`.

## Relación con HUB (migración gradual)

- El código de workers y la capa de datos es un **snapshot** de HUB para que el
  contenedor sea autosuficiente (HUB se retirará). Si cambia una función en HUB
  que un worker usa, copiar el cambio aquí (o refactorar para desacoplar).
- **Divergencia intencional**: `eccsa_db.get_remisiones_by_range()` (nueva) y el
  5º módulo de `cron_sync_pdf_storage.py` (Remisiones) existen **solo aquí**;
  HUB solo hace 4 módulos. Si HUB vuelve a necesitar el worker, copiar estos
  bloques de vuelta (el resto de los archivos siguen idénticos byte a byte).
- Flujo por worker: **apagar en `hub_python` → verificar → encender aquí →
  verificar**. Nunca los dos a la vez (mensajes/QR duplicados).
- `mcp_server` vive aquí (puerto 8000 del host: passkeys `/__webauthn/*`, push
  `/__push_subscribe__`, MCP `/message`). Activarlo implica **quitar `-p 8000:8000`**
  de `hub_python` en el mismo paso.

## Pendientes

- Migración `0036_config_catalogo.sql` del HUB: **ya aplicada** a
  `ECCSA_Admon_Pruebas` y a producción `ECCSA_Admon` (39 claves sembradas y
  registrada en `schema_migrations`), **commiteada** en el repo HUB
  (`00feeca`) y ya desplegada en `hub_python`. Quedan 6 migraciones del HUB
  sin aplicar en producción (0020, 0021, 0031, 0033, 0034, 0035): aplicarlas
  aparte con el runner normal, nunca de golpe.
  - El runner `Admon-Runner` se cayó (servicio `Stopped`) y dejó el deploy en
    cola infinita; se arrancó con `net start actions.runner.hector1516-Admon.Admon-Runner`.
    Si vuelve a pasar: revisar ese servicio Windows en ServerVM.
- CI (`.github/workflows/deploy.yml` + runner self-hosted para este repo):
  fase posterior, mientras tanto el deploy es manual (`docker build` + `docker run`).
- Activar el primer worker (hoy: ninguno activo) — ya puede hacerse desde el panel.
- Heartbeats: ningún worker llama aún `worker_heartbeat.heartbeat()` (la columna
  "Última ejecución" queda vacía).
- Smoke de imports por worker + migración 1-a-1 (apagar en `hub_python` →
  verificar → encender aquí) y quitar `-p 8000:8000` de `hub_python` al
  activar `mcp_server`.
