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
| Estado | `status_server.py` | `GET /` (HTML) y `GET /api/status` (JSON) en el puerto `STATUS_PORT` (8080) |
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

## Página de estado

- Lee 4 fuentes: `supervisorctl status` (estado + uptime → "activo desde"),
  `/data/workers_enabled.txt` (habilitado sí/no),
  `docker/conf.d.available/` (disponibles) y `/data/heartbeats/*.json`
  ("última ejecución").
- Sin dependencias externas (stdlib). Si cambias la UI, mantener tema oscuro
  ECCSA (`#0F172A`, `#1E293B`, acento `#FF6B00`/`#FFAE00`).
- `/api/status` debe seguir devolviendo el mismo JSON (es el contrato para
  monitoreo futuro).

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

- CI (`.github/workflows/deploy.yml` + runner self-hosted para este repo):
  fase posterior, mientras tanto el deploy es manual (`docker build` + `docker run`).
- Activar el primer worker con `enable_worker` (hoy: ninguno activo).
