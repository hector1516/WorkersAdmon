#!/usr/bin/env python3
"""
Reprocesamiento de cuerpos de correo malos (MIME crudo -> HTML limpio).

Ejecuta DENTRO del contenedor workersadmon:

    docker cp reprocess_bodies.py workersadmon:/tmp/reprocess_bodies.py
    docker exec -it workersadmon python /tmp/reprocess_bodies.py

Qué hace:
  1. Lee cuentas activas de HUB_MailboxCuentas (con credenciales cifradas).
  2. Descifra la contraseña IMAP de cada cuenta (Fernet + MAILBOX_ENCRYPTION_KEY).
  3. Conecta por IMAP a cada cuenta.
  4. Para cada mensaje con CuerpoGuardado=1, lee el cuerpo .gz en disco.
  5. Si el cuerpo parece MIME crudo (tiene boundaries / Content-Type multipart),
     lo vuelve a bajar con cuerpo_html() (la función ya arreglada) y lo guarda.
  6. Nunca borra registros: solo reescribe cuerpos malos.

Requiere (variables de entorno del contenedor):
  HUB_DB_SERVER, HUB_DB_USER, HUB_DB_PASSWORD, HUB_DB_DATABASE
  MAILBOX_ENCRYPTION_KEY (o MAILBOX_KEY_FILE en /data/mailbox/.mailbox_key)
  MAILBOX_DATOS_DIR (default /data/mailbox)
"""

import os, sys, gzip, hashlib, re
from datetime import datetime

# Asegurar que los módulos del worker sean importables
sys.path.insert(0, "/app")

from mailbox_worker.config import settings
from mailbox_worker.db import get_connection, filas, una, ejecuta
from mailbox_worker.almacen import leer_cuerpo, guardar_cuerpo, _cuerpo_rel
from mailbox_worker.crypto import decrypt_secret
from mailbox_worker.imap_client import IMAPClient


# ── Detectar si un cuerpo parece MIME crudo ──────────────────────────────────

_MIME_HINTES = (
    b"Content-Type: multipart",
    b"boundary=",
    b"MIME-Version",
    b"Content-Transfer-Encoding",
    b"--",
)


def cuerpo_parece_mime_crudo(html: str) -> bool:
    """True si el cuerpo parece MIME crudo en vez de HTML limpio."""
    if not html or not html.strip():
        return False
    # Un cuerpo limpio empieza con etiquetas HTML o un <pre>
    limpio = html.strip()
    if limpio.startswith(("<!DOCTYPE", "<html", "<pre", "<div", "<span", "<p", "<body")):
        return False
    crudo = html.encode("utf-8", "replace")
    for hint in _MIME_HINTES:
        if hint in crudo[:2000]:
            return True
    return False


# ── Reprocesar ───────────────────────────────────────────────────────────────

def reprocesar():
    print(f"[{datetime.now().isoformat()}] Iniciando reprocesamiento de cuerpos")

    # 1. Leer cuentas activas
    cuentas = filas(
        "SELECT Id, Alias, Email, ServidorIMAP, PuertoIMAP, TipoAuth, CredencialCifrada "
        "FROM HUB_MailboxCuentas WHERE Estado = 'ACTIVA' ORDER BY Id"
    )
    if not cuentas:
        print("No hay cuentas activas. Nada que hacer.")
        return 0

    print(f"Cuentas activas: {len(cuentas)}")

    total_reprocesados = 0
    total_revisados = 0
    total_errores = 0

    for cuenta in cuentas:
        cid = cuenta["Id"]
        email = cuenta["Email"]
        print(f"\n[{datetime.now().isoformat()}] Cuenta {cid}: {email}")

        # 2. Descifrar credencial IMAP
        try:
            password = decrypt_secret(cuenta.get("CredencialCifrada") or "")
        except Exception as exc:
            print(f"  No se pudo descifrar la credencial: {exc}")
            continue
        if not password:
            print(f"  Sin contraseña IMAP, saltando.")
            continue

        # 3. Conectar por IMAP
        host = cuenta["ServidorIMAP"]
        port = cuenta.get("PuertoIMAP") or 993
        oauth2 = (cuenta.get("TipoAuth") or "PASSWORD").upper() == "OAUTH2"

        try:
            cliente = IMAPClient(
                host=host,
                port=port,
                username=email,
                password=password,
                timeout=settings.imap_timeout,
                oauth2=oauth2,
            )
            cliente.connect()
        except Exception as exc:
            print(f"  No se pudo conectar por IMAP a {host}:{port}: {exc}")
            total_errores += 1
            continue

        try:
            # 4. Leer mensajes con cuerpo guardado de esta cuenta
            mensajes = filas(
                "SELECT Id, Carpeta, UID, ClaveCuerpo FROM HUB_MailboxMensajes "
                "WHERE IdCuenta = %s AND Eliminado = 0 AND CuerpoGuardado = 1 "
                "ORDER BY Id",
                (cid,),
            )
            print(f"  Mensajes con cuerpo guardado: {len(mensajes)}")

            for m in mensajes:
                mid = m["Id"]
                carpeta = m["Carpeta"]
                uid = m["UID"]
                clave = m["ClaveCuerpo"]

                total_revisados += 1

                # Leer cuerpo actual de disco
                html_actual = leer_cuerpo(cid, clave)

                if not cuerpo_parece_mime_crudo(html_actual):
                    continue  # Cuerpo ya está bien

                # 5. Reprocesar: bajar cuerpo limpio por IMAP
                try:
                    # Seleccionar carpeta
                    cliente.select(carpeta)
                    html_nuevo = cliente.cuerpo_html(uid)
                except Exception as exc:
                    print(f"  Mensaje {mid} (UID {uid}): error IMAP: {exc}")
                    total_errores += 1
                    continue

                if not html_nuevo:
                    print(f"  Mensaje {mid} (UID {uid}): cuerpo vacío, saltando.")
                    continue

                # 6. Guardar cuerpo limpio
                if guardar_cuerpo(cid, clave, html_nuevo):
                    total_reprocesados += 1
                    if total_reprocesados % 50 == 0:
                        print(f"    ... {total_reprocesados} reprocesados")
                else:
                    print(f"  Mensaje {mid} (UID {uid}): no se pudo guardar en disco.")
                    total_errores += 1

        finally:
            try:
                cliente.close()
            except Exception:
                pass

    print(f"\n[{datetime.now().isoformat()}] Resumen:")
    print(f"  Mensajes revisados: {total_revisados}")
    print(f"  Cuerpos reprocesados: {total_reprocesados}")
    print(f"  Errores: {total_errores}")
    return total_reprocesados


if __name__ == "__main__":
    n = reprocesar()
    sys.exit(0 if n >= 0 else 1)
