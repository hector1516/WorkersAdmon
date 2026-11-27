"""cron_vales_recordatorios.py — le recuerda AL USUARIO que suba su ticket.

Regla de negocio: un vale emitido sin ticket no está cerrado. La foto la tiene
el usuario, así que el aviso va a él (no a los administradores): su Telegram si
lo tiene vinculado y, si no, queda en el log para que el vigilante lo cuente.

Recordatorios a los 1, 3 y 7 días. Después deja de insistir: ya lo reporta el
vigilante al admin como pendiente viejo.

Tope de edad: un vale con más de `govale_recordatorio_dias_max` días desde
`FechaSolicitud` ya no recibe recordatorios (default 15). Son tickets que no se
van a subir y el aviso solo molesta; el pendiente sigue contado por el vigilante.
"""
import json
import os
import time
from datetime import datetime

import eccsa_db as db

NOMBRE = "vales_recordatorios"
CICLO_SEG = int(os.getenv("RECORDATORIOS_CICLO_SEG", "3600"))  # 1 h
# Días desde la emisión en que se avisa. El primero es el más importante.
HITOS = [1, 3, 7]
# Edad máxima para seguir recordando (configurable en HUB_Config).
DIAS_MAX_DEFECTO = 15

try:
    from worker_log import log as wlog
except Exception:  # pragma: no cover
    def wlog(*a, **k):
        pass


def _cfg(clave, defecto=None):
    try:
        return db.get_govale_config(clave) or defecto
    except Exception:
        return defecto


def _cfg_set(clave, valor):
    try:
        db.set_govale_config(clave, str(valor))
    except Exception:
        pass


def _dias_max():
    """Edad máxima para recordar. Ilegible = default (nunca deja el worker mudo)."""
    try:
        return int(float(_cfg("govale_recordatorio_dias_max", DIAS_MAX_DEFECTO)))
    except (TypeError, ValueError):
        return DIAS_MAX_DEFECTO


def _texto(vale):
    """Mensaje para el usuario: corto, sin tecnicismos y sin culpar."""
    folio = vale.get("IdValeGoVale") or ""
    monto = float(vale.get("MontoEmitido") or vale.get("MontoUnit") or 0)
    return (
        "Tu vale ya se usó y falta la foto del ticket\n\n"
        f"Folio {folio} · ${monto:,.0f} · {vale.get('Placa') or ''}\n\n"
        "Súbela desde la app (Tickets → Nuevo). Sin la foto el vale no se puede "
        "cerrar y en contabilidad queda como pendiente.\n\n"
        "— Volt"
    )


def _pendientes():
    """Vales emitidos sin ticket, con los días que llevan esperando."""
    conn = db.get_connection()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("""
            SELECT S.Id, S.IdSolicitante, S.FechaSolicitud, S.Placa,
                   S.MontoUnit, S.MontoEmitido, S.IdValeGoVale,
                   U.Nombre AS Solicitante,
                   DATEDIFF(day, S.FechaSolicitud, GETDATE()) AS dias
            FROM HUB_SolicitudVales S
            LEFT JOIN HUB_Users U ON U.Id = S.IdSolicitante
            WHERE S.Estatus IN ('GENERADO', 'CONSUMIDO')
              AND S.IdTicketCierre IS NULL
              AND S.FechaSolicitud >= CONVERT(date, %s)
            ORDER BY S.FechaSolicitud
        """, (_cfg("vigilante_desde", "2026-11-24"),))
        return cur.fetchall()
    finally:
        conn.close()


def _chat_del_usuario(id_usuario):
    """El chat de Telegram del usuario, si lo tiene vinculado."""
    conn = db.get_connection()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("""
            SELECT TOP 1 ChatId FROM HUB_TelegramUsuarios
            WHERE IdUsuario = %s AND Activo = 1
        """, (id_usuario,))
        fila = cur.fetchone()
        return int(fila["ChatId"]) if fila else None
    finally:
        conn.close()


def ciclo():
    enviados = 0
    dias_max = _dias_max()
    for vale in _pendientes():
        dias = int(vale["dias"] or 0)
        # Un vale demasiado viejo ya no se va a comprobar: insistir solo molesta.
        if dias > dias_max:
            continue
        # El hito que corresponde a su edad (el mayor que ya cumplió).
        hito = max([h for h in HITOS if dias >= h], default=None)
        if hito is None:
            continue
        marca = f"vale_recordatorio_{vale['Id']}"
        if _cfg(marca) == str(hito):
            continue  # ya se le avisó en este hito

        chat = _chat_del_usuario(vale["IdSolicitante"])
        texto = _texto(vale)
        if chat:
            try:
                db.queue_telegram_alerta("VALE_QR_GENERADO", chat, texto[:4000])
                enviados += 1
            except Exception as e:
                print(f"[recordatorios] no se pudo encolar a {chat}: {e}", flush=True)
        else:
            # Sin Telegram: el recordatorio queda en el log, y el vigilante lo
            # cuenta en el reporte de pendientes. No se inventa un canal.
            wlog(NOMBRE, f"Vale #{vale['Id']} sin ticket y el usuario no tiene Telegram",
                 nivel="WARN",
                 detalle=f"{vale['Solicitante']} · {dias} día(s) · folio {vale['IdValeGoVale']}")

        _cfg_set(marca, hito)
        wlog(NOMBRE, f"Recordatorio de ticket (día {hito}) al vale #{vale['Id']}",
             detalle=f"chat={chat} · {vale['Solicitante']}")

    if enviados:
        print(f"[recordatorios] {enviados} recordatorio(s) enviados", flush=True)
    return enviados


def main():
    print(f"[recordatorios] Iniciado (cada {CICLO_SEG}s, hitos {HITOS} días)", flush=True)
    try:
        wlog(NOMBRE, "Arrancó")
    except Exception:
        pass
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"[recordatorios] error en el ciclo: {str(e)[:200]}", flush=True)
        time.sleep(CICLO_SEG)


if __name__ == "__main__":
    main()
