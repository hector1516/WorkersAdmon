# Historial de cambios — WorkersAdmon

> Contenedor `workersadmon`: los workers en background, el servidor
> MCP/passkeys/push y el panel de control. Versión y novedades visibles en
> `static/changelog.json` y en el popup 📋 del shell.

## [1.2.1] - 2026-09-28

Revisión visual de la interfaz del panel (también recoge el bump a 1.2.0,
que no había tenido entrada en este archivo).

### Corregido
- **Botones de acción mal dibujados** (`panel/panel.css`): eran cuadrados
  (relación de aspecto 1:1 con `height:100%`), y como la altura salía del
  ancho de la columna, en escritorio salían de ~200px y en la tabla de Apps
  levantaban cada fila ~100px. Ahora las tarjetas usan `display:grid` de
  **2 por fila** (uno solo ocupa la fila entera) y la tabla de Apps mantiene
  los botones **en línea** con `flex-wrap:nowrap` + `min-width` en la celda.
  El alto mínimo sigue siendo `--tap` (44px), que es lo que pide el shell.
- **Los emoji salían vacíos en producción**: `python:3.11-slim` no trae
  ninguna fuente que pinte `💾 🗑️ 📋 🚪 📄`, así que los botones aparecían
  como píldoras de color sin nada dentro. `Dockerfile` instala
  `fonts-noto-color-emoji`.
- **Falta el botón de volver**: ahora todo módulo que no es la home lleva un
  botón con texto (`← Volver al panel`, sobreescribible con
  `page(back_href=…, back_label=…)`), y los logs vuelven a **Estado** en vez
  de saltar a la home (la flecha sola dentro del `<h2>` ya no existe).
- **Subtítulos rotos**: `esc()` escapaba por completo las etiquetas y
  mostraba el texto literal `datos en &lt;code&gt;…&lt;/code&gt;`. `page()`
  ahora acepta un subconjunto de etiquetas seguro (`_SUB_TAGS` /
  `esc_sub()`) y se usa en Estado y Logs.
- **Home con descripción duplicada**: se quitó `subtitle="elegí un módulo"`
  (el módulo ya lo dice en su descripción).
- **Campos de texto y selects** de Apps/Config/Notificaciones: los inputs sin
  `class="input"` mostraban la caja blanca del navegador; el `select` de Tipo
  se cortaba (`SOLO`, `SECR`, `NÚME`); el campo de prueba SMTP medía 190px
  fijos. Se sumó la clase `field` a los bloques `cfg-field` para que los
  labels usen el estilo del shell.
- **Badge "Este es el panel"** se estiraba a todo el ancho de la tarjeta y
  parecía botón (`.wcard > .badge{align-self:flex-start}`).
- **Bloques "no hay log"** usaban `.empty` (padding de 3rem) y encabezados en
  versalitas: ahora es una línea compacta con `wcard-name`.
- El badge de la pestaña Estado decía `shell X · app Y`: `app_version` es la
  copia del shell (`ECCSA_SHELL_VERSION`), no la versión del panel, y hacía
  parecer que el panel era una 1.10.x → ahora dice `copia`.
- `static/changelog.json` estaba en 1.1.0 con `APP_VERSION` en 1.2.0 (el
  popup 📋 nunca iba a saltar; lo valida `check.yml`).
- **El camino rebuild del deploy moría en "Sincronizar el clon canonico"**
  (`.github/workflows/deploy.yml`): git imprime su progreso en STDERR y
  `2>&1 | Out-Null` con `$ErrorActionPreference='Stop'` lo convierte en
  `NativeCommandError`, así que el paso se caía aunque git saliera 0 y el
  rebuild quedaba skipeado. Los `git` de ese paso corren ahora con
  `EAP=Continue` y sin `2>&1` (el progreso queda en el log) y el código se
  captura a mano.
- **`deploy/build.ps1` no ejecutaba ni la primera línea**: en la línea 89
  decía `"...$name: no se puede leer..."` y PowerShell 5.1 lee `$name:` como
  una variable con scope (`$env:PATH`), que es un **error de parseo**: el
  proceso hijo moría antes de crear `build.log` y el deploy fallaba con un
  "el build no llego a escribir build.log" que no explicaba nada. Con `${name}`
  parsea limpio (verificado con el PowerShell del ServerVM). El paso del
  workflow además espera hasta 60s por `build.log` y vuelca stdout/stderr del
  proceso hijo si no aparece.
- **El paso "Rebuild" seguía tirándose DESPUÉS de que el build salía bien**
  (`.github/workflows/deploy.yml`): `docker inspect -f '{{.State.Health.Status}}'`
  revienta con *"map has no entry for key Health"* porque el Dockerfile no define
  `HEALTHCHECK` (build.ps1 valida la salud con `Invoke-WebRequest` a `/healthz`),
  y en PowerShell 5.1 ese STDERR se volvía `NativeCommandError` aunque estuviera
  redirigido con `2>$null`. El template ahora usa `{{if .State.Health}}`, el
  bloque corre con `EAP=Continue` (mismo truco que los `git` del paso de
  sincronización) y se limpian/validan los strings antes de comparar imágenes.
- **La verificación "imagen nueva" nunca había salido bien**: `docker inspect
  -f '{{.Image}}'` devuelve `sha256:<64 hex>` y `docker images --format
  '{{.ID}}'` solo los 12 primeros hex, así que `$img.StartsWith(...)` daba
  *false* siempre y el paso se mataba con "el contenedor no quedo con la
  imagen nueva" aunque el contenedor acabara de recrearse con esa misma imagen.
  Se le quita el prefijo `sha256:` antes de comparar y el error ahora incluye
  los dos ids.
- **El paso quedaba en rojo aunque todos los checks pasaran**: el wrapper de
  `shell: powershell` de Actions cierra con `exit $LASTEXITCODE`, o sea que el
  código de salida del paso es el del último comando nativo, y
  `docker images … | Select-Object -First 1` cortaba el pipeline dejando
  `$LASTEXITCODE=-1` → *exit code 1* justo después de imprimir "OK -
  contenedor recreado con la imagen recien construida". El array se arma sin
  pipelinar el `docker` y el paso termina con `exit 0` explícito cuando todos
  los checks pasaron.
- **`test_60` / `check.yml` en rojo: el panel corría un shell que no existía**
  (`check_daily.py` exige `panel/shell.css` ≡ `shell/dist/shell.plain.css` y
  `ECCSA_SHELL_VERSION` ≡ `shell/VERSION`). El commit `d1edcec` propagó **solo
  los 3 archivos de la app** (la copia quedó en 1.10.1) y el fuente **nunca se
  commiteó en ECCSA-Shell**, que seguía en 1.10.0, igual que la copia
  vendorizada `shell/`. Se publicó **ECCSA-Shell 1.10.1** (los mismos 2 cambios
  en `src/body.css` + `VERSION` + `dist/` regenerado — el sha resultante
  `4e6ac2de301a` es byte a byte el de `panel/shell.css`, y `dist/BUILD.json`
  nuevo coincide con los shas que ya tenían field y AdmonApp) y se
  re-vendorizaron los 6 archivos de `shell/`. `check_daily` sale 0 y la suite
  queda en 76/76.

### Agregado
- `panel/views/apps.py`: helpers con `class="input"` y `sel-tipo` en el
  selector de tipo de valor.
- Pruebas `test_61`–`test_70` en `tests/test_panel.py` (acciones por fila,
  volver, subtítulos, badge sin estirar, select legible, fuentes de emoji,
  bloque de log compacto).

## [1.1.0] - 2026-09-27

### Nuevo
- **Adopta ECCSA-Shell 1.1.0** (`panel/shell.css` como variante sin build,
  generada por el shell; se lee de disco para poder actualizarla sin tocar
  Python).
- **Banner común**: estado de sincronización, usuario, **oficina o remoto** y
  `v{app} · shell {versión}`, con el markup de Field.
- **Barra de acciones estándar** (🚪 salir) y **badge de versión** al pie.
- **Popup de novedades 📋** con la misma regla que las apps Svelte: salta solo
  la primera vez de cada versión.
- **Verificación automática diaria del shell** (`shell/tools/check_daily.py`),
  con el resultado visible en el panel.
- Migración de los 4 workers de Field (avisos, file_indexer, legends_cron,
  legends_audit) y de los 10 del HUB: **el ecosistema queda sin Streamlit**.
- Los 4 workers de Field tenían ficha de configuración en el panel.
- Interfaz optimizada para teléfonos.
- Destinatarios de notificaciones con resumen visible por evento, y
  sub-pestañas de Telegram como en el HUB.
- El worker de PDFs genera las 5 remisiones (módulo RM).

### Corregido
- **El banner mentía sobre la ubicación**: el panel resolvía la IP por
  `X-Real-IP`, que el nginx del ServerVM sobreescribe con el `$remote_addr` del
  proxy inmediato (la IP del puente de Docker, privada) → **decía "Oficina"
  para todo el mundo**. Ahora usa la regla canónica del shell
  (`panel/lugar.py`): `X-Forwarded-For` (primera entrada) → `X-Real-IP` →
  socket.
- El header del panel quedaba **tapado por el banner fijo**: `.wrap` no
  llevaba `padding-top`. Ahora usa `.shell-below-banner` del shell.
- Legends: la semana se calculaba en UTC y dejaba la puntuación semanal en 0.
- Hostname de `worker.ecc-sa.com.mx` (sin la "s" de más).
- `build.bat` pasa `--build-arg WITH_PLAYWRIGHT=1`.

### Cambiado
- El contenedor se levanta con `-p 8000:8000` y hay documentación de la
  migración completa (10/10 workers activos).
- El despliegue usa un script de build + runbook con el patrón compartido.

## [1.0.0]

Versión inicial del panel de control de workers.

## Sin publicar

- **docs/TROUBLESHOOTING.md**: seis problemas reales con su causa y cómo
  verificarlos. El más importante es que **hay dos copias del backend y solo
  una corre** (`Field/api/`), lo que hizo que dos arreglos "desplegados" nunca
  llegaran a producción. Incluye el flujo de vales/Go Vale, la plantilla de
  Telegram, las trampas de cascada de CSS y cómo correr consultas sin
  credenciales desde el host.
- **README.md**: la fila de `vales_worker` aclara que atrapa el folio y no el
  QR, y qué pasa si el folio no se puede leer.
