# WorkersAdmon ⚙️

Contenedor Docker **`workersadmon`** con los workers de fondo de ECCSA y la
página de estado de los mismos. **Separado por completo de `field`, `admon` y
`hub_python`** — su propio repo, su propia imagen, su propia red y sus propios
volúmenes.

> **Estado actual: SIN WORKERS ACTIVOS.**
> Solo corre `status_web` (la página de estado). Los 10 programas de
> `docker/conf.d.available/` están *en standby*: el código está en la imagen,
> pero ninguno está habilitado. Se activan uno a uno con `enable_worker`.

---

## 1. Qué corre dentro

| Programa | Estado | Origen |
|---|---|---|
| `status_web` | ✅ siempre activo | `docker/conf.d/status_web.conf` |
| `bing_worker`, `tipo_cambio_worker`, `telegram_worker`, `oxxogas_worker`, `oxxogas_contactos_worker`, `pdf_storage_worker`, `network_scanner_worker`, `vales_worker`, `govale_vouchers_worker` | ⏸️ standby | `docker/conf.d.available/*.conf` (origen: HUB) |
| `mcp_server` (passkeys + push + MCP, puerto 8000) | ⏸️ standby | `docker/conf.d.available/mcp_server.conf` |

---

## 2. Construir y levantar (ServerVM)

```bat
:: 1) clonar
git clone https://github.com/hector1516/WorkersAdmon.git C:\WorkersAdmon
cd C:\WorkersAdmon

:: 2) red dedicada (una sola vez)
docker network create workersadmon_net

:: 3) build  (WITH_PLAYWRIGHT=1 solo cuando actives el primer worker de navegador)
docker build -t workersadmon .

:: 4) levantar  — SIN ningún worker, solo la página de estado
docker run -d --name workersadmon --restart unless-stopped --network workersadmon_net ^
  -p 8200:8080 ^
  -v workersadmon_data:/data ^
  -e TZ=America/Mexico_City ^
  -e HUB_DB_SERVER=10.188.141.15 ^
  -e HUB_DB_USER=sa ^
  -e HUB_DB_PASSWORD=******** ^
  -e HUB_DB_DATABASE=ECCSA_Admon ^
  -e HUB_SMTP_PASSWORD=******** ^
  workersadmon
```

| Elemento | Valor |
|---|---|
| Página de estado | `http://10.188.141.31:8200/` |
| API JSON | `http://10.188.141.31:8200/api/status` |
| Sondeo | `http://10.188.141.31:8200/healthz` |
| Puerto publicado | **solo 8200** (nada más queda expuesto) |
| Volumen | `workersadmon_data` → `/data` (lista de habilitados + heartbeats) |
| Red | `workersadmon_net` (aislada de `hub_default`) |

> Las credenciales se generan en `/app/secretos_local.py` **al arrancar**, desde
> las variables de entorno. Nunca se suben a Git.

---

## 3. Página de estado

Tema oscuro ECCSA, se recarga sola cada 15 s, y muestra por programa:

* **Estado** real de supervisord (● EN EJECUCIÓN / ■ DETENIDO / ▲ FALLO / ○ NO HABILITADO)
* **Habilitado** — si está en la lista persistente `/data/workers_enabled.txt`
* **Activo desde** — fecha/hora del último arranque del proceso (+ hace cuánto)
* **Última ejecución** — heartbeat reportado por el worker (+ hace cuánto)
* **Detalle** — texto libre y conteo que manda el worker

JSON completo en `/api/status` (mismos datos, listo para monitoreo).

---

## 4. Activar / desactivar workers

```bat
:: ver qué hay disponible, habilitado y su estado
docker exec workersadmon workers_list

:: activar uno (lo agrega a /data/workers_enabled.txt y lo arranca ya)
docker exec workersadmon enable_worker tipo_cambio_worker

:: desactivarlo (lo detiene y lo quita de la lista)
docker exec workersadmon disable_worker tipo_cambio_worker
```

La lista vive en el **volumen** → si recreas el contenedor, el entrypoint vuelve
a levantar exactamente los mismos workers.

---

## 5. Agregar un worker nuevo

1. **Código** — crear `mi_worker.py` en la raíz del repo.
2. **Conf** — crear `docker/conf.d.available/mi_worker.conf`:
   ```ini
   [program:mi_worker]
   command=/usr/local/bin/python3.11 -u /app/mi_worker.py
   directory=/app
   autostart=true
   autorestart=true
   redirect_stderr=true
   stdout_logfile=/var/log/supervisor/mi_worker.log
   stdout_logfile_maxbytes=10MB
   stopasgroup=true
   killasgroup=true
   ```
3. **Heartbeat** — dentro del loop, reportar la última ejecución:
   ```python
   from worker_heartbeat import heartbeat
   heartbeat("mi_worker", detail="12 registros procesados", count=12)
   ```
4. **Build** (si el contenedor ya existe, basta con subir los archivos):
   ```bat
   docker build -t workersadmon . 
   docker rm -f workersadmon && docker run -d ... (mismo comando del §2)
   :: o en caliente, sin recrear:
   docker cp mi_worker.py workersadmon:/app/
   docker cp docker\conf.d.available\mi_worker.conf workersadmon:/app/docker/conf.d.available/
   docker exec workersadmon enable_worker mi_worker
   ```
5. **Verificar** en `http://10.188.141.31:8200/`.

---

## 6. Estructura

```
WorkersAdmon/
├── Dockerfile                    # python:3.11-slim + freetds + smbclient (+ playwright opcional)
├── docker/
│   ├── entrypoint.sh             # secretos_local + /etc/hosts Fileserver + activa la lista de /data
│   ├── supervisord/supervisord.conf
│   ├── conf.d/status_web.conf    # ← ÚNICA conf activa por defecto
│   ├── conf.d.available/*.conf   # ←10 programas en standby (9 workers + mcp_server)
│   └── bin/{enable_worker,disable_worker,workers_list}
├── status_server.py              # página de estado + /api/status
├── worker_heartbeat.py           # helper para reportar "última ejecución"
├── eccsa_db.py / config_db.py    # capa de datos (snapshot de HUB)
├── cron_*.py, network_scanner.py # workers (código en standby)
├── mcp_server.py                 # passkeys / push / MCP (standby)
├── pdf_*.py, telegram_alerts.py… # dependencias compartidas
├── views/  fonts/  *.png
└── README.md / AGENTS.md
```

---

## 7. Relación con HUB

* `eccsa_db.py`, `pdf_generator.py`, etc. son un **snapshot** de HUB para que
  este contenedor sea autosuficiente (HUB se retirará en el futuro).
* La migración es **gradual**: cada worker se apaga en `hub_python` y se enciende
  aquí, con verificación entre medio.
* **Pendiente (fase posterior):** CI en este repo (runner self-hosted) y quitar
  de `hub_python` los `.conf` de los workers ya migrados.

---

## 8. Comandos útiles

```bat
docker logs -f workersadmon
docker exec workersadmon supervisorctl status
docker exec workersadmon workers_list
docker exec workersadmon tail -n 50 /var/log/supervisor/status_web.log
docker exec workersadmon python3 -c "from config_db import load_db_config; print(load_db_config())"
docker exec workersadmon supervisorctl restart status_web
```

Logs de cada worker: `/var/log/supervisor/<nombre>.log` dentro del contenedor.
