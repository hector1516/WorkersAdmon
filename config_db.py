import os

def _first(*vals):
    """Return the first truthy (non-empty, non-None) value, or None."""
    for v in vals:
        if v is not None and str(v).strip():
            return v
    return None

def load_db_config():
    default = {
        'server': '10.188.141.15',
        'user': 'sa',
        'password': '',
        'database': 'ECCSA_Admon'
    }
    try:
        from secretos_local import DB_CONFIG_LOCAL
        default.update(DB_CONFIG_LOCAL)
    except Exception:
        pass
    cfg = {
        'server': _first(os.environ.get('HUB_DB_SERVER'), default['server']),
        'user': _first(os.environ.get('HUB_DB_USER'), default['user']),
        'password': _first(os.environ.get('HUB_DB_PASSWORD'), default['password']),
        'database': _first(os.environ.get('HUB_DB_DATABASE'), default['database'])
    }
    if not cfg.get('password'):
        print("[config_db] WARNING: No DB password configured. "
              "Set HUB_DB_PASSWORD env var or create secretos_local.py.")
    return cfg

def load_email_fallback():
    default = {
        'smtp_server': 'smtpout.secureserver.net',
        'port': 465,
        'username': 'robot@ecc-sa.com.mx',
        'password': '',
        'use_ssl': True,
        'use_tls': False,
        'require_auth': True
    }
    try:
        from secretos_local import EMAIL_CONFIG_LOCAL
        default.update(EMAIL_CONFIG_LOCAL)
    except Exception:
        pass
    default['password'] = _first(os.environ.get('HUB_SMTP_PASSWORD'),
                                 default['password'])
    if not default.get('password'):
        print("[config_db] WARNING: No SMTP password configured. "
              "Set HUB_SMTP_PASSWORD env var or create secretos_local.py.")
    return default