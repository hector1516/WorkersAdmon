# Historial de cambios — ECCSA-Shell

## Sin publicar

### Agregado
- **`docs/PUSH.md`**: contrato de las notificaciones al teléfono. Las tres
  condiciones de iOS que el navegador no avisa, por qué las suscripciones
  necesitan columna `App`, el formato de la clave VAPID y el fallo silencioso que
  causa si no es el correcto, el payload (y por qué va `badge_count` en vez de
  `badge`), los handlers del service worker, los cinco endpoints, la receta para
  añadir push a una app nueva, un checklist y una tabla de los errores ya
  pagados. Es lo que hay que leer antes de implementar push en otra app.

  Sin cambio de `VERSION`: es documentación, no toca tokens ni componentes, así
  que no hay nada que re-vendorizar en las apps.

### Corregido
- `docs/VERSIONES.md` decía que las suscripciones push viven en
  `HUB_PushSubscriptions`. Ya no: la tabla que usan las apps es
  `HUB_PushSuscripciones`, que **lleva columna `App`** precisamente para que un
  aviso de una app no aparezca dentro de otra.


## [1.1.1] - 2026-09-27

Cambio interno, **sin efecto visual**: los valores que el CSS tenía escritos a
mano ahora salen de los tokens, y se corrigió un bug que hacía que Field
perdiera tres de ellos.

### Corregido
- **Field perdía los tokens estructurales.** La variante `t4` solo emitía el
  bloque `@theme` de Tailwind v4, y los tokens que no son colores ni medidas
  (`--color-line`, `--z-*`, `--radius-*`, `--nav-h`, `--tap`) viven en el
  bloque `:root` — que no se emitía. Como el CSS pasó a usar `var(--color-line)`
  y `var(--z-status)`, en Field el banner se quedaba **sin borde inferior** y
  el status bar **sin capa de z-index**. `build_shell.py` ahora emite el
  `:root` en t4, antes del `@theme` para no pisar lo que Tailwind genera.

### Cambiado
- **Los tokens son de verdad la fuente única.** `tokens.css` declaraba 10
  tokens que nadie usaba, mientras el CSS escribía a mano `z-index: 250`,
  `border-radius: 12px`, `min-height: 44px` y `bottom: 5.5rem`. Ahora todo eso
  sale del token. Se borró `--color-inset`, que no lo usaba nadie.
- **`.status-bar` vuelve a su propia capa** (`--z-status: 200`), por debajo
  del banner. Al mapearlo a `--z-banner` (250) quedaba al mismo nivel.
- **`.pending-badge` y `.toast-sync` borrados**: cero usos en las 3 apps.
- El `font-family` del body sale de `--font-family` en vez de la cadena
  `'Outfit', system-ui, sans-serif` repetida.

### Nuevo
- **CI que impide la desincronización** (`.github/workflows/check.yml`): en
  push, PR y una vez por día verifica que `dist/` esté generado, que el popup de
  novedades diga la misma versión que el banner, y que las 3 apps tengan
  **exactamente** la copia del shell. Este último es el que caza a alguien
  editando `src/app.css` a mano: sin él la copia se iba desfasando en silencio.
- `tools/check_changelog.py` y `propagate.py --check` (verifica sin escribir).
- **`.github/workflows/check.yml` en cada app**: falla el build si su CSS fue
  editado a mano (compara `ECCSA_SHELL_SHA`), si el popup de novedades está
  desfasado, y si el front no compila. Todo sin necesitar secretos.
- **`AGENTS.md` en Field y Admon**, y sección del shell en el de WorkersAdmon.
  Faltaba por completo y es donde se explican las reglas (qué es generado, qué
  es cableado, de dónde sale la versión).
- **`README.md` en Admon**, que no tenía.

### Corregido (herramientas)
- `sync_shell.py --target`: la variante se deducía comparando subcadenas del
  path, y `"admon" in "/ruta/WorkersAdmon"` era verdadero → el panel recibía la
  variante de Admon y, sin `--check`, escribía `src/styles/app.css` en el repo
  equivocado. Ahora se deduce por el archivo que el repo ya tiene.
- Las rutas de los clones se pueden fijar con `SHELL_APP_FIELD`,
  `SHELL_APP_ADMON` y `SHELL_APP_WORKERSADMON`; antes estaban fijas en el
  código y había dos clones de Admon que se contradecían en silencio. Si hay
  más de uno, el script dice cuál eligió.
  OJO: **no** usar prefijo `ECCA_*` para esto — en sandboxes de agentes las
  variables `ECC*` se descartan al lanzar los procesos hijos.
- `check_versiones.py` reventaba con traceback si no había `docker`. Ahora
  avisa que solo corre en el ServerVM y suma `--solo-listado` para consultar el
  mandato desde cualquier lado.
- `DESIGN.md` de WorkersAdmon decía que Admon usa `src/app.css` (usa
  `src/styles/app.css`) y que el panel replicaba los tokens a mano (ya no: se
  propagan solos).
- `check_changelog.py` daba "OK" sin revisar nada cuando no encontraba un repo.
  Un check que pasa sin mirar es peor que no tener check.

## [1.1.0] - 2026-09-27

### Nuevo
- **Popup de novedades 📋**: salta **solo la primera vez** que se ve una versión
  nueva y después se vuelve a abrir con el botón 📋 de la barra de acciones.
  Vive en el shell (`banner/Changelog.svelte` + `.shell-modal` en el CSS), y el
  contenido —qué cambió— es un `changelog.json` estático por app, así se
  edita sin recompilar. La apps Svelte comparten el estado por un módulo
  (`banner/changelog.js`) porque el botón vive en el header de cada página y el
  modal tiene que vivir en el layout; el panel lo resuelve con un script
  chico, ya que no tiene build. La regla es la misma en las tres: se guarda
  **la última versión vista** en `localStorage['eccsa:changelog:<appId>']`
  (una sola clave, no una por versión, para que no crezca sin límite).
- **Barra de acciones estándar** (`banner/ActionsBar.svelte` + `.shell-actions`
  en el CSS + `banner/actions.py.html` para el panel): 👥 usuarios en línea ·
  🔔 notificaciones · ⚙️ configuración · 🚪 salir, con el mismo markup y estilos
  en las 3 apps. Nació del header de Field, que los tenía copiados en el
  `<style>` de **una sola página**.
- **El banner de Field pasa a ser el estándar del ecosistema.** El componente de
  referencia (`banner/SyncHeader.svelte`) es ese banner, desacoplado de los
  stores: sin imports del proyecto, sin `<style>`, y con el estado, el usuario
  y el `onsync` entrando por props. Escrito en JS plano + JSDoc para que el
  build de Admon (vite sin preprocesador de TS) no se rompa.
- **Regla de ubicación compartida** (`banner/lugar.py`): una sola implementación
  de "IP privada = oficina" en Python stdlib, que copian las 3 apps
  (`api/lugar.py` y `panel/lugar.py`). Trae `lugar_de()` para ASGI y
  `lugar_de_handler()` para `http.server`, y documenta la precedencia de
  cabeceras: **X-Forwarded-For (primera entrada) → X-Real-IP → socket**.
- **`--color-info`** (`#3B82F6`) como token: formaliza el azul que ya estaba
  hardcodeado en `.badge-info` y `.toast-sync`, y lo usa el punto de
  "sincronizando".
- **`.shell-below-banner`**: el padding que evita que el contenido quede bajo el
  banner fijo sale del shell, en vez de estar repetido como número mágico en el
  layout de cada app.
- **`sync_shell.py` propaga también los artefactos** del banner (componente,
  `lugar.py` y el HTML del panel) y los valida en `--check`. El destino se
  elige por **variante**, que es lo que define el stack.
- **Estado `error`** en el banner: `Error al sincronizar — reintentando`, con
  punto rojo. Estaba en el contrato desde 1.0.0 pero ninguna app lo pintaba.
- **`GET /api/shell/state` en Field**, que era el requisito del contrato que
  Field no cumplía (y por eso el componente de referencia no le servía).

### Corregido
- **El header del panel quedaba tapado por el banner fijo**: su `.wrap` no
  llevaba `padding-top`. Ahora usa `.shell-below-banner`.
- **No se podía salir ni abrir configuración desde cualquier página que no
  fuera el home de Field o el dashboard de Admon**: los botones estaban
  duplicados en el `<style>` de esas dos páginas y en ningún otro lado. Al ser
  parte del shell, ahora están en todas las páginas.
- Admon no tenía forma de mostrar el contador de avisos que Field sí tenía.
- Los colores del punto del banner ya salen de tokens: el de "todo
  sincronizado" era `#10B981` en Field/Admon y `#22C55E` en el panel — **dos
  verdes distintos**; el de "sincronizando" era azul en Field/Admon y ámbar en el
  panel.
- El fondo del banner y sus tres fondos de estado solo existían en Field/Admon;
  el panel lacked de los tres.
- **El panel mentía sobre la ubicación**: resolvía la IP por `X-Real-IP` primero,
  que bajo nginx es el puente de Docker (privada) → todo el mundo veía
  "Oficina". Ahora usa `lugar.py` con la precedencia correcta.
- La versión del panel se lee de `config.APP_VERSION` y ya tiene
  `.version-badge` como Field y Admon, en vez de verse solo en el banner.

### Cambiado
- **El banner quedó más translúcido**: el fondo de reposo bajó de `0.72` a
  `0.58` y los fondos de estado de `0.78` a `0.64` (quedan un punto más
  presentes que el reposo, porque un aviso tiene que leerse de un vistazo). El
  `backdrop-filter: blur()` y el fondo oscuro de la apps sostienen el texto.
- `.shell-banner` (35 líneas que ninguna app usaba) se reemplaza por
  `.sync-header` en `src/body.css`, con todos sus selectores anidados bajo
  `.sync-header` para que `.dot`, `.who`, `.vers` y `.lugar` no se filtren al
  scope global.
- `docs/CONTRATO.md` reescrito: markup, precedencia de estados, textos, colores
  con su token, y la regla de ubicación.

## [1.0.0] - 2026-09-27

### Nuevo
- **Tokens** (`tokens.css`): color, tipografía, radios y z-index como fuente única.
  Bloque `@theme` (Tailwind v4) y `:root` plano (apps sin build).
- **Shell** (`src/body.css`): CSS de componentes extraído de `field/src/app.css`
  (page, header, status-bar, pending-badge, toasts, tab bar inferior, cards,
  botones, badges, empty state, splash, reglas de iPhone) + **banner común**.
- **Banner común**: fijo arriba, semitransparente con blur; muestra estado de
  sincronización, usuario, **oficina o remoto** y `v{app} · shell {versión}`.
- **Contrato** (`docs/CONTRATO.md`): `GET /api/shell/state` con app, shell, user,
  sync y lugar; textos idénticos en todas las apps; fail silent.
- **Herramientas**: `build_shell.py` (genera 3 variantes) y `sync_shell.py`
  (propaga + `--check` para detectar divergencia), que además escribe
  `src/lib/shell.js` con las versiones para las apps Svelte.
- **Receta** `docs/CREAR-APP.md` para dar de alta una app nueva.

### Apps adoptadas
- Field: `src/app.css` pasa a ser la copia canónica; banner con lugar y versión.
- Admon: `src/styles/app.css` como variante Tailwind v3; nuevo
  `GET /api/shell/state`; banner con lugar y versión.
- WorkersAdmon: el panel lee `panel/shell.css` (canónico) + `panel/panel.css`
  (propio) desde disco; banner y `GET /api/shell/state`.
