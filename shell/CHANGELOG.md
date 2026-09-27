# Historial de cambios — ECCSA-Shell

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
