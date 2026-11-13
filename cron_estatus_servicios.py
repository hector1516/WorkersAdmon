"""
cron_estatus_servicios.py — Worker de verificación periódica de servicios.

Corre cada 5 minutos. Verifica todos los servicios activos en HUB_EstatusServicios,
guarda resultados en HUB_EstatusHistorial, actualiza HUB_EstatusServicios,
detecta cambios de estatus y crea incidencias.

Corre bajo supervisor como 'estatus_servicios_worker'.
"""
import time
import sys
import os
import datetime
import json
import urllib.request
import urllib.error
import socket
import ssl
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eccsa_db as db
from tz_mx import now_mexico as now_mx

# Telegram
try:
    import telegram_alerts as tg
except Exception:
    tg = None

# Intervalo de verificación (segundos)
INTERVALO_SEG = int(os.environ.get('ESTATUS_CHECK_SEG', '300'))  # 5 min por defecto

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Estatus de Servicios worker (cada {INTERVALO_SEG}s).")

# --- Verificadores (copiados de admon/api/routers/estatus_servicios.py) ---

def _check_http(url, timeout=10):
    inicio = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ms = int((time.time() - inicio) * 1000)
            if resp.status == 200:
                return {"estatus": "verde", "detalle": f"OK ({resp.status})", "ms": ms}
            elif resp.status < 400:
                return {"estatus": "amarillo", "detalle": f"HTTP {resp.status}", "ms": ms}
            else:
                return {"estatus": "rojo", "detalle": f"HTTP {resp.status}", "ms": ms}
    except urllib.error.HTTPError as e:
        ms = int((time.time() - inicio) * 1000)
        if e.code < 500:
            return {"estatus": "amarillo", "detalle": f"HTTP {e.code}", "ms": ms}
        return {"estatus": "rojo", "detalle": f"HTTP {e.code}", "ms": ms}
    except Exception as e:
        ms = int((time.time() - inicio) * 1000)
        return {"estatus": "rojo", "detalle": str(e)[:200], "ms": ms}


def _check_tcp(host, port, timeout=10):
    inicio = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        ms = int((time.time() - inicio) * 1000)
        return {"estatus": "verde", "detalle": f"TCP OK ({host}:{port})", "ms": ms}
    except Exception as e:
        ms = int((time.time() - inicio) * 1000)
        return {"estatus": "rojo", "detalle": str(e)[:200], "ms": ms}


def _check_statuspage(url):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        componentes = []
        for comp in data.get("components", []):
            status = comp.get("status", "unknown")
            if status == "operational":
                estatus = "verde"
            elif status in ("degraded_performance", "partial_outage"):
                estatus = "amarillo"
            elif status in ("major_outage", "under_maintenance"):
                estatus = "rojo"
            else:
                estatus = "gris"
            componentes.append({"nombre": comp.get("name", ""), "estatus": estatus, "detalle": status})

        overall = data.get("status", {}).get("indicator", "none")
        if overall == "none":
            global_estatus = "verde"
        elif overall in ("minor", "major"):
            global_estatus = "amarillo"
        elif overall == "critical":
            global_estatus = "rojo"
        else:
            global_estatus = "gris"

        problemas = [c for c in componentes if c["estatus"] != "verde"]
        if problemas:
            detalle = ", ".join(f"{c['nombre']}: {c['detalle']}" for c in problemas[:3])
        else:
            detalle = "Todos los componentes operativos"
        return global_estatus, detalle
    except Exception as e:
        return "rojo", f"Error: {str(e)[:200]}"


def _check_microsoft365_rss():
    try:
        url = "https://status.office.com/feed"
        req = urllib.request.Request(url, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        items = re.findall(r"<item>(.*?)</item>", content, re.DOTALL)
        if not items:
            items = re.findall(r"<entry>(.*?)</entry>", content, re.DOTALL)

        estatus = "verde"
        detalle = "Todos los servicios operativos"
        incidencias = []

        for item in items[:10]:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", item, re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""
            title = title.replace("<![CDATA[", "").replace("]]>", "").strip()

            desc_match = re.search(r"<description[^>]*>(.*?)</description>", item, re.DOTALL)
            desc = desc_match.group(1).strip() if desc_match else ""
            desc = desc.replace("<![CDATA[", "").replace("]]>", "").strip()

            if title and "resolved" not in title.lower() and "resolved" not in desc.lower():
                estatus = "amarillo"
                incidencias.append(title)

        if incidencias:
            detalle = f"{len(incidencias)} incidencia(s) activa(s): {incidencias[0][:100]}"
        return estatus, detalle
    except Exception as e:
        return "rojo", f"Error al consultar RSS: {str(e)[:200]}"


def _verificar_servicio(nombre, categoria, url_check):
    if categoria == "microsoft":
        estatus, detalle = _check_microsoft365_rss()
        return {"estatus": estatus, "detalle": detalle, "ms": 0}
    elif categoria in ("godaddy", "cloudflare"):
        estatus, detalle = _check_statuspage(url_check)
        return {"estatus": estatus, "detalle": detalle, "ms": 0}
    elif url_check.startswith("tcp://"):
        parts = url_check.replace("tcp://", "").split(":")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 80
        return _check_tcp(host, port)
    elif url_check.startswith("http"):
        return _check_http(url_check)
    else:
        return {"estatus": "gris", "detalle": "Sin método de verificación", "ms": 0}


def _guardar_check(conn, servicio_id, resultado, estatus_anterior):
    cur = conn.cursor()
    ahora = now_mx()
    estatus = resultado["estatus"]
    detalle = resultado["detalle"]
    ms = resultado.get("ms", 0)

    cur.execute(
        "INSERT INTO HUB_EstatusHistorial (IdServicio, Estatus, Detalle, TiempoRespuestaMs, FechaCheck) "
        "VALUES (%s, %s, %s, %s, %s)",
        (servicio_id, estatus, detalle[:500], ms, ahora),
    )

    cur.execute(
        "UPDATE HUB_EstatusServicios SET UltimoCheck = %s, UltimoEstatus = %s, "
        "UltimoDetalle = %s, TiempoRespuestaMs = %s, Actualizado = %s WHERE Id = %s",
        (ahora, estatus, detalle[:500], ms, ahora, servicio_id),
    )

    if estatus_anterior and estatus_anterior != estatus:
        if estatus in ("amarillo", "rojo") and estatus_anterior == "verde":
            cur.execute(
                "INSERT INTO HUB_EstatusIncidencias (IdServicio, Titulo, Descripcion, Estatus, FechaInicio, Fuente) "
                "VALUES (%s, %s, %s, 'activa', %s, 'api')",
                (servicio_id, f"Cambio a {estatus}", detalle[:500], ahora),
            )
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Servicio {servicio_id} cambio de {estatus_anterior} a {estatus}: {detalle[:100]}")
            # Enviar alerta Telegram
            cur2 = conn.cursor(as_dict=True)
            cur2.execute("SELECT Nombre FROM HUB_EstatusServicios WHERE Id = %s", (servicio_id,))
            svc_row = cur2.fetchone()
            cur2.close()
            svc_nombre = svc_row["Nombre"] if svc_row else f"Servicio {servicio_id}"
            _enviar_alerta_telegram(svc_nombre, estatus_anterior, estatus, detalle)
        elif estatus == "verde" and estatus_anterior in ("amarillo", "rojo"):
            cur.execute(
                "UPDATE HUB_EstatusIncidencias SET Estatus = 'resuelta', FechaResolucion = %s "
                "WHERE IdServicio = %s AND Estatus = 'activa'",
                (ahora, servicio_id),
            )
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Servicio {servicio_id} recuperado a verde")
            cur2 = conn.cursor(as_dict=True)
            cur2.execute("SELECT Nombre FROM HUB_EstatusServicios WHERE Id = %s", (servicio_id,))
            svc_row = cur2.fetchone()
            cur2.close()
            svc_nombre = svc_row["Nombre"] if svc_row else f"Servicio {servicio_id}"
            _enviar_alerta_telegram(svc_nombre, estatus_anterior, estatus, detalle)

    conn.commit()
    cur.close()



def _enviar_alerta_telegram(servicio_nombre, estatus_anterior, estatus_nuevo, detalle):
    """Envia alerta por Telegram cuando un servicio cambia de estatus."""
    try:
        # Obtener chat_ids activos
        conn = db.get_connection()
        cur = conn.cursor(as_dict=True)
        cur.execute("SELECT ChatId FROM HUB_TelegramUsuarios WHERE Activo = 1")
        chat_ids = [r["ChatId"] for r in cur.fetchall() if r.get("ChatId")]
        cur.close()
        conn.close()

        if not chat_ids:
            return

        emoji = {"verde": "green", "amarillo": "yellow", "rojo": "red", "gris": "gray"}.get(estatus_nuevo, "")
        texto = f"Servicio: {servicio_nombre}\nEstatus: {estatus_anterior} -> {estatus_nuevo}\nDetalle: {detalle[:200]}"

        for cid in chat_ids:
            try:
                db.send_telegram_message(cid, texto, None, None)
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error enviando Telegram a {cid}: {e}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error en alerta Telegram: {e}")

def _verificar_todos():
    conn = db.get_connection()
    cur = conn.cursor(as_dict=True)
    cur.execute("SELECT * FROM HUB_EstatusServicios WHERE Activo = 1")
    servicios = cur.fetchall()
    cur.close()

    for svc in servicios:
        resultado = _verificar_servicio(svc["Nombre"], svc["Categoria"], svc["UrlCheck"])
        _guardar_check(conn, svc["Id"], resultado, svc.get("UltimoEstatus"))

    conn.close()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Verificación completada: {len(servicios)} servicios")


# --- Loop principal ---

while True:
    try:
        _verificar_todos()
    except Exception as err:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error en verificación: {err}")

    time.sleep(INTERVALO_SEG)