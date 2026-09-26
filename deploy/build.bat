@echo off
rem ============================================================
rem Build de la imagen `workersadmon` en ServerVM.
rem Corre DESACOPLADO de la sesion SSH (Windows mata los procesos
rem hijos cuando se cierra el ssh): lanzarlo con
rem   schtasks /create /tn WorkersBuild /tr "C:\WorkersAdmon\deploy\build.bat" /sc once /st 23:59 /f
rem   schtasks /run /tn WorkersBuild
rem y vigilar C:\WorkersAdmon\build.log hasta ver "FIN rc=0".
rem ============================================================
cd /d C:\WorkersAdmon
echo === INICIO %DATE% %TIME% === > build.log
rem WITH_PLAYWRIGHT=1: instala playwright + Chromium en la imagen
rem (obligatorio para oxxogas_contactos / vales / govale_vouchers).
docker build --build-arg WITH_PLAYWRIGHT=1 -t workersadmon . >> build.log 2>&1
echo === FIN rc=%ERRORLEVEL% %DATE% %TIME% === >> build.log
