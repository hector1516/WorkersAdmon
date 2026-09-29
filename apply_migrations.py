import os
import sys
import glob

ROOT = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(ROOT, "migrations")

sys.path.insert(0, ROOT)
from config_db import load_db_config
import pymssql

ALLOW_PRODUCTION = os.environ.get("HUB_MIGRATE_PRODUCTION", "0") == "1"


def get_connection():
    cfg = load_db_config()
    return pymssql.connect(
        server=cfg['server'],
        user=cfg['user'],
        password=cfg['password'],
        database=cfg['database'],
        autocommit=True,
    )


def split_statements(sql_text):
    """
    Parte un .sql en los lotes que ejecuta T-SQL, como SSMS y sqlcmd.

    OJO con lo que NO se puede hacer: `sql_text.split("GO")` parte por la
    SUBCADENA, y "GO" aparece dentro de cosas que no son separadores:

      · un literal: 0034_govale_saldo_telegram_event.sql tiene el evento
        'GOVALE_SALDO' y se partía dentro del string, dejando la comilla
        abierta → "Incorrect syntax near 'notificación'";
      · un comentario: "-- 2) Asignación usuario → turno" con un GO adelante.

    El efecto es que una migración que ya corrió en producción se vuelve
    inaplicable contra una base nueva, y el fallo dice "sintaxis" sin señalar
    que el culpable es el runner. El separador de lotes es una LÍNEA que sea
    solo GO (sin importar mayúsculas ni espacios), como en Management Studio.
    """
    lotes, actual = [], []
    for linea in sql_text.splitlines():
        if linea.strip().upper() == "GO":
            lote = "\n".join(actual)
            if _tiene_contenido(lote):
                lotes.append(lote)
            actual = []
        else:
            actual.append(linea)
    lote = "\n".join(actual)
    if _tiene_contenido(lote):
        lotes.append(lote)
    return lotes


def _tiene_contenido(lote):
    """False si el lote es solo comentarios y líneas en blanco: mandarle eso al
    servidor no hace nada y solo ensucia el log del runner."""
    return any(line.strip() and not line.strip().startswith("--")
               for line in lote.splitlines())


def applied_versions(conn):
    cur = conn.cursor(as_dict=True)
    try:
        cur.execute("SELECT version FROM schema_migrations")
        return {row['version'] for row in cur.fetchall()}
    except Exception:
        return set()


def run():
    cfg = load_db_config()
    dbname = cfg['database']
    print(f"Aplicando migraciones a: {cfg['server']}/{dbname}")

    if dbname != "ECCSA_Admon_Pruebas" and not ALLOW_PRODUCTION:
        print("ABORTADO: apuntando a una base que no es de pruebas.")
        print("Para aplicar a producción, define HUB_MIGRATE_PRODUCTION=1")
        sys.exit(1)

    conn = get_connection()
    applied = applied_versions(conn)

    if "0001_schema_migrations.sql" not in applied:
        print("  [bootstrap] creando tabla schema_migrations...")
        with open(os.path.join(MIGRATIONS_DIR, "0001_schema_migrations.sql"), encoding="utf-8") as f:
            for stmt in split_statements(f.read()):
                conn.cursor().execute(stmt)
        conn.commit()
        applied = applied_versions(conn)

    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    pending = [f for f in files if os.path.basename(f) not in applied]

    if not pending:
        print("Sin migraciones pendientes.")
        return

    for fpath in pending:
        name = os.path.basename(fpath)
        if "baseline" in name:
            # Los baselines son de SOLO referencia: el esquema ya existe.
            # Solo se registran como aplicados, NO se ejecutan.
            conn.cursor().execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (name,)
            )
            conn.commit()
            print(f"  [baseline - sin ejecutar] {name}")
            continue
        print(f"  Aplicando {name} ...")
        with open(fpath, encoding="utf-8") as f:
            for stmt in split_statements(f.read()):
                conn.cursor().execute(stmt)
        conn.cursor().execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)", (name,)
        )
        conn.commit()
    print("Listo.")


if __name__ == "__main__":
    run()