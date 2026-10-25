#!/usr/bin/env python3
"""
Reprocesamiento de cuerpos de correo malos (MIME crudo -> HTML limpio).
Versión sin IMAP: lee los cuerpos .gz del disco, extrae el MIME crudo que
viene envuelto en <pre> (o es MIME crudo puro), lo parsea con la librería
`email` de Python y reescribe los cuerpos limpiados.

Ejecuta DENTRO del contenedor workersadmon:

    docker cp reprocess_bodies_v3.py workersadmon:/tmp/reprocess_bodies_v3.py
    docker exec workersadmon python /tmp/reprocess_bodies_v3.py

Qué hace:
  1. Lee mensajes con CuerpoGuardado=1 de HUB_MailboxMensajes.
  2. Lee el cuerpo .gz desde /data/mailbox/cuerpos/<cuenta>/<clave>.gz.
  3. Detecta MIME crudo (boundary, Content-Type multipart, MIME-Version).
  4. Extrae el MIME crudo (de dentro de <pre> o del cuerpo completo).
  5. Parsea con email.message_from_bytes y extrae text/html o text/plain.
  6. Guarda el cuerpo limpio de vuelta en disco.
  7. Nunca borra registros.
"""

import os, sys, gzip, re, email
from email.policy import default
from html import escape
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
    Intenta extraer el MIME crudo que viene dentro de <pre>...</pre>.
    Devuelve los bytes del MIME, o None si no hay <pre> con MIME.
    """
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    interno = m.group(1).encode("utf-8", "replace")
    # El interno debe parecer MIME crudo
    if b"Content-Type:" in interno[:500] or b"boundary=" in interno[:500]:
        return interno
    return None


def extraer_html_desde_mime(mime_bytes: bytes) -> str:
    """
    Dado un mensaje MIME crudo (bytes), extrae text/html o text/plain
    y devuelve un HTML limpio listo para guardar.
    """
    msg = email.message_from_bytes(mime_bytes, policy=default)

    html_part = None
    text_part = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html" and html_part is None:
                html_part = part
            elif content_type == "text/plain" and text_part is None:
                text_part = part
    else:
        ct = msg.get_content_type()
        if ct == "text/html":
            html_part = msg
        elif ct == "text/plain":
            text_part = msg

    if html_part is not None:
        payload = html_part.get_payload(decode=True)
        if payload:
            texto = payload.decode("utf-8", "replace")
            if "<" in texto:
                return texto

    if text_part is not None:
        payload = text_part.get_payload(decode=True)
        if payload:
            texto = payload.decode("utf-8", "replace")
            if texto.strip():
                return (
                    '<html><head><meta charset="utf-8"></head><body>'
                    '<pre style="white-space:pre-wrap;font-family:sans-serif">'
                    f"{escape(texto)}</pre></body></html>"
                )

    return ""


def reprocesar():
    print(f"[{datetime.now().isoformat()}] Iniciando reprocesamiento de cuerpos (v3)")

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
            # Quizás el cuerpo entero es MIME crudo (sin envoltorio HTML)
            mime_bytes = crudo

        # Parsear MIME y extraer HTML limpio
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
