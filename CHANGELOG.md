# Historial de cambios — WorkersAdmon

> Contenedor `workersadmon`: los workers en background, el servidor
> MCP/passkeys/push y el panel de control. Versión y novedades visibles en
> `static/changelog.json` y en el popup 📋 del shell.

## [1.2.5] - 2026-09-29

### Agregado
- **`apply_migrations.py` + `migrations/` (47 archivos)**: el runner de
  migraciones y el historial del esquema, que hasta ahora vivían solo en el repo
  del HUB (la app Streamlit, retirada hoy). Este repo es el que se mantiene y
  despliega, y es donde el panel lee las tablas que esas migraciones crearon
  (`HUB_Users`, `HUB_Config`, `HUB_Telegram*`, `HUB_AsistenciaDiaria`…), así que
  es su casa natural.
- **No cambia el funcionamiento**: nada importa el runner, el `hotsync` no copia
  `.sql` y las pruebas no recorren el repo. El `COPY . .` del Dockerfile los deja
  en la imagen sin que nada los ejecute. Y dentro del contenedor el runner
  funciona, porque el entrypoint ya genera las credenciales (`pymssql` y
  `config_db` están).
- El registro es la tabla `schema_migrations` **de la base**, no del repo: por
  eso aplicar desde los dos lados no duplica nada. Lo que sí hay que evitar es
  que las dos copias diverjan, y por eso esta es la que manda.
- `migrations/README.md` con las reglas (un archivo por cambio, idempotente,
  `GO` en línea sola, nada de `;` antes de `ELSE`, sin `TRIM()` en SQL Server
  2014) y dos avisos: la numeración tiene repetidos, y hay dos migraciones
  aplicadas en producción cuyo archivo no existe en ningún repo
  (`0041_dashboard_notas`, `0042_imagen_pantalla`).

Verificado: el runner, desde su nueva casa, resolvió bien su carpeta y
reportó "Sin migraciones pendientes" contra `ECCSA_Admon_Pruebas` (donde las 47
ya están aplicadas) — o sea que funciona y no toca nada.

## [1.2.4] - 2026-09-29

Pestaña **🕒 Asistencia** en el panel. Antes el cálculo y la vista vivían
solo en el HUB (que ya no se usa), o sea que el escáner escribía datos
correctos y **nadie los miraba**.

### Nuevo
- **Vista de asistencias por día** (`panel/views/asistencia.py`, rutas
  `GET/POST /asistencia`, permiso `AccesoDeteccionRed`): tarjetas de resumen
  (afirmables · tardanzas · indeterminados · sin datos · ausentes), filtro por
  fecha y usuario, tabla con **esperada y real por separado**, y la real como
  **ventana** (`08:57–09:00 ±3 min`).
- **Bloque "Evidencia"**: por cada fila, cuántos escaneos hubo ese día, el hueco
  máximo entre ellos y la nota del cálculo. Un veredicto sin evidencia no se
  puede defender, y antes no se guardaba nada de eso.
- **"Calcular y guardar"** (POST con CSRF, porque escribe en
  `HUB_AsistenciaDiaria`): recalcula el día para todos los usuarios con turno y
  muestra el desglose por estado, con aviso cuando hubo días SIN DATOS.
- La leyenda que explica que la hora real es una ventana y que ⚪ Sin datos y
  ➖ No aplica **no** son faltas: sin eso, la diferencia entre los dos se pierde
  y el falso positivo vuelve por la puerta de atrás.

### Corregido
- **`AccesoDeteccionRed` no se leía** en el panel: `panel/db.py` solo pedía las
  columnas de permisos de sus propias pestañas, así que `auth.has_perm` no
  encontraba la de asistencia. Ahora está en la lista.
- **El día salía en inglés** ("Monday 28/09/2026"): `strftime("%A")` usa el
  locale del contenedor. Los nombres van a mano en español, y hay prueba de que
  no vuelva a pasar.

### Agregado
- 6 pruebas del panel (`test_71` a `test_77`): la ventana en la tabla, los
  estados distinguibles entre sí, el permiso (incluido que un POST sin permiso
  también da 403), que calcular sea POST y no GET, que la fecha por defecto sea
  ayer, el estado vacío y el idioma del día. Suite: 143 en verde.

## [1.2.3] - 2026-09-29

Asistencia por MAC: el desfase de 3-5 min y los falsos positivos que venían
con él. Este repo tiene su propia copia de `network_scanner.py` y
`eccsa_db.py` (es de aquí de donde corre el `network_scanner_worker`), así que
el arreglo llega por acá y no solo por el repo de HUB.

### Corregido
- **El evento de presencia se sellaba con la hora de PROCESAR (`GETDATE()`), no
  con el instante del escaneo que lo vio** (`network_scanner.py`). Con el
  worker cada 60s y el escáner cada 180s, eso metía 1-4 min de retraso
  inventado. Ahora `FechaDeteccion` guarda el escaneo y `FechaHora` sigue
  guardando el de proceso: la diferencia entre las dos ES el retraso medido.
- **La llegada se guarda como VENTANA, no como minuto.** Con el escáner cada
  3 min, lo que se sabe es que ocurrió entre el escaneo que no vio el equipo y
  el que sí (`UltimaVezVisto` + `FechaDeteccion`, `VentanaMin` con el ancho).
  El cálculo compara la ventana contra la tolerancia: `TARDE` solo si el
  extremo temprano ya la pasa, `A_SALVO` solo si el tardío no, y
  `INDETERMINADO` si la cruza. La política `PISO`/`TECHO`/`PROMEDIO` decide qué
  minuto se anota.
- **Un día sin escaneo ya no es AUSENTE.** Con el escáner caído no había
  `FechaScan`, no había eventos, y `ausente = (entrada_real is None)` marcaba a
  todo el planta. Ahora es `SIN DATOS`, y un día sin horario es `NO_APLICA`.
- **La presencia de madrugada no cuenta como entrada**: el fallback tomaba el
  primer escaneo del día, y un equipo encendido de noche salía como "llegó a
  las 00:15" y luego como puntual toda la semana.
- **Las tolerancias configurables del turno se usan.** El cálculo comparaba
  contra los literales 15 y 5, leía `Tol_Llegada_Min` y nunca lo aplicaba.
- **Tolerancias asimétricas**: entrada en 0 (era un retardo, no una
  tolerancia) y salida con piso de dos intervalos de escaneo, que es el
  debounce que evita que un AP que se cae un escaneo genere una salida falsa.
- **Los escaneos pendientes se procesan en orden cronológico** (`ASC`): antes
  pedía el más nuevo y el bucle vaciaba la cola al revés.
- **Fin del código duplicado**: `register_presence_event` y
  `update_device_state` estaban aquí y en `eccsa_db.py`, y ya divergían. Ahora
  el worker delega en la capa de datos: una sola copia.
- **`register_presence_event` reintenta con el INSERT viejo** si falla el
  completo: si el código llega antes que la migración, se siguen registrando
  eventos en vez de dejar de generar asistencia en silencio.

### Agregado
- `asistencia_core.py`: la lógica de ventanas y veredictos, en funciones puras
  sin base de datos, para que sea verificable.
- `tests/`: 4 archivos de pruebas (clasificación de ventanas, orden y sellado
  del escáner, cálculo completo con base falsa, y que el `MERGE` no se
  desalinee). Suma 59 pruebas; la suite del panel sigue verde (136 en total).
- La vista de asistencias (en HUB) ahora muestra la esperada de verdad, la
  ventana, y el desglose por estado.

### Corregido en el deploy
- **`deploy/hotsync.sh` no copiaba `network_scanner.py`.** La lista de módulos
  Python era explícita y el escáner no estaba: el hotsync copiaba todo lo
  demás, reiniciaba los procesos, reportaba éxito, y el worker seguía con el
  código viejo en memoria (importado al arrancar). Un cambio en el escáner se
  desplegaba "bien" y no hacía nada. Ahora copia también `network_scanner.py` y
  `asistencia_core.py`, y la verificación por hash los incluye.

**Requiere la migración `0042_asistencia_precisa.sql`** (ya aplicada en
producción; vive en el repo de HUB, que comparte la base).

## [1.2.2] - 2026-09-29

### Corregido
- **La tarjeta del menú era un cuadrado de 230px con 133px de aire muerto**
  (`panel/panel.css`): el shell trae `.module-card` con `aspect-ratio:1` y
  `max-height:230px` —el look cuadrado de Field, donde la tarjeta sí tiene para
  llenar ese alto—, pero el contenido del panel (icono, título y una línea de
  descripción) mide 97px. Medido con el CSS real: la tarjeta salía de
  **216.8×216.8** en escritorio y el menú se veía ralo y descompuesto. Ahora
  `aspect-ratio:auto; max-height:none` y queda de **216.8×130**, con el
  `min-height` del shell (objetivo táctil) intacto y sin recortes de texto.

  Es la única excepción declarada a la regla de "no se redefine nada del
  shell", y es deliberada: el CSS del shell está validado por hash y Field
  quiere sus tarjetas cuadradas, así que el ajuste va en el CSS propio del
  panel, igual que Admon lo hizo en su `Dashboard.svelte` sin tocar
  `styles/app.css`. El orden importa (panel.css va después de shell.css) y
  hay una prueba que lo verifica sobre el CSS que de verdad se sirve.

  Medido a 390 / 820 / 1024 / 1366 / 1920 px: 2 · 3 · 4 · 5 columnas, tarjetas
  de 130px de alto, 0 de 5 recortadas. Antes: 230px de alto, 133px de holgaje.

### Agregado
- `test_71` cubre el override de la tarjeta y que quede **después** del shell
  en la cascada; `test_61` se afinó para que prohíba `aspect-ratio` en las
  reglas de `.actions` (que era el bug del cuadrado) y no en todo el archivo.

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
