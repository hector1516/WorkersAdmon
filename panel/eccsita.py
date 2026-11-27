"""
panel/eccsita.py — almacenamiento y cronica basica del grupo ECCSita
====================================================================
Un solo dueno de las tablas del modulo (migracion 0071):

  * `guardar_mensaje()` la llama `panel/wa_webhook.py` cuando el mensaje llega
    del grupo configurado en `HUB_Config.wa_grupo_eccsita_id`;
  * `listar_semanas()`, `detalle_semana()`, `generar_basico()` y
    `marcar_revisada()` las usa `panel/api_eccsita.py`.

La semana es ISO y se calcula con la `Fecha` en hora de Mexico (`tz_mx`), que
es la misma que guarda el webhook. Este modulo arma la cronica determinista
(`GeneradoPor='BASICO'`); la version con IA vive en `panel/eccsita_cronica.py`,
que reusa `_guardar_semana()` para escribir (`GeneradoPor='IA'`).

`enviar_aviso()` manda UNA vez el aviso de consentimiento al grupo (lo dispara
`POST /api/panel/eccsita/aviso`); el dedupe vive en
`HUB_Config.wa_grupo_aviso_enviado`.

Reglas:

  * La captura nunca lanza hacia el webhook: OpenWA necesita su 200.
  * El dedupe es por `(ChatId, IdMensaje)`, igual que los chats individuales.
  * Las fechas de la semana salen de `date.fromisocalendar`, no de cuentas a
    mano, para que el lunes sea el lunes tambien en la semana 1 y en la 53.
"""
import json
import logging
from datetime import date, timedelta

from tz_mx import now_mx

from . import db

log = logging.getLogger("panel.eccsita")

CLAVE_GRUPO = "wa_grupo_eccsita_id"
CLAVE_CAPTURA = "wa_grupo_captura"
CLAVE_AVISO = "wa_grupo_aviso_consentimiento"
CLAVE_AVISO_ENVIADO = "wa_grupo_aviso_enviado"

# Aviso fijo que el grupo recibe UNA vez: el texto no se configura desde el
# panel a proposito, para que nadie lo reescriba y deje de avisar.
AVISO_CONSENTIMIENTO = (
    "Este grupo guarda la conversación para hacer la crónica semanal. "
    "Si no quieres aparecer, dilo y se borra tu parte."
)

CLAVES_CONFIG = (
    CLAVE_GRUPO,
    CLAVE_CAPTURA,
    "wa_grupo_resumen",
    "wa_grupo_resumen_dia",
    "wa_grupo_resumen_hora",
    "wa_grupo_retencion_dias",
    "wa_grupo_ia",
    "wa_grupo_semana_dia",
    "wa_grupo_semana_hora",
    CLAVE_AVISO,
    CLAVE_AVISO_ENVIADO,
)

MAX_LIMITE = 52
MIN_CITA = 30          # largo minimo para que un mensaje cuente como cita

DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado",
        "domingo")


# ─── configuracion ───────────────────────────────────────────────────────────
def _si(valor, defecto=False):
    if valor is None:
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "on", "yes")
    return bool(valor)


def config():
    """Claves de ECCSita en HUB_Config, con sus defaults."""
    valores = db.get_config_values(CLAVES_CONFIG) or {}
    return {
        "grupo": (valores.get(CLAVE_GRUPO) or "").strip(),
        "captura": _si(valores.get(CLAVE_CAPTURA), True),
        "resumen": _si(valores.get("wa_grupo_resumen"), True),
        "resumen_dia": valores.get("wa_grupo_resumen_dia") or "1",
        "resumen_hora": valores.get("wa_grupo_resumen_hora") or "09:00",
        "retencion_dias": valores.get("wa_grupo_retencion_dias") or "365",
        # IA de la cronica: encendida por omision solo si hay ApiKey y modelo
        # (lo comprueba `eccsita_cronica.ia_disponible`).
        "ia": _si(valores.get("wa_grupo_ia"), True),
        "semana_dia": valores.get("wa_grupo_semana_dia") or "1",
        "semana_hora": valores.get("wa_grupo_semana_hora") or "09:00",
        # Aviso de consentimiento: encendido por omision y guarda la fecha en
        # que se mando (vacio = todavia no se manda).
        "aviso": _si(valores.get(CLAVE_AVISO), True),
        "aviso_enviado": (valores.get(CLAVE_AVISO_ENVIADO) or "").strip(),
    }


def enviar_aviso():
    """
    Manda UNA sola vez el aviso de consentimiento al grupo.

    No se manda si falta el grupo, si WhatsApp esta apagado
    (`canales.activo("whatsapp")`), si el aviso esta apagado o si
    `wa_grupo_aviso_enviado` ya tiene fecha. Devuelve (ok, mensaje|error).

    La clave guarda solo la fecha (no el grupo): para volver a avisar a otro
    grupo hay que borrarla a mano, nunca se manda solo.
    """
    cfg = config()
    grupo = cfg.get("grupo") or ""
    if not grupo:
        return False, "Primero elige el grupo de ECCSita."
    if cfg.get("aviso_enviado"):
        return False, "El aviso de consentimiento ya se mandó a ese grupo."
    if not cfg.get("aviso"):
        return False, ("El aviso de consentimiento está apagado "
                       "(wa_grupo_aviso_consentimiento = 0).")

    try:
        import canales
        whatsapp = canales.activo("whatsapp")
    except Exception as exc:
        # Igual que `canales`: un fallo de lectura no deja mudo al sistema.
        log.warning("no se pudo leer el canal de WhatsApp: %s", exc)
        whatsapp = True
    if not whatsapp:
        return False, "WhatsApp está apagado; el aviso no se mandó."

    try:
        from openwa_client import enviar_texto
        ok, detalle = enviar_texto(grupo, AVISO_CONSENTIMIENTO)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if not ok:
        return False, f"No se pudo enviar el aviso: {detalle}"

    guardado, error = db.set_config_values({
        CLAVE_AVISO_ENVIADO: now_mx().isoformat(timespec="seconds")})
    if not guardado:
        # El aviso SI salio: se avisa del riesgo de repetirlo, no se miente.
        log.warning("aviso enviado pero no se pudo marcar: %s", error)
        return True, ("El aviso salió, pero no se pudo marcar como enviado "
                      f"({error}); revísalo antes de volver a dispararlo.")
    return True, "Aviso de consentimiento enviado."


def set_grupo(chat_id):
    """Guarda el chat del grupo. Devuelve (ok, error)."""
    return db.set_config_values({CLAVE_GRUPO: chat_id})


# ─── captura ─────────────────────────────────────────────────────────────────
def semana_iso(fecha):
    """(anio, semana) ISO de una fecha en hora de Mexico."""
    if fecha is None:
        fecha = now_mx()
    anio, semana, _dia = fecha.isocalendar()
    return anio, semana


def guardar_mensaje(datos, crudo):
    """
    Inserta un mensaje del grupo. Devuelve "guardado" o "duplicado".

    `datos` trae chat_id, id_mensaje, autor_id, autor_nombre, tipo, texto y
    fecha ya normalizados por el webhook.
    """
    fecha = datos.get("fecha") or now_mx()
    anio, semana = semana_iso(fecha)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM HUB_GrupoMensajes "
                "WHERE ChatId = %s AND IdMensaje = %s",
                (datos["chat_id"], datos["id_mensaje"]))
            if cur.fetchone():
                return "duplicado"

            cur.execute(
                "INSERT INTO HUB_GrupoMensajes "
                "(ChatId, IdMensaje, AutorId, AutorNombre, Tipo, Texto, "
                " Fecha, Semana, Anio, Crudo) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (datos["chat_id"], datos["id_mensaje"], datos.get("autor_id"),
                 datos.get("autor_nombre"), datos.get("tipo"),
                 datos.get("texto"), fecha, semana, anio,
                 json.dumps(crudo, ensure_ascii=False)))
        conn.commit()
        return "guardado"
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        # 2601/2627: el indice unico atrapo una segunda entrega simultanea.
        mensaje = str(exc)
        if "2601" in mensaje or "2627" in mensaje:
            return "duplicado"
        raise
    finally:
        conn.close()


# ─── lectura ─────────────────────────────────────────────────────────────────
def _iso(valor):
    """datetime/date -> ISO; cadena tal cual; None se queda None."""
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        try:
            return valor.isoformat(timespec="seconds")
        except TypeError:
            return valor.isoformat()
    return str(valor)


def _semana_fechas(anio, semana):
    """(lunes, domingo) de la semana ISO, o (None, None) si no existe."""
    try:
        inicio = date.fromisocalendar(int(anio), int(semana), 1)
    except (TypeError, ValueError):
        return None, None
    return inicio, inicio + timedelta(days=6)


def _lista(valor):
    """Columna JSON -> lista (nunca revienta por un texto suelto)."""
    if isinstance(valor, (list, tuple)):
        return list(valor)
    if not valor:
        return []
    try:
        datos = json.loads(valor)
    except (TypeError, ValueError):
        return []
    return datos if isinstance(datos, list) else []


def _texto_json(valor):
    return json.dumps(valor if valor is not None else [], ensure_ascii=False)


def _fila_semana(chat_id, anio, semana):
    """La fila de HUB_GrupoSemanas, o None."""
    filas = db._rows(
        "SELECT ChatId, Anio, Semana, Inicio, Fin, Mensajes, Participantes, "
        "       Resumen, Temas, Personajes, Momentos, Estado, GeneradoPor, "
        "       FechaGeneracion "
        "FROM HUB_GrupoSemanas "
        "WHERE ChatId = %s AND Anio = %s AND Semana = %s",
        (chat_id, int(anio), int(semana)))
    return dict(filas[0]) if filas else None


def _mensajes_semana(chat_id, anio, semana):
    """Mensajes de la semana en orden cronologico."""
    return list(db._rows(
        "SELECT AutorId, AutorNombre, Tipo, Texto, Fecha "
        "FROM HUB_GrupoMensajes "
        "WHERE ChatId = %s AND Anio = %s AND Semana = %s "
        "ORDER BY Fecha, Id",
        (chat_id, int(anio), int(semana))) or [])


def listar_semanas(chat_id, limite=MAX_LIMITE):
    """
    Semanas con mensajes o con cronica, la mas reciente primero.

    Se cruzan las dos tablas con FULL OUTER JOIN: una semana capturada aparece
    aunque nadie la haya resumido, y una semana resumida sigue en la lista
    aunque la purga ya le haya borrado sus mensajes.
    """
    filas = db._rows(
        "SELECT COALESCE(m.Anio, s.Anio) AS Anio, "
        "       COALESCE(m.Semana, s.Semana) AS Semana, "
        "       COALESCE(m.Mensajes, s.Mensajes, 0) AS Mensajes, "
        "       COALESCE(m.Participantes, s.Participantes, 0) AS Participantes, "
        "       s.Inicio AS Inicio, s.Fin AS Fin, s.Estado AS Estado "
        "FROM (SELECT Anio, Semana, COUNT(*) AS Mensajes, "
        "             COUNT(DISTINCT AutorId) AS Participantes "
        "      FROM HUB_GrupoMensajes WHERE ChatId = %s "
        "      GROUP BY Anio, Semana) m "
        "FULL OUTER JOIN HUB_GrupoSemanas s "
        "  ON s.ChatId = %s AND s.Anio = m.Anio AND s.Semana = m.Semana "
        "ORDER BY Anio DESC, Semana DESC",
        (chat_id, chat_id))

    limite = max(1, min(int(limite or MAX_LIMITE), MAX_LIMITE))
    items = []
    for fila in (filas or [])[:limite]:
        anio, semana = fila.get("Anio"), fila.get("Semana")
        inicio, fin = _semana_fechas(anio, semana)
        items.append({
            "anio": anio,
            "semana": semana,
            "inicio": _iso(fila.get("Inicio") or inicio),
            "fin": _iso(fila.get("Fin") or fin),
            "mensajes": int(fila.get("Mensajes") or 0),
            "participantes": int(fila.get("Participantes") or 0),
            "estado": (fila.get("Estado") or "CAPTURADA"),
        })
    return items


def _personas(mensajes):
    """[{nombre, mensajes}] con el conteo por autor, de mayor a menor."""
    cuentas = {}
    for m in mensajes:
        nombre = (m.get("AutorNombre") or m.get("AutorId") or "(sin nombre)")
        nombre = str(nombre).strip() or "(sin nombre)"
        cuentas[nombre] = cuentas.get(nombre, 0) + 1
    return [{"nombre": nombre, "mensajes": total}
            for nombre, total in sorted(cuentas.items(),
                                        key=lambda p: (-p[1], p[0].lower()))]


def _detalle(chat_id, anio, semana, fila, mensajes):
    """Forma publica de una semana: cabecera + mensajes + cronica."""
    inicio, fin = _semana_fechas(anio, semana)
    fila = fila or {}
    return {
        "anio": int(anio),
        "semana": int(semana),
        "inicio": _iso(fila.get("Inicio") or inicio),
        "fin": _iso(fila.get("Fin") or fin),
        "total": len(mensajes) or int(fila.get("Mensajes") or 0),
        "participantes": (len({(m.get("AutorId") or m.get("AutorNombre")
                                or "(sin nombre)") for m in mensajes})
                          or int(fila.get("Participantes") or 0)),
        "estado": fila.get("Estado") or ("CAPTURADA" if mensajes else "VACIA"),
        "generadoPor": fila.get("GeneradoPor") or None,
        "fechaGeneracion": _iso(fila.get("FechaGeneracion")),
        "resumen": fila.get("Resumen") or "",
        "temas": _lista(fila.get("Temas")),
        "personajes": _lista(fila.get("Personajes")),
        "momentos": _lista(fila.get("Momentos")),
        "mensajes": [{
            "autor": (m.get("AutorNombre") or m.get("AutorId") or ""),
            "texto": m.get("Texto") or "",
            "tipo": m.get("Tipo") or "text",
            "fecha": _iso(m.get("Fecha")),
        } for m in mensajes],
        # `personas` es el conteo crudo de la semana, siempre calculado.
        "personas": _personas(mensajes),
    }


def detalle_semana(chat_id, anio, semana):
    """La semana con sus mensajes. None si no hay ni mensajes ni cronica."""
    fila = _fila_semana(chat_id, anio, semana)
    mensajes = _mensajes_semana(chat_id, anio, semana)
    if not fila and not mensajes:
        return None
    return _detalle(chat_id, anio, semana, fila, mensajes)


# ─── cronica determinista (sin IA) ───────────────────────────────────────────
def _por_dia(mensajes):
    """{fecha: [mensajes]} ordenado por fecha."""
    dias = {}
    for m in mensajes:
        fecha = m.get("Fecha")
        dia = fecha.date() if hasattr(fecha, "date") else fecha
        dias.setdefault(dia, []).append(m)
    return dict(sorted(dias.items(), key=lambda d: d[0]))


def _nombre(m):
    return (m.get("AutorNombre") or m.get("AutorId") or "(sin nombre)").strip()


def _texto(m):
    return str(m.get("Texto") or "").strip()


def _citas(mensajes):
    """Mensajes con texto util, el mas largo primero (top 5)."""
    utiles = [m for m in mensajes
              if _texto(m) and len(_texto(m)) >= MIN_CITA]
    utiles.sort(key=lambda m: (-len(_texto(m)), m.get("Fecha") or now_mx(),
                               _nombre(m)))
    return utiles[:5]


def resumen_basico(mensajes, anio, semana):
    """
    Cronica determinista: por dia quien hablo y la cita mas larga; luego las
    citas mas largas de la semana y el conteo por persona.

    Devuelve (resumen, temas, personajes, momentos) listos para guardar.
    """
    inicio, fin = _semana_fechas(anio, semana)
    rango = (f" ({inicio.strftime('%d/%m')} al {fin.strftime('%d/%m')})"
             if inicio and fin else "")
    personas = _personas(mensajes)
    lineas = [f"Semana {semana} de {anio}{rango}",
              f"{len(mensajes)} mensajes de {len(personas)} personas.", ""]

    for dia, del_dia in _por_dia(mensajes).items():
        etiqueta = (f"{DIAS[dia.weekday()].capitalize()} "
                    f"{dia.strftime('%d/%m')}" if hasattr(dia, "weekday")
                    else str(dia))
        cuentas = _personas(del_dia)
        quienes = ", ".join(f"{p['nombre']} ({p['mensajes']})"
                            for p in cuentas)
        lineas.append(etiqueta)
        lineas.append(f"Hablaron: {quienes}.")
        largo = max(del_dia, key=lambda m: len(_texto(m)))
        if _texto(largo):
            lineas.append(f"Lo mas largo: «{_texto(largo)[:400]}» "
                          f"— {_nombre(largo)}.")
        lineas.append("")

    citas = _citas(mensajes)
    if citas:
        lineas.append("Citas de la semana")
        for m in citas:
            fecha = m.get("Fecha")
            dia = (fecha.strftime("%d/%m") if hasattr(fecha, "strftime")
                   else "")
            lineas.append(f"· «{_texto(m)[:500]}» — {_nombre(m)} ({dia})")
        lineas.append("")

    lineas.append("Conteo por persona")
    for p in personas:
        lineas.append(f"· {p['nombre']}: {p['mensajes']} mensajes")

    momentos = [{
        "autor": _nombre(m),
        "fecha": _iso(m.get("Fecha")),
        "texto": _texto(m)[:1000],
    } for m in citas]

    return ("\n".join(lineas).strip(), [], personas, momentos)


def _guardar_semana(chat_id, anio, semana, mensajes, participantes,
                    resumen, temas, personajes, momentos,
                    generado_por="BASICO"):
    """MERGE de la cronica en HUB_GrupoSemanas (`generado_por`: BASICO | IA)."""
    inicio, fin = _semana_fechas(anio, semana)
    db._execute(
        "MERGE HUB_GrupoSemanas AS t "
        "USING (SELECT %s AS ChatId, %s AS Anio, %s AS Semana) AS s "
        "  ON t.ChatId = s.ChatId AND t.Anio = s.Anio AND t.Semana = s.Semana "
        "WHEN MATCHED THEN UPDATE SET Inicio = %s, Fin = %s, Mensajes = %s, "
        "  Participantes = %s, Resumen = %s, Temas = %s, Personajes = %s, "
        "  Momentos = %s, Estado = 'RESUMIDA', GeneradoPor = %s, "
        "  FechaGeneracion = %s "
        "WHEN NOT MATCHED THEN INSERT "
        "  (ChatId, Anio, Semana, Inicio, Fin, Mensajes, Participantes, "
        "   Resumen, Temas, Personajes, Momentos, Estado, GeneradoPor, "
        "   FechaGeneracion) "
        "  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'RESUMIDA', "
        "          %s, %s);",
        (chat_id, int(anio), int(semana), inicio, fin, len(mensajes),
         participantes, resumen, _texto_json(temas),
         _texto_json(personajes), _texto_json(momentos), generado_por,
         now_mx(),
         chat_id, int(anio), int(semana), inicio, fin, len(mensajes),
         participantes, resumen, _texto_json(temas),
         _texto_json(personajes), _texto_json(momentos), generado_por,
         now_mx()))


def generar_basico(chat_id, anio, semana):
    """Arma y guarda la cronica determinista. None si la semana no existe."""
    mensajes = _mensajes_semana(chat_id, anio, semana)
    if not mensajes:
        return None
    resumen, temas, personajes, momentos = resumen_basico(mensajes, anio,
                                                          semana)
    participantes = len({(m.get("AutorId") or m.get("AutorNombre")
                          or "(sin nombre)") for m in mensajes})
    _guardar_semana(chat_id, anio, semana, mensajes, participantes, resumen,
                    temas, personajes, momentos)
    fila = {
        "Estado": "RESUMIDA",
        "GeneradoPor": "BASICO",
        "FechaGeneracion": now_mx(),
        "Resumen": resumen,
        "Temas": _texto_json(temas),
        "Personajes": _texto_json(personajes),
        "Momentos": _texto_json(momentos),
    }
    return _detalle(chat_id, anio, semana, fila, mensajes)


def marcar_revisada(chat_id, anio, semana):
    """Estado = REVISADA. None si la semana no tiene cronica."""
    filas = db._execute(
        "UPDATE HUB_GrupoSemanas SET Estado = 'REVISADA' "
        "WHERE ChatId = %s AND Anio = %s AND Semana = %s",
        (chat_id, int(anio), int(semana)))
    if not filas:
        return None
    return detalle_semana(chat_id, anio, semana)
