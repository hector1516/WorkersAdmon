#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# WorkersAdmon · deploy rápido (hotsync) — sin reconstruir la imagen.
#
# Qué es: el camino rápido del deploy. El panel es Python de biblioteca
# estándar y se lee desde disco, así que no hay NADA que compilar: este script
# copia el código al contenedor y reinicia los procesos. De minutos a segundos.
#
# Cuándo se usa: cuando el push NO tocó Dockerfile, requirements.txt ni
# docker/ (eso lo decide .github/workflows/deploy.yml). Si los toca, el
# workflow reconstruye la imagen.
#
# OJO con los workers: copiar un .py no recarga un proceso que ya está
# corriendo. Por eso se reinician los que importan. Un reinicio de worker es
# barato (vuelven a dormir hasta su próxima hora) y NO se tocan los que
# escriben en la BD de forma especial sin necesidad, así que la lista es
# explícita y no un "restart all".
#
# Uso:  bash deploy/hotsync.sh [--dry-run] [--full]
#         --dry-run  solo imprime los comandos
#         --full     reinicia TODOS los programas de supervisor, no solo la
#                    lista de abajo (útil tras tocar un módulo compartido como
#                    eccsa_db.py, del que importan casi todos)
#
# Requisitos: docker. Nada más.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CONTAINER=workersadmon
HEALTH_URL=http://localhost:8200/healthz
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DRY=0
FULL=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --full)    FULL=1 ;;
  esac
done

run() {
  if [ "$DRY" = "1" ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

say() { echo "[hotsync] $*"; }

# ── 0. Precondiciones ────────────────────────────────────────────────────────
if [ "$DRY" = "0" ]; then
  command -v docker >/dev/null || { say "ERROR: no está docker en el PATH"; exit 1; }
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    say "ERROR: el contenedor '$CONTAINER' no está corriendo."
    say "       Para uno caído o sin contenedor, reconstruye la imagen."
    exit 1
  fi
fi

# ── 1. Copiar el código ──────────────────────────────────────────────────────
# El panel lee su CSS de disco (panel/shell.css + panel/panel.css) para que el
# shell se pueda actualizar sin tocar Python, así que el CSS entra por aquí.
say "1/3 copiando panel/, api/ y los módulos raíz"
run docker cp "$ROOT/panel/." "$CONTAINER:/app/panel"
run docker cp "$ROOT/api/." "$CONTAINER:/app/api"
for f in eccsa_db.py eccsa_db_server.py config_db.py telegram_alerts.py \
         pdf_generator.py shared_report_pdf.py numbers_helper.py \
         worker_heartbeat.py; do
  [ -f "$ROOT/$f" ] && run docker cp "$ROOT/$f" "$CONTAINER:/app/$f"
done
# Los .conf de supervisor viven en conf.d.available: se copian para que un
# worker nuevo sea activable sin reconstruir la imagen.
[ -d "$ROOT/docker/conf.d.available" ] && \
  run docker cp "$ROOT/docker/conf.d.available/." "$CONTAINER:/app/docker/conf.d.available"
# La copia vendorizada del shell (la usa el chequeo diario del panel).
[ -d "$ROOT/shell" ] && run docker cp "$ROOT/shell/." "$CONTAINER:/app/shell"
# La versión del shell, que lee el banner.
run docker cp "$ROOT/ECCSA_SHELL_VERSION" "$CONTAINER:/app/ECCSA_SHELL_VERSION"

# ── 2. Reiniciar procesos ────────────────────────────────────────────────────
# El panel lee los .py al importar, así que hay que reiniciarlo sí o sí.
# Los workers solo si cambió algo que usan; con --full se reinician todos.
if [ "$FULL" = "1" ]; then
  say "2/3 reiniciando TODOS los programas de supervisor"
  run docker exec "$CONTAINER" supervisorctl restart all
else
  say "2/3 reiniciando el panel y los workers que tocan estos módulos"
  run docker exec "$CONTAINER" supervisorctl restart status_web
  run docker exec "$CONTAINER" supervisorctl restart mcp_server
  run docker exec "$CONTAINER" supervisorctl restart oxxogas_worker
  run docker exec "$CONTAINER" supervisorctl restart oxxogas_contactos_worker
  run docker exec "$CONTAINER" supervisorctl restart govale_vouchers_worker
  run docker exec "$CONTAINER" supervisorctl restart vales_worker
  run docker exec "$CONTAINER" supervisorctl restart telegram_worker
  run docker exec "$CONTAINER" supervisorctl restart tipo_cambio_worker
  run docker exec "$CONTAINER" supervisorctl restart pdf_storage_worker
  run docker exec "$CONTAINER" supervisorctl restart network_scanner_worker
  run docker exec "$CONTAINER" supervisorctl restart bing_worker
  run docker exec "$CONTAINER" supervisorctl restart avisos
  run docker exec "$CONTAINER" supervisorctl restart legends_cron
  run docker exec "$CONTAINER" supervisorctl restart legends_audit
  run docker exec "$CONTAINER" supervisorctl restart file_indexer
fi

# ── 3. Health check ──────────────────────────────────────────────────────────
if [ "$DRY" = "0" ]; then
  say "3/3 esperando salud en $HEALTH_URL"
  ok=0
  for i in $(seq 1 20); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 1
  done
  if [ "$ok" = "0" ]; then
    say "ERROR: el panel no respondió en 20s."
    say "       Si el push tocó la imagen, hay que reconstruirla."
    exit 1
  fi
  say "    estado de supervisor:"
  run docker exec "$CONTAINER" supervisorctl status
fi

say "OK · WorkersAdmon actualizado sin reconstruir la imagen"
