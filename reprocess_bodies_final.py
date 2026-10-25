#!/usr/bin/env python3
"""
Reprocesamiento de cuerpos de correo malos (MIME crudo -> HTML limpio).
Versión final robusta: lee los cuerpos .gz del disco, extrae el MIME crudo
de dentro de <pre>, desentidadiza, normaliza saltos de línea, y extrae
text/html o text/plain dividiendo por boundaries.

Ejecuta DENTRO del contenedor workersadmon:

    docker cp reprocess_bodies_final.py workersadmon:/tmp/reprocess_bodies_final.py
    docker exec workersadmon python /tmp/reprocess_bodies_final.py

Qué hace:
  1. Lee mensajes con CuerpoGuardado=1 de HUB_MailboxMensajes.
  2. Lee el cuerpo .gz desde /data/mailbox/cuerpos/<cuenta>/<clave>.gz.
  3. Detecta MIME crudo (boundary, Content-Type multipart, etc.).
  4. Extrae el MIME crudo (de dentro de <pre> o del cuerpo completo).
  5. Desentidadiza y normaliza saltos de línea.
  6. Divide por boundaries y busca la parte text/html o text/plain.
  7. Decodifica quoted-printable / base64 según Content-Transfer-Encoding.
  8. Guarda el cuerpo limpio de vuelta en disco.
  9. Nunca borra registros.
"""

import os, sys, gzip, re, base64, quopri
from html import unescape, escape
from datetime import datetime

sys.path.insert(0, "/app")

from mailbox_worker.config import settings
from mailbox_worker.db import filas
from mailbox_worker.almacen import leer_cuerpo, guardar_cuerpo, _cuerpo_rel


def tiene_mime_crudo(html: str) -> bool:
    """True si el cuerpo contiene MIME crudo (boundary, multipart, etc.)."""
    if not html or not html.strip():
        return False
    crudo = html.encode("utf-8", "replace")
    return any(h in crudo for h in (
        b"Content-Type: multipart",
        b"boundary=",
        b"MIME-Version:",
        b"Content-Transfer-Encoding:",
    ))


def extraer_mime_del_pre(html: str) -> bytes | None:
    """
    Extrae el MIME crudo de dentro de <pre>...</pre>.
    Desentidadiza (&quot; -> ", etc.) y devuelve bytes.
    """
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    interno = unescape(m.group(1))
    interno_bytes = interno.encode("utf-8", "replace")
    if b"Content-Type:" in interno_bytes[:500] or b"boundary=" in interno_bytes[:500]:
        return interno_bytes
    return None


def decodificar_transferencia(datos: bytes, encoding: str) -> bytes:
    """Decodifica quoted-printable o base64."""
    enc = (encoding or "").strip().upper()
    if enc == "BASE64":
        try:
            return base64.b64decode(datos)
        except Exception:
            return datos
    elif enc == "QUOTED-PRINTABLE":
        try:
            return quopri.decodestring(datos)
        except Exception:
            return datos
    return datos


def extraer_partes_por_boundary(mime_str: str) -> list:
    """
    Divide el MIME crudo por boundaries y devuelve una lista de
    (cabeceras, cuerpo) para cada parte.
    """
    partes = []

    # Encontrar el boundary del nivel superior: busca la primera línea que empieza con --
    lineas = mime_str.split("\n")
    boundary_superior = None
    for linea in lineas:
        if linea.startswith("--") and len(linea) > 2:
            boundary_superior = linea[2:]  # quitar el -- inicial
            break

    if not boundary_superior:
        return []

    # Dividir por el boundary
    patron_boundary = re.compile(r"\n--" + re.escape(boundary_superior))
    secciones = patron_boundary.split(mime_str)

    for seccion in secciones:
        seccion = seccion.strip()
        if not seccion or seccion == "--":
            continue

        # Separar cabeceras del cuerpo
        if "\n\n" in seccion:
            cabeceras, cuerpo = seccion.split("\n\n", 1)
        else:
            cabeceras, cuerpo = seccion, ""

        # Buscar Content-Type en las cabeceras
        ct_match = re.search(r'Content-Type:\s*([^\s;]+)', cabeceras, re.IGNORECASE)
        content_type = ct_match.group(1).lower() if ct_match else ""

        # Buscar Content-Transfer-Encoding
        enc_match = re.search(r'Content-Transfer-Encoding:\s*(\S+)', cabeceras, re.IGNORECASE)
        encoding = enc_match.group(1) if enc_match else ""

        partes.append({
            "content_type": content_type,
            "encoding": encoding,
            "cuerpo": cuerpo,
        })

    return partes


def extraer_html_desde_mime(mime_bytes: bytes) -> str:
    """
    Dado un mensaje MIME crudo (bytes), extrae text/html o text/plain
    y devuelve un HTML limpio listo para guardar.
    """
    # Normalizar saltos de línea y desentidadizar ya está hecho antes de llamar
    mime_str = mime_bytes.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")

    # Buscar directamente Content-Type: text/html en todo el MIME.
    # Después de las cabeceras de esa parte, el cuerpo va hasta el siguiente
    # boundary (línea que empieza con --) o al final del string.
    patron_html = re.compile(
        r'Content-Type:\s*text/html\b.*?\n\n(.*?)(?=\n--|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    match = patron_html.search(mime_str)
    if match:
        cuerpo = match.group(1).strip()
        # Buscar Content-Transfer-Encoding de esta parte
        inicio = match.start()
        fin_cab = mime_str.find("\n\n", inicio)
        cab = mime_str[inicio:fin_cab] if fin_cab > inicio else ""
        enc_match = re.search(r'Content-Transfer-Encoding:\s*(\S+)', cab, re.IGNORECASE)
        encoding = enc_match.group(1) if enc_match else ""
        texto = decodificar_transferencia(cuerpo.encode("utf-8", "replace"), encoding)
        texto = texto.decode("utf-8", "replace")
        if texto and "<" in texto:
            return texto

    # Buscar text/plain
    patron_texto = re.compile(
        r'Content-Type:\s*text/plain\b.*?\n\n(.*?)(?=\n--|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    match = patron_texto.search(mime_str)
    if match:
        cuerpo = match.group(1).strip()
        inicio = match.start()
        fin_cab = mime_str.find("\n\n", inicio)
        cab = mime_str[inicio:fin_cab] if fin_cab > inicio else ""
        enc_match = re.search(r'Content-Transfer-Encoding:\s*(\S+)', cab, re.IGNORECASE)
        encoding = enc_match.group(1) if enc_match else ""
        texto = decodificar_transferencia(cuerpo.encode("utf-8", "replace"), encoding)
        texto = texto.decode("utf-8", "replace")
        if texto and texto.strip():
            return (
                '<html><head><meta charset="utf-8"></head><body>'
                '<pre style="white-space:pre-wrap;font-family:sans-serif">'
                f"{escape(texto)}</pre></body></html>"
            )

    return ""


def reprocesar():
    print(f"[{datetime.now().isoformat()}] Iniciando reprocesamiento de cuerpos (final)")

    mensajes = filas(
        "SELECT Id, IdCuenta, Carpeta, UID, ClaveCuerpo FROM HUB_MailboxMensajes "
        "WHERE Eliminado = 0 AND CuerpoGuardado = 1 ORDER BY Id"
    )
    if not mensajes:
        print("No hay mensajes con cuerpo guardado.")
        return 0

    print(f"Mensajes con cuerpo guardado: {len(mensajes)}")

    total_revisados = 0
    total_reprocesados = 0
    total_errores = 0
    total_sin_cambio = 0

    for m in mensajes:
        mid = m["Id"]
        cid = m["IdCuenta"]
        clave = m["ClaveCuerpo"]

        total_revisados += 1

        # Leer cuerpo actual de disco
        html_actual = leer_cuerpo(cid, clave)

        if not tiene_mime_crudo(html_actual):
            total_sin_cambio += 1
            continue  # Cuerpo ya está bien

        # Leer bytes crudos del .gz
        rel = _cuerpo_rel(cid, clave)
        ruta = os.path.join(settings.datos_dir, rel)
        try:
            with open(ruta, "rb") as fh:
                crudo = fh.read()
            if crudo[:2] == b"\x1f\x8b":
                crudo = gzip.decompress(crudo)
        except Exception as exc:
            print(f"  Mensaje {mid}: no se pudo leer .gz: {exc}")
            total_errores += 1
            continue

        html_str = crudo.decode("utf-8", "replace")

        # Intentar extraer MIME de dentro de <pre>
        mime_bytes = extraer_mime_del_pre(html_str)

        if mime_bytes is None:
            mime_bytes = crudo

        # Extraer HTML limpio del MIME
        html_nuevo = extraer_html_desde_mime(mime_bytes)

        if not html_nuevo:
            print(f"  Mensaje {mid}: no se pudo extraer texto del MIME.")
            total_errores += 1
            continue

        # Guardar cuerpo limpio
        if guardar_cuerpo(cid, clave, html_nuevo):
            total_reprocesados += 1
            if total_reprocesados % 25 == 0:
                print(f"    ... {total_reprocesados} reprocesados")
        else:
            print(f"  Mensaje {mid}: no se pudo guardar en disco.")
            total_errores += 1

    print(f"\n[{datetime.now().isoformat()}] Resumen:")
    print(f"  Mensajes revisados: {total_revisados}")
    print(f"  Ya estaban bien (sin cambio): {total_sin_cambio}")
    print(f"  Cuerpos reprocesados: {total_reprocesados}")
    print(f"  Errores: {total_errores}")
    return total_reprocesados


if __name__ == "__main__":
    n = reprocesar()
    sys.exit(0 if n >= 0 else 1)
