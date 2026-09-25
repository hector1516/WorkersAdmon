# Copia este archivo como secretos_local.py (NO se sube a Git) y llena tus credenciales reales.
# El código usa variables de entorno primero y cae a estos valores si no están definidas.

DB_CONFIG_LOCAL = {
    'server': '10.188.141.15',
    'user': 'sa',
    'password': 'TU_PASSWORD_DB',
    'database': 'ECCSA_Admon'
}

EMAIL_CONFIG_LOCAL = {
    'smtp_server': 'smtpout.secureserver.net',
    'port': 465,
    'username': 'robot@ecc-sa.com.mx',
    'password': 'TU_PASSWORD_SMTP',
    'use_ssl': True,
    'use_tls': False,
    'require_auth': True
}