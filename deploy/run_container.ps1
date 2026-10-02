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
$cfg = $null; $smtp = $null; $hubmail = @{}
if (Test-Path $envLocal) {
    $vars = @{}
    foreach ($line in (Get-Content $envLocal)) {
        if ($line -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $vars[$Matches[1]] = $Matches[2] }
    }
    $cfg  = [pscustomobject]@{ server = $vars["HUB_DB_SERVER"]; user = $vars["HUB_DB_USER"]
                               password = $vars["HUB_DB_PASSWORD"]; database = $vars["HUB_DB_DATABASE"] }
    $smtp = [pscustomobject]@{ password = $vars["HUB_SMTP_PASSWORD"] }
    # Todo lo que empiece con HUBMAIL_ se pasa tal cual al worker de correo.
    # Se hace por prefijo y no con una lista fija porque ese grupo incluye
    # secretos (HUBMAIL_DB_PASSWORD, HUBMAIL_ENCRYPTION_KEY) que no pueden
    # quedar escritos en un .conf versionado: entran solo por aqui, desde
    # env.local, que esta en .gitignore.
    foreach ($k in $vars.Keys) {
        if ($k -like 'HUBMAIL_*') { $hubmail[$k] = $vars[$k] }
    }
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

# Aviso temprano del worker de correo: son las dos que, si faltan, lo dejan
# sin poder trabajar. No se aborta el deploy por esto (el resto del contenedor
# no depende del correo), pero conviene verlo en el log del despliegue.
if ($hubmail.Count -gt 0) {
    $faltan = @()
    if (-not $hubmail.ContainsKey("HUBMAIL_DB_PASSWORD")) { $faltan += "HUBMAIL_DB_PASSWORD" }
    if (-not $hubmail.ContainsKey("HUBMAIL_ENCRYPTION_KEY")) { $faltan += "HUBMAIL_ENCRYPTION_KEY" }
    if ($faltan.Count -gt 0) {
        Write-Host "AVISO: hubmail_worker quedara sin $($faltan -join ', ')."
        Write-Host "       Sin HUBMAIL_ENCRYPTION_KEY no puede descifrar las cuentas IMAP."
        Write-Host "       Sacala con:  docker exec hubmail cat /data/.hubmail_key"
    }
}

# Env-file en UTF-8 SIN BOM (el BOM romperia la primera variable)
$lines = @(
  "HUB_DB_SERVER=$($cfg.server)",
  "HUB_DB_USER=$($cfg.user)",
  "HUB_DB_PASSWORD=$($cfg.password)",
  "HUB_DB_DATABASE=$($cfg.database)",
  "HUB_SMTP_PASSWORD=$($smtp.password)",
  "TZ=America/Mexico_City"
)
foreach ($k in ($hubmail.Keys | Sort-Object)) { $lines += "$k=$($hubmail[$k])" }
# El worker baja los adjuntos a un volumen que comparte con la app de correo,
# montado en /data/hubmail (ver mas arriba). Si env.local no lo define, se pone
# aqui el path que corresponde a ese montaje. Con /data/attachments (el default
# del codigo) los adjuntos caerian en el volumen de workersadmon y la app no
# los encontraria.
if (-not $hubmail.ContainsKey("HUBMAIL_ATTACHMENTS_DIR")) {
    $lines += "HUBMAIL_ATTACHMENTS_DIR=/data/hubmail/attachments"
}
# Igual para la clave de cifrado: el archivo del volumen montado es la
# alternativa a pasarla por variable (y evita dejarla en el env-file).
if (-not $hubmail.ContainsKey("HUBMAIL_KEY_FILE")) {
    $lines += "HUBMAIL_KEY_FILE=/data/hubmail/.hubmail_key"
}
$envFile = "C:\WorkersAdmon\env.workersadmon"
[System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "ENV: server=$($cfg.server) db=$($cfg.database) user=$($cfg.user) pass_len=$($cfg.password.Length)"

docker network inspect workersadmon_net > $null 2>&1
if ($LASTEXITCODE -ne 0) { docker network create workersadmon_net | Out-Null }

docker rm -f workersadmon 2>$null | Out-Null

# El volumen de HUBMail se monta ADEMAS para el worker de correo, que necesita
# dos cosas que no estan en la base:
#   · /data/hubmail/.hubmail_key  -> la clave Fernet de las cuentas IMAP
#                                  (alternativa a HUBMAIL_ENCRYPTION_KEY)
#   · /data/hubmail/attachments/ -> los adjuntos que baja el worker y LEE la app
#
# OJO el path distinto (/data/hubmail, no /data) y por eso HUBMAIL_ATTACHMENTS_DIR
# tiene que apuntar ahí. Funciona igual porque en la base
# HUBMAIL_Attachments.FilePath se guarda RELATIVA (`<Cuenta>/<Carpeta>/<UID>/...`):
# el worker resuelve contra su base y la app contra la suya, y los dos llegan al
# mismo archivo. Si se guardara la ruta absoluta, la app buscaría en su volumen y
# no encontraría nada.
#
# Si el volumen `hubmail_data` no existe (app de correo nunca desplegada), esto
# no rompe nada: Docker crea el volumen vacío y el worker simplemente no tendrá
# adjuntos ni clave, y lo dirá en su log.

# -p 8000:8000: puerto 8000 del host lo hereda este contenedor (MCP /message,
# passkeys /__webauthn/* y push /__push_*). Se quitó de hub_python al migrar
# mcp_server aquí (ver AGENTS.md, seccion Relacion con HUB).
$id = docker run -d --name workersadmon --restart unless-stopped --network workersadmon_net `
  -p 8200:8080 `
  -p 8000:8000 `
  -v workersadmon_data:/data `
  -v hubmail_data:/data/hubmail `
  --env-file $envFile `
  workersadmon

Remove-Item $envFile -ErrorAction SilentlyContinue    # no dejar credenciales en disco

if (-not $id) { Write-Host "ERROR: docker run fallo"; exit 1 }
Write-Host "CONTENEDOR: $id"
Start-Sleep -Seconds 5
docker ps --filter name=workersadmon --format "{{.Names}} | {{.Status}} | {{.Ports}}"
docker exec workersadmon python3 -c "from panel.db import db_ping; print('DB ping:', db_ping())"
