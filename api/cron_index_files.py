"""Worker de indexación de archivos del Repositorio Clientes.
Recorre \\\\FILESERVER\\Docs\\Shared y \\\\FILESERVER\\Docs\\Aplicaciones
y mantiene un índice en SQL Server (HUB_FileIndex).
"""
import os
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection

try:
    from smb.SMBConnection import SMBConnection
except ImportError:
    print("[file_indexer] ERROR: pysmb no instalado. Ejecuta: pip install pysmb")
    sys.exit(1)

# Configuración SMB
SMB_USER = "eccsa"
SMB_PASS = "eyccazo"
SMB_SERVER = "FILESERVER"
SMB_IP = "10.188.141.15"
SMB_DOMAIN = ""
SMB_CLIENT = "field_indexer"  # nombre de este cliente

SHARES = [
    {"share": "Docs", "base": "/Shared", "servidor": "Shared"},
    {"share": "Docs", "base": "/Aplicaciones", "servidor": "Aplicaciones"},
]

INTERVALO = 300  # 5 minutos
CHUNK_SIZE = 500  # archivos por batch UPSERT


def get_smb_connection():
    """Crea conexión SMB a FileServer."""
    conn = SMBConnection(SMB_USER, SMB_PASS, SMB_CLIENT, SMB_SERVER,
                         domain=SMB_DOMAIN, use_ntlm_v2=True,
                         is_direct_tcp=True)
    if not conn.connect(SMB_IP, 445, timeout=30):
        raise ConnectionError("No se pudo conectar a FILESERVER via SMB")
    return conn


def walk_smb(conn, share, base_path):
    """Recorre recursivamente una carpeta SMB. Genera (nombre, es_carpeta, tamano, fecha, ruta_completa, ruta_padre)."""
    try:
        entries = conn.listPath(share, base_path)
    except Exception as e:
        print(f"[file_indexer] Error listando {share}/{base_path}: {e}")
        return

    for entry in entries:
        name = entry.filename
        if name in (".", ".."):
            continue

        full_path = f"{base_path}/{name}".replace("//", "/")
        is_dir = entry.isDirectory
        size = entry.file_size if not is_dir else 0
        mtime = datetime.fromtimestamp(entry.last_write_time) if entry.last_write_time else None

        yield {
            "nombre": name,
            "extension": "" if is_dir else os.path.splitext(name)[1].lower(),
            "es_carpeta": 1 if is_dir else 0,
            "tamanio": size,
            "fecha_modificado": mtime,
            "ruta_completa": full_path,
            "ruta_padre": base_path,
        }

        if is_dir:
            yield from walk_smb(conn, share, full_path)


def upsert_batch(rows):
    """Inserta/actualiza un batch de archivos en HUB_FileIndex."""
    if not rows:
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute("""
                    IF EXISTS (SELECT 1 FROM HUB_FileIndex WHERE RutaCompleta = %s)
                        UPDATE HUB_FileIndex
                        SET Nombre = %s, Extension = %s, EsCarpeta = %s,
                            Tamanio = %s, FechaModificado = %s, RutaPadre = %s,
                            Servidor = %s, Indexado = GETDATE()
                        WHERE RutaCompleta = %s
                    ELSE
                        INSERT INTO HUB_FileIndex
                        (RutaCompleta, Nombre, Extension, EsCarpeta, Tamanio, FechaModificado, RutaPadre, Servidor, Indexado)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, GETDATE())
                """, (
                    r["ruta_completa"], r["nombre"], r["extension"], r["es_carpeta"],
                    r["tamanio"], r["fecha_modificado"], r["ruta_padre"], r["servidor"],
                    r["ruta_completa"],
                    r["ruta_completa"], r["nombre"], r["extension"], r["es_carpeta"],
                    r["tamanio"], r["fecha_modificado"], r["ruta_padre"], r["servidor"],
                ))
            conn.commit()
        return len(rows)
    except Exception as e:
        print(f"[file_indexer] Error en upsert: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def indexar_share(conn, share_cfg):
    """Indexa un share SMB completo."""
    share = share_cfg["share"]
    base = share_cfg["base"]
    servidor = share_cfg["servidor"]
    base_smb = base if base.startswith("/") else f"/{base}"

    print(f"[file_indexer] Indexando {share}{base_smb}...", flush=True)
    batch = []
    total = 0
    start = time.time()

    try:
        for item in walk_smb(conn, share, base_smb):
            batch.append(item)
            if len(batch) >= CHUNK_SIZE:
                n = upsert_batch([{**r, "servidor": servidor} for r in batch])
                total += n
                batch = []
        if batch:
            n = upsert_batch([{**r, "servidor": servidor} for r in batch])
            total += n
    except Exception as e:
        print(f"[file_indexer] Error indexando {share}{base_smb}: {e}")
        traceback.print_exc()

    elapsed = time.time() - start
    print(f"[file_indexer] {share}{base_smb}: {total} archivos indexados en {elapsed:.1f}s", flush=True)
    return total


def main():
    print("[file_indexer] Worker de indexación de archivos iniciado", flush=True)
    while True:
        try:
            conn = get_smb_connection()
            total = 0
            for share_cfg in SHARES:
                try:
                    total += indexar_share(conn, share_cfg)
                except Exception as e:
                    print(f"[file_indexer] Error en {share_cfg}: {e}")
                    traceback.print_exc()
            try:
                conn.close()
            except Exception:
                pass
            print(f"[file_indexer] Ciclo completado: {total} archivos totales", flush=True)
        except Exception as e:
            print(f"[file_indexer] Error de conexión SMB: {e}")
            traceback.print_exc()
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
