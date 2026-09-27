<#
    register_task.ps1 — Registra la tarea programada "WorkersBuild" para desplegar
    el panel de WorkersAdmon con un comando, desde cualquier sesión.

    Uso (una sola vez, desde PowerShell como Administrador en el ServerVM):
        powershell -ExecutionPolicy Bypass -File C:\WorkersAdmon\deploy\register_task.ps1

    Para qué existe: la tarea quedó en modo "Solo interactivo", que significa
    que SÓLO arranca si hay una sesión de escritorio abierta en el ServerVM. El
    runner de GitHub Actions es un servicio, no una sesión: ahí `schtasks /run`
    responde "CORRECTO" y no ejecuta nada, y el deploy se queda esperando hasta
    que el watchdog da timeout. Registrándola con /ru + /rp el modo pasa a
    "Password" (batch), que corre sin sesión y con la misma cuenta.

    ── Lecciones de Windows (2026-09-27) ───────────────────────────────────────
    · No existe /sc ondemand: schtasks /create responde "tipo de programa no
      válido". Se usa /sc once.
    · Una tarea con fecha (/sd) YA VENCIDA no se puede lanzar: /run responde
      "CORRECTO" pero no arranca y build.log nunca aparece. Por eso se programa
      UNA sola vez en un futuro lejano (31/12/2099): no se auto-dispara nunca y
      /run sí funciona.
    · No se usa /ru SYSTEM: el `docker` de SYSTEM no ve el pipe de Docker
      Desktop, y además git rechazaría el repo por "dubious ownership".
    · En sesión SSH, $env:USERDOMAIN puede venir como WORKGROUP aunque la cuenta
      sea local, y schtasks responde "no se efectuó ninguna asignación entre los
      nombres de cuenta y los identificadores de seguridad". La identidad de
      Windows siempre da el nombre correcto (SERVERVM\ECCSA), por eso el script
      la consulta en vez de confiar en las variables de entorno.

    La contraseña NO está en el repo: se pide por parámetro, por
    $env:DEPLOY_TASK_PASSWORD o de forma interactiva, y queda solo dentro de la
    tarea programada (protegida por LSA).

    Requisito previo: C:\WorkersAdmon debe tener el repo (el paso
    "Sincronizar el clon canonico" del deploy lo deja listo).
#>

param(
    [string]$Password = $env:DEPLOY_TASK_PASSWORD
)

$ErrorActionPreference = 'Stop'
$repo     = 'C:\WorkersAdmon'
$script   = Join-Path $repo 'deploy\build.bat'
$taskName = 'WorkersBuild'
$user     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not $user) { $user = "$env:COMPUTERNAME\$env:USERNAME" }

if (-not (Test-Path $script)) { throw "No existe $script (¿el repo está en $repo?)" }

if (-not $Password) {
    $sec = Read-Host "Contraseña de Windows para $user (solo queda en la tarea, no en el repo)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$action = 'cmd.exe /c "' + $script + '"'
schtasks /create /tn $taskName /tr $action /sc once /st 23:59 /sd 31/12/2099 /ru $user /rp $Password /f | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'schtasks /create falló' }

# Verificacion: el modo de inicio de sesion es el que hace fallar los deploys
# en silencio, asi que se comprueba recien registrada en vez de suponerlo.
$info = schtasks /query /tn $taskName /fo LIST /v 2>&1 | Out-String
if ($info -match 'Solo interactivo') {
    Write-Output "AVISO: la tarea quedo en 'Solo interactivo'. /run no arrancara sin sesion de escritorio."
} else {
    Write-Output "OK: la tarea corre sin sesion de escritorio (modo batch)."
}

Write-Output "Tarea $taskName registrada (una vez, 31/12/2099 -> nunca se auto-dispara)."
Write-Output "  ejecuta : $action"
Write-Output "  usuario : $user (modo batch, con contraseña)"
Write-Output ""
Write-Output "Para desplegar:"
Write-Output "  schtasks /run /tn $taskName"
Write-Output "  Get-Content $repo\build.log -Wait     # hasta 'FIN rc=0'"
