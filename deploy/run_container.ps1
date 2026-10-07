# =============================================================
# run_container.ps1 - levanta (o recrea) el contenedor `workersadmon`
# en ServerVM con las MISMAS credenciales que usa `hub_python`.
#
#   powershell -ExecutionPolicy Bypass -File C:\WorkersAdmon\deploy\run_container.ps1
#
# Las credenciales se leen de /app/secretos_local.py dentro de hub_python y se
# pasan mediante un --env-file temporal que se BORRA al terminar (PowerShell
# serializa mal los hashes en los argumentos -e, por eso no se usan).
# =============================================================
$ErrorActionPreference = "Continue"

# 1) Credenciales PROPIAS (deploy/env.local, formato HUB_DB_*=...) si existe:
#    es la fuente estable, no depende de que hub_python tenga su env completo.
# 2) Si no, se leen de hub_python (mismo fallback historico).
$envLocal = "C:\WorkersAdmon\deploy\env.local"
$cfg = $null; $smtp = $null
if (Test-Path $envLocal) {
    $vars = @{}
    foreach ($line in (Get-Content $envLocal)) {
        if ($line -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $vars[$Matches[1]] = $Matches[2] }
    }
    $cfg  = [pscustomobject]@{ server = $vars["HUB_DB_SERVER"]; user = $vars["HUB_DB_USER"]
                               password = $vars["HUB_DB_PASSWORD"]; database = $vars["HUB_DB_DATABASE"] }
    $smtp = [pscustomobject]@{ password = $vars["HUB_SMTP_PASSWORD"] }
    Write-Host "ENV: desde $envLocal"
} else {
    $cfg  = docker exec -w /app hub_python python3 -c "import json,secretos_local;print(json.dumps(secretos_local.DB_CONFIG_LOCAL))" | ConvertFrom-Json
    $smtp = docker exec -w /app hub_python python3 -c "import json,secretos_local;print(json.dumps(secretos_local.EMAIL_CONFIG_LOCAL))" | ConvertFrom-Json
    Write-Host "ENV: desde hub_python"
}

if (-not $cfg.server) { Write-Host "ERROR: no se pudieron leer las credenciales"; exit 1 }
if (-not $cfg.password) {
    Write-Host "ERROR: contrasena de BD vacia - revisa deploy/env.local o el env de hub_python"
    exit 1
}

$envFile = "C:\WorkersAdmon\env.workersadmon"
[System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "ENV: server=$($cfg.server) db=$($cfg.database) user=$($cfg.user) pass_len=$($cfg.password.Length)"

docker network inspect workersadmon_net > $null 2>&1
if ($LASTEXITCODE -ne 0) { docker network create workersadmon_net | Out-Null }

docker rm -f workersadmon 2>$null | Out-Null

# -p 8000:8000: puerto 8000 del host lo hereda este contenedor (MCP /message,
# passkeys /__webauthn/* y push /__push_*). Se quitó de hub_python al migrar
# mcp_server aquí (ver AGENTS.md, seccion Relacion con HUB).
$id = docker run -d --name workersadmon --restart unless-stopped --network workersadmon_net `
  -p 8200:8080 `
  -p 8000:8000 `
  -v workersadmon_data:/data `
  --env-file $envFile `
  workersadmon

Remove-Item $envFile -ErrorAction SilentlyContinue    # no dejar credenciales en disco

if (-not $id) { Write-Host "ERROR: docker run fallo"; exit 1 }
Write-Host "CONTENEDOR: $id"
Start-Sleep -Seconds 5
docker ps --filter name=workersadmon --format "{{.Names}} | {{.Status}} | {{.Ports}}"
docker exec workersadmon python3 -c "from panel.db import db_ping; print('DB ping:', db_ping())"
