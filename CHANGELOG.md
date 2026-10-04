# Historial de cambios — WorkersAdmon

> Contenedor `workersadmon`: los workers en background, el servidor
> MCP/passkeys/push y el panel de control. Versión y novedades visibles en
> `static/changelog.json` y en el popup 📋 del shell.

## [1.4.0] - 2026-10-02

### Agregado
- **`hubmail_worker`: la sincronización de correo sale del backend de HUBMail y
  entra aquí.** Hasta ahora la app web de correo levantaba un hilo por cuenta
  (`app/main.py:102`) que sincronizaba IMAP → MySQL, aplicaba la cola
  `HUBMAIL_PendingOps` y disparaba el push. Se movió el motor completo a
  `cron_sync_hubmail.py` + el paquete `hubmail_worker/`.
  Motivo: una app que se reinicia no debe dejar de sincronizar, y desplegar una
  app no debe arrastrar un ciclo de IMAP de 5 minutos.
- `PyMySQL` en `requirements.txt`. Es la **única** app del ecosistema que habla
  MySQL: la caché de correo (`HUBMAIL_*`) vive ahí y no en `ECCSA_Admon`. No es
  parte del mandato de versiones porque Field y Admon no usan MySQL, así que se
  pinnea aparte (`==1.1.1`) para que un rebuild no cambie el driver sin avisar.
- `docker/conf.d.available/hubmail_worker.conf` y su entrada en el panel
  (Workers → Configuración → **Correo ECCSA**), donde se ven y se cambian las 11
  variables: las dos obligatorias, las tres llaves de operación y las de
  credenciales. Ninguna está escrita en el `.conf`.
- `tests/test_hubmail_worker.py` (15 tests) para las tres guardas que no se
  anuncian solas: la clave de cifrado, el modo ensayo y las rutas de adjuntos.
- `tests/test_imap_store.py` (15 tests) alrededor del `UID STORE`, incluido un
  IMAP mínimo en localhost que registra lo que llega al socket, y una réplica de
  cómo Python 3.11 arma la línea (para que el test siga sirviendo aunque la
  máquina corra 3.13/3.14, donde `uid()` ya envuelve el flag por su cuenta).
- `hotsync.sh`: el worker entra en las tres listas que importan — copia, verificación
  por hash y reinicio — más la copia del **directorio** `hubmail_worker/`. La
  lista de copia es explícita a propósito (ver el comentario en el propio
  script: olvidar un módulo hace que el deploy reporte éxito sin cambiar nada),
  y este es el caso nuevo porque es un paquete y no un archivo suelto.

### Corregido
- **`UID STORE` sin paréntesis: marcar como leído fallaba en algunos buzones.**
  Encontrado leyendo `HUBMAIL_PendingOps` al preparar la migración: 3 ops
  `failed` con el mismo error, `BAD [UID STORE extra parameters supplied
  "\Seen"]`, del 2026-09-01 y 2026-09-09, y 5 `seen` atascadas en `pending` desde
  el 2026-08-26.
  La causa: `imaplib.uid()` de **Python 3.11** pasa los argumentos a
  `_command()` sin tocarlos (solo concatena `b' ' + arg`), así que
  `uid("store", uid, "+FLAGS", flag)` mandaba el flag-list **suelto**:
  `UID STORE 1474 +FLAGS \Seen`. El RFC 3501 lo exige entre paréntesis.
  GoDaddy lo toleraba (107 `seen` salieron bien), pero el servidor de la cuenta
  34 no. Y no se notaba porque la UI refleja el cambio al instante en la caché:
  el correo se veía marcado mientras el `STORE` contra IMAP nunca ocurría.
  Ahora los 5 sitios (`set_flag`, `set_flags`, `move_message`, `delete_message`,
  `delete_messages`) arman la línea explícita y verificada
  `UID STORE <uid> ±FLAGS.SILENT (\Seen)`, que no depende de la versión de
  Python. `APPEND` tenía el mismo detalle con sus flags y también se corrigió.
  Ahora los `STORE` verifican `typ != "OK"`; antes el fallo se perdía.
  **Con una excepción deliberada:** `move_message` NO lanza si falla el
  `\Deleted`. El `UID COPY` ya salió bien, así que el mensaje ya está en el
  destino: lanzar ahí marcaba la op como `failed`, el siguiente ciclo rehacía
  el COPY completo y dejaba una copia nueva cada 5 minutos, sin límite. Avisa en
  el log y deja que el sincronizador reconcilie. Los otros cuatro sí lanzan,
  porque son idempotentes.
- **La clave de cifrado ya no se puede inventar.** El código original generaba
  una clave Fernet nueva si no encontraba el archivo, y escribía el resultado.
  En la API eso es inofensivo; en el worker es rompiente: `decrypt_secret`
  devolvía `""` para **todas** las cuentas y el proceso fallaba contra IMAP cada
  5 minutos sin que nada lo delatara. Ahora no hay autogeneración: sin clave no
  hay descifrado, y `decrypt_secret` **lanza** con un mensaje que dice qué
  variable falta en vez de devolver vacío.
- **`HUBMAIL_Attachments.FilePath` guarda una ruta relativa**
  (`<Cuenta>/<Carpeta>/<UID>/<n>_<nombre>`), no la absoluta del worker. Con la
  absoluta, la app la buscaba en **su** volumen, no la encontraba,
  `os.path.isfile` devolvía `False` **sin error**, y los adjuntos salían
  vacíos. `sync.resolver_adjunto()` resuelve las dos formas, así que lo ya
  escrito sigue siendo legible.

### Seguridad
- `hubmail_worker/config.py` **no trae secretos por defecto**. El original de
  HUBMail traía la contraseña de MySQL, la de SQL Server, el secreto JWT y la
  clave VAPID privada escritas en el código (`eyccazo`,
  `cambia-este-secreto-hubmail`): están en el historial de git y además hacían
  que un despliegue sin variables arrancara "funcionando" contra producción con
  credenciales equivocadas. Aquí todo secreto viene del entorno, y `_requerido()`
  avisa en el arranque si falta.

## [1.3.2] - 2026-10-04

### Corregido
- **Un vale QR se perdía por un screenshot de depuración.** Tras hacer clic en
  "Generar Vale" se tomaba `page.screenshot(...)` sin ninguna protección. Esa
  captura se cuelga (la página de resultado tarda y el default de Playwright son
  30 s), la excepción subía y el vale se reportaba como fallido **aunque ya
  estuviera creado en Go Vale**. Con el error, el worker revertía la solicitud a
  `PENDIENTE` y como solo consulta las `APROBADO`, **el vale desaparecía de la
  cola sin que nadie se enterara**: fue lo que pasó con la solicitud #19
  (Héctor Peña, TDX-777-B, $500, 04-oct 11:21), que se quedó sin QR y sin
  folio.
- Las cuatro capturas de depuración pasan ahora por `_capturar_debug()`, que se
  traga el error y usa un timeout de 5 s. Una captura es una ayuda, no puede
  decidir si un vale se generó o no.
- Hay una prueba que falla si alguien vuelve a escribir un `page.screenshot`
  pelado, que es como volverá el bug.

### Pendiente de decisión
- Al fallar después del clic, el vale puede haberse creado en Go Vale sin
  registrar. **Hay que revisar en Go Vale si el #19 aparece** antes de
  re-aprobarlo, o se cobraría dos veces.

## [1.3.1] - 2026-09-29



### Agregado
- **Avisos por WhatsApp con OpenWA** (gateway de whatsapp-web.js que corre en
  el ServerVM). 5 avisos, con la misma información que ya tenía el resto del
  sistema: saldo Go Vale diario · **saldo bajo (umbral 2,000)** · registro de
  kilómetros · reporte de servicio firmado **con el PDF** · ticket OxxoGas
  **con la foto**.
- **La API key se pega en la pantalla**, en `Notificaciones > Conexión > OpenWA`
  (campo de tipo password, como el token del bot). No se siembra en la base ni
  se pide en variables de entorno: es un paso de configuración, no de despliegue.
- **A quién avisa se elige con los teléfonos, separados por comas**, en la
  sub-pestaña `Avisos`. No hay cuentas que vincular ni permisos por usuario: se
  pega a quien deba recibirlo (`5218123211516, 5512345678`). Con 10 dígitos se
  asume México y se agrega el 52; si algo no es un número, la pantalla lo dice
  y **no guarda** en vez de mandar medio aviso.
- `notif_messages.py`: lo que **se dice** en un aviso vive en un solo módulo,
  compartido por Telegram y WhatsApp. Antes el texto estaba pegado a Telegram;
  con dos canales, dos textos distintos acabarían diciendo cosas distintas del
  mismo dato.
- Botón `🧪 Mandar prueba`: primero consulta `/api/health` (que es público) y
  luego manda un WhatsApp de verdad, porque una key mal pegada con solo el
  health parecería estar bien hasta el primer aviso de la noche.
- **Aviso de ticket OxxoGas en el registro manual del panel**: antes ese camino
  no notificaba a nadie (el push solo le llegaba a quien lo registró).
- `build.ps1` ahora recrea el contenedor conectado a **todas** sus redes, no
  solo a la principal. `workersadmon` quedó en `openwa_default` (para poder
  hablarle a OpenWA por hostname) y, con el script anterior, eso se perdía en
  cada redespliegue: las alertas de WhatsApp se habrían caído solas, sin que
  nada lo indicara.

### Agregado (1.3.1)
- **Botón `🧪 Reenviar último` en cada aviso de WhatsApp**: manda a los
  teléfonos de ese aviso el último caso real, para comprobar que el texto y el
  adjunto salen bien sin tener que esperar a que pase algo.
- Una respuesta a por qué hacía falta: **el aviso no se guarda como evento**.
  Lo que queda en `HUB_WhatsappQueue` es el texto ya rendido (y se borra a los
  30 días), no el evento. Así que el botón reconstruye el caso releyendo el
  último registro de la tabla del módulo: el odómetro de `HUB_RegistroKilometros`,
  el ticket con su foto de `HUB_OxxoGasTickets`, o el último reporte con firma
  de `ReportesServicio` (y su PDF se regenera). El saldo no es un evento sino
  una revisión periódica, así que ahí manda el estado de ahora y lo dice.
- Si no hay ningún registro, lo dice en vez de inventar uno; y si el PDF o la
  foto no se pueden reconstruir, manda el texto solo y avisa.

### Corregido
- **Las fotos de los tickets vinculados a un Vale QR llegan con 16 bytes de
  basura delante del JPEG**, y PIL no las podía abrir, así que el aviso se
  quedaba sin foto (era el caso de los tickets #13, #15, #17, #18, #19 y #55,
  todos con el mismo prefijo `75ab5a8a…`; por cierto terminan bien en `ff d9`,
  o sea que el JPEG de adentro está intacto). Ahora `notif_messages` recorta el
  JPEG de adentro antes de procesarlo: **18 de las 19 fotos guardadas se
  recuperan** (antes 13) y los avisos de ticket vuelven a llevar su foto, en
  WhatsApp y en Telegram. Quien las escribe es el otro lado (Field, flujo del
  Vale QR) y no se toca desde aquí; con el recorte del lector se recuperan
  también las que ya estaban guardadas.
- El botón "Reenviar último" del aviso de saldo bajo ya no manda un WhatsApp
  que dice "saldo bajo" con un "OK" abajo: si el saldo está sano, explica que
  no envió nada.
- **`oxxogas_vales_automation` no se podía conectar a la base**: pasaba
  `tds_version='7.4'` a `pymssql.connect()`, que la versión de FreeTDS de la
  imagen rechaza ("unrecognized tds version: 7.4"). El worker `govale_vouchers`
  llevaba 237 errores seguidos, uno cada 5 minutos: el sync de vales de Go Vale
  y el aviso diario de saldo estaban caídos. Verificado dentro del contenedor
  contra producción: con el parámetro falla, sin él conecta. Al arreglarlo hizo
  el sync de rezago y el saldo pasó de $11,709 (27-sep) a $9,709 (hoy).
- `deploy/hotsync.sh` no copiaba `oxxogas_vales_automation.py`, así que el
  arreglo anterior habría llegado al repo pero no al contenedor.

### Cambiado
- `telegram_alerts._normalize_foto_para_cola` ahora delega en
  `notif_messages.normalizar_foto`: las fotos se recortan igual para los dos
  canales (WhatsApp también las recorta, y pesa menos en los datos del móvil).
- `notif_messages` omite la línea de una etiqueta que quedó sin valor
  (👤 Firmado:) en vez de dejarla colgando con el colon.
- Los avisos de kilómetros que llegan del sync de Field ahora llevan el nombre
  del usuario que las registró: ese camino manda el nombre pero no el id, y
  antes quedaba "Usuario None" en el mensaje.

### Notas
- Telegram y WhatsApp **conviven**: no se tocó ninguna tabla de Telegram ni su
  worker. Si algún día se quiere dejar Telegram, es apagar el bloque.
- El bloque de WhatsApp usa el permiso `AccesoTelegram`: los dos son "avisar a
  la gente" y quien administra uno administra el otro. Separarlos sería un
  `ALTER TABLE` con una columna `AccesoWhatsapp`.
- La migración 0043 se aplicó primero en `ECCSA_Admon_Pruebas`. **Falta aplicarla
  en producción antes de pegar la key.**

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
  en la imagen **solo en un rebuild**, y sin que nada los ejecute; con hotsync no
  se copian (su lista de módulos Python es explícita y no incluye `.sql`), que
  es lo que pasó en este deploy. No importa: el runner se usa a mano, desde un
  clon, no desde el contenedor.
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
