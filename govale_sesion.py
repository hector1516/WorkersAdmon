"""Sesión de Go Vale compartida entre el generador y el sincronizador.

El login pasó a Microsoft Entra y el navegador del worker arranca limpio cada
vez, así que **cada corrida vuelve a autenticarse**. Aquí se guarda la sesión en
el volumen y se reutiliza; si el archivo no sirve, el worker hace su login de
siempre (correo y contraseña).

Un solo archivo, un solo formato: el generador la guarda y el sincronizador la
consume, para que los dos vean lo mismo que ve una persona en su teléfono.
"""
import os

SESION = "/data/govale_session.json"


def sesion_guardada():
    """El estado guardado, o None si no hay (Playwright acepta None)."""
    try:
        if os.path.isfile(SESION) and os.path.getsize(SESION) > 100:
            return SESION
    except Exception:
        pass
    return None


def guardar(context):
    """Guarda cookies y storage (incluye el token de Entra) tras autenticarse."""
    try:
        context.storage_state(path=SESION)
        os.chmod(SESION, 0o600)
        return True
    except Exception as e:
        print(f"[govale_sesion] no se pudo guardar la sesión: {str(e)[:120]}", flush=True)
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
