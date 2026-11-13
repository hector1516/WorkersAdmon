"""
cron_docker_cleanup.py — Limpieza diaria de Docker.

Corre cada 24 horas. Limpia imágenes no usadas, build cache y logs de contenedores.
Previene que el disco se llene y cause corrupción de bases de datos (como pasó con Arcane).

Corre bajo supervisor como 'docker_cleanup_worker'.
"""
import time
import sys
import os
import subprocess
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Docker cleanup worker (diario).")

INTERVALO_SEG = 86400  # 24 horas

def _ejecutar(cmd):
    """Ejecuta un comando y devuelve (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def _limpiar():
    ahora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ahora}] Iniciando limpieza de Docker...")

    # 1. Limpiar imágenes no usadas
    out, err, rc = _ejecutar("docker image prune -af")
    print(f"[{ahora}] Image prune: {out or err}")

    # 2. Limpiar build cache
    out, err, rc = _ejecutar("docker builder prune -af")
    print(f"[{ahora}] Builder prune: {out or err}")

    # 3. Verificar espacio en disco
    out, err, rc = _ejecutar("df -h / | tail -1")
    print(f"[{ahora}] Disco: {out}")

    # 4. Limpiar archivos temporales viejos
    _ejecutar("find /tmp -type f -mtime +7 -delete")
    _ejecutar("find /var/tmp -type f -mtime +7 -delete")

    print(f"[{ahora}] Limpieza completada.")

while True:
    try:
        _limpiar()
    except Exception as err:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error en limpieza: {err}")

    time.sleep(INTERVALO_SEG)