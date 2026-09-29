# Migraciones de la base de ECCSA

Un archivo `.sql` numerado por **cambio de estructura** (crear/alterar/borrar
una tabla o columna). Se aplica **una vez** y queda registrado en la tabla
`schema_migrations` de la base.

La base es una sola y la comparten el panel, Field, admon, el Dashboard y
todos los workers, así que el registro también es uno solo: aplicar desde
cualquier repo que tenga el runner no duplica nada, porque lo que manda es la
tabla, no el repo.

## Cómo aplicar

```bash
# a la base de pruebas (lo default: si no es esta, el runner aborta)
HUB_DB_DATABASE=ECCSA_Admon_Pruebas python3 apply_migrations.py

# a producción, explícito
HUB_MIGRATE_PRODUCTION=1 python3 apply_migrations.py
```

El runner lee los archivos **en orden de nombre**, salta los que ya están en
`schema_migrations` y aplica el resto. Nunca borra una tabla que no sea suya.

## Reglas

1. **Un archivo por cambio**, numerado con el siguiente número libre. No se
   edita uno ya aplicado: la base ya lo ejecutó, y editarlo no cambia nada
   (quien lo lea después Cree que sí).
2. **Idempotente**: cada archivo se guarda con `IF OBJECT_ID(...) IS NULL` /
   `IF COL_LENGTH(...) IS NULL` / `IF NOT EXISTS (...)`. Así aplicarlo dos
   veces no rompe nada.
3. **`GO` en una línea sola.** El separador de lotes de T-SQL es una línea que
   sea únicamente `GO`. Escribirlo pegado a otra cosa parte la sentencia, y
   también parte si aparece **dentro de un string o de un comentario** (p.ej.
   el evento `'GOVALE_SALDO'`): el error que sale es "sintaxis" y no señala al
   runner, que es donde se fue una hora de trabajo.
4. **Nada de `;` antes de `ELSE`.** En T-SQL el `;` cierra el `IF` y deja el
   `ELSE` huérfano.
5. Un archivo con `baseline` en el nombre **no se ejecuta**: solo se registra
   (el esquema ya existe, el archivo es de referencia).
6. Sin `TRIM()`: el SQL Server es 2014 y no la tiene. Usar `LTRIM(RTRIM(...))`.

## De dónde viene esta historia

Estas 47 migraciones vivían en el repo del **HUB** (la app Streamlit, retirada
el 2026-09-29: sus workers ya estaban en el contenedor `workersadmon` desde el
2026-09-26). Aquí está la copia que sobrevive.

Dos cosas que conviene saber:

- La numeración tiene huecos y repetidos (dos `0020`, dos `0021`, dos `0023`,
  dos `0025`, dos `0033`): distintas sesiones agregaron archivos sin
  coordinarse. El runner compara por **nombre de archivo**, no por número, así
  que no molesta, pero engaña al ojo.
- Hay **dos migraciones aplicadas en producción cuyo archivo no existe en
  ningún repo**: `0041_dashboard_notas.sql` y `0042_imagen_pantalla.sql`. Se
  aplicaron a mano en el servidor. Están en `schema_migrations` pero su SQL no
  se conserva; si alguna vez hay que reconstruir una base desde cero, esos dos
  cambios habrá que rehacerlos a mano.
