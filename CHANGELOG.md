# Historial de cambios — WorkersAdmon

> Contenedor `workersadmon`: los workers en background, el servidor
> MCP/passkeys/push y el panel de control. Versión y novedades visibles en
> `static/changelog.json` y en el popup 📋 del shell.

## [1.8.0] - 2026-10-10

### Agregado
- **El aviso del ticket de OxxoGas lleva el saldo del monedero.** Cuando se
  registra un ticket, el aviso ahora muestra cuánto queda en el monedero
  OxxoGas/Go Vale (`HUB_Config.govale_saldo`, el mismo dato del aviso de saldo).
  Sale por los tres canales del ticket: **WhatsApp** (`notif_messages.datos_ticket`),
  **Telegram** (`api/telegram_hub.py` y `telegram_alerts.py`) y el **push de Admon**
  (`notif_dispatch.py` → `TICKET_OXXOGAS`). Si el saldo todavía no tiene revisión,
  la línea se omite en vez de salir vacía.
- **Migración `0057_oxxogas_ticket_saldo.sql`:** agrega el placeholder `{Saldo}`
  a las plantillas que ya existen en `HUB_WhatsappEventos` y `HUB_TelegramEventos`
  (respeta lo personalizado: solo agrega la línea si aún no menciona `{Saldo}`).

## [1.7.1] - 2026-10-06

### Corregido
- **Se retiró el worker de correo viejo.** `hubmail_worker` escribía la caché
  del buzón en **MySQL** y era el único usuario de MySQL de la app
  (`hubmail_worker/db.py` es un pool de conexiones; el worker no toca SQL Server
  en ningún punto). Se fue el paquete completo, `cron_sync_hubmail.py`, su
  `.conf`, su runbook y sus tests, y PyMySQL salió de `requirements.txt`.
- **El correo no se pierde.** `mailbox_worker` hace lo mismo (sincroniza IMAP y
  drena la cola) contra SQL Server, y ya era el único worker habilitado en la
  práctica: `hubmail_worker` llevaba días sin arrancar (`FATAL` al iniciar el
  contenedor). Ahora hay un worker de correo y una sola base de datos.
- **Fuera la huella del worker en el deploy y en el panel:** el paso de
  variables `HUBMAIL_*`, el aviso de secretos faltantes y el montaje del volumen
  `hubmail_data` en `run_container.ps1`; el copiado, la verificación por hash, el
  reinicio y el alta de programa en `hotsync.sh`; y la ficha del worker con sus
  campos de configuración en `panel/workers.py` y `panel/spec.py`.
- **Un deploy venía fallando desde antes:** el banner de versión (1.5.1) y el
  popup del changelog (1.4.0) no cuadraban, y `check.yml` exige que sean iguales.
  Ambos quedan en 1.7.1.

### Notas
- Un test se reapuntó en vez de borrarse: `tests/test_imap_store.py` fija la
  semántica de IMAP que sí importa y `mailbox_worker` tiene esos métodos casi sin
  cubrir. `tests/test_almacen_adjuntos.py` sí se borró: probaba el backend SMB
  que `mailbox_worker` no tiene.
- El volumen `hubmail_data` no se tocó: lo compartían `admon` y `migra_adj`.

## [1.7.0] - 2026-10-06

### Corregido
- **El concepto de Go Vale ya no exige descripción.** La app Field dejó de
  pedirla (desde el 2026-10-06 pedir un vale es solo elegir el vehículo), así que
  `crear_vale()` la armaba con la vacía: `Vale QR - PLACA - `, con el guion
  colgando, o con un `None` si venía nula. Ahora solo agrega la descripción si
  viene con texto. El resto de la generación (navegación, selección de empresa y
  contacto, importe y cantidad) queda igual.

### Agregado
- **El despachador entrega también las colas de Field.** `cron_avisos_push.py`
  recorre `AVISOS_APPS` (por omisión `admon,field`) en el mismo bucle, en vez de
  un proceso por app: un proceso más serían una segunda entrada de supervisor, un
  segundo log y un segundo heartbeat para lo que es el MISMO bucle. `AVISOS_APP`
  sigue funcionando para quien quiera una sola app. **Los detectores NO se
  comparten**: son de Admon (leen tablas de Admon y encolan para destinatarios de
  Admon), así que corren una sola vez, con `AVISOS_APP_DETECTORES`.
- **Aviso de vale generado** (`oxxogas_vales_automation.py`): cuando Playwright
  confirma el folio en Go Vale, se encola `VALE_GENERADO` (`App='field'`) para
  `HUB_SolicitudVales.IdSolicitante`, que es quien lo pidió. Solo cuando hay
  folio: sin él el vale sigue `APROBADO` y se reintenta, así que todavía no hay
  nada que entregar.

### Corregido
- **El `App` ahora llega hasta el envío, no solo hasta la cola.** Antes
  `_correos_de()` tenía `App = 'admon'` hardcodeado y
  `get_push_subscriptions_by_emails()` leía `HUB_PushSubscriptions` (la tabla
  VIEJA, por `UserEmail`, sin columna `App`). Una fila de la cola de Field buscaba
  los equipos de Admon, no encontraba ninguno y quedaba en `FALLADO` con el
  detalle "sin dispositivos que acepten": **el aviso se perdía entero sin un solo
  error.** Ahora ambas funções reciben `app` y filtran por
  `HUB_PushSuscripciones.App`.
- **El `tag` de los avisos lleva el app** (`field-reporte_firmado` en vez de
  `admon-…`): si un mismo tipo existiera en dos apps, el aviso de una taparía al
  de la otra.

### Pruebas
- 5 pruebas nuevas en `tests/test_avisos.py` (`TestDespachoPorApp`): que
  `_correos_de()` y `despachar()` usen el `App` que se les pasa, que el resumen
  salga por su app, y que sin equipos la fila quede `FALLADO` y no `ENVIADO`.
  Suite completa: **503 en verde**.

## [1.6.0] - 2026-10-06

### Agregado
- **Módulo "📣 Avisos"** (`panel/views/avisos.py`): tarjeta nueva en la home y
  pantalla con los cinco tipos de aviso, su interruptor, a cuántas personas le
  llegarían ahora mismo y el horario de jornada. Requiere `AccesoAppConfig`.
- **`notif_dispatch.py`**: la lógica de avisos, sin bucle y sin base de datos
  para poder probarla. Destinatarios, horario, resumen y los cinco detectores de
  evento.
- **`cron_avisos_push.py`** (programa `avisos_push`): un turno cada 5 minutos que
  detecta y despacha. Heartbeat en cada vuelta, incluso sin novedades: un worker
  que no reporta se ve igual que uno trabado.
- **`HUB_PushSuscripciones`** (migración `0056`): suscripciones push **con columna
  `App`**. `HUB_PushSubscriptions` no la tiene, y por eso una suscripción de Admon
  era indistinguible de una de Field: el aviso de una app llegaba dentro del
  service worker de la otra. Se dejó la vieja intacta porque la escribe
  `mcp_server`.
- **`HUB_AvisosCola`** (migración `0056`): la cola interna que permite el
  horario y el resumen. **Sin interfaz ni endpoint**: el usuario pidió que Admon
  no tenga historial de avisos, y esto es estado interno del worker.
- **`eccsa_db._normalizar_vapid_privada()`**: convierte una clave VAPID que haya
  quedado en PEM o DER a los 32 bytes crudos que `pywebpush` sí entiende. Se
  encontró una así en la base de pruebas; con esa, TODOS los push fallan con un
  error que no dice nada.

### Corregido
- **El worker `avisos` no mandaba NADA y su log decía lo contrario.**
  `api/routers/push.py` consulta la columna `userId` sobre `HUB_PushSubscriptions`,
  que está indexada por `UserEmail`; el error se tragaba dentro de
  `send_push_notification` y el contador subía igual. Cada lunes a las 8AM se
  registraban "3 push enviados" con cero entregas. Queda documentado como
  obsoleto en el catálogo del panel y sustituido por `avisos_push`.
- **Una conexión por ciclo en los detectores.** `eccsa_db.get_connection()`
  devuelve una conexión cacheada por hilo y cerrarla la invalida para quien la
  tenga en la mano: el `INSERT` de la cola entraba y el marcador "ya avisado" se
  perdía, así que el siguiente ciclo volvía a avisar lo mismo.

- **`pendientes()` falló en el primer turno de producción** con "not enough
  arguments for format string": armaba el `TOP` con `%d` y la sentencia también
  llevaba el `%s` de `App`, y un solo operador `%` se come los dos argumentos a
  la vez. El `TOP` va ahora concatenado como `TOP (N)`. Lo detectó el heartbeat,
  no las pruebas: el doble de los tests no aplica el formato. Hay una clase
  (`TestFormaDelSQL`) que comprueba que los marcadores que declara la sentencia
  son los argumentos que se pasan, que es la regla que DB-Lib sí aplica.

### Documentación
- **`shell/docs/PUSH.md`** (repo ECCSA-Shell, re-vendorizado acá): el contrato
  de los avisos al teléfono, para que otra app pueda repetirlos sin reconstruir
  el conocimiento desde cero. Incluye la parte de iOS, que es la que más cuesta
  y la que no da ningún error visible.

### Decisiones
- **El reparto se hace al ENCOLAR, no al enviar.** Si alguien pierde el permiso de
  Cotizaciones un minuto después de que se firmó algo, no debe enterarse por un
  aviso que llevaba diez minutos esperando a las 9 de la mañana.
- **El resumen es por usuario**, no uno global: el que solo tiene permiso de
  Cotizaciones no necesita saber que se registró un kilometer.
- **Kilómetros es un aviso por VEHÍCULO y semana, no por evento**, y se salta al
  que ya registró algo (es el comportamiento del cron que ya existía).

- **Descubrimiento de carpetas**: un `LIST` del buzón por ciclo (cada 6, no en
  cada uno) llena `HUB_MailboxCarpetas`. Es lo que hace que las carpetas que el
  usuario crea en Gmail/Hostinger aparezcan como pestañas en ECCSA: hasta ahora
  el worker solo sincronizaba la carpeta raíz y el resto de la organización era
  invisible.
- **`status_folder()`** (`imap_client.py`): `STATUS MESSAGES UNSEEN` de una
  carpeta. **No descarga contenido**, así que las pestañas muestran el contador
  real del buzón de carpetas que todavía no se han sincronizado. Es lo que hace
  manejable decidir si se enciende una carpeta con 4000 mensajes.
- **`es_carpeta_sistema(nombre, flags)`**: clasifica por los **FLAGS del
  servidor**, no por el nombre. En Gmail se llaman `[Gmail]/Spam` y en Hostinger
  `Correo no deseado`; una lista de nombres sería medio diccionario que además
  falla en español, en inglés y en lo que invente el siguiente proveedor.
- **`_priorizar(carpetas, cuenta)`**: la raíz primero y después las que MENOS
  mensajes indexados tengan. Cola de Constructor. Sin esto, con las carpetas
  grandes siempre de últimas, una carpeta de 300 mensajes nunca se sincroniza
  porque cada ciclo agota el presupuesto en la de 4000.
- **Presupuesto de 600 mensajes nuevos por carpeta y ciclo**: encender 15 carpetas
  no puede dejar al worker horas sin cerrar el ciclo ni atrasar el resto de las
  cuentas. Lo que no se alcanza queda para el siguiente.

### Corregido
- **Una carpeta nueva entra APAGADA, no encendida.** Con el default en 1, el
  primer listado encendió 75 carpetas en la cuenta de Héctor y 25 en `robot@`,
  que tiene `INBOX/IT` con 3 800 mensajes e `INBOX/Onedrive` con 4 203.
- **El contador de ciclos vive en el módulo.** Estaba en el dict de la cuenta,
  que se relee de la base en cada ciclo: se reiniciaba a 1 y nunca llegaba al
  umbral, así que el `LIST` se hacía en todos los ciclos en vez de cada 6.

## [1.5.4] - 2026-10-06

### Agregado
- **Acción de regla `SPAM`** (`filtros.py`): mueve el mensaje a la carpeta de
  spam del buzón con IMAP MOVE. Es lo único que se puede hacer desde la cuenta,
  porque la carpeta de spam del proveedor no se escribe por IMAP.
- La carpeta de spam se resuelve **una vez por ciclo** con candidatos en español
  e inglés (`spam`, `junk`, `junk e-mail`, `correo no deseado`, `no deseado`,
  `desechados`): el buzón de Hostinger está en español y el de Gmail en inglés.
- **No cae a un nombre inventado.** Si `_carpeta_por_nombre` no encuentra
  ninguna, la regla no se aplica y queda el aviso en el log. Crear una carpeta
  fantasma sería peor: el usuario no la ve en su cliente de correo y el mensaje
  desaparece de ECCSA sin explicación.



### Agregado
- **`create_folder()`** (`imap_client.py`): crea la carpeta en IMAP con
  `CREATE`, traduce el nombre a **modified UTF-7** —lo que exige IMAP para
  acentos, o «Facturación 2026» se guarda como `Facturaci&APg-n 2026`— y
  devuelve **el nombre que reporta el servidor**, no el que pidió el usuario.
  Indexar por el nombre pedido deja las pestañas apuntando a una carpeta que no
  existe cuando hay acentos.
- El worker procesa la operación de cuenta **`crear_carpeta`** y registra la
  carpeta en `HUB_MailboxCarpetas`, **aunque esté vacía**: si no, la carpeta
  existe en el correo del usuario y no aparece en la app hasta que alguien le
  mueva un mensaje.
- `_registrar_carpeta()` no puede tumbar la sincronización si la tabla todavía
  no existe: perder el catálogo de carpetas es tolerable, tumbar el worker no.

## [1.5.2] - 2026-10-06

### Corregido
- **`BODY.PEEK[TEXT]` en vez de `RFC822.TEXT`: sincronizar marcaba los correos
  como leídos EN EL BUZÓN REAL.** `RFC822.TEXT` pone el flag `\Seen` en el
  servidor de correo, así que la **primera** sincronización dejó los 419 mensajes
  con `Visto = 1` y, en los buzones de `hector.pena@ecc-sa.com.mx` y
  `robot@ecc-sa.com.mx`, dejó de estar sin leer 407 correos que nadie había
  abierto. Es un efecto secundario en el correo de otra persona, no en nuestra
  copia, y no se puede deshacer: IMAP no recuerda qué estaba sin leer antes.

  `PEEK` es exactamente para esto, y ya se usaba en `fetch_parte`. El mismo
  criterio que ya está documentado en el cliente ("el worker no debe marcar como
  leído un mensaje que el usuario solo descargó") faltaba aquí.

## [1.5.1] - 2026-10-06

### Corregido
- **`NameError: name 'Optional' is not defined` al arrancar en el contenedor.**
  `imap_client.py` usaba `Optional[datetime]` en la firma de `_parse_fecha` sin
  importar `typing`. Parece inocuo porque en **Python 3.14 las anotaciones se
  difieren** (PEP 649) y el nombre nunca se resuelve —y la máquina de desarrollo
  corre 3.14—, pero el contenedor corre **3.11**, donde sí se evalúan. El worker
  moría al arrancar, y **425 tests pasaban en local**.

  Es el peor orden posible: la suite en verde y el servicio caído. La lección
  concreta es que **probar con la versión del contenedor** (`python3.11 -m
  unittest discover -s tests` da los mismos 425 en verde, y habría cazado esto).

  Al revisar los demás módulos con el mismo criterio (usar `Optional`/`List`/`Dict`
  sin importar `typing`) solo apareció este.

### Agregado
- Desplegado: `mailbox_worker` activo en `workersadmon`, volumen `mailbox_data`
  compartido con la app, streaming en `:8201` y las 13 cuentas de HUBMail
  validadas contra sus servidores IMAP reales (12 `ACTIVA`, 1 con la contraseña
  cambiada en origen).

## [1.5.0] - 2026-10-05

### Agregado
- **`mailbox_worker/`: el motor de sincronización de la PWA ECCSA_Mailbox.**
  Reemplaza a `hubmail_worker` (que queda intacto hasta el corte) y reescribe el
  modelo de adjuntos: los adjuntos **no se descargan**. Del sync solo sale el
  índice (cabeceras + `BODYSTRUCTURE`, que es el manifiesto sin los bytes); los
  bytes se piden por HTTP cuando el usuario abre el archivo, en rangos de
  256 KB, así que un adjunto de 300 MB pasa por el proceso sin que la memoria
  dependa del tamaño. El motivo es concreto: `BODY[]` bajaba los adjuntos
  piggyback con cada correo nuevo, y una bandeja con un correo de 8 MB se
  descargaba entera cada 5 minutos.
  Módulos, todos con docstring explicando la decisión de diseño:
  - `imap_client.py` (23 tests) — port del cliente viejo más lo que el streaming
    necesita: `BODY.PEEK[parte]`, rangos `<offset.count>`, XOAUTH2, parser de
    `BODYSTRUCTURE` y ahora `fetch_encabezados` y `cuerpo_html`.
  - `almacen.py` — cuerpos HTML en gzip en el volumen compartido y cache de
    adjuntos que es un **coalescador, no un almacén** (6 h / 2 GB): cinco
    personas abriendo el mismo PDF hacen un solo fetch a IMAP.
  - `stream.py` (19 tests) — el único camino de un byte al dispositivo.
  - `smtp.py` (48 tests) — construcción MIME con firma resuelta.
  - `filtros.py` — reglas por usuario y auto-responder con sus tres guardas.
  - `sync.py`, `crypto.py`, `config.py`, `db.py` — el ciclo y sus piezas.
- **`tests/test_mailbox_smtp.py` (48)** y **`tests/test_mailbox_stream.py` (19)**.
  No son tests de cobertura: cada caso fija un modo de falla concreto, y varios
  los accompanan del "por qué".
- `hotsync.sh`: `mailbox_worker` entra en las cuatro listas que importan
  (copia, verificación por hash, reinicio). La de verificación compara los
  hashes de los módulos que de verdad importan, porque un `docker cp` puede
  salir con código 0 sin haber escrito nada y el deploy se reportaba igual como
  "actualizado" — un deploy que miente es peor que uno que falla.

### Corregido
- **`Content-ID` de la firma: el correo llegaba con el logo en blanco.** La
  firma se guarda en HTML con `cid:logo`, y si el `Content-ID` de la parte MIME
  no coincide exactamente con ese `cid:`, la imagen **viaja en el correo pero no
  se ve**. No hay error, no hay log, no hay queja inmediata: el usuario asume que
  el que lo mandó no puso su logo.
- **`ValueError: Cannot convert alternative to related` (Python).** Un correo con
  texto plano, HTML e imágenes de firma necesita `multipart/related` →
  `multipart/alternative` → `text/*`, con las imágenes colgando del `related`
  (RFC 2046: `alternative` solo lleva texto). La API de azúcar de `email` no
  puede construir eso y lanza; el MIME se arma con `MIMEMultipart` a mano.
- **Correo solo con HTML se veía EN BLANCO en el Outlook de escritorio.** El
  `TextoSnapshot` vacío producía un mensaje sin parte `text/plain`. Ahora el
  texto plano se deriva del HTML (`_html_a_texto`, que borra `script`/`style`/CSS
  completos) cuando no existe.
- **Remitente visible perdido en Exchange/Outlook.** `user@dominio (Nombre)` se
  parseaba con el nombre vacío y la lista mostraba el email crudo en todas las
  filas.
- **Tope de inline que no se aplicaba.** El guard de `_MAX_INLINE` estaba escrito
  pero la condición que debía activarlo fijaba `cantidad=0`, que en ese código
  significa "servirlo todo": el opposite de lo buscado. Un `cid` malicioso
  apuntando a un adjunto de 300 MB habría provocado el OOM del worker, y
  con él se caerían todas las cuentas, porque comparten proceso. Ahora el tamaño
  se comprueba contra el índice **antes** de pedir los bytes.
- **`getaddresses` desempaquetado como par.** Devuelve una lista de pares; con dos
  destinatarios devolvía tuplas equivocadas y con uno lanzaba `ValueError`.
- **Destinatarios partidos por comas.** `"Pérez, Juan" <j@x.com>` es una
  dirección, no dos; un `split(",")` mandaba basura al servidor remoto.

- **`push.py`** — WebPush. El 404/410 del push service se trata como lo que es
  (la suscripción ya no existe) y la fila se marca `Activo=0` en vez de tractarse
  como error: si no, la lista crece para siempre y cada correo intenta entregar
  a endpoints muertos. Un 401/403 NO da de baja la suscripción (esa sí sirve) sino
  que avisa de que la llave VAPID no coincide con la del service worker: es un
  error de configuración y no del usuario. El push NO lleva el asunto del
  correo, solo quién escribe: el asunto se vería en la pantalla de bloqueo del
  iPhone sin abrir el correo.
- **`cron_sync_mailbox.py`** — el entrypoint. Tres guardas: `sp_getapplock` para
  una sola instancia (se sale con código 3, no repite trabajo), `MAILBOX_DRY_RUN`
  para leer sin escribir durante el corte, y `MAILBOX_SYNC_ENABLED=0` para apagar
  sin editar código. Un hilo por cuenta con reloj propio — una cuenta caída no
  puede rezagar a las demás — y el servidor de adjuntos en un hilo aparte, porque
  `serve_forever` no vuelve y en el principal el loop de sync nunca empezaría.
- **`docker/conf.d.available/mailbox_worker.conf`**, sin ninguna variable
  `MAILBOX_*` adentro a propósito: este worker descifra credenciales de correo
  reales y una clave en un `.conf` versionado queda en el historial de git para
  siempre.

### Corregido (segunda tanda)
- **`AttributeError` en el arranque del worker.** `_avisar_arranque` llamaba
  `settings.aviso_arranque()`, y `aviso_arranque` es una función del módulo, no
  un método de `Settings`. El proceso moría en el primer segundo de vida, que es
  la forma más cara de tener un bug de una línea.
- **`vapid_subject` no existía** en la config y `push.py` lo usa: es el `sub` de
  los claims VAPID, que RFC 8292 exige que sea `mailto:` o una URL HTTPS.

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

## [1.3.3] - 2026-10-04

> Registro completo del periodo en `docs/BITACORA-2026-09-29_10-04.md`.

### Corregido
- **El escáner de red llevaba 5 días sin producir nada** (último escaneo del
  30-sep). El worker seguía vivo y el panel mostraba su "último ciclo" como si
  todo estuviera bien, así que no había forma de notarlo desde la pantalla: el
  estado en sitio/fuera era de otra época. La causa era que el que **hace** el
  barrido (`network_scanner_host.py`) vivía fuera del contenedor y **nunca se
  versionó en este repo**; al mover el stack a WebbApps murió con el servidor
  viejo y nadie lo reemplazó.
- `network_scanner_host.py` entra al repo, reescrito para funcionar en WebbApps:
  el host es Debian y no tiene `arp-scan` ni `pymssql`, así que usa `ping` +
  `/proc/net/arp` y delega el guardado al contenedor (que sí tiene `pymssql`).
  Cubre lo que al anterior le faltaba: descarta entradas de ARP a medio llenar y
  MACs multicast, y resuelve hostnames con presupuesto de tiempo (un DNS lento
  cuelgaba el ciclo).
- Se instala como servicio systemd (`network-scanner-host.service`) con
  `Restart=always`, para que si se muere se levante solo y quede rastro en el log.
- **La banda de la pestaña Asistencia** muestra el último escaneo **real** y
  avisa en rojo cuando lleva más de 10 min sin registrar equipos, diciendo que la
  asistencia no es confiable. Antes no había forma de distinguir "el worker está
  vivo" de "entró alguien al que no le llegan datos".
- `network_scanner.py` ya no mete `/workspace/hub_repo` ni `/workspace/HUB` al
  `sys.path`. En una máquina con el HUB clonado al lado, esas rutas hacían que el
  HUB **sombreara los módulos de este repo**: las pruebas del escáner acababan
  approvesbando el `network_scanner_host.py` equivocado sin avisar.

### Nota
- Los hostnames vuelven vacíos: no hay DNS inverso en la red y `/etc/hosts` no
  tiene la LAN (los nombres viejos venían de otra fuente que no se conserva). El
  MAC es lo que usa la asistencia, así que no la afecta.

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
