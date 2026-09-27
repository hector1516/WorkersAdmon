@echo off
rem ============================================================
rem Build + deploy de WorkersAdmon en ServerVM.
rem
rem Antes este archivo hacia UN solo comando:
rem     docker build -t workersadmon .
rem Construia la imagen y NADA MAS: no detenia el contenedor, no lo
rem recreaba, no chequeaba salud y no hacia rollback. El contenedor
rem seguia con la imagen vieja y el log decia "FIN rc=0": un deploy que
rem no despliega. Ahora delega en deploy/build.ps1, que hace el ciclo
rem completo (build -> recrear con la config actual -> salud -> rollback)
rem igual que Field y Admon.
rem
rem Se mantiene el nombre porque la tarea programada WorkersBuild
rem (schtasks) y el paso "Rebuild" del deploy apuntan a este archivo.
rem ============================================================
cd /d C:\WorkersAdmon
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\WorkersAdmon\deploy\build.ps1"
exit /b %ERRORLEVEL%
