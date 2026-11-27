# QA visual Apple del panel — revisión y cierre

Fecha: 2026-11-26 · Rama `main` (sin push, sin despliegue, sin notificaciones).

## Resumen

El rediseño ya estaba aplicado en las vistas; faltaba cerrarlo: pruebas que
afirmaban el look viejo, nodos ocultos de compatibilidad, CSS dentro del HTML y
varios fallos de accesibilidad reales (contraste en oscuro, foco de teclado,
zoom bloqueado). Nada se revirtió al diseño anterior.

## Crítico (corregido)

1. **Texto blanco sobre rellenos oscuros insuficiente.**
   `success` 4.40:1 en claro y `#30D158`/`#FF9F0A`/`#FF453A` 2.02–3.41:1 en
   oscuro. Se añadieron tokens `--accent-solid #0066CC`, `--success-solid
   #1B6B31`, `--warning-solid #8A4B00`, `--danger-solid #B3261E`; los botones y
   las burbujas salientes los usan. Ratios 5.57–6.80:1.
2. **`--text-2 #6E6E73` al borde.** 4.53:1 sobre `--fill` y 4.66:1 sobre `--bg`,
   por debajo del mínimo en etiquetas de 13 px. Pasa a `#5F5F65`
   (5.66:1 / 5.82:1).
3. **Teclado sin foco en los campos.** `input:focus { outline:none }` quitaba el
   anillo también al navegar con Tab. Se añadió `:focus-visible` con anillo de
   acento para inputs, selects y textareas.
4. **Zoom bloqueado.** El viewport traía `maximum-scale=1,user-scalable=no`.
   Se retiró: el texto puede ampliarse.
5. **Hora de las burbujas salientes.** Blanco al 85 % sobre `#0A84FF` daba
   4.0:1. Fondo `--accent-solid` y opacidad 0.95 → 5.17:1.

## Alto (corregido)

- **Nodos ocultos de compatibilidad eliminados**: `_COMPAT_CANDADO` y
  `_COMPAT_ROJO` en `notifications.py`; `LEGADO` (clases de badge viejas) en
  `wa_conversaciones.py`. Las burbujas ya no escriben «Entrante/Saliente» como
  texto suelto: la dirección va en `data-direccion`, alineación y `aria-label`.
- **Estilos fuera del HTML**: se eliminaron los bloques `<style>` de
  `workers.py`, `notifications.py` y `wa_conversaciones.py` y los 77 atributos
  `style=""` de las vistas. Viven como clases en `panel/apple.css`
  (`.dashx__*`, `.ncard__*`, `.wa-*`, `.as-*`, `.co-*`, `.tr-*`, `.nota-*`).
  Una prueba nueva vigila que no vuelvan.
- **Navegación**: barra inferior de **5 destinos + «Más»**; `aria-current` en el
  activo; en 768–1023 px el rail solo-iconos ahora lleva `aria-label` y `title`
  (antes el texto se ocultaba con `display:none`, que también lo ocultaba al
  lector de pantalla).
- **Tablas**: la de Asistencia y la de Logs quedaron dentro de
  `table-wrap`/`tscroll`; se quitó `white-space:nowrap` de los encabezados y de
  `.list__meta`.
- **Dashboard sin scroll**: el alto y la rejilla salieron del `style=""` a
  `.dash--estado` / `.dash__kpis`; `.content:has(.dash)` fija la columna y solo
  las listas internas se desplazan.
- **Emojis decorativos**: tarjetas del menú, «Logs» y «Qué hace» usan
  `icono()`; el icono `info` se agregó a `panel/icons.py`. Los estados del
  login pasaron a texto.

## Medio (corregido)

- `theme-color` viejo `#0F172A` retirado (queda claro/oscuro).
- `padding:0` y demás ajustes sueltos pasaron a `.card--flush`, `.co-sep`, etc.
- Se retiró el banner/changelog muerto de `templates.py` y sus imports en
  `server.py`.
- `panel/panel.css` se eliminó: ya no se cargaba y sus reglas están en
  `apple.css`.
- El color del borde de las cuentas de correo ya no es un `style=""`: se mapea
  a clases de la paleta; un color desconocido cae al borde neutro.

## Juicio propio

- Se conservan en `apple.css` los neutralizadores `.sync-header`,
  `.bottom-nav` y `.nav-icon` solo para HTML viejo en caché del service worker;
  no los emite ninguna vista.
- `panel/shell.css` se queda aunque el navegador no lo cargue: `check_daily.py`
  y `check.yml` lo comparan contra `ECCSA-Shell`.
- Permitir que los encabezados de tabla partan línea es preferible a forzar
  scroll horizontal por una etiqueta larga.
- El campo «Icono» de Correo conserva el emoji por defecto (`📮`): es un dato
  que Mailbox pinta, no decoración del panel.

## Rojos de entorno (pre-existentes, documentados)

`python -m unittest discover -s tests` → **759 pruebas, 3 fallas, 6 errores**
(antes del cambio: 758, 6 fallas, 7 errores). Ningún rojo es nuevo.

- `test_06`, `test_07`, `test_08`, `test_10`, `test_16`, `test_22` (y a veces
  `test_36`): el panel de prueba no arranca en Windows porque no hay
  `supervisorctl`/`status_web`; los dobles no cubren ese camino.
- `test_60_chequeo_diario_del_shell`: `dist/ desactualizado` porque
  `build_shell.py` revienta con cp1252 al imprimir `→`/`≠` en Windows.
- `test_sin_equipos_de_esa_app_no_marca_enviado` y
  `test_texto_lleva_folio_cliente_tecnico_fecha_y_firma`: datos/fechas de otros
  workers, ajenos al panel; ya fallaban antes.

## Verificación

- `python -m py_compile` de todo `panel/` → OK (`compileall`).
- `python -m unittest tests.test_ui tests.test_ui_estado tests.test_ui_config
  tests.test_ui_wa tests.test_ui_ops` → **63 pruebas, OK**.
- `python -m unittest tests.test_panel` → 138 pruebas, 1 falla (`test_60`) y 7
  errores, todos de entorno.
