"""
eccsita_perfiles.py — el roast por persona que sale del grupo ECCSita
====================================================================

`extraer(conn, mensaje)` se llama desde la captura del grupo
(`panel/wa_webhook.py`) DESPUES de guardar el mensaje en `HUB_GrupoMensajes`.
Busca los alias de cada usuario registrado dentro del texto y guarda la ORACION
donde aparece, en `HUB_GrupoPerfiles` (migracion 0072).

Alias de una persona, comparados en minusculas y sin acentos, como palabra
completa:

  · el primer nombre de `HUB_Users.Nombre`;
  · el nombre completo;
  · lo que diga `HUB_Config.wa_grupo_alias`, un JSON opcional
    `{"2":["hector","hech"], ...}` (IdUsuario -> lista de alias).

`alias_lista(conn)` y `guardar_alias(conn, id_usuario, alias)` son lo que edita
el panel (`/api/panel/eccsita/alias`): la clave se guarda completa y cada alias
necesita 3+ letras, sin repetirse entre personas.

Cada oracion se clasifica:

  · APODO       -> trae «apod», «le dicen» o «alias»;
  · INSISTENCIA -> trae «otra vez», «siempre» o «de nuevo»;
  · BROMA       -> todo lo demas.

Reglas:

  · No se guarda lo que la propia persona escribio de si misma.
  · No se guarda una oracion con insultos graves (lista fija abajo): el bot no
    repite eso ni aunque el grupo lo diga.
  · `Hash = sha256(chat + oracion normalizada)`; el indice unico
    `(IdUsuario, Hash)` impide guardar dos veces la misma mencion.
  · La oracion se recorta a 500 caracteres, que es el ancho de la columna.

`perfiles(conn, usuario_id=None)` y `aprobar()` / `borrar()` son lo que usa
`panel/api_eccsita.py` para exponer y moderar los perfiles. El roast del bot
(`wa_persona._roast_grupo`) solo lee los APROBADOS de los ultimos 90 dias.

Las funciones reciben la conexion desde fuera (como el resto de los modulos de
la capa de datos) para que las pruebas no abran SQL Server.

    python -m py_compile eccsita_perfiles.py
    python -m unittest tests.test_eccsita_perfiles -v
"""
import hashlib
import json
import re
import unicodedata

from tz_mx import now_mx

CLAVE_ALIAS = "wa_grupo_alias"

MAX_TEXTO = 500
MIN_ORACION = 8          # una mencion mas corta no dice nada util
MAX_AUTOR = 120
MIN_ALIAS = 3            # un alias de dos letras atrapa media conversacion
MAX_ALIAS = 40
MAX_ALIAS_JSON = 500     # HUB_Config.Valor es NVARCHAR(500)

TIPOS = ("BROMA", "APODO", "INSISTENCIA")

# Lista pedida: si la oracion trae alguna, no se guarda. Se compara sobre el
# texto normalizado y como subcadena, porque el insulto aparece con terminacion
# ("pendejada", "chingadera") y no interesa dejar pasar la variante.
INSULTOS = ("pendejo", "puto", "madre", "ching", "verga", "culero")

_APODO = ("apod", "le dicen", "alias")
_INSISTENCIA = ("otra vez", "siempre", "de nuevo")

SQL_USUARIOS = "SELECT Id, Nombre FROM HUB_Users WHERE Activo = 1"
SQL_ALIAS = "SELECT Valor FROM HUB_Config WHERE Clave = %s"
SQL_EXISTE = ("SELECT 1 FROM HUB_GrupoPerfiles "
              "WHERE IdUsuario = %s AND Hash = %s")
SQL_INSERTAR = """
    INSERT INTO HUB_GrupoPerfiles
        (IdUsuario, Texto, Autor, ChatId, Fecha, Anio, Semana, Tipo,
         Aprobado, Hash)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
SQL_RESUMEN = """
    SELECT p.IdUsuario, MAX(u.Nombre) AS Nombre, COUNT(*) AS Perfiles,
           MAX(p.Fecha) AS Ultimo
    FROM HUB_GrupoPerfiles p
    LEFT JOIN HUB_Users u ON u.Id = p.IdUsuario
    GROUP BY p.IdUsuario
    ORDER BY MAX(p.Fecha) DESC
"""
SQL_DETALLE = """
    SELECT Id, Texto, Autor, Fecha, Tipo, Aprobado
    FROM HUB_GrupoPerfiles
    WHERE IdUsuario = %s
    ORDER BY Fecha DESC, Id DESC
"""
SQL_APROBAR = "UPDATE HUB_GrupoPerfiles SET Aprobado = %s WHERE Id = %s"
SQL_BORRAR = "DELETE FROM HUB_GrupoPerfiles WHERE Id = %s"
SQL_GUARDAR_ALIAS = """
    MERGE HUB_Config AS target
    USING (SELECT %s AS Clave) AS source
    ON target.Clave = source.Clave
    WHEN MATCHED THEN UPDATE SET Valor = %s, Actualizado = GETDATE()
    WHEN NOT MATCHED THEN INSERT (Clave, Valor) VALUES (%s, %s);
"""


# ─── utilidades ──────────────────────────────────────────────────────────────
def _normalizar(texto):
    """Minusculas, sin acentos y con espacios colapsados (para comparar)."""
    txt = unicodedata.normalize("NFD", str(texto or ""))
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return " ".join(txt.lower().split())


def _si(valor, defecto=False):
    """Interpreta un valor de HUB_Config como booleano («0» es apagado)."""
    if valor is None:
        return defecto
    if isinstance(valor, str):
        limpio = valor.strip().lower()
        if not limpio:
            return defecto
        return limpio not in ("0", "false", "no", "off")
    return bool(valor)


def _iso(valor):
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        try:
            return valor.isoformat(timespec="seconds")
        except TypeError:
            return valor.isoformat()
    return str(valor)


def _filas(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _una(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _oraciones(texto):
    """Parte el mensaje en oraciones; tambien corta por salto de linea."""
    partes = re.split(r"(?<=[.!?…])\s+|\r?\n+", str(texto or ""))
    return [p.strip() for p in partes if p.strip()]


def _trae(normalizado, frases):
    return any(frase in normalizado for frase in frases)


def _tipo(oracion_norm):
    """APODO > INSISTENCIA > BROMA (en ese orden, como pide la ficha)."""
    if _trae(oracion_norm, _APODO):
        return "APODO"
    if _trae(oracion_norm, _INSISTENCIA):
        return "INSISTENCIA"
    return "BROMA"


def _insulto(oracion_norm):
    return any(palabra in oracion_norm for palabra in INSULTOS)


def _alias_texto(conn):
    """Valor crudo de `HUB_Config.wa_grupo_alias` ("" si no existe)."""
    try:
        fila = _una(conn, SQL_ALIAS, (CLAVE_ALIAS,))
    except Exception:
        return ""
    if not fila or fila[0] in (None, ""):
        return ""
    return str(fila[0])


def _alias_crudo(conn):
    """JSON de la config tal cual ({} si falta o esta roto)."""
    texto = _alias_texto(conn)
    if not texto:
        return {}
    try:
        datos = json.loads(texto)
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _alias_extra(conn):
    """{IdUsuario: {alias normalizados}} de `HUB_Config.wa_grupo_alias` (o {})."""
    alias = {}
    for clave, lista in _alias_crudo(conn).items():
        try:
            uid = int(clave)
        except (TypeError, ValueError):
            continue
        if not isinstance(lista, (list, tuple)):
            lista = [lista]
        limpios = {_normalizar(a) for a in lista if str(a or "").strip()}
        if limpios:
            alias[uid] = limpios
    return alias


def _usuarios(conn):
    """[(idUsuario, nombre)] de `HUB_Users` activos, sin ids repetidos."""
    usuarios = []
    for fila in _filas(conn, SQL_USUARIOS):
        try:
            uid = int(fila[0])
        except (TypeError, ValueError):
            continue
        usuarios.append((uid, str(fila[1] or "").strip()))
    return usuarios


def _alias_por_usuario(conn):
    """
    {IdUsuario: (nombre_normalizado, {alias})} de HUB_Users + la config.

    Todo normalizado: la comparacion contra la oracion es directa.
    """
    mapa = {}
    for uid, nombre_completo in _usuarios(conn):
        nombre = _normalizar(nombre_completo)
        if not nombre:
            continue
        piezas = nombre.split()
        alias = {nombre}
        if piezas and len(piezas[0]) >= 2:
            alias.add(piezas[0])
        mapa[uid] = (nombre, alias)

    for uid, extra in _alias_extra(conn).items():
        nombre, alias = mapa.get(uid, ("", set()))
        alias = set(alias) | set(extra)
        mapa[uid] = (nombre, {a for a in alias if len(a) >= 2})
    return mapa


def _es_el_autor(autor_norm, nombre, alias):
    """True si quien escribe es la misma persona de la que se habla."""
    if not autor_norm:
        return False
    if autor_norm == nombre:
        return True
    return any(autor_norm == a or autor_norm.startswith(a + " ") for a in alias)


def _mencionados(oracion_norm, alias_por_usuario, autor_norm):
    """Ids de usuario cuyo alias aparece como palabra completa en la oracion."""
    encontrados = []
    for uid, (nombre, alias) in alias_por_usuario.items():
        if _es_el_autor(autor_norm, nombre, alias):
            continue
        for a in alias:
            if re.search(r"\b" + re.escape(a) + r"\b", oracion_norm):
                encontrados.append(uid)
                break
    return encontrados


def _hash(chat_id, oracion_norm):
    return hashlib.sha256(
        (str(chat_id or "") + oracion_norm).encode("utf-8")).hexdigest()


# ─── extraccion ──────────────────────────────────────────────────────────────
def extraer(conn, mensaje):
    """
    Guarda las menciones de un mensaje del grupo. Devuelve cuantas guardo.

    `mensaje` es el dict normalizado del webhook: chat_id, autor_id,
    autor_nombre, texto, fecha. Nunca se llama para el chat individual.
    """
    if not isinstance(mensaje, dict):
        return 0
    chat = str(mensaje.get("chat_id") or "").strip()
    texto = str(mensaje.get("texto") or "").strip()
    if not chat or not texto:
        return 0

    alias_por_usuario = _alias_por_usuario(conn)
    if not alias_por_usuario:
        return 0

    autor_norm = _normalizar(mensaje.get("autor_nombre"))
    autor = str(mensaje.get("autor_nombre") or "").strip()[:MAX_AUTOR] or None
    fecha = mensaje.get("fecha") or now_mx()
    anio, semana, _dia = fecha.isocalendar() if hasattr(fecha, "isocalendar") \
        else now_mx().isocalendar()

    guardadas = 0
    try:
        for oracion in _oraciones(texto):
            if len(oracion) > MAX_TEXTO:
                oracion = oracion[:MAX_TEXTO]
            oracion_norm = _normalizar(oracion)
            if len(oracion_norm) < MIN_ORACION or _insulto(oracion_norm):
                continue
            for uid in _mencionados(oracion_norm, alias_por_usuario,
                                    autor_norm):
                digest = _hash(chat, oracion_norm)
                if _una(conn, SQL_EXISTE, (uid, digest)):
                    continue
                tipo = _tipo(oracion_norm)
                cur = conn.cursor()
                try:
                    cur.execute(SQL_INSERTAR, (
                        uid, oracion, autor, chat, fecha, int(anio),
                        int(semana), tipo, 1, digest))
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
                guardadas += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return guardadas


# ─── alias editables desde el panel ──────────────────────────────────────────
def alias_lista(conn):
    """
    `[{idUsuario, nombre, alias}]` de las personas activas.

    `alias` son SOLO los alias extra de `HUB_Config.wa_grupo_alias` (lo que el
    panel edita); el primer nombre y el nombre completo los deduce `extraer`.
    """
    crudo = _alias_crudo(conn)
    extras = {}
    for clave, lista in crudo.items():
        try:
            uid = int(clave)
        except (TypeError, ValueError):
            continue
        if not isinstance(lista, (list, tuple)):
            lista = [lista]
        limpios = [str(a).strip() for a in lista if str(a or "").strip()]
        if limpios:
            extras[uid] = limpios

    items = [{
        "idUsuario": uid,
        "nombre": nombre,
        "alias": extras.get(uid, []),
    } for uid, nombre in _usuarios(conn)]
    items.sort(key=lambda i: i["nombre"].lower())
    return items


def _alias_de_otros(conn, uid):
    """{alias normalizado: idUsuario} de todos MENOS `uid`."""
    ajenos = {}
    for otro, (_nombre, alias) in _alias_por_usuario(conn).items():
        if otro == uid:
            continue
        for a in alias:
            ajenos.setdefault(a, otro)
    return ajenos


def guardar_alias(conn, id_usuario, alias):
    """
    Guarda los alias extra de una persona. Devuelve (ok, error).

    Cada alias necesita 3+ letras y no puede ser el nombre ni el alias de OTRA
    persona: con dos duenos la mencion se guardaria en los dos perfiles.
    """
    try:
        uid = int(id_usuario)
    except (TypeError, ValueError):
        return False, "Usuario no válido."
    if uid <= 0:
        return False, "Usuario no válido."

    if isinstance(alias, str):
        alias = [alias]
    if not isinstance(alias, (list, tuple)):
        return False, "Los alias deben venir en una lista."

    nombres = dict(_usuarios(conn))
    if uid not in nombres:
        return False, "Ese usuario no existe o está inactivo."

    limpios = []
    vistos = set()
    for valor in alias:
        texto = str(valor or "").strip()
        if not texto:
            continue
        norm = _normalizar(texto)
        if len(norm) < MIN_ALIAS:
            return False, f"El alias «{texto}» necesita al menos 3 letras."
        if len(texto) > MAX_ALIAS:
            return False, f"El alias «{texto[:20]}…» es demasiado largo."
        if norm in vistos:
            continue
        vistos.add(norm)
        limpios.append(texto)

    ajenos = _alias_de_otros(conn, uid)
    for texto in limpios:
        otro = ajenos.get(_normalizar(texto))
        if otro is not None:
            return False, (f"El alias «{texto}» ya lo usa "
                           f"{nombres.get(otro) or 'otra persona'}.")

    # Se reescribe la clave COMPLETA: los demas usuarios conservan sus alias.
    texto_previo = _alias_texto(conn)
    if texto_previo:
        try:
            crudo = json.loads(texto_previo)
        except (TypeError, ValueError):
            return False, "La config de alias está rota; corrígela a mano."
        if not isinstance(crudo, dict):
            crudo = {}
    else:
        crudo = {}

    nuevo = {}
    for clave, lista in crudo.items():
        try:
            mismo = int(clave) == uid
        except (TypeError, ValueError):
            mismo = False
        if not mismo:
            nuevo[clave] = lista
    if limpios:
        nuevo[str(uid)] = limpios

    texto = json.dumps(nuevo, ensure_ascii=False)
    if len(texto) > MAX_ALIAS_JSON:
        return False, ("Se pasó del límite de la config (500 caracteres): "
                       "quita alias o acórtalos.")

    cur = conn.cursor()
    try:
        cur.execute(SQL_GUARDAR_ALIAS, (CLAVE_ALIAS, texto, CLAVE_ALIAS, texto))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(exc)
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return True, ""


# ─── lectura y moderacion (panel) ────────────────────────────────────────────
def perfiles(conn, usuario_id=None):
    """
    Sin `usuario_id`: resumen por persona
    `[{idUsuario, nombre, perfiles, ultimo}]`.
    Con `usuario_id`: sus menciones
    `[{id, texto, autor, fecha, tipo, aprobado}]`.
    """
    if usuario_id is None:
        return [{
            "idUsuario": int(fila[0]),
            "nombre": str(fila[1] or "").strip(),
            "perfiles": int(fila[2] or 0),
            "ultimo": _iso(fila[3]),
        } for fila in _filas(conn, SQL_RESUMEN)]

    try:
        uid = int(usuario_id)
    except (TypeError, ValueError):
        return []
    return [{
        "id": int(fila[0]),
        "texto": str(fila[1] or "").strip(),
        "autor": str(fila[2] or "").strip(),
        "fecha": _iso(fila[3]),
        "tipo": str(fila[4] or "BROMA").strip().upper(),
        "aprobado": bool(fila[5]),
    } for fila in _filas(conn, SQL_DETALLE, (uid,))]


def aprobar(conn, id_perfil, valor):
    """Pone `Aprobado` en 1/0. False si la mencion ya no existe."""
    try:
        pid = int(id_perfil)
    except (TypeError, ValueError):
        return False
    cur = conn.cursor()
    try:
        cur.execute(SQL_APROBAR, (1 if _si(valor, True) else 0, pid))
        filas = int(cur.rowcount or 0)
        conn.commit()
        return filas > 0
    finally:
        try:
            cur.close()
        except Exception:
            pass


def borrar(conn, id_perfil):
    """Borra la mencion. False si ya no existia."""
    try:
        pid = int(id_perfil)
    except (TypeError, ValueError):
        return False
    cur = conn.cursor()
    try:
        cur.execute(SQL_BORRAR, (pid,))
        filas = int(cur.rowcount or 0)
        conn.commit()
        return filas > 0
    finally:
        try:
            cur.close()
        except Exception:
            pass
