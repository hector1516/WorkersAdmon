#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# WorkersAdmon — entrypoint del contenedor `workersadmon`
#  1. /etc/hosts → Fileserver (SMB de PDFs)
#  2. secretos_local.py desde las variables de entorno
#  3. directorio de datos persistente (/data en el volumen)
#  4. habilita los workers listados en /data/workers_enabled.txt
#     (la lista vive en el VOLUMEN → sobrevive a recrear el contenedor)
#  5. arranca supervisord (status_web siempre + los habilitados)
# ─────────────────────────────────────────────────────────────────────────────

# 1) Resolución del Fileserver para smbclient / mounts
echo "10.188.141.15 Fileserver" >> /etc/hosts 2>/dev/null || true

# 2) Credenciales generadas en cada arranque (NUNCA se suben a Git)
cat > /app/secretos_local.py << EOF
DB_CONFIG_LOCAL = {
    'server': '${HUB_DB_SERVER:-10.188.141.15}',
    'user': '${HUB_DB_USER:-sa}',
    'password': '${HUB_DB_PASSWORD:-}',
    'database': '${HUB_DB_DATABASE:-ECCSA_Admon}'
}

EMAIL_CONFIG_LOCAL = {
    'smtp_server': 'smtpout.secureserver.net',
    'port': 465,
    'username': 'robot@ecc-sa.com.mx',
    'password': '${HUB_SMTP_PASSWORD:-}',
    'use_ssl': True,
    'use_tls': False,
    'require_auth': True
}
EOF
echo "[entrypoint] secretos_local.py generado"

# 3) Datos persistentes (volumen workersadmon_data → /data)
mkdir -p /data/heartbeats
touch /data/workers_enabled.txt

# 4) Activar los workers marcados en la lista persistente
AVAILABLE="/app/docker/conf.d.available"
while IFS= read -r raw || [ -n "$raw" ]; do
    name="${raw%%#*}"
    name="$(echo "$name" | tr -d '[:space:]')"
    [ -z "$name" ] && continue
    if [ -f "$AVAILABLE/$name.conf" ]; then
        cp "$AVAILABLE/$name.conf" "/etc/supervisor/conf.d/$name.conf" \
            && echo "[entrypoint] worker habilitado: $name"
    else
        echo "[entrypoint] AVISO: no existe conf para '$name' en $AVAILABLE"
    fi
done < /data/workers_enabled.txt

# 5) Proceso principal
exec /usr/local/bin/supervisord -c /etc/supervisor/supervisord.conf
