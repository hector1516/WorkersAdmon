# =============================================================
# run_container.ps1 — levanta (o recrea) el contenedor `workersadmon`
# en ServerVM con las MISMAS credenciales que usa `hub_python`.
#
#   powershell -ExecutionPolicy Bypass -File C:\WorkersAdmon\deploy\run_container.ps1
#
# Las credenciales se leen de /app/secretos_local.py dentro de hub_python y se
# pasan mediante un --env-file temporal que se BORRA al terminar (PowerShell
# serializa mal los hashes en los argumentos -e, por eso no se usan).
# =============================================================
$ErrorActionPreference = "Continue"

$cfg  = docker exec -w /app hub_python python3 -c "import json,secretos_local;print(json.dumps(secretos_local.DB_CONFIG_LOCAL))" | ConvertFrom-Json
$smtp = docker exec -w /app hub_python python3 -c "import json,secretos_local;print(json.dumps(secretos_local.EMAIL_CONFIG_LOCAL))" | ConvertFrom-Json

if (-not $cfg.server) { Write-Host "ERROR: no se pudieron leer las credenciales de hub_python"; exit 1 }

# Env-file en UTF-8 SIN BOM (el BOM romperia la primera variable)
$lines = @(
  "HUB_DB_SERVER=$($cfg.server)",
  "HUB_DB_USER=$($cfg.user)",
  "HUB_DB_PASSWORD=$($cfg.password)",
  "HUB_DB_DATABASE=$($cfg.database)",
  "HUB_SMTP_PASSWORD=$($smtp.password)",
  "TZ=America/Mexico_City"
)
$envFile = "C:\WorkersAdmon\env.workersadmon"
[System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "ENV: server=$($cfg.server) db=$($cfg.database) user=$($cfg.user) pass_len=$($cfg.password.Length)"

docker network inspect workersadmon_net > $null 2>&1
if ($LASTEXITCODE -ne 0) { docker network create workersadmon_net | Out-Null }

docker rm -f workersadmon 2>$null | Out-Null

$id = docker run -d --name workersadmon --restart unless-stopped --network workersadmon_net `
  -p 8200:8080 `
  -v workersadmon_data:/data `
  --env-file $envFile `
  workersadmon

Remove-Item $envFile -ErrorAction SilentlyContinue    # no dejar credenciales en disco

if (-not $id) { Write-Host "ERROR: docker run fallo"; exit 1 }
Write-Host "CONTENEDOR: $id"
Start-Sleep -Seconds 5
docker ps --filter name=workersadmon --format "{{.Names}} | {{.Status}} | {{.Ports}}"
docker exec workersadmon python3 -c "from panel.db import db_ping; print('DB ping:', db_ping())"
