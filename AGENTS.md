# AGENTS.md — WorkersAdmon

Repo del contenedor **`workersadmon`**: los workers de fondo de ECCSA + la
página de estado de los mismos. **Independiente de `field`, `admon` y `HUB`.**

## Arquitectura

| Capa | Archivo(s) | Rol |
|---|---|---|
| Imagen | `Dockerfile` | python:3.11-slim + freetds + smbclient + supervisor. Playwright **solo** con `--build-arg WITH_PLAYWRIGHT=1` |
| Arranque | `docker/entrypoint.sh` | genera `secretos_local.py` desde env vars → `/etc/hosts` Fileserver → activa los workers listados en `/data/workers_enabled.txt` → `supervisord` |
| Programas activos | `docker/conf.d/*.conf` | **hoy solo `status_web`** en la imagen; al habilitar uno, `enable_worker` copia aquí su conf desde `conf.d.available/` |
| Plantillas | `docker/conf.d.available/*.conf` | Las 14 confs (10 de HUB/mcp + **4 de Field**: `avisos`, `file_indexer`, `legends_cron`, `legends_audit`), **todas habilitadas el 2026-09-26** (lista vigente en `/data/workers_enabled.txt`) |
| Código Field | `api/` | snapshot de `field/api` (crons + `db.py`/`config.py`/`auth.py`/`routers/`); los conf de Field usan `directory=/app/api` y `environment=TZ="UTC"` |
| Helpers | `docker/bin/` | `enable_worker`, `disable_worker`, `workers_list` |
| Panel | `status_server.py` → `panel/` | **Interfaz de control** en `STATUS_PORT` (8080): login HUB, pestañas Workers (activar/desactivar/reiniciar/logs), **Configuración** (`panel/spec.py` + `panel/envconf.py`), **Notificaciones** (`panel/views/notifications.py` + `panel/probes.py`) y **Apps** (`panel/views/apps.py`), `GET /api/status` (JSON) |
| Heartbeats | `worker_heartbeat.py` | escribe `/data/heartbeats/<worker>.json` con la última ejecución |
| Datos | `eccsa_db.py`, `config_db.py` | snapshot de la capa de datos de HUB (misma BD `ECCSA_Admon`) |

## Ecosistema sin Streamlit (2026-09-27)

- La app del **HUB (Streamlit) está retirada**: no se toma en cuenta para nada
  (ni para versiones, ni para desplegar, ni para el shell). `hub_python` queda
  apagado salvo que se vuelva a levantar.
- Por lo tanto **esta imagen no lleva Streamlit** (ni `pandas`, `altair` ni
  `openpyxl`): se quitaron de `requirements.txt` tras verificar con un import
  real que ni el panel ni los 14 workers los necesitan. La imagen bajó de
  2.93 GB a 2.45 GB.
- Quedaron 3 imports *tolerantes* (`try/except ImportError`) por si acaso:
  `eccsa_db.py` y `telegram_alerts.py` (ya lo eran: usan `st.session_state` /
  `st.cache_data` con fallback) y `views/vales_oxxogas.py` + `views/utils.py`
  (la UI de HUB que ya no se usa; los workers solo llaman
  `fetch_and_sync_oxxogas_emails`, que no toca `st` cuando recibe `user_id`).
- Ojo: `views/` es carpeta de UI heredada del HUB. La lógica que usan los
  workers (sync de vales) vive ahí; si algún día se quiere limpiar, mover
  `fetch_and_sync_oxxogas_emails` a un módulo sin `views/`.

## Avisos por WhatsApp (OpenWA)

OpenWA es un gateway de WhatsApp (`whatsapp-web.js`) que corre como `openwa-api`
en la red docker `openwa_default`. La app le pide por HTTP que mande mensajes:
**no** se habla con WhatsApp directamente.

| | |
|---|---|
| Base (dentro de docker) | `http://openwa:2785` — **por hostname**, nunca por IP: OpenWA tiene un guard anti-SSRF que rechaza callbacks a IPs internas |
| Auth | header `X-API-Key` |
| Sesión | `openwa_session_id` en `HUB_Config` (la `principal`, ya conectada) |
| API key | **se pega en la pantalla** (`Notificaciones > Conexion > OpenWA`), en `HUB_Config` como `openwa_api_key`. `OPENWA_API_KEY` del entorno es solo el respaldo para CI/tests |
| Endpoints | `GET /api/health` (publico) · `POST /api/sessions/{id}/messages/send-text` · `.../send-document` · `.../send-image` |

Archivos: `openwa_client.py` (HTTP + normalizacion de numeros) ·
`openwa_alerts.py` (dispatcher) · `cron_sync_openwa.py` (worker, corre como
`openwa_worker`) · `notif_messages.py` (**lo que se dice, compartido con
Telegram**) · migracion `0043_openwa_whatsapp.sql` (`HUB_WhatsappEventos`,
`HUB_WhatsappQueue`).

**Reglas que ya se aprendieron:**

1. El multimedia va **en base64 dentro del JSON** (campo `base64`), no multipart,
   y `mimetype` es obligatorio cuando se manda base64.
2. `workersadmon` tiene que estar en la red `openwa_default`
   (`docker network connect openwa_default workersadmon`). `build.ps1` ya
   recrea el contenedor en **todas** sus redes, asi que no se pierde al
   redesplegar.
3. Los destinatarios son **telefonos escritos a mano, separados por comas**
   (`Telefonos` del evento). Un numero de 11 digitos que empieza con 1 se
   **rechaza**: es ambiguo entre el formato antiguo de Mexico y uno de EUA, y
   adivinar produce un numero valido que no es de nadie.
4. La API key nunca se loguea: `openwa_client._limpiar_detalle` la tapa antes de
   devolver cualquier error (al log del worker va justo lo que se devuelve).
5. La cola reintenta 3 veces y luego marca `FALLADO` con el motivo. Los
   adjuntos pesan, asi que se limpia lo cerrado a mas de 30 dias
   (`limpiar_openwa_historial`), nunca lo pendiente.


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
| **A** | `panel/` + login HUB + módulos Estado y Logs (acciones y logs) | ✅ |
| **B** | pestaña **Configuración por worker**: `panel/spec.py` (catálogo), `panel/envconf.py` (overrides de entorno persistidos en `/data/worker_env.json` y reaplicados por el entrypoint), edición de `HUB_Config`, constantes solo lectura, bitácora sin valores secretos | ✅ |
| **C** | pestaña Notificaciones (Telegram `HUB_Telegram*` con **5 sub-pestañas** Conexión/Eventos/Destinatarios/Vinculados/Historial iguales a `views/telegram.py` del HUB, **WhatsApp/OpenWA `HUB_Whatsapp*` con 3 sub-pestañas** Conexión/Avisos/Historial, Push `HUB_PushConfig`, SMTP `HUB_EmailConfig`, IA `HUB_AiConfig`) con botón de prueba | ✅ |
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

## El shell (ECCSA-Shell)

El panel es una de las **3 apps del ecosistema ECCSA-Shell** (Field, Admon y
este panel). El diseño, el banner, la barra de acciones, el popup de novedades
y la regla de ubicación viven en el repo `hector1516/ECCSA-Shell` y aquí hay
**copias generadas**.

| Archivo | Estado |
|---|---|
| `panel/shell.css` | **GENERADO** por `sync_shell.py`. No se edita a mano |
| `panel/lugar.py` | Copia canónica de la regla "IP privada = oficina" |
| `panel/banner.py.html`, `panel/actions.py.html`, `panel/changelog.py.html` | Referencias del markup del shell |
| `ECCSA_SHELL_VERSION`, `ECCSA_SHELL_SHA` | Los estampa `sync_shell.py` |
| `shell/` | Copia vendorizada del repo del shell, para el chequeo diario |
| `panel/panel.css` | Esto **sí** es propio del panel (wcard, chklist, cfg-*, pk-*) |

`check.yml` falla el build si se edita `panel/shell.css` a mano (compara
`ECCSA_SHELL_SHA`). Para cambiar el diseño se edita `tokens.css` o
`src/body.css` en el repo del shell y se propaga.

**Ojo con propagar a mano**: `sync_shell.py`/`propagate.py` actualizan los
archivos de la app (`panel/shell.css`, `ECCSA_SHELL_*` y los artefactos del
banner), **pero no `shell/`**, que es un espejo del repo del shell. Si la
propagación se hace a mano hay que re-vendorizar `shell/` en el mismo commit
(copiar los archivos que cambien desde `hector1516/ECCSA-Shell`) y asegurarse
de que el cambio esté commiteado ALLÁ: `check_daily.py` (`test_60`) compara
`panel/shell.css` contra `shell/dist/shell.plain.css` y `ECCSA_SHELL_VERSION`
contra `shell/VERSION`. Así salió el rojo de `check.yml` del 2026-09-28: la
app corría un shell 1.10.1 que nunca existió en el repo del shell.

`panel/templates.py` **lee los CSS de disco** (`_read_css()`) justamente para
que actualizar el shell no requiera tocar Python.

**El panel sí tiene sus propias piezas del shell**, porque no hay build:

- `shell_banner()` pinta el mismo markup que `banner/SyncHeader.svelte`, con los
  5 estados. Es un `<div>`, no un `<button>`: el banner es de solo lectura acá
  (no hay cola offline que empujar).
- `shell_actions()` solo expone 🚪 Salir y 📋 Novedades: Configuración y
  Notificaciones ya son pestañas de la tab bar, y un botón se pinta solo si su
  manejador existe.
- `changelog_modal()` + el script chico: el popup "salta la primera vez de cada
  versión" con la misma clave de localStorage que las apps Svelte
  (`eccsa:changelog:workersadmon`).
- La **versión sale de `panel/config.py: APP_VERSION`**, no de un literal.
- `.wrap` lleva `shell-below-banner`: sin ese padding el header queda tapado
  por el banner fijo.

**Ojo con la IP**: para el lugar se usa `lugar_de_handler()` de `panel/lugar.py`,
**nunca** `auth.client_ip()`. Esta última mira `X-Real-IP` primero, que el nginx
del ServerVM sobreescribe con el `$remote_addr` del proxy inmediato (la IP del
puente de Docker, privada) → el banner decía "Oficina" para todo el mundo. La
precedencia correcta es `X-Forwarded-For` (primera entrada) → `X-Real-IP` →
socket.

## Convenciones

- Español en UI y comentarios; comentarios explicativos en el código.
- Emojis en títulos/tarjetas de UI.
- Estructura de un worker: docstring → imports → constantes con env var →
  `main()` con loop → `if __name__ == "__main__": main()`.
- Sin placeholders `?` en SQL (pymssql/DB-Lib no los soporta) → interpolar `%s`.


## Módulos del panel (shell ECCSA)

El panel se organiza en **módulos**, uno por sección, declarados en
`panel/config.py:TABS`. Cada uno tiene `id`, `label` (con emoji, para la barra
inferior), `href`, `modulo` (el título de la página) y `desc`.

| id | Módulo | Ruta | Permiso |
|---|---|---|---|
| `estado` | 📊 Estado | `/` | — |
| `logs` | 📄 Logs | `/logs` | — |
| `config` | ⚙️ Configuración | `/configuracion` | — |
| `notificaciones` | 🔔 Notificaciones | `/notificaciones` | AccesoTelegram / AccesoConfigurarCorreo / AccesoConfigAI |
| `apps` | ⚙️ Apps | `/apps` | AccesoAppConfig |

- El `id` es lo que las vistas pasan a `templates.page(id, ...)` para marcar la
  pestaña activa. **Renombrar un `id` obliga a actualizar las llamadas.**
- La barra inferior (`templates._nav`) usa `.bottom-nav` / `.nav-item` / `.nav-icon`
  del shell. Un módulo sin permiso o deshabilitado sale como `.nav-item off`.
- `views/workers.py` implementa dos módulos: `logs_index()` (el índice de Logs)
  y `logs_page()` (el log crudo de un worker, en `/workers/<nombre>/logs`).

### El panel usa los componentes del shell, no los suyos

Todo lo que el shell ya define **no** se re-declara en `panel/panel.css`. Si
aparece una clase duplicada ahí, es una regresión: como `panel.css` carga
DESPUÉS de `shell.css`, la versión del panel gana y el panel deja de verse como
Field y Admon.

Del shell: `.header` `.brand` `.brand-col` `.card` `.btn` `.btn-primary`
`.btn-secondary` `.btn-success` `.btn-warning` `.btn-sm` `.btn-block` `.input`
`.field` `.badge` `.badge-success` `.badge-warning` `.badge-danger`
`.badge-info` `.bottom-nav` `.nav-item` `.nav-icon` `.empty` `.sync-header`
`.shell-actions` `.shell-modal` `.version-badge` y los tokens `--color-*`.

Lo propio del panel (no existe en el shell, por eso vive acá): `.wcard*`
(tarjeta de worker), `.chklist`/`.chk` (checklist), `.cfg-*` (configuración),
`.tnav` (pestañas internas), `.wgroup` (separador con línea), `.ro` (solo
lectura), `.pill`, `.box` (login), `.version-badge`, `.submit`, `.logs`.

Los tokens cortos del panel (`--bg`, `--txt`, `--orange`…) son **alias** de los
del shell, no hex propios: si se repite el valor a mano, el panel deja de
seguir al shell.


## Relación con HUB (migración gradual)

- El código de workers y la capa de datos es un **snapshot** de HUB para que el
  contenedor sea autosuficiente (HUB se retirará). Si cambia una función en HUB
  que un worker usa, copiar el cambio aquí (o refactorar para desacoplar).
- **Workers de Field (migrados 2026-09-26)**: `api/` es un snapshot de
  `field/api`; salieron de `field/docker/supervisord.conf` (el repo Field quedó
  con solo `nginx` + `api`, commit `017519d`). Reglas:
  - **`legends_audit` = 1 sola instancia en TODO el entorno** (duplica
    `ScoreLog`); verificar que `field` no lo corre antes de habilitarlo aquí.
  - Los 4 conf fijan `environment=TZ="UTC"` (Field corre con TZ=UTC; no mover
    comportamientos de `datetime.now()` — `cron_avisos` es inmune porque usa
    `utcnow()-6`).
  - Env necesaria: solo `HUB_DB_*` (ya presentes); SMB de `file_indexer` y
    credenciales de push están hardcodeadas/persistidas en `HUB_Config`.
  - Deps nuevas en `requirements.txt`: `fastapi==0.115.0` (lo importa
    `routers/push.py` vía `avisos`) y `pysmb` (file_indexer).
- **Divergencia intencional**: `eccsa_db.get_remisiones_by_range()` (nueva) y el
  5º módulo de `cron_sync_pdf_storage.py` (Remisiones) existen **solo aquí**;
  HUB solo hace 4 módulos. Si HUB vuelve a necesitar el worker, copiar estos
  bloques de vuelta (el resto de los archivos siguen idénticos byte a byte).
- Flujo por worker: **apagar en `hub_python` → verificar → encender aquí →
  verificar**. Nunca los dos a la vez (mensajes/QR duplicados).
- `mcp_server` vive aquí desde 2026-09-26 (puerto 8000 del host: passkeys
  `/__webauthn/*`, push `/__push_subscribe__`, MCP `/message`). El `-p 8000:8000`
  se quitó de `hub_python` (contenedor y `deploy.yml`); `deploy/run_container.ps1`
  lo publica aquí con `-p 8000:8000`.

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
- ~~Activar el primer worker~~ — los **10** están activos desde 2026-09-26.
- Heartbeats: ningún worker llama aún `worker_heartbeat.heartbeat()` (la columna
  "Última ejecución" queda vacía).
- Smoke de imports por worker (los 14). La **migración 1-a-1 ya terminó**
  (2026-09-26): los 14 programas (10 de HUB/mcp + 4 de Field) corren aquí,
  `hub_python` quedó solo con `streamlit`, `field` con `nginx` + `api`, los
  `.conf` de workers se eliminaron de `docker/prod/conf.d/` del repo HUB y
  `-p 8000:8000` se quitó de su `deploy.yml`.
