# ECCSA-Shell

**Design system y app shell compartidos de las apps ECCSA.**
Fuente única de: tokens, CSS del shell, banner común, contrato del endpoint de
estado, contrato de las notificaciones push y la receta para crear apps nuevas.

Apps que lo consumen: **Field**, **Dashboard** (el kiosco de la oficina),
**Admon** (`AdmonApp`) y **WorkersAdmon**.

## Estructura

| Ruta | Qué es |
|---|---|
| `tokens.css` | **Fuente de los valores** (color, tipografía, radios, z). Bloque `@theme` (Tailwind v4) + `:root` plano. |
| `src/body.css` | **Fuente de los componentes** (page, header, status-bar, tab bar, cards, botones, banner, reglas iPhone). Sin prólogo de Tailwind. |
| `src/utilities.css` | Utilidades de color que Tailwind v3 (Admon) necesita. |
| `dist/` | **Generado**: `shell.t4.css`, `shell.t3.css`, `shell.plain.css`. No editar. |
| `banner/SyncHeader.svelte` | **El banner estándar** (Svelte): el de Field, desacoplado de los stores. Sin `<style>` — el CSS va en `body.css`. |
| `banner/ActionsBar.svelte` | **La barra de acciones estándar**: 👥 en línea · 🔔 notificaciones · ⚙️ config · 🚪 salir · 📋 novedades. Un botón se pinta solo si su manejador está. |
| `banner/Changelog.svelte` | **El popup de novedades**: salta solo la primera vez de cada versión. Monta el modal y la lógica de "solo una vez". |
| `banner/changelog.js` | Estado compartido del popup (lo usan `Changelog` y `ActionsBar`) + la clave de localStorage. |
| `banner/lugar.py` | **La regla de ubicación** (IP privada = oficina). Python stdlib, lo copian las apps con backend. |
| `banner/banner.py.html` | El mismo banner para apps sin build (el panel). Es un `<div>`: ahí no hay sincronización que disparar. |
| `banner/actions.py.html` | La misma barra para apps sin build, con enlaces en vez de manejadores. |
| `banner/changelog.py.html` | El mismo modal para apps sin build, con la lógica en un script chico. |
| `docs/CONTRATO.md` | Contrato del banner: `GET /api/shell/state`, markup, textos, colores y la regla de ubicación. |
| `docs/PUSH.md` | Contrato de los **avisos al teléfono**: las tres condiciones de iOS, la tabla de suscripciones con `App`, el payload, los handlers del service worker, los endpoints y la receta para añadir push a una app nueva. |
| `docs/CREAR-APP.md` | Receta de 6 pasos para una app nueva. |
| `tools/build_shell.py` | Genera `dist/` desde `tokens.css` + `src/`. |
| `tools/sync_shell.py` | Copia el CSS **y los artefactos del banner** a cada app, y estampa `ECCSA_SHELL_VERSION`. `--check` sale 1 si divergen. |
| `VERSION` | Versión del shell (semver). |

## Qué se propaga a cada app

`tools/sync_shell.py --all` copia dos cosas, elegidas por **variante** (que es
lo que define el stack, no el nombre de la carpeta):

| App | Variante | CSS | Artefactos |
|---|---|---|---|
| Field | `t4` | `src/app.css` | `src/lib/components/{SyncHeader,ActionsBar,Changelog}.svelte`, `src/lib/changelog.js`, `api/lugar.py` |
| Admon | `t3` | `src/styles/app.css` | `src/components/{SyncHeader,ActionsBar,Changelog}.svelte`, `src/lib/changelog.js`, `api/lugar.py` |
| WorkersAdmon | `plain` | `panel/shell.css` | `panel/lugar.py`, `panel/{banner,actions,changelog}.py.html` |

El `.svelte` va **sin `<style>`**: su CSS está en `body.css` como
`.sync-header` y `.shell-modal`, que además necesita ser global para que el
panel (Python, sin build) comparta el mismo código. Lo que la app conserva es su
cableado: los stores y el `onsync` se los pasa por props al componente.

## Uso diario

```bash
# 1. Cambias el diseño (tokens o componentes) aquí
vim tokens.css            # o src/body.css

# 2. Regenerar las variantes y propagar a las apps
python tools/build_shell.py
python tools/sync_shell.py --all

# 3. Verificar que nadie se desincronizó (en CI o antes de un release)
python tools/build_shell.py --check && python tools/sync_shell.py --all --check
```

Cada app guarda su copia (para poder construirse sin red y funcionar offline) y
un `ECCSA_SHELL_VERSION`; el banner muestra `v{app} · shell {versión}` para que
se vea de inmediato si una app quedó atrás.

## Variantes

| App | Stack | Variante |
|---|---|---|
| Field | SvelteKit + Tailwind v4 | `t4` (`src/app.css`) |
| Dashboard | SvelteKit + Tailwind v4 (kiosco Full HD, sin sesión) | `t4` (`src/app.css`) |
| Admon | Svelte + Tailwind v3 | `t3` (`src/styles/app.css`) |
| WorkersAdmon (panel) | Python stdlib, sin build | `plain` (`panel/shell.css`, se lee de disco) |
