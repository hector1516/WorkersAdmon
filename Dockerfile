# ─────────────────────────────────────────────────────────────────────────────
# WorkersAdmon — imagen del contenedor `workersadmon`
# Corre los workers de fondo de ECCSA + el servidor MCP/Passkeys + la página
# de estado. HOY NO LLEVA NINGÚN WORKER ACTIVADO (solo status_web).
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS builder

WORKDIR /app

# freetds-dev: pymssql; gcc/g++: por si hay que compilar wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        freetds-dev \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir supervisor

# ─── Etapa runtime ───────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# freetds (pymssql) · smbclient (PDFs al shared) · netcat (diagnóstico)
# fonts-dejavu-core: respaldo de fuentes para reportlab
RUN apt-get update && apt-get install -y --no-install-recommends \
        freetds-dev \
        cifs-utils \
        smbclient \
        netcat-openbsd \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

ENV PYTHONUNBUFFERED=1
ENV TZ=America/Mexico_City
ENV STATUS_PORT=8080
ENV WORKERS_DATA_DIR=/data

# Playwright + Chromium: SOLO para workers de navegador (Go Vale / OxxoGas).
# build: docker build --build-arg WITH_PLAYWRIGHT=1 -t workersadmon .
ARG WITH_PLAYWRIGHT=0
RUN if [ "$WITH_PLAYWRIGHT" = "1" ]; then \
        pip install --no-cache-dir playwright && \
        playwright install chromium && \
        (playwright install-deps chromium || true); \
    fi

# Supervisor + programa por defecto (status_web) + helpers de activación
COPY docker/supervisord/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/conf.d/ /etc/supervisor/conf.d/
COPY docker/bin/enable_worker /usr/local/bin/enable_worker
COPY docker/bin/disable_worker /usr/local/bin/disable_worker
COPY docker/bin/workers_list /usr/local/bin/workers_list
RUN chmod +x /usr/local/bin/enable_worker /usr/local/bin/disable_worker /usr/local/bin/workers_list

# Código (snapshot de la capa de datos + workers en standby)
COPY . .

RUN sed -i 's/\r$//' /app/docker/entrypoint.sh && \
    chmod +x /app/docker/entrypoint.sh && \
    mkdir -p /var/log/supervisor /var/run/supervisor /data

# 8080 → página de estado / API JSON
EXPOSE 8080

ENTRYPOINT ["/app/docker/entrypoint.sh"]
