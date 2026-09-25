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
| Panel | `status_server.py` → `panel/` | **Interfaz de control** en `STATUS_PORT` (8080): login HUB, pestaña Workers (activar/desactivar/reiniciar/logs), pestaña **Configuración** (`panel/spec.py` + `panel/envconf.py`), `GET /api/status` (JSON) |
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
| **C** | pestaña Notificaciones (Telegram `HUB_Telegram*`, Push `HUB_PushConfig`, SMTP `HUB_EmailConfig`, IA `HUB_AiConfig`) con botón "Enviar prueba" | 🚧 |
| **D** | pestaña Apps: catálogo `HUB_ConfigCatalogo` (metadatos) + valores en `HUB_Config`, agrupados por app | 🚧 |
| **E** | subdominio `workers.ecc-sa.com.mx` vía Cloudflare (dashboard Zero Trust → Add public hostname → `http://10.188.141.31:8200`) | 🚧 |

Reglas del panel:

- **Auth**: cookie `ecsa_token` + tabla `HUB_Sessions` (mismo mecanismo que el
  HUB; `HUB_Users.Password` está en texto plano y se compara igual). Permiso
  requerido: `AccesoConfiguracion`. CSRF de doble envío en **todo** POST.
  Nunca guardar passwords ni tokens en el repo: se leen de BD/env.
- **Sin dependencias**: solo stdlib + `pymssql`. HTML generado en Python,
  formularios POST + Post/Redirect/Get (sin JS obligatorio).
- **Fuente de verdad de la config**: reusar las tablas existentes
  (`HUB_Config`, `HUB_EmailConfig`, `HUB_AIConfig`, `HUB_PushConfig`,
  `HUB_Telegram*`); **no duplicar valores** en archivos del contenedor y no
  crear tablas nuevas hasta la migración de la Fase D.
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
- Notificaciones (Fase C): bloque propio en `panel/views/notifications.py` +
  rutas `POST /notificaciones/<bloque>`; sondas en `panel/probes.py`. Mismas
  tablas que el HUB, secretos nunca pintados, permiso por bloque.
- Tests: `python tests/test_panel.py` (doble de BD + supervisorctl falso; no
  requiere BD ni supervisord).

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
- Flujo por worker: **apagar en `hub_python` → verificar → encender aquí →
  verificar**. Nunca los dos a la vez (mensajes/QR duplicados).
- `mcp_server` vive aquí (puerto 8000 del host: passkeys `/__webauthn/*`, push
  `/__push_subscribe__`, MCP `/message`). Activarlo implica **quitar `-p 8000:8000`**
  de `hub_python` en el mismo paso.

## Pendientes

- **Fase D** del panel: pestaña Apps + migración `0036_config_catalogo.sql`
  (usar el runner de HUB, `apply_migrations.py`, con guard de DB de pruebas).
- **Fase D**: hostname `workers.ecc-sa.com.mx` en el dashboard de Cloudflare.
- CI (`.github/workflows/deploy.yml` + runner self-hosted para este repo):
  fase posterior, mientras tanto el deploy es manual (`docker build` + `docker run`).
- Activar el primer worker (hoy: ninguno activo) — ya puede hacerse desde el panel.
