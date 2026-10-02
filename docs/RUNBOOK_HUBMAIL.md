# Runbook — el worker de correo sale de HUBMail y entra a `workersadmon`

> Migración del 2026-10-02. **Léelo completo antes de ejecutar nada.**
> Todo lo de aquí se ejecuta en el **ServerVM** (`docker exec`, `docker cp`),
> porque toca el contenedor `workersadmon`, que de todas las apps del
> ecosistema (y del que dependen Field, Admon y el panel).

---

## 0. Por qué este runbook tiene tres llaves

El riesgo NO es que el worker nuevo falle: eso se ve en el log. El riesgo es
que **los dos workers escriban en el buzón a la vez**, que es silencioso:

- los dos drenan `HUBMAIL_PendingOps` → la misma operación se ejecuta dos veces;
- el `APPEND` de un correo enviado se hace dos veces → **duplicados en
  Enviados** del cliente, que no se pueden deshacer;
- las dos mitades abren conexión IMAP sobre la misma cuenta.

Por eso el orden de los pasos importa más que cualquier comando. Y hay una red
de seguridad: el worker toma un `GET_LOCK` de MySQL (`eccsa_hubmail_sync_worker`)
y **se sale** si ya hay otro dueño. O sea que el peor caso de un error de
orden es que el worker nuevo no arranque y toques a verlo — no que se duplique
nada.

| Llave | Efecto | Para qué |
|---|---|---|
| `HUBMAIL_SYNC_DRYRUN=1` | lee IMAP y llena la caché, **no escribe en el buzón** | probar en paralelo |
| `HUBMAIL_INLINE_SYNC=0` | el backend de HUBMail **no** levanta su hilo de sync | apagar el viejo |
| `GET_LOCK` (automático) | solo una instancia puede sincronizar | red de seguridad |

---

## 1. Estado real hoy (medido en la BD el 2026-10-02)

| Dato | Valor |
|---|---|
| Cuentas IMAP canónicas | **13** (de 34 filas; 21 son compartidas que apuntan a una canónica) |
| Mensajes en caché | **148.705** |
| Última sincronización | **2026-09-29 23:07** (hace ~2.5 días) → el hilo viejo **ya no está sincronizando** |
| Cola `PendingOps` | 149 `done` · **5 `pending`** · **3 `failed`** |
| Las 5 `pending` | todas `seen` de la **cuenta 19**, del 2026-08-26 |
| Los 3 `failed` | `seen` de la **cuenta 34**, `BAD [UID STORE extra parameters supplied "\Seen"]` |
| Errores de sync recientes | cuenta 19: *"Credenciales IMAP incorrectas o cuenta bloqueada"* |

**Dos conclusions que cambian el plan:**

1. **El worker viejo ya está parado de facto** (no sincroniza desde el 29-sep).
   Apagarlo no interrumpe nada que esté funcionando.
2. **La cuenta 19 está caída** (credenciales IMAP). Sus 5 `pending` **no se van
   a drenar** con este worker tampoco, porque `sync_account(19)` falla antes.
   No es un problema de esta migración: hay que corregir las credenciales de la
   19 aparte. Lo mismo con los 3 `failed` de la 34, que se reintentarán y ahora
   deberían salir bien (ver §6, el bug de `UID STORE`).

---

## 2. Lo que hay que tener a la mano antes de empezar

```bash
# A) La clave Fernet. ESTA es la que no se puede inventar.
docker exec hubmail cat /data/.hubmail_key
# Si el contenedor de HUBMail ya no existe, búscala en el backup del volumen:
#   docker run --rm -v hubmail_data:/v alpine ls -l /v/.hubmail_key
#
# B) Las credenciales de MySQL (base HUBMAIL)
docker exec hubmail printenv | grep HUBMAIL_DB_
```

Guarda la clave **fuera** de un archivo versionado. Va directo a
`deploy/env.local` (que está en `.gitignore`) del repo de WorkersAdmon.

---

## 3. Desplegar el worker nuevo (todavía apagado)

```bash
cd C:\WorkersAdmon
git pull                       # o clonar si es la primera vez
```

Agrega a `deploy/env.local` (esto **no** va a git):

```ini
HUBMAIL_DB_SERVER=172.26.90.159
HUBMAIL_DB_USER=hubmail
HUBMAIL_DB_PASSWORD=<la de MySQL>
HUBMAIL_DB_NAME=HUBMAIL
HUBMAIL_ENCRYPTION_KEY=<contenido de /data/.hubmail_key, tal cual>
HUBMAIL_VAPID_PUBLIC=<la que ya usaba HUBMail>
HUBMAIL_VAPID_PRIVATE=<la que ya usaba HUBMail>
HUBMAIL_SYNC_DRYRUN=1
```

> `run_container.ps1` recoge **todo** lo que empiece por `HUBMAIL_` de
> `env.local` y lo pasa al contenedor, así que las variables nuevas no
> requieren tocar el script. Si faltan las dos obligatorias, avisa en el log del
> despliegue pero **no aborta** (el resto del contenedor no depende del correo).

Luego el despliegue normal del repo:

```powershell
deploy\build.ps1              # o build.bat vía schtasks
```

Y comprueba que el worker está **habilitado** (la lista vive en el volumen
`/data/workers_enabled.txt`, así que sobrevive a los rebuild):

```bash
docker exec workersadmon workers_list
```

---

## 4. Encenderlo en modo ensayo y VERIFICAR (lo importante)

El ensayo lee IMAP y llena la caché pero **no escribe nada en el buzón**, así
que puede convivir con el worker viejo.

```bash
docker exec workersadmon supervisorctl start hubmail_worker
docker exec workersadmon supervisorctl status hubmail_worker

# El log debe decir de dónde sacó la clave y cuántas cuentas tomó:
docker exec workersadmon tail -40 /var/log/supervisor/hubmail_worker.log
```

**Checklist antes de seguir.** Todo tiene que cumplirse:

```bash
# 1) Clave de cifrado cargada (si dice "No encuentro la clave", PARA aquí)
docker exec workersadmon grep -c "Clave de cifrado cargada" /var/log/supervisor/hubmail_worker.log

# 2) Las 13 cuentas se están sincronizando
docker exec workersadmon grep "arrancando" /var/log/supervisor/hubmail_worker.log | tail -1

# 3) La caché se está actualizando DE VERDAD (esto es la prueba buena):
#    el LastSync de HUBMAIL_SyncState tiene que ponerse al minuto.
docker exec hubmail python3 -c "
import os; os.environ.setdefault('HUBMAIL_DB_PASSWORD','<la de MySQL>')
import pymysql
c=pymysql.connect(host='172.26.90.159',user='hubmail',password=os.environ['HUBMAIL_DB_PASSWORD'],database='HUBMAIL')
cur=c.cursor(); cur.execute('SELECT AccountID,MAX(LastSync) FROM HUBMAIL_SyncState GROUP BY AccountID ORDER BY 2 DESC LIMIT 5')
[print('  ',r) for r in cur.fetchall()]"

# 4) Heartbeat vivo (la columna "Última ejecución" del panel)
docker exec workersadmon cat /data/heartbeats/hubmail_worker.json

# 5) Los errores NO se multiplican
docker exec workersadmon grep -c "ciclo de .* falló" /var/log/supervisor/hubmail_worker.log
```

Si (3) no se mueve, el worker **no está llegando al buzón**: revisa
`IMAPHost/IMAPPort` de las cuentas y que el contenedor tenga salida a internet.

También míralo en el panel: <http://10.188.141.31:8200> → pestaña **Workers** →
fila **Correo ECCSA**. Debe salir con la última ejecución en verde.

---

## 5. Apagar el worker viejo (el backend de HUBMail)

Solo cuando §4 esté completo.

```bash
# Opción A (reversible sin redesplegar, recomendada): por variable de entorno.
#   HUBMail lee HUBMAIL_INLINE_SYNC; en 0 NO levanta el hilo de sincronización.
docker exec hubmail printenv HUBMAIL_INLINE_SYNC        # debe salir vacío o 0
# Para dejarlo explícito hay que recrear el contenedor con la variable, o
# equivalentemente, hacer el despliegue de HUBMail con el código de este repo
# (que ya trae el corte y su reversa).
#
# Opción B: si HUBMail se va a jubilar de todos modos,
docker stop hubmail
```

Verifica que **de verdad** paró:

```bash
# No debe haber ningún hilo de sync: el arranque lo anuncia.
docker logs hubmail 2>&1 | grep "sincronización INLINE apagada"
```

---

## 6. Quitar el modo ensayo (pase a producción real)

```bash
# 1) Sacar DRYRUN de deploy/env.local y redesplegar el contenedor, o
#    cambiarlo en caliente desde el panel:
#    Notificaciones/Configuración → hubmail_worker → "Modo ensayo" = OFF
docker exec workersadmon supervisorctl restart hubmail_worker

# 2) Confirmar en el log que ya NO dice "ensayo"
docker exec workersadmon grep "modo " /var/log/supervisor/hubmail_worker.log | tail -1
#    debe decir:  · modo normal

# 3) Ahora sí se drena la cola. Ver que baja la de 'pending':
docker exec hubmail python3 -c "
import os,pymysql
c=pymysql.connect(host='172.26.90.159',user='hubmail',password='<PASSWORD>',database='HUBMAIL')
cur=c.cursor(); cur.execute('SELECT Status,COUNT(*) FROM HUBMAIL_PendingOps GROUP BY Status')
[print('  ',r) for r in cur.fetchall()]"
```

Sobre los 5 `pending` de la cuenta 19: **no se van a resolver**. Son `seen` de
un buzón cuyas credenciales IMAP están malas; mientras no se arreglen, ese
worker falla al abrir la cuenta y la cola se acumula. Cuando se corrijan, se
drenan solos.

---

## 7. Reversa

Si algo sale mal y hay que volver al estado anterior:

```bash
# a) Sacar el worker nuevo (el candado se libera solo al morir el proceso)
docker exec workersadmon disable_worker hubmail_worker

# b) Reactivar el hilo de HUBMail
#    con HUBMAIL_INLINE_SYNC=1 en el contenedor de HUBMail
docker restart hubmail
```

Ojo: **no los dos a la vez.** Si reactivás el viejo sin quitar el nuevo, el
`GET_LOCK` no protege (solo protege entre instancias del worker nuevo), y
vuelven los duplicados en Enviados.

---

## 8. El bug que se opportunisticamente se corrigió

Al leer `HUBMAIL_PendingOps` aparecieron 3 `failed` con

```
BAD [UID STORE extra parameters supplied "\Seen"]
```

`imaplib.uid()` de **Python 3.11** pasa los argumentos a `_command()` sin
tocarlos (solo `data = data + b' ' + arg`), de modo que
`uid("store", uid, "+FLAGS", flag)` mandaba el flag-list **suelto**, y el RFC
3501 lo exige entre paréntesis. GoDaddy lo toleraba; el servidor de la cuenta
34 no. No se notaba porque la UI marca el correo al instante en la caché.

Los 5 sitios (`set_flag`, `set_flags`, `move_message`, `delete_message`,
`delete_messages`) arman ahora `UID STORE <uid> ±FLAGS.SILENT (\Seen)`, que no
depende de la versión de Python, y verifican `typ != "OK"` en vez de tragarse
el fallo. Cubierto por `python tests/test_imap_store.py`.

---

## 9. Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `No encuentro la clave de cifrado` | falta `HUBMAIL_ENCRYPTION_KEY` | §2. Sin esto el worker NO arranca el sync. |
| `ya no es la misma que se usó para cifrarla` | clave distinta a la original | compararla con `docker exec hubmail cat /data/.hubmail_key` |
| `YA HAY UN WORKER DE CORREO CORRIENDO` | el candado está tomado | hay otro worker vivo. **Es la red de seguridad: no lo fuerces.** |
| `Credenciales IMAP incorrectas` en una cuenta | credenciales de esa cuenta | la 19 ya venía caída; es independiente de esta migración |
| Sincroniza pero no baja `LastSync` | `HUBMAIL_SYNC_ENABLED=0` | la llave de corte sigue en OFF |
| No baja `LastSync` y el log dice `(ensayo)` | `DRYRUN` sigue en 1 | el ensayo **sí** escribe la caché; si no baja, no está llegando a IMAP |
| Los adjuntos no abren | el volumen `hubmail_data` no quedó montado | `docker exec workersadmon ls /data/hubmail/attachments` |