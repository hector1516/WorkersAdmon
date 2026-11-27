# Diseño del panel WorkersAdmon — contrato

Rediseño completo del panel con lenguaje **Apple / HIG**, independiente del
shell de Field y Admon. El panel deja de cargar `shell.css`: tiene su propio
sistema en `panel/apple.css`, sus iconos en `panel/icons.py` y su layout en
`panel/templates.py`.

## Principios

1. **Contenido primero.** El dato manda; el adorno no existe.
2. **Una sola firma visual:** tarjetas translúcidas sobre un fondo neutro, con
   un acento azul del sistema. Todo lo demás calla.
3. **Familiar, no copia.** Navegación y controles con medidas de sistema
   (44 px táctil, foco visible), pero sin parecerse a Field ni a Admon.
4. **Ligero.** Sin fuentes externas, sin frameworks, sin imágenes pesadas:
   SVG en línea y CSS propio.

## Tokens

### Color

| Rol | Claro | Oscuro |
|---|---|---|
| `--bg` | `#F5F5F7` | `#000000` |
| `--surface` | `#FFFFFF` | `#1C1C1E` |
| `--surface-2` | `#FAFAFC` | `#2C2C2E` |
| `--text` | `#1D1D1F` | `#F5F5F7` |
| `--text-2` | `#5F5F65` | `#A1A1A6` |
| `--separator` | `rgba(0,0,0,.08)` | `rgba(255,255,255,.12)` |
| `--accent` | `#0A84FF` | `#0A84FF` |
| `--success` | `#248A3D` | `#30D158` |
| `--warning` | `#B25000` | `#FF9F0A` |
| `--danger` | `#D70015` | `#FF453A` |
| `--teal` | `#0071A4` | `#40C8E0` |

Sigue `prefers-color-scheme`; no hay interruptor propio de apariencia.
Contraste mínimo: 4.5:1 en texto normal, 3:1 en ≥18 px o negrita.

Los rellenos que llevan texto blanco usan tokens `--*-solid` (más oscuros que
los de badge): `--accent-solid #0066CC`, `--success-solid #1B6B31`,
`--warning-solid #8A4B00`, `--danger-solid #B3261E`. Los tonos claros del
sistema (`#30D158`, `#FF9F0A`, `#FF453A`) no llegan a 4.5:1 con blanco.

### Tipografía

```
--font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
        Roboto, "Helvetica Neue", Arial, sans-serif
```

Escala: 34 (título de página), 22 (sección), 17 (cuerpo), 15 (secundario),
13 (metadatos/tablas), 11 (etiquetas). Peso 400 cuerpo, 590 títulos, 700 cifras.
`letter-spacing: -.01em` en títulos; `font-variant-numeric: tabular-nums` en
cifras.

### Espacio, radio, sombra

- Espaciado en múltiplos de 4: 4, 8, 12, 16, 20, 24, 32, 40.
- Radios: 10 (controles), 16 (tarjetas), 22 (hojas).
- Sombra: `0 1px 2px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.06)`.
- Movimiento: 180–220 ms `cubic-bezier(.2,.8,.2,1)`. Con
  `prefers-reduced-motion` no hay transiciones.

## Layout

| Ancho | Navegación |
|---|---|
| ≥ 1024 px | barra lateral de 248 px, fija, con etiquetas |
| 768–1023 px | barra lateral de 76 px, solo iconos (tooltip/aria-label) |
| < 768 px | barra inferior de 4 destinos + hoja «Más» (5 huecos) |

- Destinos: **Estado** (dashboard), **Logs**, **Configuración**, **Apps**,
  **Avisos**, **Asistencia**, **Notificaciones**, **WhatsApp**, **Correo**,
  **Triaje**.
- Barra inferior: Estado, Logs, WhatsApp, Notificaciones y Más. Avisos,
  Configuración, Apps, Asistencia, Correo y Triaje viven en la hoja «Más».
- Contenido: `max-width: 1200px`, centrado; `padding` 16/24/32.
- `height: 100dvh`, `display: flex`; solo el área de contenido hace scroll.

## Dashboard (Estado)

Es la **raíz**: al abrir `/` se ve el tablero (y `/estado` sigue sirviendo lo
mismo). Por eso el módulo no muestra «Volver al panel».

- **Sin scroll** ni horizontal ni vertical en ningún tamaño: `overflow: hidden`
  en `.dash`, `min-height: 0` en hijos, tipografía y gráficas con `clamp()`.
- **Cuatro KPIs exactos** en una fila: Corriendo, Con error, Detenidos y
  Latidos viejos. Avisos no es un quinto KPI (creaba una segunda fila y hacía
  scroll): su cifra va como badge en la cabecera de la tarjeta Control.
- Rejilla de **2 columnas** (`minmax(0, 1fr)`): Workers ocupa las dos filas de
  la izquierda; Avisos y Control se reparten la derecha. El `Avisos` de la
  tarjeta es su lista; el badge de Control es el conteo.
- La fila de un worker muestra **estado, último latido y Logs**. La descripción
  («qué hace») vive en Configuración y las acciones (Reiniciar, Deshabilitar,
  Activar, Config) en la tarjeta Control.
- En móvil: KPIs 2×2, una columna de tarjetas y la lista de workers con el
  doble de alto; solo las listas internas se desplazan, la página no.

## Comprobación manual (sin scroll)

Con el panel servido, en `/` y `/estado`, a **1440×900** y **390×844**:

```js
document.documentElement.scrollHeight <= window.innerHeight + 1   // true
document.querySelectorAll('.dash--estado').length                  // 1
document.querySelectorAll('.kpi').length                           // 4
```

`tests/test_ui_estado.py` fija la estructura (`.dash`, 4 `.kpi`, badge de
avisos en Control, fila de worker sin ruido) y `tests/test_panel.py` las rutas
y la barra inferior (4 enlaces + «Más»). Medido el 2026-11-27 en Chrome:
`scrollHeight` = `innerHeight` (900) en escritorio y (844) en móvil, en las
dos rutas.

## Componentes (clases)

```
.app            contenedor raíz
.rail           navegación (lateral o inferior según ancho)
.rail__brand    marca (icono + nombre)
.nav-item       destino; .is-active
.topbar         título, subtítulo, acciones
.card           tarjeta base
.kpi            tarjeta de cifra: .kpi__label .kpi__value .kpi__hint
.grid           rejilla de tarjetas
.list           lista de filas: .list__row .list__main .list__meta
.table          tabla: envolver SIEMPRE en .table-wrap
.field .input .select .textarea
.btn .btn--primary .btn--quiet .btn--danger .btn--sm
.badge .badge--ok .badge--warn .badge--bad .badge--info
.segmented      control segmentado (filtros)
.sheet          hoja inferior (móvil), .sheet__backdrop
.empty          estado vacío con icono y una acción
.skeleton       carga
```

Todo control mide **44×44 px** mínimo en táctil y 28×28 en puntero fino; el
foco se ve siempre (`:focus-visible` con anillo de 2 px del acento).

## Iconos

`panel/icons.py` → `icono(nombre, tam=20)` devuelve SVG en línea estilo Lucide
(trazo 1.75, `currentColor`, `aria-hidden="true"`). Nombres:
`dashboard`, `logs`, `settings`, `apps`, `bell`, `clock`, `whatsapp`, `chat`,
`users`, `check`, `alert`, `refresh`, `search`, `chevron`, `plus`, `logout`,
`send`, `trash`, `play`, `pause`, `folder`, `mail`, `car`, `ticket`.

## Lo que no se toca

- HTML generado en Python; sin JS obligatorio (solo mejoras progresivas).
- Contratos: `templates.page(active, body, ...)`, rutas y formularios POST
  existentes. El rediseño es visual y de layout, no de comportamiento.
- Los secretos nunca se pintan; los formularios conservan CSRF.
