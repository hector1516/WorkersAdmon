# Contrato del banner · ECCSA-Shell

> **El banner de Field es el estándar.** Nació de `field/src/lib/components/
> SyncHeader.svelte` y es lo que deben pintar Field, Admon y el panel. El
> componente de referencia del shell es ese mismo banner, desacoplado de los
> stores de la app (ver `banner/SyncHeader.svelte`).

## 1. Reparto de responsabilidades

Esto es lo que evita que el shell se desincronice:

| El shell manda | La app manda |
|---|---|
| El markup (`.sync-header`) | Sus stores y el estado de sincronización |
| Los textos y las clases | Cuándo sincronizar (el `onsync`) |
| El CSS — vive en `body.css` como `.sync-header`, **no** en un `<style>` del componente | `usuario` y las versiones, que ya tiene en `$lib/shell.js` |
| La barra de acciones (`.shell-actions`) y qué botones existen | Qué botones expone y qué hace cada uno |
| La regla de ubicación por IP (`banner/lugar.py`) | Su CSS propio aparte del shell |

El componente de referencia **no importa nada del proyecto**: recibe todo por
props. Por eso el mismo archivo sirve en Field (SvelteKit + TS) y en Admon
(Vite, sin preprocesador de TS) — está escrito en JS plano con JSDoc a
propósito.

## 2. Endpoint: `GET /api/shell/state`

Las apps **deben** exponerlo. El componente lo usa para `lugar`/`ip` (y
como respaldo de `usuario`/versión del shell); el panel lo pinta directo en el
servidor.

```json
{
  "app":   { "id": "field", "nombre": "Field", "version": "0.0.1" },
  "shell": { "version": "1.1.0" },
  "user":  { "nombre": "Hector Pena", "email": "…", "rol": "admin" },
  "sync":  { "estado": "idle|syncing|pending|offline|error",
             "pendientes": 0, "ultimo": "2026-09-27T01:20:00" },
  "lugar": { "modo": "oficina|remoto|desconocido", "ip": "10.188.141.57" }
}
```

| Campo | Regla |
|---|---|
| `app.version` | La de la app: Field y Admon, `package.json` (la lee `$lib/shell.js`); panel, `panel/config.py:APP_VERSION`. |
| `shell.version` | La del shell. Cada app guarda una copia de `VERSION` (el sync la estampa como `ECCSA_SHELL_VERSION`); si no la tiene, el banner muestra `shell ?`. |
| `sync.estado` | `idle` = al día · `syncing` = sincronizando · `error` = falló y se reintenta · `offline` = sin red · `pending` = hay N cambios locales. |
| `sync.pendientes` | Entero (0 si no aplica). |
| `lugar.modo` | `oficina` si la IP es privada/red ECCSA, `remoto` si es pública, `desconocido` si no se puede saber. Lo calcula `lugar.py` (ver §5). |
| `user` | Del login compartido (`HUB_Users` en `ECCSA_Admon`). |

**Autenticación — el prop `fetcher`.** El endpoint exige sesión y no todas las
apps autentican igual:

| App | Cómo autentica | Qué pasa |
|---|---|---|
| Field | `Authorization: Bearer <token>` | el componente necesita un `fetcher` propio |
| Dashboard | **no hay sesión** (es una pantalla) | el kiosco expone el endpoint **sin** `require_user`: `user: null` y el resto igual |
| Admon | `Authorization: Bearer <token>` | ídem |
| Panel | cookie de sesión | el `fetch` same-origin del componente sirve |

Por eso el componente acepta `fetcher`: la app le pasa `(url) => fetch(url, {headers})`
con su cabecera, y si no se pasa usa el `fetch` same-origin peludo. Sin esto el
endpoint devolvería 401 y el lugar se quedaría siempre en `📍 —` sin que se
notara.

### 2b. La app sin sesión (Dashboard)

El kiosco de la oficina es una **pantalla**: no hay quien inicie sesión ni
botones que abrir. El contrato se cumple igual, pero con tres diferencias:

1. `GET /api/shell/state` **responde sin `require_user`**. Devuelve `app`,
   `shell`, `lugar` y `sync`; `user` va en `null` y el banner omite el bloque
   `.who` (eso ya lo hace el componente: sin `usuario` no se pinta).
2. `sync.estado` no es una cola del cliente sino **la salud real del último
   refresco de datos** del backend: `idle` si el snapshot está al día,
   `error` si el último refresco falló y quedó desactualizado. Es el único dato
   que puede leer el banner en una pantalla sin usuario, y es el que evita que
   el kiosco muestre ceros falsos sin avisar.
3. `ActionsBar` se monta solo con los botones que la app exponga. En el kiosco
   eso es únicamente el 📋 de novedades (el de "toca para sincronizar" no
   aplica: la sincronización es del servidor).

## 3. Markup

```html
<button class="sync-header offline|syncing|has-items">
  <span class="dot offline|syncing|error|pending|ok"></span>
  <span>…texto del estado…</span>
  <span class="who">👤 {nombre}
    <span class="lugar oficina|remoto|desconocido" title="{ip}">{icono} {texto}</span>
  </span>
  <span class="vers">v{app.version} · shell {shell.version}</span>
</button>
```

Precedencia: `offline` > `syncing` > `error` > `pending` > `idle`. Sin
`usuario` no se pinta el bloque `.who`. En el panel es un `<div>` en vez de
`<button>`: ahí el banner es de solo lectura y un botón sin acción sería un
control muerto.

## 4. Textos y colores

Idénticos en todas las apps, y salen de los tokens (no de literales):

| Estado | Texto | Fondo del banner | Punto |
|---|---|---|---|
| `offline` | `Sin conexión — modo offline` | `rgba(127,29,29,.64)` | `--color-danger` |
| `syncing` | `Sincronizando...` | `rgba(30,58,95,.64)` | `--color-info` |
| `error` | `Error al sincronizar — reintentando` | (el de base) | `--color-danger` |
| `pending` | `{N} pendiente/s — toca para sincronizar` | `rgba(120,53,15,.64)` | `--color-warning` |
| `idle` | `Todo sincronizado` | `rgba(30,41,59,.58)` | `--color-success` |

El banner es **bien translúcido a propósito** (0.58 en reposo): se apoya en el
`backdrop-filter: blur()` y en que la app ya es oscura, así que el texto se lee
igual y se ve lo que hay detrás. Los fondos de estado van un punto más
opaques (0.64) porque un aviso o un error tienen que leerse de un vistazo.

Lugar: `oficina` → 🏢 Oficina (`--color-success`) · `remoto` → 🏠 Remoto
(`--color-primary-light`) · `desconocido` → 📍 —.

## 4b. Barra de acciones (`.shell-actions`)

Los botones de siempre, con el mismo markup en todas las apps:

```html
<div class="shell-actions">
  <button class="btn btn-sm btn-secondary act-btn" title="Usuarios en línea">👥<span class="act-badge">3</span></button>
  <button class="btn btn-sm btn-secondary" title="Novedades">📋</button>
  <button class="btn btn-sm btn-secondary" title="Configuración">⚙️</button>
  <button class="btn btn-sm btn-secondary" title="Cerrar sesión">🚪 Salir</button>
</div>
```

- **🔔 Notificaciones se quitó del contrato (2026-09-27).** Ya no se usa: las
  notificaciones viven donde corresponde a cada app, no en la barra global — el
  panel las tenía además como pestaña, y quedaba duplicado. La prop `onnotif`,
  el número `notificaciones` y el badge `.act-badge.alert` se conservan en el
  código por compatibilidad, pero el shell ya no los emite. Si una app los
  necesita, que use su propio markup.
- **Un botón se pinta solo si su manejador está.** Cada app decide qué expone:
  el panel ya tiene Configuración como pestaña de su tab bar, y ahí se pasa en
  `null` para no duplicarla.
- El contador (`.act-badge`) va absoluto en la esquina del botón; `.alert` lo
  pinta en `--color-danger` y el de usuarios en línea en `--color-info`.
- Los botones reusan los `.btn` / `.btn-sm` / `.btn-secondary` del shell: la
  barra no define estilos de botón propios.
- Va dentro del `.header` de la app, a la derecha de la marca:

  ```svelte
  <div class="header">
    <div class="brand-col"><h1 class="brand">…</h1></div>
    <ActionsBar … />
  </div>
  ```

  `.shell-actions` ya trae `flex: 1` + `justify-content: flex-end`, así que no
  hacen falta `style="flex:1"` ni `style="display:flex"` inline.

## 4c. Popup de novedades (📋)

Salta **solo la primera vez** que se ve una versión nueva, y después se abre a
mano con el botón 📋.

**Contenido** — un `changelog.json` estático por app, en la carpeta de
estáticos de cada una (`static/` en Field y el panel, `public/` en Admon):

```json
{
  "app": "field",
  "version": "1.10.0",
  "cambios": ["🏢 El banner ahora dice si estás en la oficina o en remoto."]
}
```

Está fuera del código a propósito: se edita sin recompilar. La `version` del
JSON es la que decide si el popup salta, y debería coincidir con la de la app
(`package.json` → `$lib/shell.js`, o `config.APP_VERSION` en el panel).

**"Solo la primera vez"** — se guarda **la última versión vista** en
`localStorage['eccsa:changelog:<appId>']`. Una sola clave, no una por versión:
así no crece sin límite aunque la app se actualice muchas veces. Si esa
versión no coincide con la actual, el popup se muestra y se guarda el valor
**al mostrarse**, no al cerrarse (si el usuario lo cerró sin leer, ya lo vio).

**Dónde va montado** — una sola vez, en el layout (`+layout.svelte` /
`App.svelte`) y no en cada página: si viviera en el header no saltaría al
entrar por cualquier ruta. En el panel, que no tiene build, el HTML se pinta
oculto y un script pequeño decide si lo muestra, con la misma clave.

```svelte
<Changelog appId="field" appName="Field" version={APP_VERSION}
           url="/changelog.json" />
```

El botón 📋 no se cablea: `ActionsBar` lo abre por defecto si el componente
está montado. En el panel el botón usa `data-changelog-abrir`.

## 5. La regla de ubicación

La define **una sola vez** `banner/lugar.py`, que se copia a `api/lugar.py`
(Field, Admon) y `panel/lugar.py` (WorkersAdmon). Los 3 backends son Python, así
que alcanza con un módulo de stdlib.

```python
from lugar import lugar_de            # ASGI / FastAPI
modo, ip = lugar_de(request.headers, request.client.host)

from lugar import lugar_de_handler    # http.server (el panel)
modo, ip = lugar_de_handler(handler)
```

Precedencia de cabeceras, y por qué:

1. **`X-Forwarded-For`, primera entrada** = el cliente original. Es la única
   fuente fiable con un proxy delante.
2. `X-Real-IP` — solo si no hay `X-Forwarded-For`.
3. El host del socket.

> El nginx del ServerVM **reescribe** `X-Real-IP` con el `$remote_addr` del
> proxy inmediato (la IP del puente de Docker, que es privada). Por eso leer
> `X-Real-IP` primero hace que **todo el mundo** caiga en "oficina". Field ya
> lo tenía documentado en `api/routers/online.py`; el panel lo tenía mal y por
> eso su banner mentía.

## 6. Reglas de layout

1. El banner es **fijo arriba** (`position: fixed; top: 0`) con fondo
   semitransparente + `backdrop-filter: blur()`.
2. El contenido lleva la clase **`.shell-below-banner`** para no quedar debajo.
   El alto del banner vive en esa clase, no en un número repetido en el layout
   de cada app.
3. Tocar el banner dispara la sincronización, en las apps con cola offline
   (el `onsync` de cada una).
4. El banner nunca muestra datos sensibles: solo nombre, lugar, versión y estado.
5. Si el endpoint falla, el banner **se sigue viendo** y solo el lugar queda
   `📍 —` (fail silent). El shell no puede tumbar una app.
6. En escritorio el `.vers` se alinea a la derecha con `margin-left: auto`; bajo
   820px se oculta para no comerse el ancho.
