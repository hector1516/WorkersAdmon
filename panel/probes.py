"""
panel/probes.py — Pruebas de conectividad de las notificaciones (Fase C)
========================================================================
Botones "🧪 Probar" de la pestaña Notificaciones. Cada sonda hace UNA sola
comprobación de red y devuelve (ok, mensaje); los mensajes nunca contienen
contraseñas ni tokens completos.

  probe_smtp   → envía un correo de prueba con la config guardada
  probe_ai     → valida la API key de Gemini listando modelos
  probe_push   → envía un push de prueba a las suscripciones registradas
(La sonda de Telegram vive en panel.workers.test_telegram_bot.)
"""
import json


# ── Correo SMTP ──────────────────────────────────────────────────────────────
def probe_smtp(cfg, destino):
    """Envía un correo de prueba con la configuración indicada."""
    import smtplib
    from email.mime.text import MIMEText

    servidor = (cfg.get("smtp_server") or "").strip()
    destino = (destino or "").strip()
    if not servidor:
        return False, "Falta el servidor SMTP."
    if "@" not in destino:
        return False, "Indica un correo de destino válido."

    port = int(cfg.get("port") or 465)
    remitente = (cfg.get("username") or "no-reply@ecc-sa.com.mx").strip()
    msg = MIMEText("Prueba de configuración SMTP desde el panel WorkersAdmon.\n\n"
                   "Si recibes este correo, la configuración es correcta.")
    msg["Subject"] = "Prueba SMTP · WorkersAdmon"
    msg["From"] = remitente
    msg["To"] = destino

    try:
        if cfg.get("use_ssl"):
            server = smtplib.SMTP_SSL(servidor, port, timeout=15)
        else:
            server = smtplib.SMTP(servidor, port, timeout=15)
            if cfg.get("use_tls"):
                server.starttls()
        try:
            if cfg.get("require_auth"):
                server.login(cfg.get("username") or "", cfg.get("password") or "")
            server.sendmail(remitente, [destino], msg.as_string())
        finally:
            server.quit()
    except Exception as exc:                      # noqa: BLE001
        return False, f"Error SMTP: {exc}"
    return True, f"Correo de prueba enviado a {destino}."


# ── IA (Gemini) ──────────────────────────────────────────────────────────────
def probe_ai(cfg):
    """Valida la API key listando los modelos disponibles (una sola llamada)."""
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return False, "Falta la API key de Gemini."
    try:
        import google.generativeai as genai        # mismo SDK que el HUB
    except ImportError:
        return False, "google-generativeai no está instalado en este contenedor."
    try:
        genai.configure(api_key=key)
        models = list(genai.list_models())
    except Exception as exc:                      # noqa: BLE001
        return False, f"Google rechazó la clave: {exc}"
    if not models:
        return False, "La API key no devolvió modelos."
    first = getattr(models[0], "name", "?")
    return True, f"{len(models)} modelos disponibles (p.ej. {first})."


# ── Push (Web Push) ──────────────────────────────────────────────────────────
def probe_push():
    """Envía un push de prueba a TODAS las suscripciones registradas."""
    from . import db

    cfg = db.get_push_config()
    priv = (cfg.get("private") or "").strip()
    if not priv:
        return False, "Faltan las claves VAPID (pública/privada)."
    subs = db.get_push_subscriptions()
    if not subs:
        return False, ("No hay suscripciones: alguien debe habilitar las "
                       "notificaciones en su navegador (HUB → 🔔).")
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return False, "pywebpush no está instalado en este contenedor."

    payload = json.dumps({
        "title": "Prueba WorkersAdmon",
        "body": "Push de prueba desde el panel de configuración.",
    })
    vapid_claims = {"sub": f"mailto:{(cfg.get('email') or 'robot@ecc-sa.com.mx').strip()}"}
    sent = failed = 0
    detalle = ""
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["Endpoint"],
                    "keys": {"p256dh": sub["P256dhKey"], "auth": sub["AuthKey"]},
                },
                data=payload,
                vapid_private_key=priv,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):          # suscripción caducada → se limpia
                db.remove_push_subscription(sub["Endpoint"])
            failed += 1
            detalle = f"HTTP {code}"
        except Exception as exc:            # noqa: BLE001
            failed += 1
            detalle = str(exc)

    if sent and not failed:
        return True, f"Push de prueba enviado a {sent} suscriptor(es)."
    if sent:
        return True, f"Enviados {sent}, fallaron {failed} ({detalle})."
    return False, f"No se pudo enviar ({detalle or 'revisa claves VAPID'})."
