"""Sesión de Go Vale compartida entre el generador y el sincronizador.

El login pasó a Microsoft Entra y el navegador del worker arranca limpio cada
vez, así que **cada corrida vuelve a autenticarse**. Aquí se guarda la sesión en
el volumen y se reutiliza; si el archivo no sirve, el worker hace su login de
siempre (correo y contraseña).

Un solo archivo, un solo formato: el generador la guarda y el sincronizador la
consume, para que los dos vean lo mismo que ve una persona en su teléfono.
"""
import json
import os

SESION = "/data/govale_session.json"


def sesion_guardada():
    """El estado guardado, o None si no hay o no sirve.

    Se valida el JSON completo a propósito: dos procesos escriben este archivo
    (generador y sincronizador) y una escritura a medias lo dejó con dos
    objetos pegados. Playwright entonces reventaba en new_context y el worker
    moría en CADA ciclo sin llegar a hacer login. Con la validación, un archivo
    roto se ignora y el login lo reemplaza.
    """
    try:
        if not os.path.isfile(SESION) or os.path.getsize(SESION) < 100:
            return None
        with open(SESION, encoding="utf-8") as f:
            estado = json.load(f)
        if not isinstance(estado, dict) or "cookies" not in estado:
            return None
        return SESION
    except Exception as e:
        print(f"[govale_sesion] sesión guardada ilegible, se ignora: {str(e)[:120]}",
              flush=True)
        return None


def guardar(context):
    """Guarda cookies y storage (incluye el token de Entra) tras autenticarse.

    Se escribe en un temporal y se reemplaza de golpe (os.replace es atómico en
    el mismo sistema de archivos): quien lea el archivo ve la versión anterior
    completa o la nueva completa, nunca una mezcla. Antes se escribía directo
    sobre el bueno y dos workers a la vez lo dejaban con dos JSON pegados.
    """
    tmp = f"{SESION}.{os.getpid()}.tmp"
    try:
        context.storage_state(path=tmp)
        # No se pisa el bueno con algo que no se puede volver a leer.
        with open(tmp, encoding="utf-8") as f:
            json.load(f)
        os.replace(tmp, SESION)
        try:
            os.chmod(SESION, 0o600)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[govale_sesion] no se pudo guardar la sesión: {str(e)[:120]}", flush=True)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


def valida(page):
    """True si la página ya está dentro (sin formulario de acceso)."""
    try:
        if page.query_selector('input[name="username"], input[name="loginfmt"], input[type="email"]'):
            return False
        if page.query_selector('button:has-text("Ingresa")'):
            return False
        return True
    except Exception:
        return False
