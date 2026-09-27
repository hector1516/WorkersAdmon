# ECCSA Shell — el patrón de diseño de las apps ECCSA

> **Nombre del patrón: “ECCSA Shell”** (repo `hector1516/ECCSA-Shell`).
> Es la APP SHELL de PWA + un design system propio, compartido por
> **Field**, **Admon** y **WorkersAdmon**. La idea: *toda app nueva arranca
> copiando el shell y los tokens, y no inventa su propia interfaz*.

---

## 1. ¿Por qué tiene nombre?

El patrón no es un invento nuestro: es la **“PWA app shell”** que documentan
Google/Microsoft (cascarón que se descarga y luego funciona sin red) con el
**layout de navegación inferior** (“bottom tab bar / bottom navigation”) que
usan las apps nativas de iOS. Lo que lo convierte en *nuestro* es la capa de
identidad: tokens, textura, passkeys, push y las reglas de pulido de iPhone.

| Estándar / patrón | Qué aporta | Dónde vive en nuestro caso |
|---|---|---|
| **PWA app shell** | Cascarón instalable: header + navegación + contenido, carga instantánea y funciona offline | `panel/templates.py: page()` (panel), `src/routes/+layout.svelte` (Field), `src/App.svelte` (Admon) |
| **Bottom tab bar** | Navegación fija abajo, al alcance del pulgar, con safe-area del notch | `.bottom-nav` / `.nav-item` |
| **Design tokens** | Una sola fuente de color, tipografía y radios | `@theme` de Tailwind (Field/Admon) ≡ `:root` del panel |
| **Mobile-first + HIG-ish** | Objetivos ≥44px, inputs 16px, sin zoom iOS, sin tap-highlight | media queries del panel + `src/app.css` |
| **Offline-first** | Cache local (IndexedDB) y reintentos al volver la red | `admonDb.js` / `fieldDb.js` (`legendsCache`, contactos…) |

## 2. Tokens (idénticos en las 3 apps)

```css
--color-bg:        #0F172A   /* fondo */
--color-surface:   #1E293B   /* tarjetas, tab bar */
--color-surface-2: #334155   /* campos, encabezados de tabla */
--color-primary:   #FF6B00   /* acento ECCSA */
--color-primary-light: #FFAE00
--color-text:      #F8FAFC
--color-text-muted:#94A3B8
--color-success:   #22C55E
--color-warning:   #F59E0B
--color-danger:    #EF4444
--font-family: Outfit, system-ui, sans-serif
--radius: 16px   /* tarjetas */   --radius-sm: 12px   /* botones, campos */
```

- Fondo: `#0F172A` + **textura de engrane** (`engrane.png`) repetida a `140px` con `opacity: .05` y `pointer-events:none`.
- Icono/identidad: en Field y Admon el logo vive dentro de un cuadro oscuro
  (`#0B1220`) con borde `rgba(255,255,255,.08)`.

## 3. Estructura del shell

```
┌───────────────────────────────┐  ← body::before = engrane 5%
│ header  (sticky, blur 12px)   │    logo + título + piloto ● CONECTADO
├───────────────────────────────┤
│ flash (ok / err)             │
│ contenido                     │    .page / .wrap con
│   métricas (.cards)           │    padding-bottom = nav + safe-area
│   listas → TARJETAS           │
│   datos tabulares → .tscroll  │
├───────────────────────────────┤
│ 🔧 Workers ⚙️ Config 🔔 Notif │  ← .bottom-nav fija (icono + etiqueta)
└───────────────────────────────┘     padding-bottom: max(.4rem, env(safe-area-inset-bottom))
```

En escritorio (≥900px) la tab bar se centra y se redondea por arriba
(`width: min(720px, 100%)`) para que no se pegue a los bordes.

## 4. Reglas de móvil (las que más se notan en iPhone)

1. **Objetivos táctiles ≥44px** en botones, nav-items, checkboxes y filas.
2. **Inputs y selects a 16px**: por debajo, iOS hace zoom al enfocar y deja la
   página descolocada.
3. **Safe areas**: `viewport-fit=cover` + `env(safe-area-inset-*)` en contenido
   y tab bar; el header usa `sticky` para no perderse al hacer scroll.
4. **`100dvh`** (altura dinámica) para respetar la barra de direcciones móvil.
5. **Nada de tablas para listas**: los datos tabulares van en `.tscroll`
   (scroll horizontal); las listas y estados, en **tarjetas** (`.wcard`).
6. `-webkit-tap-highlight-color: transparent` y `touch-action: manipulation`
   (elimina el flash gris y el retardo de 300 ms).
7. `.btn:active { transform: scale(.97) }` — “squash” al presionar, como las
   apps nativas.

## 5. Checklist para una app nueva

- [ ] Copiar `tokens` (§2) y la textura de engrane.
- [ ] `viewport` con `viewport-fit=cover` + metadatos iOS (`apple-mobile-web-app-*`).
- [ ] Manifest + iconos (120/152/167/180/192/512 + maskable) + service worker
      (**solo GET**; nunca interceptar POST con FormData).
- [ ] Tab bar inferior con `env(safe-area-inset-bottom)`.
- [ ] Botones ≥44px, inputs 16px, listas en tarjetas.
- [ ] Login con **passkey** (WebAuthn, RP raíz `ecc-sa.com.mx`) y contraseña
      como respaldo.
- [ ] Push (VAPID en `HUB_Config`) y modo offline con IndexedDB.
- [ ] Probado en iPhone *instalado* (no solo en el navegador).

## 6. Dónde vive el shell en cada app

> **Esta sección estaba desactualizada y mentía.** Decía que Admon usaba
> `src/app.css` (usa `src/styles/app.css`) y que el panel replicaba los tokens
> a mano (ya no: se propagan solos). La tabla de abajo es la real.

El shell vive en su **propio repo**, `hector1516/ECCSA-Shell`, y cada app
guarda una **copia** de lo que le toca. Las copias se generan; no se editan.

| App | Stack | Variante | CSS (copia generada) | Artefactos del banner |
|---|---|---|---|---|
| Field | SvelteKit + Tailwind v4 | `t4` | `src/app.css` | `src/lib/components/{SyncHeader,ActionsBar,Changelog}.svelte`, `src/lib/changelog.js`, `api/lugar.py` |
| Admon (AdmonApp) | Svelte + Tailwind v3 | `t3` | `src/styles/app.css` | `src/components/{SyncHeader,ActionsBar,Changelog}.svelte`, `src/lib/changelog.js`, `api/lugar.py` |
| WorkersAdmon | Python stdlib (sin build) | `plain` | `panel/shell.css` | `panel/lugar.py`, `panel/{banner,actions,changelog}.py.html` |

**Cómo se propaga:**

```bash
# en el repo ECCSA-Shell
vim tokens.css src/body.css      # 1. se cambia el diseño AQUÍ
python tools/build_shell.py       # 2. genera dist/ (3 variantes)
python tools/sync_shell.py --all  # 3. copia a las 3 apps + estampa versiones
```

- Si cambiaste el diseño hay que **subir `VERSION`**: la guarda te lo reclama
  comparando el SHA de `ECCSA_SHELL_SHA`.
- Un `git push` al shell propaga solo (`.github/workflows/propagate.yml`), y
  cada app se despliega con su propio workflow.
- El `check.yml` de cada app verifica que su copia **no** fue editada a mano
  (compara el SHA del CSS). El `check.yml` del shell verifica que las 3 copias
  estén al día.

**Lo que NO se replica a mano:** nada. El panel ya no mantiene sus propios
tokens: lee `panel/shell.css` de disco, así que actualizar el shell no requiere
tocar Python. Lo único propio del panel queda en `panel/panel.css`.
