import os

def load_db_config():
    return {
        'server': os.environ.get('HUB_DB_SERVER', '10.188.141.15'),
        'user': os.environ.get('HUB_DB_USER', 'sa'),
        'password': os.environ.get('HUB_DB_PASSWORD', ''),
        'database': os.environ.get('HUB_DB_DATABASE', 'ECCSA_Admon'),
    }

def load_jwt_config():
    return {
        'secret': os.environ.get('JWT_SECRET', 'field-dev-secret-change-in-production'),
        'algorithm': 'HS256',
        'expires_days': 90,
    }
