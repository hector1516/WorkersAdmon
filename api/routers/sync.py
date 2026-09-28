from fastapi import APIRouter, Depends
from pydantic import BaseModel
from auth import require_user
from db import get_connection
from typing import Any

router = APIRouter()

# Entity display names for notifications
_ENTITY_NAMES = {
    "reporte": ("Reporte de servicio", "reportes"),
    "ticket": ("Ticket OxxoGas", "tickets"),
    "vale": ("Solicitud de vale", "vales"),
    "kilometro": ("Registro de kilómetros", "kilometros"),
    "firma": ("Firma de reporte", "reportes"),
    "remision_firma": ("Firma de remisión", "remisiones"),
    "reporte_foto": ("Foto de reporte", "reportes"),
    "ticket_foto": ("Foto de ticket", "tickets"),
}

def _resolve_id_reporte(p: dict) -> int | None:
    """
    Resuelve el IdReporte numérico a partir del payload del sync.

    Acepta `id_reporte` (numérico) o `id_reporte_local` si es un entero
    (reporte que ya existía en el server). Devuelve None si solo hay un
    UUID local pendiente de remapear en el cliente (sync.ts) — el server
    no almacena id_local de ReportesServicio, así que no puede resolverlo.
    """
    for key in ("id_reporte", "id_reporte_local"):
        val = p.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s.isdigit():
            return int(s)
    return None

def _notify_hub(entity: str, user_name: str, folio: str = None, tg: dict = None):
    """Notify HUB users (push) + Telegram si hay destinatarios configurados."""
    try:
        from routers.push import notify_all_users
        display = _ENTITY_NAMES.get(entity, (entity, ""))
        title = f"📋 Nuevo {display[0]}"
        body = f"{user_name} registró un {display[0].lower()}" + (f" ({folio})" if folio else "")
        notify_all_users(title, body)
    except Exception:
        pass
    try:
        if tg:
            import telegram_hub
            kind = tg.get("kind")
            if kind == "km":
                telegram_hub.alertar_km(tg.get("id_auto"), tg.get("km"), user_name)
            elif kind == "vale":
                telegram_hub.alertar_vale(user_name, tg.get("id_auto"), tg.get("id_cliente"), tg.get("desc", ""))
            elif kind == "ticket":
                telegram_hub.alertar_ticket_oxxogas(
                    user_name, folio, tg.get("folio_cli"), tg.get("id_auto"), tg.get("id_cliente"), tg.get("desc", ""),
                    foto_bytes=tg.get("foto_bytes"), foto_nombre=tg.get("foto_nombre"),
                    cantidad=tg.get("cantidad", ""))
            elif kind == "firma":
                telegram_hub.alertar_reporte_por_id(tg.get("id_reporte"))
    except Exception:
        pass

class SyncItem(BaseModel):
    entity: str
    action: str
    id_local: str
    payload: dict[str, Any]

@router.post("/push")
def sync_push(items: list[SyncItem], user: dict = Depends(require_user)):
    results = []
    conn = get_connection()
    with conn.cursor() as cur:
        for item in items:
            try:
                if item.entity == "kilometro":
                    p = item.payload
                    cur.execute("""
                        SELECT COUNT(*) FROM HUB_RegistroKilometros
                        WHERE IdAutomovil = %s AND CAST(FechaHora AS DATE) = CAST(%s AS DATE)
                    """, (p.get("id_auto"), p.get("fecha", "")[:10]))
                    if cur.fetchone()[0] > 0:
                        results.append({"id_local": item.id_local, "status": "duplicate"})
                        continue
                    cur.execute("""
                        INSERT INTO HUB_RegistroKilometros (IdAutomovil, Kilometros, FechaHora, IdUsuario)
                        VALUES (%s, %s, %s, %s)
                    """, (p.get("id_auto"), p.get("km"), p.get("fecha"), user["id"]))
                    conn.commit()
                    results.append({"id_local": item.id_local, "status": "ok"})
                    _notify_hub("kilometro", user["nombre"],
                                tg={"kind": "km", "id_auto": p.get("id_auto"), "km": p.get("km")})

                elif item.entity == "reporte":
                    p = item.payload
                    cur.execute("SELECT ISNULL(MAX(IdReporte), 0) + 1 FROM ReportesServicio")
                    new_id = cur.fetchone()[0]
                    folio = f"RS-{new_id:05d}"
                    cur.execute("""
                        INSERT INTO ReportesServicio
                        (Folio, Cliente, Contacto, CorreoContacto, Fecha, Tecnico,
                         DescripcionServicio, Notas, MaquinaLinea, FechaHoraInicio,
                         FechaHoraFin, TiempoTraslado, TiempoComida, Estatus)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (folio, p.get("cliente"), p.get("contacto"), p.get("correo_contacto"),
                          p.get("fecha"), user["nombre"], p.get("descripcion"),
                          p.get("notas"), p.get("maquina_linea"), p.get("fecha_inicio"),
                          p.get("fecha_fin"), p.get("tiempo_traslado", 0),
                          p.get("tiempo_comida", 0), p.get("estatus", "Borrador")))
                    conn.commit()
                    results.append({"id_local": item.id_local, "id_server": new_id, "folio": folio, "status": "ok"})
                    _notify_hub("reporte", user["nombre"], folio)

                elif item.entity == "firma":
                    p = item.payload
                    # Aceptar id_reporte numérico o id_reporte_local numérico (compat)
                    id_reporte = _resolve_id_reporte(p)
                    if not id_reporte:
                        results.append({"id_local": item.id_local, "status": "error",
                                        "message": "Reporte local aún no sincronizado"})
                        continue
                    cur.execute("""
                        UPDATE ReportesServicio SET FirmaConformidad = %s, Estatus = 'Firmado'
                        WHERE IdReporte = %s AND Tecnico = %s
                    """, (p.get("firma_base64"), id_reporte, user["nombre"]))
                    if cur.rowcount == 0:
                        results.append({"id_local": item.id_local, "status": "error", "message": "Sin permiso para firmar"})
                        continue
                    conn.commit()
                    results.append({"id_local": item.id_local, "status": "ok"})
                    _notify_hub("firma", user["nombre"],
                                tg={"kind": "firma", "id_reporte": id_reporte})

                elif item.entity == "remision_firma":
                    p = item.payload
                    # Solo el usuario asignado puede firmar; rowcount 0 = sin permiso
                    cur.execute("""
                        UPDATE IndiceRemisiones
                        SET FirmaConformidad = %s, FechaFirma = GETDATE()
                        WHERE IdRemision = %s AND IdUsuarioAsignado = %s
                    """, (p.get("firma_base64"), p.get("id_remision"), user["id"]))
                    if cur.rowcount == 0:
                        results.append({"id_local": item.id_local, "status": "error",
                                        "message": "Sin permiso para firmar"})
                        continue
                    conn.commit()
                    results.append({"id_local": item.id_local, "status": "ok"})
                    _notify_hub("remision_firma", user["nombre"],
                                folio=str(p.get("id_remision") or ""))

                elif item.entity == "ticket":
                    p = item.payload
                    cur.execute("SELECT ISNULL(MAX(Id), 0) + 1 FROM HUB_OxxoGasTickets")
                    new_id = cur.fetchone()[0]
                    # Usar el folio real del ticket (manual o IA); solo fallback TK-##### si vacío
                    folio_real = (p.get("folio_ticket") or "").strip()
                    folio = folio_real if folio_real else f"TK-{new_id:05d}"
                    # Estación (obligatoria en nuevo flujo)
                    estacion_val = (p.get("estacion") or "").strip()
                    # Decodificar foto si viene en base64
                    foto_bytes = None
                    foto_nombre = None
                    if p.get("foto_base64"):
                        try:
                            import base64
                            foto_bytes = base64.b64decode(p["foto_base64"])
                            foto_nombre = p.get("foto_nombre", "ticket.jpg")
                        except Exception:
                            pass
                    cur.execute("""
                        INSERT INTO HUB_OxxoGasTickets
                        (FolioTicket, Estacion, IdVehiculo, IdCliente, Descripcion, IdUsuario, ImagenTicket, ImagenNombre)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (folio, estacion_val, p.get("id_vehiculo"), p.get("id_cliente"),
                          p.get("descripcion", ""), user["id"], foto_bytes, foto_nombre))
                    conn.commit()
                    results.append({"id_local": item.id_local, "id_server": new_id, "folio": folio, "status": "ok"})
                    _notify_hub("ticket", user["nombre"], folio,
                                tg={"kind": "ticket", "folio_cli": p.get("folio_ticket"),
                                    "id_auto": p.get("id_vehiculo"), "id_cliente": p.get("id_cliente"),
                                    "estacion": estacion_val,
                                    "desc": p.get("descripcion", ""),
                                    "cantidad": p.get("cantidad") or p.get("litros") or "",
                                    "foto_bytes": foto_bytes, "foto_nombre": foto_nombre})

                elif item.entity == "vale":
                    p = item.payload
                    cur.execute("""
                        SELECT COUNT(*) FROM HUB_SolicitudVales
                        WHERE IdAutomovil = %s
                          AND CAST(FechaSolicitud AS DATE) = CAST(GETDATE() AS DATE)
                          AND Estatus IN ('PENDIENTE', 'APROBADO', 'GENERADO')
                    """, (p.get("id_automovil"),))
                    if cur.fetchone()[0] > 0:
                        results.append({"id_local": item.id_local, "status": "duplicate", "message": "Ya tiene vale hoy"})
                        continue

                    # Validate km >= current
                    km_val = p.get("kilometros")
                    if km_val is not None and km_val > 0:
                        cur.execute("""
                            SELECT ISNULL((SELECT TOP 1 k.Kilometros FROM HUB_RegistroKilometros k
                                           WHERE k.IdAutomovil = %s
                                           ORDER BY k.FechaHora DESC, k.Id DESC), 0)
                        """, (p.get("id_automovil", 0),))
                        kms_registro = cur.fetchone()[0]
                        cur.execute("""
                            SELECT ISNULL((SELECT TOP 1 Kilometros FROM HUB_SolicitudVales
                                           WHERE IdAutomovil = %s AND Kilometros IS NOT NULL AND Kilometros > 0
                                           ORDER BY FechaSolicitud DESC, Id DESC), 0)
                        """, (p.get("id_automovil", 0),))
                        kms_vale = cur.fetchone()[0]
                        kms_actuales = max(kms_registro, kms_vale)
                        if kms_actuales > 0 and km_val < kms_actuales:
                            results.append({"id_local": item.id_local, "status": "km_invalid", "message": f"Km menores a {kms_actuales}"})
                            continue

                    cur.execute("SELECT Placas FROM HUB_Automoviles WHERE Id = %s", (p.get("id_automovil"),))
                    auto_row = cur.fetchone()
                    placa = auto_row[0] if auto_row else ''

                    cur.execute("""
                        INSERT INTO HUB_SolicitudVales
                        (IdSolicitante, IdAutomovil, IdCliente, Placa, Descripcion, Cantidad, MontoUnit, Notas, Kilometros)
                        VALUES (%s, %s, %s, %s, %s, 1, 500.00, %s, %s);
                        SELECT SCOPE_IDENTITY();
                    """, (user["id"], p.get("id_automovil"), p.get("id_cliente"),
                          placa.strip(), p.get("descripcion", ""), p.get("notas", ""),
                          p.get("kilometros")))
                    new_id = int(cur.fetchone()[0])
                    conn.commit()
                    folio = f"VL-{new_id:05d}"
                    results.append({"id_local": item.id_local, "id_server": new_id, "folio": folio, "status": "ok"})
                    _notify_hub("vale", user["nombre"], folio,
                                tg={"kind": "vale", "id_auto": p.get("id_automovil"),
                                    "id_cliente": p.get("id_cliente"), "desc": p.get("descripcion", "")})

                elif item.entity == "reporte_foto":
                    p = item.payload
                    # Aceptar id_reporte numérico o id_reporte_local numérico (compat).
                    # UUID sin resolver → reintenta tras remapeo en el cliente (sync.ts).
                    id_reporte = _resolve_id_reporte(p)
                    if not id_reporte:
                        results.append({"id_local": item.id_local, "status": "error",
                                        "message": "Reporte local aún no sincronizado"})
                        continue
                    # Verify report exists
                    cur.execute("SELECT IdReporte FROM ReportesServicio WHERE IdReporte = %s", (id_reporte,))
                    if not cur.fetchone():
                        results.append({"id_local": item.id_local, "status": "error", "message": "Reporte no existe en servidor"})
                        continue
                    foto_bytes = None
                    if p.get("foto_base64"):
                        try:
                            import base64
                            raw = p["foto_base64"]
                            if raw.startswith("data:"):
                                raw = raw.split(",", 1)[1]
                            foto_bytes = base64.b64decode(raw)
                        except Exception:
                            pass
                    if not foto_bytes:
                        results.append({"id_local": item.id_local, "status": "error", "message": "Sin foto"})
                        continue
                    # Comprimir antes de INSERTar (disco SQL Server)
                    try:
                        from routers.reportes import _compress_photo
                        foto_bytes = _compress_photo(foto_bytes)
                    except Exception:
                        pass
                    orden = p.get("orden", 0)
                    cur.execute("""
                        INSERT INTO ReportesServicioFotos (IdReporte, FotoComprimida, Orden)
                        VALUES (%s, %s, %s)
                    """, (id_reporte, foto_bytes, orden))
                    conn.commit()
                    results.append({"id_local": item.id_local, "status": "ok"})

                else:
                    results.append({"id_local": item.id_local, "status": "unknown_entity"})

            except Exception as e:
                conn.rollback()
                results.append({"id_local": item.id_local, "status": "error", "message": str(e)})

    # sync_completado eliminado: cada push otorgaba +1 aunque el usuario
    # no hiciera nada útil (la app sincroniza en segundo plano).
    return {"results": results}

@router.get("/pull")
def sync_pull(user: dict = Depends(require_user)):
    conn = get_connection()
    with conn.cursor(as_dict=True) as cur:
        # Vehicles: only user's assigned + unassigned
        cur.execute("""
            SELECT Id, MarcaModelo, Placas, IdUsuarioAsignado
            FROM HUB_Automoviles
            WHERE IdUsuarioAsignado = %s OR IdUsuarioAsignado IS NULL
            ORDER BY MarcaModelo
        """, (user["id"],))
        vehiculos = cur.fetchall()
        cur.execute("SELECT IdCliente, Cliente FROM clientes ORDER BY Cliente")
        clientes = cur.fetchall()
        cur.execute("SELECT Id, Nombre FROM HUB_Users WHERE Activo = 1 ORDER BY Nombre")
        usuarios = cur.fetchall()
    return {
        "vehiculos": vehiculos,
        "clientes": clientes,
        "usuarios": usuarios,
    }
