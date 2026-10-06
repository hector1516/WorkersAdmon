# Contrato de notificaciones push · ECCSA-Shell

> Avisos al teléfono de las PWA. Documento de referencia para **añadir push a una
> app** o para arreglar una que ya lo tiene y no suena.
>
> Hoy lo consumen **Admon** (`AdmonApp`) y el despachador de **WorkersAdmon**.
> Mailbox tiene el suyo propio y funciona; el resto de apps todavía no.

Implementaciones de referencia, en orden de utilidad:

| Pieza | Dónde |
|---|---|
| Cliente (navegador) | `AdmonApp/src/lib/push.js` · `ECCSA_Mailbox/src/lib/push.js` |
| Service worker | `AdmonApp/public/sw.js` (handlers `push` y `notificationclick`) |
| Endpoints | `AdmonApp/api/main.py` (`/api/push/*`) |
| Despachador (envía) | `WorkersAdmon/notif_dispatch.py` + `cron_avisos_push.py` |
| Tablas | migración `0056_avisos_push.sql` de `WorkersAdmon` |

---

## 1. El problema que hay que resolver antes de nada

**Una suscripción push pertenece a un ORIGEN.** El endpoint que devuelve
`pushManager.subscribe()` está atado al service worker del sitio donde se
suscribió. Si el servidor manda un aviso de Field a una suscripción creada en
Mailbox, la notificación la muestra el service worker de Mailbox: el usuario ve
"Registra tus kilómetros" dentro de Mailbox.

Eso se llama **notificaciones cruzadas entre apps** y es exactamente el tipo de
cosa que hace que la gente desactive los permisos para siempre. Se resuelve con
una columna `App` en la tabla de suscripciones y filtrando siempre por ella.

Por eso existe `HUB_PushSuscripciones` y **no** hay que usar
`HUB_PushSubscriptions` (la vieja, sin columna de app).

---

## 2. iOS: las tres condiciones que el navegador NO avisa

Este es el 80% de los tickets de "el botón no hace nada". Ninguna de las tres
produce un error legible: simplemente falla.

| # | Condición | Qué pasa si no se cumple |
|---|---|---|
| 1 | **iOS 16.4 o superior** | No hay Web Push en PWA. |
| 2 | **La PWA está INSTALADA** en la pantalla de inicio | `requestPermission()` puede pedir permiso y después `pushManager.subscribe()` revienta con `NotSupportedError`. |
| 3 | **`requestPermission()` se llama desde un GESTO del usuario** | iOS lo ignora en silencio y `permission` sigue en `'default'`. En escritorio lo correcto es llamarlo al cargar; en iOS no. |

Y una cuarta, que no es una condición sino un bug esperando:

> **En iOS el permiso nunca llega a `'denied'`.** Si el usuario no lo acepta, se
> queda en `'default'` para siempre. Por eso hay que **exigir `'granted'`** y
> nunca deducir "no está denegado, entonces está bien".

### Cómo se traduce esto en código

El módulo de cliente **no llama a la API: la envuelve** y devuelve
`{ ok, motivo }`. La vista muestra el motivo real en vez de un "no se pudo":

```js
export function soporte() {
    // ...detecta navegador, versión de iOS, y si la PWA está instalada
    if (base.ios && !iosCumpleElMinimo()) {
        base.motivo = `Las notificaciones en iPhone necesitan iOS 16.4 o superior.`;
        return base;
    }
    if (base.ios && !base.instalada) {
        base.motivo = 'En iPhone hay que instalar la app en la pantalla de inicio.';
        return base;
    }
    base.listo = true;
    return base;
}

export async function activar() {
    const s = soporte();
    if (!s.listo) return { ok: false, motivo: s.motivo };   // <- se explica
    const permiso = await Notification.requestPermission();
    if (permiso !== 'granted') {                            // <- se EXIGE granted
        return { ok: false, motivo: ... };
    }
    // ...
}
```

iPadOS 13+ se hace pasar por Mac: para detectarlo hay que mirar
`/Macintosh/.test(ua) && navigator.maxTouchPoints > 1`.

---

## 3. Contrato de la base de datos

Las tres apps comparten `ECCSA_Admon`, así que `schema_migrations` es un registro
único. La numeración también es global.

### `HUB_PushSuscripciones` (migración `0056_avisos_push.sql`, en WorkersAdmon)

| Columna | Tipo | Para qué |
|---|---|---|
| `Id` | `int identity` | |
| `App` | `nvarchar(20)` | `'admon'`, `'field'`, `'mailbox'`… **siempre se filtra por esto** |
| `IdUsuario` | `int` → `HUB_Users(Id)` | a quién pertenece el dispositivo |
| `Endpoint` | `nvarchar(2048)` | la URL del push service (Apple, Google, Mozilla) |
| `EndpointHash` | `binary(32)` **computed, PERSISTED** | `HASHBYTES('SHA2_256', Endpoint)`. Existe solo para poder indexar el endpoint |
| `P256dhKey`, `AuthKey` | `nvarchar(512)` | las claves del `subscribe()` |
| `Plataforma` | `nvarchar(20)` | `'iOS'`, `'Android'`, `'Escritorio'`. Informativo: para decir "3 iPhone, 1 PC" |
| `Creado`, `UltimoUso`, `Activo` | | |

**Por qué `EndpointHash`:** los endpoints de Apple miden ~180 caracteres y
`NVARCHAR(2048)` son 4096 bytes, muy por encima del tope de 900 bytes de un
índice de SQL Server. Sin el hash no se puede poner un índice único, y sin índice
único un mismo dispositivo suscrito dos veces **recibe cada aviso dos veces**.
(Se usa SHA2_256 porque SHA1 quedó fuera en SQL 2016; esto corre en 2014.)

### Sobre `HUB_PushSubscriptions` (la vieja)

- **No tiene columna `App`.** No sirve para una app más.
- La sigue escribiendo `mcp_server` (`/__push_subscribe__`).
- Si se migra alguna vez, es un `ALTER TABLE ... ADD App` **más** arreglar
  `api/routers/push.py` (que consulta una columna `userId` que no existe y por eso
  no ha mandado un solo push; ver §8).

### `HUB_AvisosCola` — la cola del despachador

Es estado interno del worker: **sin interfaz y sin endpoint**. Existe porque el
horario laboral y el resumen necesitan GUARDAR lo pendiente, no solo decidir
enviar o no. Si en una app no se quiere horario ni resumen, no hace falta.

---

## 4. Las claves VAPID

| Dónde | Para qué |
|---|---|
| `HUB_PushConfig` (Id=1) | `VapidPublicKey`, `VapidPrivateKey`, `VapidEmail` — **la que usa el despachador** |
| `HUB_Config` → `vapid_public_key` / `vapid_private_key` | las de Mailbox. Las de producción ya están bien formadas |

### La privada va como base64url de los 32 bytes CRUDOS

No como PEM, no como DER. `pywebpush` (y `py-vapid`) quieren el escalar crudo.

```python
# MAL: 43 caracteres que en realidad son un DER → todos los push fallan
# BIEN: 43 caracteres que decodifican a exactamente 32 bytes
import base64
priv = base64.urlsafe_b64encode(
    clave.private_numbers().private_value.to_bytes(32, "big")).rstrip(b"=").decode()
```

El público sí son 65 bytes: el punto sin comprimir (`0x04` + X + Y), que es lo
que espera `applicationServerKey` en el navegador.

> **El síntoma de una clave mal formada es el peor posible:** con PEM o DER el
> envío falla SIEMPRE y `pywebpush` devuelve un error que no dice por qué. Parece
> "no llegó a ningún dispositivo". Convertirla es barato, así que **conviene
> hacerlo siempre al leer**, no solo al escribir:
>
> ```python
> def _normalizar_vapid_privada(priv):
>     # si decodifica a 32 bytes, ya está bien; si no, se convierte desde PEM/DER
> ```

La clave **pública sí puede ir en el bundle** (es pública por definición: la
tiene cualquiera que instale la app). La privada nunca sale del servidor.

---

## 5. Contrato del payload

El servidor manda JSON. Estos son los campos que el service worker entiende:

```json
{
  "title": "✍️ Reporte firmado",
  "body": "Rosa firmó el reporte RS-00123 del cliente HUSA.",
  "url": "/reportes",
  "tag": "admon-reporte_firmado",
  "badge_count": 1,
  "silent": false,
  "vibrate": [200, 100, 200]
}
```

| Campo | Qué hace |
|---|---|
| `title`, `body` | Los dos obligatorios. Con `userVisibleOnly: true` el servidor **siempre** manda `body`; si no llega, no hay nada que mostrar y un aviso vacío es peor que nada. |
| `url` | A dónde abre la notificación. Sin esto abre la raíz y el usuario no sabe qué mirar. |
| `tag` | **Agrupa**: si llegan tres avisos del mismo tipo, el segundo reemplaza al primero en vez de apilar tres. En iPhone la pila se llena rápido y tapar la pantalla es peor que resumir. |
| `badge_count` | Ver abajo. |
| `silent` | Sin sonido (para avisos de relleno). |

### `badge_count`, no `badge`

**`badge` cambia de significado según la plataforma:**

- **iOS** → es un **NÚMERO** que se pinta en el ícono de la app.
- **Android** → es la **URL de una IMAGEN** pequeña.

Mandarlo igual se ve mal en alguno de los dos. Por eso el servidor manda el
conteo y **el service worker decide**:

```js
badge: _esIOS() ? String(payload.badge_count || 0) : '/icons/icon-192x192.png'
```

### `icon` siempre local

En iOS **no se usan imágenes remotas**: el ícono tiene que estar empaquetado con
la app. Y el ícono grande (Aperture) sale del bundle, no del service worker: no
se controla desde ahí.

---

## 6. Contrato del service worker

### Al recibir el push

```js
self.addEventListener('push', (e) => {
    if (!e.data) return;                    // sin body no hay nada que mostrar
    let payload = {};
    try { payload = e.data.json(); } catch { payload = { title: '…', body: e.data.text() }; }
    e.waitUntil(self.registration.showNotification(payload.title || '…', {
        body: payload.body || '',
        data: { url: payload.url || '/' },
        tag: payload.tag || 'app',
        renotify: !!payload.tag,
        silent: !!payload.silent,
        icon: '/icons/icon-192x192.png',    // local, siempre
        badge: _esIOS() ? String(payload.badge_count || 0) : '/icons/icon-192x192.png'
    }));
});
```

### Al tocar la notificación: **enfocar, no abrir**

Este es el detalle que hace que funcione en un iPhone de verdad:

```js
self.addEventListener('notificationclick', (e) => {
    e.notification.close();
    const url = e.notification.data?.url || '/';
    e.waitUntil((async () => {
        // Abrir una ventana NUEVA desde una notificación deja la PWA en un
        // estado raro (a veces en blanco). Enfocar la existente siempre.
        const tabs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const tab of tabs) {
            if (tab.url.startsWith(self.location.origin)) {
                await tab.focus();
                tab.postMessage({ type: 'ABRIR_RUTA', url });   // <- la app DEBE escuchar
                return;
            }
        }
        await self.clients.openWindow(url);   // no había ninguna pestaña
    })());
});
```

> **La app tiene que escuchar `ABRIR_RUTA`.** Si nadie escucha el `postMessage`,
> el toque "no hace nada": la PWA se enfoca pero no navega, y parece que el aviso
> está roto. Es el bug más común de los que no dan error.

Se usa `postMessage` y no `tab.navigate()` porque la ruta puede llevar query
(`?msg=123`) y el router la lee del `history`, no de un evento.

### Limpiar el badge

El ícono del iPhone lo pinta el sistema operativo y no hay otra forma de
quitarlo. La app avisa por `postMessage` y el SW lo limpia:

```js
if (d.type === 'LIMPIAR_BADGE') {
    e.waitUntil((async () => {
        if (typeof self.registration.clearAppBadge === 'function') {
            await self.registration.clearAppBadge();   // va por `registration`, no por `self`
        }
    })());
}
```

---

## 7. Los endpoints

Cinco, y ninguno es opcional si la app quiere mostrar qué equipos tiene:

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/push/vapid-public-key` | La clave pública. El cliente la pide antes de suscribirse. |
| `POST` | `/api/push/subscribe` | Registra el dispositivo de **este** usuario. |
| `POST` | `/api/push/unsubscribe` | Da de baja **solo este** dispositivo. |
| `GET` | `/api/push/suscripciones` | Los equipos del usuario, para poder decir "este teléfono sí, esa laptop no". |
| `POST` | `/api/push/prueba` | Un aviso de prueba **solo a los equipos de quien lo apretó**. |

### El subscribe es un UPSERT por endpoint

```python
# SQL Server 2014 no tiene ON CONFLICT: UPDATE y, si no tocó filas, INSERT.
cursor.execute("UPDATE HUB_PushSuscripciones SET IdUsuario = %s, ..., Activo = 1, "
               "    UltimoUso = GETDATE() WHERE Endpoint = %s", (user_id, endpoint))
if cursor.rowcount == 0:
    cursor.execute("INSERT INTO HUB_PushSuscripciones (App, IdUsuario, Endpoint, "
                   "P256dhKey, AuthKey, Plataforma, Creado, Activo) "
                   "VALUES (%s, %s, %s, %s, %s, %s, GETDATE(), 1)", (...))
```

**Por qué re-registrar:** la suscripción pertenece al service worker y sobrevive
al cierre de sesión, así que si el usuario entra con otra cuenta en el mismo
teléfono el endpoint seguiría apuntando al usuario anterior. Un `revincular()` al
iniciar sesión corrige la asignación **sin volver a pedir el permiso**.

**Por qué `Activo` en vez de borrar:** si el usuario vuelve, la fila sigue ahí y
reactivarla es un `UPDATE` en vez de un `INSERT`.

### La prueba va por `IdUsuario`, nunca en broadcast

El botón "🧪" tiene que probar **el teléfono de quien lo apretó**. Probar el
teléfono propio no puede ser un aviso a media empresa.

---

## 8. Quién envía: el despachador centralizado

`WorkersAdmon/notif_dispatch.py` (lógica) + `cron_avisos_push.py` (el turno).

**La app NO envía los avisos automáticos.** La razón es que el evento puede
ocurrir en una app y tener que avisar a un usuario que entra por otra: el ticket
OxxoGas lo captura Field, la cotización la firma el cliente desde Field, y el
aviso lo tiene que ver quien abre Cotizaciones en Admon. Si cada app avisara de
lo suyo, el aviso saldría de donde el usuario no lo espera.

Reglas del modelo:

1. **El reparto se hace al ENCOLAR, no al enviar.** Se deduce de los
   `Acceso*` de `HUB_Users` —los mismos que usa la sesión de la app—, de modo que
   quien puede abrir un módulo es quien recibe sus avisos. Si se hiciera al
   enviar, un permiso revocado entre el evento y el envío dejaría filtrado un
   aviso de un módulo que ya no puede abrir.
2. **El marcador "ya avisado" avanza en la misma transacción que encola.** Si no,
   un reinicio del worker repite todos los avisos, o se pierden.
3. **Un tipo se puede apagar sin desinstalar nada:** es un booleano en
   `HUB_Config`.
4. **Las 410/404 borran la suscripción.** Es el push service diciendo que el
   endpoint murió (PWA desinstalada).

> **Aviso sobre `get_connection()`:** en `eccsa_db` devuelve una conexión
> **cacheada por hilo** y cerrarla con `with` la invalida para quien la tenga en
> la mano. Si un detector abre una conexión y la presta a alguien que abre la
> suya, al volver tiene un cursor muerto: el `INSERT` entra y el marcador se
> pierde, y el siguiente ciclo repite el aviso. **Una conexión por ciclo.**

---

## 9. Receta: añadir push a una app nueva

1. **Copiar `AdmonApp/src/lib/push.js`** tal cual. Solo cambian las cinco rutas y
   el nombre de la app en los textos. Los comentarios no son adorno: explican las
   tres condiciones de iOS, y son la razón de que el botón funcione en un iPhone.
2. **Copiar los handlers `push` y `notificationclick`** del `public/sw.js` de
   Admon. Cambiar el `icon` y el prefijo del `tag` (`admon-…` → `miapp-…`).
3. **Subir la versión del `CACHE`** del service worker, o los usuarios con el SW
   viejo seguirán con el anterior.
4. **Copiar los cinco endpoints** del `api/main.py` de Admon. Cambiar
   `APP_PUSH = "admon"` por el nombre de la app.
5. **Escuchar `ABRIR_RUTA`** en el componente raíz, junto al `FORCE_RELOAD` que
   ya exista. Sin esto el toque no navega.
6. **Pintar la tarjeta** en la pantalla de configuración: activar, equipos
   registrados y el botón de prueba.
7. **Dar de alta la app en el catálogo** (`HUB_ConfigCatalogo`), para que sus
   claves aparezcan en la pestaña "Apps" del panel.
8. **Si la app tiene usuarios propios** (no `HUB_Users`), el paso 4 se queda
   corto: la tabla tiene `IdUsuario` con FK a `HUB_Users`. Hace falta o una tabla
   por app, o una columna con el id de la app.

---

## 10. Checklist antes de decir que funciona

- [ ] En un **iPhone real**, con la PWA **instalada**, `requestPermission()` devuelve `granted`.
- [ ] La fila aparece en `HUB_PushSuscripciones` **con la `App` correcta**.
- [ ] `POST /api/push/prueba` devuelve `sent: 1` (no `failed: 1`).
- [ ] La notificación **llega con sonido** y el ícono es el de la app.
- [ ] Al tocarla **navega** a la ruta del aviso (no se queda en la home).
- [ ] El aviso aparece **con el ícono de la app**, no con el del navegador.
- [ ] El aviso de otra app **no** llega a este equipo.

---

## 11. Errores ya pagados

| Síntoma | Causa |
|---|---|
| El botón no hace nada en iPhone, sin error | La PWA no está instalada, o el permiso se pidió fuera de un gesto. |
| El permiso nunca se rehabilita | En iOS no llega a `'denied'`; hay que exigir `'granted'`. |
| Llega el aviso de otra app | Suscripciones sin columna `App` (o consulta a la tabla vieja). |
| Cada aviso llega dos veces | Sin índice único por endpoint: falta el `EndpointHash`. |
| Al tocar la notificación se abre en blanco | Se abrió una ventana nueva en vez de enfocar la existente. |
| Se enfoca la app pero no navega | Nadie escucha el `postMessage` `ABRIR_RUTA`. |
| "No llegó a ningún dispositivo" | La clave VAPID está en PEM/DER en vez de 32 bytes crudos. |
| El ícono se ve raro en Android | Se mandó `badge` con un número. Va `badge_count`. |
| Avisos repetidos cada ciclo | El marcador no avanzó en la misma transacción que encoló. |
| Un `INSERT` entra y el marcador se pierde | Se cerraron dos conexiones cacheadas del mismo hilo. |
| `not enough arguments for format string` | Un `%` aplicado a una sentencia que tiene `%d` **y** `%s`. El `TOP` va concatenado: `TOP (N)`. |
