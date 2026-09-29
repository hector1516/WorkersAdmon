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

# En Git Bash (MSYS2) las rutas que empiezan con / se convierten solas a rutas
# de Windows antes de llegar al comando. Con `docker cp` eso hace que la copia
# no llegue a entrar al contenedor: el comando sale con codigo 0 y el archivo
# adentro sigue siendo el del build de imagen. Este es el mismo problema que
# se resolvio en Field y Admon con MSYS_NO_PATHCONV, que aqui faltaba.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
ROOT_DOCKER="$(cygpath -m "$ROOT" 2>/dev/null || echo "$ROOT")"

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
  # Primero: ¿responde docker? Sin esta comprobación, un runner cuyo servicio
  # no tiene acceso al pipe de Docker Desktop(reporta "el contenedor no está
  # corriendo", que es un diagnóstico FALSO: el problema es el runner).
  # Pasa cuando el servicio corre como NETWORK SERVICE, que es el default de
  # config.cmd --runasservice.
  if ! docker ps >/dev/null 2>&1; then
    say "ERROR: 'docker' no responde desde esta cuenta."
    say "       Si esto es el runner de un deploy, el servicio corre con una"
    say "       cuenta sin acceso a Docker Desktop. En el ServerVM tiene que"
    say "       estar en la misma cuenta que el runner de hubmail (eccsa), no en"
    say "       NETWORK SERVICE."
    docker ps 2>&1 | head -3 | sed 's/^/       /'
    exit 1
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    say "ERROR: el contenedor '$CONTAINER' no está corriendo (docker sí responde)."
    say "       Para una app caída o sin contenedor, usa el deploy completo:"
    say "       schtasks /run /tn WorkersBuild"
    exit 1
  fi
fi

# ── 1. Copiar el código ──────────────────────────────────────────────────────
# El panel lee su CSS de disco (panel/shell.css + panel/panel.css) para que el
# shell se pueda actualizar sin tocar Python, así que el CSS entra por aquí.
say "1/4 copiando panel/, api/ y los módulos raíz"
run docker cp "$ROOT_DOCKER/panel/." "$CONTAINER:/app/panel"
run docker cp "$ROOT_DOCKER/api/." "$CONTAINER:/app/api"
# OJO con la lista: es explícita a propósito, pero si se agrega un módulo de
# Python del que dependen los workers y NO está aquí, el hotsync copia todo lo
# demás, reinicia los procesos, y aun así el worker sigue con el código viejo en
# memoria (importado al arrancar). Eso pasó con network_scanner.py: estaba
# fuera de la lista, así que un cambio en el escáner se desplegaba "bien" y no
# cambiaba nada. La verificación del paso 1b compara hashes justamente para que
# esto no vuelva a pasar en silencio.
for f in eccsa_db.py eccsa_db_server.py config_db.py telegram_alerts.py \
         pdf_generator.py shared_report_pdf.py numbers_helper.py \
         worker_heartbeat.py network_scanner.py asistencia_core.py \
         notif_messages.py openwa_client.py openwa_alerts.py cron_sync_openwa.py; do
  [ -f "$ROOT/$f" ] && run docker cp "$ROOT_DOCKER/$f" "$CONTAINER:/app/$f"
done
# Los .conf de supervisor viven en conf.d.available: se copian para que un
# worker nuevo sea activable sin reconstruir la imagen.
[ -d "$ROOT/docker/conf.d.available" ] && \
  run docker cp "$ROOT_DOCKER/docker/conf.d.available/." "$CONTAINER:/app/docker/conf.d.available"
# La copia vendorizada del shell (la usa el chequeo diario del panel).
[ -d "$ROOT/shell" ] && run docker cp "$ROOT_DOCKER/shell/." "$CONTAINER:/app/shell"
# La versión del shell, que lee el banner.
run docker cp "$ROOT_DOCKER/ECCSA_SHELL_VERSION" "$CONTAINER:/app/ECCSA_SHELL_VERSION"

# ── 1b. Verificar que la copia ENTRÓ de verdad ────────────────────────────────
# Un `docker cp` puede salir con codigo 0 sin haber escrito nada (tipico en
# Windows cuando la ruta viene de Git Bash), y el script se reportaba igual
# como "actualizado". Un deploy que miente es peor que uno que falla: uno se
# nota, el otro no. Se compara el hash del archivo de origen con el que quedo
# ADENTRO del contenedor; si difieren, se aborta sin reiniciar nada.
say "1b/4 comprobando que la copia quedo dentro del contenedor"
fallos=0
for par in panel/panel.css panel/templates.py panel/shell.css ECCSA_SHELL_VERSION \
           eccsa_db.py network_scanner.py asistencia_core.py \
           notif_messages.py openwa_client.py openwa_alerts.py cron_sync_openwa.py; do
  [ -f "$ROOT_DOCKER/$par" ] || continue
  a=$(sha256sum "$ROOT_DOCKER/$par" | cut -d' ' -f1)
  b=$(docker exec "$CONTAINER" sha256sum "/app/$par" 2>/dev/null | cut -d' ' -f1)
  if [ -n "$a" ] && [ "$a" = "$b" ]; then
    say "    OK    $par"
  else
    say "    FALLO $par  origen=${a:0:12} contenedor=${b:0:12}"
    fallos=$((fallos + 1))
  fi
done
if [ "$fallos" -gt 0 ]; then
  say "ERROR: $fallos archivo(s) no llegaron al contenedor."
  say "       No se reinicia nada: produccion sigue como estaba."
  say "       Suele ser el canal de docker o una ruta mal convertida por MSYS."
  exit 1
fi

# ── 2. Reiniciar procesos ────────────────────────────────────────────────────
# El panel lee los .py al importar, así que hay que reiniciarlo sí o sí.
# Los workers solo si cambió algo que usan; con --full se reinician todos.
if [ "$FULL" = "1" ]; then
  say "2/4 reiniciando TODOS los programas de supervisor"
  run docker exec "$CONTAINER" supervisorctl restart all
else
  # Un .conf NUEVO no lo conoce supervisor hasta que relee la configuracion:
  # sin esto el worker nuevo se queda en "no such process" y sus avisos nunca
  # salen, aunque el deploy reporte que fue bien.
  say "2/4 dando de alta programas nuevos de supervisor"
  run docker exec "$CONTAINER" supervisorctl reread
  run docker exec "$CONTAINER" supervisorctl update
  say "2/4 reiniciando el panel y los workers que tocan estos módulos"
  run docker exec "$CONTAINER" supervisorctl restart status_web
  run docker exec "$CONTAINER" supervisorctl restart mcp_server
  run docker exec "$CONTAINER" supervisorctl restart oxxogas_worker
  run docker exec "$CONTAINER" supervisorctl restart oxxogas_contactos_worker
  run docker exec "$CONTAINER" supervisorctl restart govale_vouchers_worker
  run docker exec "$CONTAINER" supervisorctl restart vales_worker
  run docker exec "$CONTAINER" supervisorctl restart telegram_worker
  # El worker de WhatsApp consume la cola de OpenWA: si no se reinicia, sigue
  # con el codigo anterior en memoria y no entrega nada nuevo.
  run docker exec "$CONTAINER" supervisorctl restart openwa_worker
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
  say "3/4 esperando salud en $HEALTH_URL"
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
