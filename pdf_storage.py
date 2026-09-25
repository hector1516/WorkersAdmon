"""
pdf_storage.py - Guardado automático de PDFs en share SMB.

Usa smbclient (CLI) para subir archivos directamente al share de red.
Lee la configuración de HUB_Config a través de eccsa_db.
"""

import os
import subprocess
import datetime

MESES = {
    1: "01 Enero", 2: "02 Febrero", 3: "03 Marzo",
    4: "04 Abril", 5: "05 Mayo", 6: "06 Junio",
    7: "07 Julio", 8: "08 Agosto", 9: "09 Septiembre",
    10: "10 Octubre", 11: "11 Noviembre", 12: "12 Diciembre"
}

MODULOS = {
    "materiales": "Cotizaciones_Materiales",
    "servproy": "Cotizaciones_ServProy",
    "reportes": "Reportes_Servicio",
    "oc": "Ordenes_Compra",
    "remisiones": "Remisiones_Materiales",
}


def _get_smb_config():
    """Obtiene la configuración SMB desde HUB_Config."""
    try:
        import eccsa_db as db
        cfg = db.get_pdf_storage_config()
        share_path = cfg.get('smb_share_path', '').strip()
        # Convertir \\Fileserver\hub a //Fileserver/hub
        if share_path.startswith('\\\\'):
            parts = share_path[2:].split('\\', 1)
            server = parts[0]
            share = parts[1] if len(parts) > 1 else ''
        else:
            server = ''
            share = ''
        return {
            'server': server,
            'share': share,
            'user': cfg.get('smb_user', ''),
            'password': cfg.get('smb_password', ''),
            'full_share': f"//{server}/{share}" if server and share else '',
        }
    except Exception:
        return {}


def _smb_run(commands):
    """Ejecuta comandos smbclient. commands: lista de strings. Retorna (stdout, stderr, returncode)."""
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return '', 'No SMB config', 1
    # Unir comandos con ; para smbclient -c
    cmd_str = '; '.join(commands)
    # Escapar el password para shell
    password = cfg['password'].replace("'", "'\\''")
    shell_cmd = f"smbclient '{cfg['full_share']}' -U '{cfg['user']}%{password}' -c '{cmd_str}'"
    try:
        result = subprocess.run(shell_cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return '', 'Timeout', 1
    except Exception as e:
        return '', str(e), 1


def _build_smb_dir(modulo, fecha):
    """Construye la ruta de directorio en el share."""
    nombre_modulo = MODULOS.get(modulo, modulo)
    if isinstance(fecha, datetime.datetime):
        fecha = fecha.date()
    year = fecha.year
    mes_folder = MESES.get(fecha.month, f"{fecha.month:02d} Mes")
    return f"{nombre_modulo}/{year}/{mes_folder}"


def _smb_makedirs(dir_path):
    """Crea carpetas en el share SMB (ignora si ya existen)."""
    parts = dir_path.split('/')
    cmds = []
    current = ''
    for part in parts:
        current = f"{current}/{part}" if current else part
        cmds.append(f'mkdir {current} 2>/dev/null')
    _smb_run(cmds)


def _smb_file_exists(filepath):
    """Verifica si un archivo existe en el share SMB."""
    parts = filepath.rsplit('/', 1)
    if len(parts) != 2:
        return False
    dir_path, filename = parts
    stdout, _, rc = _smb_run([f'cd {dir_path}', 'ls'])
    if rc != 0:
        return False
    return filename in stdout


def _smb_upload(filepath, pdf_bytes):
    """Sube bytes a un archivo en el share SMB usando cd + put."""
    import tempfile
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return False
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        parts = filepath.rsplit('/', 1)
        if len(parts) == 2:
            dir_path, filename = parts
            _smb_makedirs(dir_path)
            # cd al directorio y put solo el nombre
            stdout, stderr, rc = _smb_run([f'cd {dir_path}', f'put {tmp_path} {filename}'])
        else:
            stdout, stderr, rc = _smb_run([f'put {tmp_path} {filepath}'])
        if rc != 0:
            print(f"SMB upload error: {stderr or stdout}")
            return False
        return True
    except Exception as e:
        print(f"SMB upload error: {e}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _smb_list_dir(dir_path):
    """Lista contenidos de un directorio en el share SMB."""
    stdout, _, rc = _smb_run([f'cd {dir_path}', 'ls'])
    if rc != 0:
        return []
    entries = []
    for line in stdout.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('>') or 'blocks available' in line:
            continue
        # Formato: nombre  attribs  fecha  hora
        parts = line.split()
        if len(parts) >= 1:
            name = parts[0]
            if name in ('.', '..'):
                continue
            is_dir = 'D' in (parts[1] if len(parts) > 1 else '')
            entries.append({'name': name, 'is_directory': is_dir})
    return entries


def pdf_exists(modulo, folio, fecha):
    """Verifica si un PDF ya existe en el share SMB."""
    if not fecha:
        fecha = datetime.date.today()
    dir_path = _build_smb_dir(modulo, fecha)
    filename = f"{folio}.pdf"
    return _smb_file_exists(f"{dir_path}/{filename}")


def save_pdf(modulo, folio, pdf_bytes, fecha):
    """
    Guarda el PDF en el share SMB.
    Retorna la ruta del archivo guardado, o "" si no está configurado.
    """
    if not pdf_bytes:
        return ''
    if not fecha:
        fecha = datetime.date.today()
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return ''
    dir_path = _build_smb_dir(modulo, fecha)
    filepath = f"{dir_path}/{folio}.pdf"
    try:
        _smb_makedirs(dir_path)
        if _smb_upload(filepath, pdf_bytes):
            return filepath
        return ''
    except Exception as e:
        print(f"Error saving PDF via SMB: {e}")
        return ''


def list_pdfs(modulo=None):
    """Lista PDFs en el share SMB. Retorna lista de dicts."""
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return []
    modulos_a_listar = [MODULOS.get(m, m) for m in modulo] if isinstance(modulo, list) else (
        [MODULOS.get(modulo, modulo)] if modulo else list(MODULOS.values())
    )
    resultados = []
    for nombre_modulo in modulos_a_listar:
        modulo_path = nombre_modulo
        for year_entry in _smb_list_dir(modulo_path):
            if not year_entry['is_directory']:
                continue
            year = year_entry['name']
            year_path = f"{modulo_path}/{year}"
            for mes_entry in _smb_list_dir(year_path):
                if not mes_entry['is_directory']:
                    continue
                mes = mes_entry['name']
                mes_path = f"{year_path}/{mes}"
                for file_entry in _smb_list_dir(mes_path):
                    if file_entry['is_directory']:
                        continue
                    fname = file_entry['name']
                    if not fname.lower().endswith('.pdf'):
                        continue
                    resultados.append({
                        'modulo': nombre_modulo,
                        'year': year,
                        'mes': mes,
                        'filename': fname,
                        'size': 0,
                        'path': f"{mes_path}/{fname}",
                        'mtime': datetime.datetime.now(),
                    })
    return resultados


def count_pdfs_by_module():
    """Cuenta PDFs agrupados por módulo en el share SMB."""
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return {}
    counts = {}
    for nombre_modulo in MODULOS.values():
        count = 0
        modulo_path = nombre_modulo
        for year_entry in _smb_list_dir(modulo_path):
            if not year_entry['is_directory']:
                continue
            year_path = f"{modulo_path}/{year_entry['name']}"
            for mes_entry in _smb_list_dir(year_path):
                if not mes_entry['is_directory']:
                    continue
                mes_path = f"{year_path}/{mes_entry['name']}"
                for file_entry in _smb_list_dir(mes_path):
                    if not file_entry['is_directory'] and file_entry['name'].lower().endswith('.pdf'):
                        count += 1
        if count > 0:
            counts[nombre_modulo] = count
    return counts


def test_connection():
    """Prueba la conexión SMB. Retorna (ok, mensaje)."""
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return False, "No se ha configurado la ruta SMB (smb_share_path)"
    if not cfg.get('user'):
        return False, "No se ha configurado el usuario SMB (smb_user)"
    stdout, stderr, rc = _smb_run(['ls'])
    if rc != 0:
        msg = stderr or stdout or 'Error desconocido'
        return False, f"Error de conexión: {msg}"
    return True, f"Conexión exitosa a {cfg['full_share']}"


def read_pdf(modulo, folio, fecha):
    """
    Lee un PDF del share SMB y retorna los bytes.
    Retorna bytes o None si no existe.
    """
    cfg = _get_smb_config()
    if not cfg.get('full_share'):
        return None
    dir_path = _build_smb_dir(modulo, fecha)
    filename = f"{folio}.pdf"
    filepath = f"{dir_path}/{filename}"
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp_path = tmp.name
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(f"prompt off\n")
            f.write(f"get {filepath} {tmp_path}\n")
            f.write(f"quit\n")
            cmd_file = f.name
        import subprocess
        cmd_smb = [
            'smbclient', cfg['full_share'],
            '-U', f"{cfg['user']}%{cfg['password']}",
            '-A', cmd_file
        ]
        result = subprocess.run(cmd_smb, capture_output=True, text=True, timeout=30)
        try:
            import os
            os.unlink(cmd_file)
        except Exception:
            pass
        if result.returncode == 0 and os.path.exists(tmp_path):
            with open(tmp_path, 'rb') as f:
                data = f.read()
            os.unlink(tmp_path)
            return data
        return None
    except Exception as e:
        print(f"Error reading PDF from SMB: {e}")
        return None
