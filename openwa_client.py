"""
Cliente de OpenWA, el gateway de WhatsApp que corre en el ServerVM.

Que es OpenWA: un servicio (whatsapp-web.js) que expone una API HTTP y manda
mensajes de WhatsApp con una sesion ya iniciada. La app no habla con WhatsApp
directo, le pide por HTTP: "manda este texto a este numero".

Endpoints que usa este modulo (leidos del Swagger, no adivinados):

    GET  /api/health
    GET  /api/sessions/{id}/groups
    POST /api/sessions/{id}/messages/send-text
    POST /api/sessions/{id}/messages/send-document
    POST /api/sessions/{id}/messages/send-image

Dos detalles del API que conviene no olvidar:

- El multimedia va **en base64 dentro del JSON** (campo `base64`), no multipart,
  y `mimetype` es obligatorio cuando se manda base64. Tambien existe `url`, pero
  no la usamos: el archivo se queda en nuestro servidor y no sale por internet.
- La autenticacion es el header `X-API-Key`. DENTRO de docker se llama al host
  por **hostname** (`http://openwa:2785`): OpenWA tiene un guard anti-SSRF que
  rechaza callbacks a IPs internas.

Todo lo que sale de aqui devuelve `(ok, detalle)`. Nunca lanza excepcion hacia el
worker: una alerta que falla no puede tumbar el proceso que la dispara. Y el
detalle nunca lleva la API key, porque estos errores acaban en logs y en el
historial de la cola.
"""

import base64
import os
import urllib.parse

import requests

# Limite de WhatsApp. Si el texto se pasa, OpenWA lo rechaza con 400.
MAX_TEXTO = 4096

# Configuracion por defecto si no hay nada en la base ni en el entorno.
# El hostname es el de docker: la IP cambiaria y ademas la rechazaria el
# guard anti-SSRF.
BASE_POR_DEFECTO = 'http://openwa:2785'
CLAVE_BASE_URL = 'openwa_base_url'
CLAVE_SESSION = 'openwa_session_id'
CLAVE_API_KEY = 'openwa_api_key'


# ── Telefonos ────────────────────────────────────────────────────────────────

def normalizar_chat_id(numero):
    """
    Convierte lo que una persona escribe en el campo de telefonos en un chatId
    de WhatsApp (`<numero>@c.us`).

    Acepta lo que se pega de un pastel de RFCs o de WhatsApp: `8123211516`,
    `+52 1 (55) 1234-5678`, `5218123211516`, `5218123211516@c.us`. Si el numero
    trae 10 digitos se asume Mexico (52); con 12 o mas se respeta tal cual
    porque ya trae pais.

    Tambien acepta un chatId de grupo (`1234567890-123456@g.us`) **tal cual**.
    Un grupo no se inventa: se elige de la lista de la sesion (sub-pestaña
    Grupos del panel) y se pega su id completo. Un `@g.us` a medias o con otra
    forma no es un grupo de WhatsApp y se rechaza.

    Un numero de 11 digitos que empieza con 1 se **rechaza** en vez de
    adivinarse: es imposible saber si es el formato antiguo de Mexico
    (`1 55 1234 5678`) o un numero de EUA (`+1 415 555 2671`). Adivinar produce
    un numero que existe, tiene formato valido y no es de nadie, asi que el
    aviso se pierde en silencio; rechazarlo lo muestra en la interfaz y quien
    lo pego lo corrige escribiendo el codigo de pais.

    Devuelve None si no es un numero, para que el caller pueda reportarlo en vez
    de mandarlo a un chat equivocado.
    """
    if not numero:
        return None
    txt = str(numero).strip()
    if not txt:
        return None
    if '@' in txt:
        # Ya viene como chatId. Los individuales (@c.us) y los grupos (@g.us)
        # se aceptan tal cual; los grupos salen de la lista de la sesion, no
        # de un numero. Cualquier otro sufijo no es un chat de WhatsApp.
        if txt.endswith('@c.us') and len(txt) > len('@c.us'):
            return txt
        if txt.endswith('@g.us') and txt.index('@') > 0:
            return txt
        return None

    digitos = ''.join(c for c in txt if c.isdigit())
    if len(digitos) == 10:
        digitos = '52' + digitos
    elif not (12 <= len(digitos) <= 15):
        return None
    return f'{digitos}@c.us'


def normalizar_chat_ids(texto):
    """
    Normaliza el campo de telefonos completo, que el usuario pega separado por
    comas: `"5218123211516, 5512345678"`.

    Devuelve `(validos, rechazados)`. Los rechazados se muestran en la interfaz
    para que se note el typo en lugar de perder medio aviso en silencio.
    """
    if not texto:
        return [], []
    crudo = str(texto).replace('\n', ',').replace(';', ',')
    validos, rechazados, vistos = [], [], set()
    for trozo in crudo.split(','):
        trozo = trozo.strip()
        if not trozo:
            continue
        chat = normalizar_chat_id(trozo)
        if not chat:
            rechazados.append(trozo)
        elif chat not in vistos:
            vistos.add(chat)
            validos.append(chat)
    return validos, rechazados


# ── Secretos y limites ───────────────────────────────────────────────────────

def enmascarar(texto):
    """
    Deja ver de que se trata sin dejar ver el secreto. Se usa en logs y en
    diagnosticos; en la interfaz el campo es de tipo password y nunca se
    rellena con el valor real.

    Muestra 4 caracteres de cada lado porque con menos no se reconoce de que
    provider es, y con mas se Poderia reconstruir una key corta.
    """
    txt = (texto or '').strip()
    if not txt:
        return ''
    if len(txt) <= 8:
        return '*' * len(txt)
    return f'{txt[:4]}...{txt[-4:]}'


def recortar_texto(texto, maximo=MAX_TEXTO):
    """Recorta al limite de WhatsApp dejando claro que se recorto."""
    txt = texto or ''
    if len(txt) <= maximo:
        return txt
    return txt[:maximo - 24].rstrip() + '\n\n... (recortado)'


# ── Payloads ─────────────────────────────────────────────────────────────────

def payload_texto(chat_id, texto):
    """Cuerpo de send-text. `linkPreview` se deja al default de OpenWA."""
    return {'chatId': chat_id, 'text': recortar_texto(texto)}


def payload_archivo(chat_id, datos, mimetype, nombre, caption=None):
    """
    Cuerpo de send-document / send-image: el archivo en base64 dentro del JSON.
    `mimetype` es obligatorio aqui (OpenWA no adivina y responde 400 sin el).
    """
    payload = {
        'chatId': chat_id,
        'base64': base64.b64encode(datos).decode('ascii'),
        'mimetype': mimetype,
    }
    if nombre:
        payload['filename'] = nombre
    if caption:
        payload['caption'] = recortar_texto(caption)
    return payload


# ── Configuracion (base de datos primero, entorno como respaldo) ──────────────

def obtener_config():
    """
    Devuelve `{api_key, base_url, session_id, origen}`.

    La base de datos gana sobre el entorno: lo que se pega en la interfaz es una
    decision explicita de la persona, y si la variable de entorno tuviera
    prioridad la pegaria y no serviria de nada, que es la peor forma de fallar.
    El entorno queda como respaldo para CI, tests y arranque en frio.
    """
    base_url, session_id, api_key, origen = '', '', '', 'ninguna'
    try:
        import eccsa_db
        cfg = eccsa_db.get_openwa_config()
        base_url = (cfg.get('base_url') or '').strip()
        session_id = (cfg.get('session_id') or '').strip()
        api_key = (cfg.get('api_key') or '').strip()
        if api_key:
            origen = 'base de datos'
    except Exception as e:
        print(f"openwa: no se pudo leer la configuracion de la base ({e}); se usara el entorno")

    if not api_key:
        api_key = (os.environ.get('OPENWA_API_KEY') or '').strip()
        if api_key:
            origen = 'entorno'
    if not base_url:
        base_url = (os.environ.get('OPENWA_BASE_URL') or BASE_POR_DEFECTO).strip()
    if not session_id:
        session_id = (os.environ.get('OPENWA_SESSION_ID') or '').strip()

    return {'api_key': api_key, 'base_url': base_url,
            'session_id': session_id, 'origen': origen}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _limpiar_detalle(detalle, clave):
    """Red de seguridad: si la key llega a un texto de error, se tapa."""
    txt = str(detalle or '')
    if clave and len(clave) > 6:
        txt = txt.replace(clave, enmascarar(clave))
    return txt[:400]


def _pedir(metodo, url, headers, json_body=None, timeout=60, sesion=None):
    """
    Una sola llamada HTTP. Devuelve `(ok, detalle)` y nunca deja escapar la
    excepcion: el worker la necesita para marcar la fila de la cola, y una
    alerta no puede tumbar el proceso que la dispara.
    """
    http = sesion or requests
    try:
        if metodo == 'GET':
            r = http.get(url, headers=headers, timeout=10)
        else:
            r = http.post(url, headers=headers, json=json_body, timeout=timeout)
        if 200 <= r.status_code < 300:
            return True, 'ok'
        return False, f'HTTP {r.status_code}: {_limpiar_detalle(r.text, headers.get("X-API-Key"))}'
    except Exception as e:
        return False, f'{type(e).__name__}: {_limpiar_detalle(e, headers.get("X-API-Key"))}'


def _url(base, session_id, sufijo):
    return f'{(base or "").rstrip("/")}/api/sessions/{session_id}/messages/{sufijo}'


def _pedir_json(url, headers, sesion=None):
    """
    GET que devuelve el cuerpo ya decodificado: `(ok, datos, detalle)`.
    Como `_pedir`, nunca deja escapar la excepcion y tapa la key en el error.
    """
    http = sesion or requests
    clave = headers.get("X-API-Key")
    try:
        r = http.get(url, headers=headers, timeout=10)
        if not (200 <= r.status_code < 300):
            return False, None, (f'HTTP {r.status_code}: '
                                 f'{_limpiar_detalle(r.text, clave)}')
        try:
            return True, r.json(), 'ok'
        except ValueError:
            return False, None, 'la respuesta no es JSON'
    except Exception as e:
        return False, None, f'{type(e).__name__}: {_limpiar_detalle(e, clave)}'


def normalizar_grupos(datos):
    """
    Convierte la respuesta de OpenWA en `[{id, nombre, participantes}]`.

    No se adivina la forma: la API puede contestar una lista pelada o un sobre
    (`groups` / `data` / `result`), y cada grupo puede traer el id como texto o
    como objeto serializado. Lo que no se reconoce se descarta; un grupo sin id
    no sirve para mandarle nada.
    """
    if isinstance(datos, dict):
        for clave in ('groups', 'data', 'result', 'items', 'value'):
            if isinstance(datos.get(clave), (list, tuple)):
                datos = datos[clave]
                break
        else:
            datos = [datos] if (datos.get('id') or datos.get('groupId')) else []
    if not isinstance(datos, (list, tuple)):
        return []

    grupos, vistos = [], set()
    for g in datos:
        if not isinstance(g, dict):
            continue
        ident = g.get('id') or g.get('groupId') or g.get('idGrupo')
        if isinstance(ident, dict):
            ident = ident.get('_serialized') or ident.get('id')
        ident = str(ident or '').strip()
        if not ident or ident in vistos:
            continue
        if '@' not in ident:
            ident += '@g.us'
        if not ident.endswith('@g.us'):
            continue
        vistos.add(ident)

        participantes = g.get('participants') or g.get('participantes')
        if isinstance(participantes, (list, tuple)):
            total = len(participantes)
        else:
            total = (g.get('size') or g.get('participantsCount')
                     or g.get('cantidadParticipantes') or 0)
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0

        nombre = str(g.get('name') or g.get('subject')
                     or g.get('nombre') or '').strip()
        grupos.append({'id': ident, 'nombre': nombre, 'participantes': total})
    grupos.sort(key=lambda g: (g['nombre'] or g['id']).lower())
    return grupos


def listar_grupos(base=None, clave=None, session_id=None, sesion=None):
    """
    Grupos de la sesion de WhatsApp. Devuelve `(ok, grupos|detalle)`.

    Prueba primero `GET /api/sessions/{id}/groups` y, si esa ruta no existe en
    la version de OpenWA instalada, `GET /groups`. La key nunca sale en el
    detalle del error (lo tapa `_limpiar_detalle`).
    """
    cfg = {'api_key': clave or '', 'base_url': base or BASE_POR_DEFECTO,
           'session_id': session_id or ''}
    if base is None and clave is None and session_id is None:
        cfg = obtener_config()
    if not cfg.get('api_key'):
        return False, ('falta la API key de OpenWA: pegala en '
                       'Notificaciones > Conexion > OpenWA')
    if not cfg.get('session_id'):
        return False, 'falta el id de sesion de OpenWA'

    raiz = (cfg.get('base_url') or BASE_POR_DEFECTO).rstrip('/')
    headers = {'X-API-Key': cfg['api_key']}
    primero = None
    for url in (f'{raiz}/api/sessions/{cfg["session_id"]}/groups',
                f'{raiz}/groups'):
        ok, datos, detalle = _pedir_json(url, headers, sesion=sesion)
        if ok:
            return True, normalizar_grupos(datos)
        primero = primero or detalle
    return False, primero


# ── API pública ──────────────────────────────────────────────────────────────

def health(base=None, clave=None, sesion=None):
    """
    Estado del gateway. El `/api/health` es publico, asi que sirve para
    distinguir "OpenWA esta caido" de "la API key esta mal pegada" sin
    depender de la key.
    """
    cfg = obtener_config() if base is None else {'base_url': base, 'api_key': clave or ''}
    base_url = base or cfg.get('base_url') or BASE_POR_DEFECTO
    headers = {'X-API-Key': clave if clave is not None else cfg.get('api_key', '')}
    return _pedir('GET', f'{(base_url or "").rstrip("/")}/api/health', headers, sesion=sesion)


def _enviar(sufijo, payload, cfg, sesion, timeout):
    """Camino comun de los tres envíos."""
    if not payload.get('chatId'):
        return False, 'sin chatId: el telefono no se pudo normalizar'
    if not cfg.get('api_key'):
        return False, ('falta la API key de OpenWA: pegala en '
                       'Notificaciones > Conexion > OpenWA')
    if not cfg.get('session_id'):
        return False, 'falta el id de sesion de OpenWA'
    url = _url(cfg.get('base_url') or BASE_POR_DEFECTO, cfg['session_id'], sufijo)
    return _pedir('POST', url, {'X-API-Key': cfg['api_key'],
                                'Content-Type': 'application/json'},
                  payload, timeout=timeout, sesion=sesion)


def enviar_texto(chat_id, texto, base=None, clave=None, session_id=None, sesion=None,
                 timeout=30):
    """Manda un texto a un numero. Devuelve `(ok, detalle)`."""
    cfg = {'api_key': clave or '', 'base_url': base or BASE_POR_DEFECTO,
           'session_id': session_id or ''}
    if base is None and clave is None and session_id is None:
        cfg = obtener_config()
    return _enviar('send-text', payload_texto(chat_id, texto), cfg, sesion, timeout)


def telefono_de_contacto(chat_id, base=None, clave=None, session_id=None, sesion=None):
    """
    Numero real de un chat. Devuelve '' si no se puede resolver.

    WhatsApp ya manda a veces un **LID** (un identificador largo que termina en
    `@lid`) en vez del telefono, y con ese id el bot no reconoce a nadie. OpenWA
    guarda la relacion y la expone en `/contacts/<id>/phone`.
    """
    cfg = {'api_key': clave or '', 'base_url': base or BASE_POR_DEFECTO,
           'session_id': session_id or ''}
    if base is None and clave is None and session_id is None:
        cfg = obtener_config()
    if not chat_id or not cfg.get('api_key') or not cfg.get('session_id'):
        return ''
    url = (f'{str(cfg.get("base_url") or BASE_POR_DEFECTO).rstrip("/")}'
           f'/api/sessions/{cfg["session_id"]}/contacts/'
           f'{urllib.parse.quote(str(chat_id), safe="")}/phone')
    ok, datos, _ = _pedir_json(url, {'X-API-Key': cfg['api_key']}, sesion)
    if not ok or not isinstance(datos, dict):
        return ''
    return ''.join(c for c in str(datos.get('phone') or '') if c.isdigit())


def _enviar_archivo(sufijo, chat_id, datos, nombre, caption, mimetype,
                    base, clave, session_id, sesion, timeout):
    if not datos:
        return False, f'archivo vacio: no se mando {nombre or sufijo}'
    cfg = {'api_key': clave or '', 'base_url': base or BASE_POR_DEFECTO,
           'session_id': session_id or ''}
    if base is None and clave is None and session_id is None:
        cfg = obtener_config()
    payload = payload_archivo(chat_id, datos, mimetype, nombre, caption)
    return _enviar(sufijo, payload, cfg, sesion, timeout)


def enviar_documento(chat_id, datos, nombre, caption=None, base=None, clave=None,
                     session_id=None, sesion=None, timeout=90):
    """Manda un archivo (el PDF del reporte firmado)."""
    return _enviar_archivo('send-document', chat_id, datos, nombre, caption,
                           'application/pdf', base, clave, session_id, sesion, timeout)


def enviar_imagen(chat_id, datos, nombre, caption=None, base=None, clave=None,
                  session_id=None, sesion=None, timeout=90):
    """Manda una imagen (la foto del ticket)."""
    return _enviar_archivo('send-image', chat_id, datos, nombre, caption,
                           'image/jpeg', base, clave, session_id, sesion, timeout)
