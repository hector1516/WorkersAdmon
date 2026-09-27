<#
    build.ps1 — Build + deploy de WorkersAdmon (el panel y sus workers).

    POR QUÉ EXISTE Y POR QUÉ NO ERA SUFICIENTE build.bat
    ─────────────────────────────────────────────────────
    deploy/build.bat tenía exactamente UN comando de docker:

        docker build --build-arg WITH_PLAYWRIGHT=1 -t workersadmon .

    Construía la imagen y nada más: no detenía el contenedor, no lo recreaba,
    no chequeaba salud y no hacía rollback. El contenedor seguía corriendo con
    la imagen vieja, y el log decía "FIN rc=0". O sea, el camino de rebuild era
    un deploy que no despliega — el mismo tipo de fallo silencioso que el
    `git diff` del gate y que el `docker cp` del hotsync.

    Este script hace el ciclo completo, igual que el de Field y Admon:
    construir → retag (dejando rollback) → recrear el contenedor reutilizando
    SU configuración actual → esperar salud → si falla, restaurar el anterior.

    ── Decisiones ─────────────────────────────────────────────────────────────
    · La configuración del contenedor (puertos, red, volumes, env, restart) se
      lee con `docker inspect` del contenedor que YA corre. Nunca se hardcodean
      secretos ni puertos: así este script sirve aunque cambien.
    · `docker stop` se VERIFICA antes de renombrar. Un timeout del cliente no
      significa que el stop no se aplicó, y hacerlo mal deja el contenedor
      exited con el nombre tomado (producción abajo). Preferimos abortar con
      producción intacta.
    · El `stop` de más de un contenedor NO puede 'fallar' sin más: se
      re-consulta el estado antes de seguir.
    · WITH_PLAYWRIGHT=1 es obligatorio: oxxogas_contactos, vales y
      govale_vouchers necesitan playwright + Chromium dentro de la imagen.
    · Windows PowerShell 5.1 + $ErrorActionPreference='Stop' convierte la
      salida por stderr de un comando nativo en error TERMINANTE, y BuildKit
      escribe todo su progreso por stderr. Run-Native lo evita.
    · GIT_TERMINAL_PROMPT=0: sin credenciales, git no falla sino que abre un
      prompt por stdin y se queda esperando para siempre.
    · Levá BOM UTF-8. Sin BOM, PowerShell 5.1 lo lee como CP1252 y los acentos
      se vuelven comillas tipográficas que rompen el parseo.

    Uso:
        powershell -ExecutionPolicy Bypass -File C:\WorkersAdmon\deploy\build.ps1
#>

$ErrorActionPreference = 'Stop'
$env:GIT_TERMINAL_PROMPT = '0'

$root    = Split-Path -Parent $PSScriptRoot
$log     = Join-Path $root 'build.log'
$image   = 'workersadmon'
$name    = 'workersadmon'
$healthUrl = 'http://localhost:8200/healthz'
$healthTimeoutSec = 90

# Windows PowerShell 5.1 + $ErrorActionPreference='Stop' convierte CUALQUIER
# salida por stderr de un comando nativo en error terminante. docker build
# (BuildKit) escribe TODO su progreso por stderr, asi que sin esto el script
# muere en la linea 2 del build. Para esos comandos se baja a 'Continue' y se
# juzga por $LASTEXITCODE, que es lo que corresponde.
function Run-Native {
    param([scriptblock]$Cmd)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Cmd } finally { $ErrorActionPreference = $prev }
}

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Output $line
    Add-Content -Path $log -Value $line
}

function Fail($msg) {
    Log "ERROR: $msg"
    Add-Content -Path $log -Value ("FIN rc=1")
    exit 1
}

Set-Location $root
Set-Content -Path $log -Value ("=== build WorkersAdmon {0} ===" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Log "root=$root"

# ── 0. Preflight ──────────────────────────────────────────────────────────────
if (-not (Test-Path (Join-Path $root 'Dockerfile'))) { Fail 'no hay Dockerfile: esto no es la raíz del repo' }
if (-not (Test-Path (Join-Path $root 'panel\server.py')))  { Fail 'no hay panel/server.py: revisa que estés en la raíz' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'docker no esta en PATH' }
Log ("docker: " + (docker version --format '{{.Server.Version}}' 2>&1))

$existe = docker ps -a --filter "name=^${name}$" --format '{{.Names}}'
if ($existe -notcontains $name) { Fail "no existe el contenedor $name: no se puede leer su configuracion" }

# ── 1. Codigo actualizado ─────────────────────────────────────────────────────
# El clon canonico lo deja al dia el paso "Sincronizar el clon canonico" del
# deploy. Este pull es una red de seguridad para cuando alguien dispara el
# build a mano.
if (Test-Path (Join-Path $root '.git')) {
    Log 'git pull --ff-only'
    Run-Native { git pull --ff-only 2>&1 } | ForEach-Object { Log "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Log "AVISO: el git pull fallo; se construye con lo que hay en disco."
        Log "       Si creias estar desplegando otra cosa, NO sigas: el codigo"
        Log "       del clon canonico esta desactualizado."
    }
    $commit = (git rev-parse --short HEAD 2>$null)
    Log "commit=$commit"
}

# ── 2. Imagen ─────────────────────────────────────────────────────────────────
Log "docker build -t ${image}:latest ."
Run-Native { docker build --progress=plain --build-arg WITH_PLAYWRIGHT=1 -t "${image}:latest" . 2>&1 } |
    ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "docker build fallo (rc=$LASTEXITCODE); la imagen actual queda intacta" }

# ── 3. Config del contenedor actual (para recrear igual) ─────────────────────
$inspect = docker inspect $name 2>$null | ConvertFrom-Json
if (-not $inspect) { Fail 'docker inspect no devolvio configuracion' }
$c = $inspect[0]
Log "contenedor actual: $($c.Id.Substring(0,12)) running=$($c.State.Running)"

$runArgs = @('run', '-d', '--name', $name)
if ($c.HostConfig.RestartPolicy.Name) { $runArgs += @('--restart', $c.HostConfig.RestartPolicy.Name) }
if ($c.HostConfig.NetworkMode)          { $runArgs += @('--network', $c.HostConfig.NetworkMode) }
foreach ($b in @($c.HostConfig.Binds))    { if ($b) { $runArgs += @('-v', $b) } }
foreach ($p in $c.HostConfig.PortBindings.PSObject.Properties) {
    foreach ($b in @($p.Value)) {
        if ($null -eq $b) { continue }
        $hp = if ($b.HostIp) { "$($b.HostIp):$($b.HostPort)" } else { $b.HostPort }
        $runArgs += @('-p', "${hp}:$($p.Name)")
    }
}
foreach ($e in @($c.Config.Env)) { if ($e) { $runArgs += @('-e', $e) } }
$runArgs += "${image}:latest"

# ── 4. Recrear ────────────────────────────────────────────────────────────────
Log 'deteniendo el contenedor actual (t=10)'
Run-Native { docker stop -t 10 $name 2>&1 } | ForEach-Object { Log "  $_" }
$stillRunning = (docker inspect -f '{{.State.Running}}' $name 2>$null)
if ($stillRunning -eq 'true') {
    Fail 'el contenedor sigue corriendo: NO se renombra, produccion intacta'
}
Log 'contenedor detenido OK'

docker rename $name "${name}_old"
if ($LASTEXITCODE -ne 0) { Fail 'rename falló; produccion sigue con el contenedor viejo' }

Log 'creando el contenedor nuevo con la misma configuracion'
$newId = docker @runArgs
if ($LASTEXITCODE -ne 0) {
    Log 'docker run falló; se restaura el contenedor anterior'
    docker rename "${name}_old" $name | Out-Null
    docker start $name | Out-Null
    Fail 'docker run fallo; se restorations el contenedor anterior'
}
Log "contenedor nuevo: $((($newId | Select-Object -Last 1).ToString().Trim()).Substring(0,12))"

# ── 5. Salud ──────────────────────────────────────────────────────────────────
Log "esperando salud en $healthUrl (hasta ${healthTimeoutSec}s)"
$healthy = $false
for ($i = 0; $i -lt [int]($healthTimeoutSec / 3); $i++) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}
if (-not $healthy) {
    Log "salud NO ok; rollback al contenedor anterior"
    docker logs --tail 40 $name 2>&1 | ForEach-Object { Log "  $_" }
    docker rm -f $name | Out-Null
    docker rename "${name}_old" $name | Out-Null
    docker start $name | Out-Null
    Fail "health check fallo; restaurado el contenedor anterior"
}

# ── 6. Listo ──────────────────────────────────────────────────────────────────
Log 'salud OK'
Run-Native { docker rm -f "${name}_old" 2>&1 } | ForEach-Object { Log "  $_" }
Log ("imagen activa: " + (docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' "${image}:latest" | Select-Object -First 1))
Add-Content -Path $log -Value ("FIN rc=0")
Write-Output "=== FIN rc=0 ==="
exit 0
