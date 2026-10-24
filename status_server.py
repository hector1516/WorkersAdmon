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

from panel import envconf  # noqa: E402
from panel.server import run_server  # noqa: E402

# El panel cifra la contraseña IMAP al guardar una cuenta (panel/db_correo.py),
# pero las variables del worker viven en /data/worker_env.json y el entrypoint
# solo las escribe en el .conf de supervisor: este proceso no las heredaba.
# Sin esto, guardar una contraseña de correo fallaba con un 500.
envconf.aplicar_a_entorno("mailbox_worker")

if __name__ == "__main__":
    run_server()
