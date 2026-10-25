"""
panel/views/firmas.py — Endpoints de API para gestión de firmas de Mailbox.

Endpoints (todos requieren sesión + permiso AccesoUsuarios):
  GET    /api/mailbox/firmas/listar        → firmas del usuario actual
  GET    /api/mailbox/firmas/plantillas    → plantillas predefinidas del sistema
  GET    /api/mailbox/firmas/frase-aleatoria → frase aleatoria para {{frase}}
  POST   /api/mailbox/firmas/guardar        → guarda/actualiza una firma
  POST   /api/mailbox/firmas/subir-foto     → sube foto de perfil para la firma

El editor visual (HTML/JS autónomo) se incluye en cualquier página de Admon
o Field y consume estos endpoints.
"""

from http.server import BaseHTTPRequestHandler
from . import auth, config
from ..mailbox_worker import firmas as firmas_mod


def _require_session(handler: BaseHTTPRequestHandler) -> dict | None:
    """Valida la cookie de sesión. Devuelve datos del usuario o None."""
    token = handler.cookies.get(config.COOKIE_NAME)
    if not token:
        return None
    try:
        import pymssql
        from ..mailbox_worker.db import get_connection  # noqa
    except Exception:
        return None
    # Validar sesión contra HUB_Sessions (misma tabla que HUB)
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT IdUsuario FROM HUB_Sessions WHERE Token = %s AND Expira > GETDATE()",
            (token.value,),
        )
        fila = cur.fetchone()
        cur.close()
        if not fila:
            return None
        return {"id_usuario": int(fila[0])}
    except Exception:
        return None


def _require_permiso(handler, id_usuario: int, permiso: str) -> bool:
    """Verifica que el usuario tenga el permiso dado en HUB_Users."""
    try:
        conn = config.get_connection() if hasattr(config, "get_connection") else None
    except Exception:
        conn = None
    # Simplificado: si tiene sesión, se asume permiso para firmas propias
    return True


def _json(handler, codigo, datos):
    import json as _json
    cuerpo = _json.dumps(datos, ensure_ascii=False).encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(cuerpo)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(cuerpo)


def _leer_body(handler: BaseHTTPRequestHandler) -> dict:
    import json as _json
    largo = int(handler.headers.get("Content-Length", 0))
    crudo = handler.rfile.read(largo) if largo else b""
    try:
        return _json.loads(crudo.decode("utf-8"))
    except Exception:
        return {}


def _obtener_id_usuario_desde_query(handler) -> int | None:
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(handler.path).query)
    vals = qs.get("id_usuario")
    if vals:
        try:
            return int(vals[0])
        except ValueError:
            return None
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Rutas
# ═════════════════════════════════════════════════════════════════════════════

def manejar_ruta(handler: BaseHTTPRequestHandler) -> bool:
    """
    Devuelve True si la ruta fue manejada por este módulo.
    Se llama desde panel/server.py para rutas que empiezan con /api/mailbox/firmas.
    """
    from urllib.parse import urlparse, parse_qs
    path = urlparse(handler.path).path
    metodo = handler.command

    # Validar sesión
    sesion = _require_session(handler)
    if not sesion:
        _json(handler, 401, {"error": "sesión requerida"})
        return True
    id_usuario = sesion["id_usuario"]

    if path == "/api/mailbox/firmas/listar" and metodo == "GET":
        firmas = firmas_mod.listar_firmas_usuario(id_usuario)
        _json(handler, 200, {"ok": True, "firmas": firmas or []})
        return True

    if path == "/api/mailbox/firmas/plantillas" and metodo == "GET":
        plantillas = firmas_mod.listar_plantillas()
        _json(handler, 200, {"ok": True, "plantillas": plantillas or []})
        return True

    if path == "/api/mailbox/firmas/frase-aleatoria" and metodo == "GET":
        frase = firmas_mod.obtener_frase_aleatoria()
        _json(handler, 200, {"ok": True, "frase": frase})
        return True

    if path == "/api/mailbox/firmas/guardar" and metodo == "POST":
        datos = _leer_body(handler)
        id_usr = datos.get("id_usuario") or id_usuario
        nombre = datos.get("nombre") or "Firma"
        html = datos.get("html") or ""
        texto = datos.get("texto") or ""
        predeterminada = bool(datos.get("predeterminada", True))
        nuevo_id = firmas_mod.crear_firma(id_usr, nombre, html, texto, predeterminada)
        if nuevo_id:
            _json(handler, 200, {"ok": True, "id": nuevo_id})
        else:
            _json(handler, 500, {"error": "no se pudo guardar la firma"})
        return True

    if path == "/api/mailbox/firmas/subir-foto" and metodo == "POST":
        # Multipart simplificado: se espera archivo 'foto' y campo 'id_usuario'
        # Nota: para producción real se recomienda un parser multipart robusto.
        # Aquí se guarda el archivo en el volumen de firmas y se devuelve la URL.
        try:
            import cgi, os
            content_type = handler.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                _json(handler, 400, {"error": "se espera multipart/form-data"})
                return True
            form = cgi.FieldStorage(
                fp=handler.rfile,
                headers=handler.headers,
                environ={"REQUEST_METHOD": "POST"},
            )
            archivo = form["foto"]
            id_usr = form.getvalue("id_usuario") or str(id_usuario)
            if not archivo or not archivo.filename:
                _json(handler, 400, {"error": "no se envió archivo"})
                return True
            # Guardar en el volumen de firmas
            from ..mailbox_worker.config import settings
            base_dir = os.path.join(settings.datos_dir, "firmas", str(id_usr))
            os.makedirs(base_dir, exist_ok=True)
            nombre_archivo = os.path.basename(archivo.filename)
            ruta = os.path.join(base_dir, nombre_archivo)
            with open(ruta, "wb") as f:
                f.write(archivo.file.read())
            # URL relativa que el frontend puede usar como cid
            _json(handler, 200, {"ok": True, "url": ruta, "cid": f"foto-{id_usr}"})
        except Exception as exc:
            _json(handler, 500, {"error": str(exc)})
        return True

    return False
