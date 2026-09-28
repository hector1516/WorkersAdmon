# Troubleshooting · WorkersAdmon

Problemas reales que han pasado, con la causa y cómo verificar que quedó bien.
Cada uno dice **cómo confirmarlo**, porque varios fallaron *"pareciendo* estar
arreglados".

---

## 1. Hay DOS copias del backend y solo una corre

**El que más daño ha hecho, y el más fácil de no ver.**

| Copia | ¿Sirve peticiones? |
|---|---|
| `Field/api/` | ✅ **Sí.** Corre `uvicorn` dentro del contenedor `field` (puerto 8100) y su nginx hace proxy de `/api/` a ahí. |
| `WorkersAdmon/api/` | ❌ **No.** El mismo código, pero ningún proceso lo levanta. |

Y una tercera distinta: `AdmonApp/api/` es **otra app** (cotizaciones, reportes,
SAT). No tiene vales ni tickets.

**Cómo confirmar dónde vive cada cosa:**

```bash
# qué contenedor tiene uvicorn
docker exec field sh -c "ss -tlnp | grep uvicorn"   # -> 0.0.0.0:8100
docker exec field sh -c "grep -c uvicorn /proc/*/cmdline 2>/dev/null"

# ver si elWorkersAdmon sirve /api en su 8000
docker exec workersadmon python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json').status)"
# 404 = no es la API; ese 8000 es el mcp_server
```

**Regla:** si tocás un router (`api/routers/*.py`) o `api/telegram_hub.py`, el
cambio va a **`Field/api/`**. Si solo tocás un worker, va en este repo.

> **Cómo pasó:** el arreglo del aviso de Telegram (los `{Nombre}` literales) se
> aplicó dos veces a `WorkersAdmon/api/` y ambos deploys salieron `success`.
> Nunca llegó a producción, porque la copia que corre es la de Field. El bug
> parecía vivo tres días.

---

## 2. El deploy podía no llevar el backend y aun así decir `success`

El gate de `Field` decide entre *rebuild* (recrea la imagen) y *hotsync* (solo
compila el estático y lo copia al contenedor vivo). El hotsync **no toca `api/`**.

Si el gate no reconoce un cambio en `api/`, cae en hotsync, el backend queda
viejo, y el deploy reporta éxito.

**Ya está arreglado** (el gate incluye `api`, `docker`, `deploy`, `.github`), pero
si alguna vez vuelve a pasar:

```bash
# 1. ver qué modo eligió el workflow
gh api repos/hector1516/field/actions/runs/<id>/jobs \
  --jq '.jobs[].steps[] | select(.name|test("Hotsync|Rebuild")) | "\(.name): \(.conclusion)"'
# si dice "Hotsync: success" y "Rebuild: skipped" con cambios en api/ -> el gate falló

# 2. confirmar que el código llegó al contenedor
docker exec field grep -c "lo_que_buscaste" /app/api/routers/tuarchivo.py
```

Ojo con dos trampas del gate que ya se pisaron:

- El patrón del filtro es `"$_/*"`, así que una entrada con **barra final**
  genera `api//*` y no matchea nada. La lista va **sin barras**.
- Un selector pegado a un `/*` se come el comentario **y la regla siguiente**.
  Passó con `.flash/* comentario */`.

---

## 3. Un vale queda `GENERADO` y sin QR, y nadie dice nada

**Síntoma:** el usuario pide un vale en Field, no le llega nada, y en la BD:

```
Estatus = GENERADO | IdValeGoVale = 'Filtros' | CodigoQR vacío
```

**El vale sí se creó** en Go Vale. Lo roto era nuestra lectura de la pantalla.

**El flujo real:**

1. Field crea la solicitud → `PENDIENTE`
2. Se aprueba → `APROBADO`
3. `vales_worker` (cada 5 min) la genera en Go Vale con Playwright → `GENERADO`
4. **El QR se manda a mano por WhatsApp.** La automatización nunca hazekerlo:
   los 7 vales históricos también tienen `CodigoQR` vacío.

**Por qué el QR no sale solo:** después de darle *Generar Vale*, Go Vale
muestra el **listado**, no una confirmación. En el listado cada fila trae el
folio en grande y la fecha debajo, y **el QR no aparece** (sale al reenviar o
en el detalle).

**Cómo diagnosticarlo:**

```bash
# a) el log del worker
docker exec workersadmon tail -40 /var/log/supervisor/vales_worker.log

# b) si ves "folio=Filtros, qr=no" -> se leyo el menu, no la fila
# c) la captura que el propio worker guarda
docker cp workersadmon:/tmp/govale_result.png ./govale.png
```

**La captura es el paso decisivo:** ahí se ve el folio real de la primera fila
y el toast de Go Vale (`¡El vale fue enviado con éxito!`). Si el toast está, el
vale existe aunque nuestra BD diga otra cosa.

**El folio se extrae anclado a la fila**, no al texto suelto:

```
(\d{6,15})\s*\n\s*\d{1,2}/\d{1,2}/\d{2,4}
```

El patrón anterior (`(Vale|Folio|#)[\s:]*([A-Z0-9-]{6,})` sobre todo el body)
era inútil: el menú tiene *Vales*, *Facturas*, *Filtros*… y la primera palabra
de 6+ letras tras "Vale" era un ítem del menú.

**Fallo silencioso (ya corregido):** se ponía `GENERADO` y se logueaba
*"generado exitosamente"* aunque no hubiera folio ni QR. Como el worker solo
reintenta los `APROBADO` **sin QR**, el vale quedaba `GENERADO` para siempre y
nunca se reintentaba. Ahora: si hay folio → `GENERADO`; si **no** hay folio →
se deja en `APROBADO` para reintentar y se avisa (porque sin folio no se sabe
si se duplicó).

---

## 4. La plantilla de Telegram sale con los `{placeholders}` literales

**Síntoma en el chat:**

```
Ticket de OXXO Gas
Fecha: 27/09/2026
Nombre: {Nombre}
Cantidad: {Cantidad}
Folio ticket: 324745780
Cliente: {Cliente}
```

**El texto se arma y se guarda YA RENDERIZADO** en `HUB_TelegramQueue`. Cuando
el worker lo manda solo copia lo que hay: **dañarlo es fácil y arreglarlo
después es imposible.**

**La causa nunca fue la plantilla** (esa está bien en `HUB_TelegramEventos`):
fue que el payload usaba los nombres internos (`NombreRegistro`, `Empresa`,
`ProyectoServicio`) y la plantilla pide los planos (`Nombre`, `Cliente`,
`Descripcion`). **No hay coincidencia y el `{...}` se queda.**

**Cómo confirmar que el arreglo está vivo** (importante: contra el contenedor
que corre, no contra el repo):

```bash
docker exec field grep -c "alias = {" /app/api/telegram_hub.py    # debe ser > 0
```

Y la prueba real, con la plantilla de la BD:

```bash
docker exec -i -w /app/api field python3 -c "
import re, telegram_hub
t,_,_ = telegram_hub._get_event_template('OXXOGAS_TICKET')
viejo = {'Fecha':'x','NombreRegistro':'Hector','Folio':'1','FolioTicket':'1',
         'Auto':'a','Empresa':'b','ProyectoServicio':'c'}
out = telegram_hub._apply_template(t, viejo)
print(re.findall(r'\{(\w+)\}', out) or 'NINGUNO')"
# tiene que imprimir NINGUNO
```

`_apply_template` resuelve alias, así que da igual qué versión del productor
encole. Y si queda algún placeholder sin resolver, avisa por consola.

> Recordar: hay que probar contra **`Field/api/`** (ver §1), no contra este repo.

---

## 5. Cambios de CSS que parecen no aplicarse

El orden de carga es `shell.css` → `panel.css`. Cualquier ajuste del panel
gana **a igual especificidad**, así que un selector de `panel.css` puede
anular al del shell sin avisar.

Tres casos reales:

- **`.wrap` + `.shell-below-banner` en el mismo elemento.** El atajo de
  `padding` de `.wrap` borraba el `padding-top` del banner: cada página
  empezaba ~61px debajo. Ahora van en **contenedores anidados**, como Field.
- **Reglas de móvil muertas.** `header{...}` (0,0,1) perdía contra
  `header.header` (0,1,1). Se escribió `header.header` y aplicaron.
- **Un comentario que se comió una regla.** `.flash/* ... */` dejó el
  selector como `.flash form.inline`, así que **`.flash.err` no existía** y
  todos los errores salían sin estilo.

**Para ver qué gana de verdad:** los dos `<style>` van juntos en el HTML
servido, así que hay que buscar en la página, no en los archivos.

---

## 6. Correr consultas sin las credenciales

El `db` de este repo resuelve env vars > `secretos_local.py` > defaults, y
**no hay credenciales fuera del contenedor**. Para consultar:

```bash
# desde el host, pasando el script en base64 (la línea de Windows es corta)
B64=$(base64 -w0 script.py)
sshpass -e ssh eccsa@10.188.141.31 \
  "echo $B64 | docker exec -i -w /app/api workersadmon python3 -c \
   \"import base64,sys;exec(base64.b64decode(sys.stdin.read()).decode())\""
```

Tres cosas que se pierden el tiempo:

- `sshpass -e` (lee `SSHPASS` del entorno) para no dejar la clave en la
  línea de comandos.
- El contenedor **no** tiene `base64` ni `grep` con las mismas opciones; se
  resuelve con `python3 -c`.
- `api/db.py:get_connection()` **no hace commit automático** (es una conexión
  thread-local). Un `rollback()` explícito sí deshace, y si el script muere
  antes, la conexión se descarta al cerrar. Se puede probar contra producción
  sin riesgo.

---

## 7. pymssql + `as_dict=True`: toda columna necesita nombre

**Síntoma:** un registro no aparece en ninguna parte. Ni en el servidor ni en
la lista del cliente. Y el `POST` al que lo mandaba responde **200 OK**.

**Causa:** el error se come dentro del item, la respuesta del lote sigue siendo
200, y del lado del cliente no hay nada que mostrar.

```python
with conn.cursor(as_dict=True) as cur:      # <-- as_dict
    cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT)")   # <-- sin alias
    cur.fetchone()
# pymssql._pymsql.ColumnsWithoutNamesError:
#   Specified as_dict=True and there are columns with no names: [0]
```

Con `as_dict=True`, pymssql **rechaza** la consulta si alguna columna no tiene
nombre. No es un `KeyError` después: falla al devolver las filas. Always:

```python
cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
new_id = int(cur.fetchone()["new_id"])
```

Con `conn.cursor()` a secas (tupla) el mismo `SELECT` sí funciona, así que el
mismo texto es correcto en un archivo y falla en otro. **Revisar siempre con qué
cursor se está.**

**Cómo se nota que un item del sync falla sin que se entere:**

```bash
docker logs field 2>&1 | findstr /c:"sync/push"
# 200 OK NO significa que el item se guardo: el 200 es del LOTE.
# Cada item lleva su propio status en la respuesta.
```

Y ojo con los `status` que el cliente acepta, en
`src/lib/stores/sync.ts`:

```js
if (r.status === 'ok' || r.status === 'duplicate') { /* marcar sincronizado */ }
```

Cualquier otro valor cuenta como **fallo**, se reintenta, y **a los 5 intentos
el item se borra de la cola** (`db.syncQueue.delete`). Eso convierte un detalle
en pérdida de datos silenciosa. El servidor tiene que devolver exactamente
`ok` o `duplicate`.

---

## 8. Con internet, la lista solo enseña lo del servidor

En la pantalla de tickets, la rama `if ($online)` hacía `tickets = data` con lo
que devuelve `GET /tickets`. Los registros locales encolados se usaban solo
para limpiar duplicados, **nunca se mostraban**. La rama offline sí los
enseñaba.

Resultado: cualquier ticket que no hubiera sincronizado era **invisible**, sin
aviso. La plantilla ya tenía el estilo `.ticket-card.pending` (borde de
advertencia) y `{@const isSynced = t.synced !== false}`; solo faltaba que
llegaran los datos.

Regla: **un registro pendiente se ve, siempre**, con su marca. Si puede haber
uno en cola, la lista tiene que incluirlo.
