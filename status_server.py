"""
status_server.py — Punto de entrada del panel de control de WorkersAdmon
========================================================================
Proceso `status_web` bajo supervisord (docker/conf.d/status_web.conf).
La lógica vive en el paquete `panel/`:

  panel/config.py     rutas, cookies, permisos, pestañas
  panel/db.py         SQL Server (HUB_Users, HUB_Sessions, bitácora)
  panel/auth.py       sesión, CSRF, límite de intentos de login
  panel/workers.py    estado + activar/desactivar/reiniciar + logs
  panel/templates.py  HTML (tema oscuro ECCSA)
  panel/views/        pestañas (workers; notificaciones y apps en fases B/C)
  panel/server.py     routing HTTP

Endpoints: GET / (Workers) · GET /login · GET /api/status · GET /healthz
"""
import os
import sys

# Permite ejecutar `python status_server.py` desde cualquier directorio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panel.server import run_server  # noqa: E402

if __name__ == "__main__":
    run_server()
