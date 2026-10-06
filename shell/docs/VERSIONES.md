# Versiones del ecosistema ECCSA

> **Mandato: Field.** `versiones/requisitos-canonicos.txt` es copia literal de las
> versiones que usa **Field** (`field/api/requirements.txt` + su contenedor).
> **Field no se modifica**; Admon y WorkersAdmon se alinean a esta lista en todo
> lo compartido.

## Por qué importa

Las apps apuntan a la **misma base** (`ECCSA_Admon`) y comparten código:

| Compartido | Dónde |
|---|---|
| `pymssql` | todas leen/escriben las mismas tablas |
| `reportlab` + `Pillow` | PDFs y PNG de QR que se ven igual en Field, Admon y el panel |
| `pywebpush` + `py-vapid` | las claves VAPID en `HUB_PushConfig` (y en `HUB_Config` para Mailbox) y las suscripciones en `HUB_PushSuscripciones`, que **lleva columna `App`** para que un aviso de una app no aparezca en otra. Ver [`PUSH.md`](PUSH.md). |
| `webauthn` + `pyjwt` | passkeys con RP raíz `ecc-sa.com.mx` compartidas entre Field y Admon |
| `fastapi` + `pydantic` | Admon y los workers de Field en WorkersAdmon comparten `api/` |

Con versiones distintas/passaba esto: Field generaba PDFs con reportlab 4.4.0 y
el panel con 5.0.1 → el mismo documento se veía distinto según dónde se abriera,
y un QR podía cambiar de tamaño o formato.

## La lista canónica (lo que las apps deben tener igual)

| Librería | Versión (mandato Field) |
|---|---|
| Python | 3.11.15 |
| fastapi | 0.115.0 |
| uvicorn[standard] | 0.30.0 |
| pydantic | 2.9.0 |
| pyjwt | 2.9.0 |
| webauthn | 3.0.0 |
| pywebpush | 2.0.0 |
| py-vapid | 1.9.1 |
| python-multipart | 0.0.9 |
| pymssql | 2.2.11 |
| reportlab | 4.4.0 |
| Pillow | 11.0.0 |
| pytz | (sin pin) |
| pysmb | (sin pin) |
| google-generativeai | (sin pin) |

Fuera del mandato (cada app según su necesidad, Field no los usa): `playwright`
y `qrcode` y `supervisor` y `requests` (solo WorkersAdmon).

## Verificación automática

```bash
python tools/check_versiones.py            # compara los 3 contenedores vs el mandato
```

Corre en el ServerVM (tarea programada diaria `ShellCheck`): por cada contenedor
ejecuta un `docker exec … python3` que lee las versiones instaladas y las
compara con `requisitos-canonicos.txt`. Escribe el resultado en
`/data/versiones.json` y el **panel lo muestra**: `● VERSIONES OK` o
`▲ VERSIONES DESINCRONIZADAS` con el detalle por app.

Si algo no cuadra, `build_versiones.py` (o el propio mensaje del panel) dice qué
paquete y en qué app.

## Versiones de aplicación (el número del banner)

Política común para todas:

1. **SemVer**: `MAJOR.MINOR.PATCH`.
   - `PATCH` = correcciones/fixes.
   - `MINOR` = funcionalidad nueva.
   - `MAJOR` = cambio incompatible (p. ej. romper la API o el login).
2. **Una sola fuente por app**, y el banner la lee de ahí:
   - Field y Admon: `package.json` → `src/lib/shell.js` (lo genera `sync_shell.py`).
   - WorkersAdmon: `panel/config.py` → `APP_VERSION`.
3. **No se edita a mano el valor que aparece en el banner**: se cambia el
   `package.json` / `APP_VERSION` y se corre `sync_shell.py --all`.
4. Se sube la versión en el mismo commit del cambio y se anota en el `CHANGELOG.md`
   del repo.

Estado actual: Field `1.9.16` (no se toca, es el mandato), Admon `1.0.0`,
Dashboard `1.0.0`, WorkersAdmon `1.0.0`, shell `1.9.0`.

## Cómo se cambia una versión compartida

1. Editar `versiones/requisitos-canonicos.txt` (y `field/api/requirements.txt` si
   el cambio nace en Field).
2. `python tools/build_versiones.py --check` para ver qué apps se desvían.
3. Propagar: actualizar `api/requirements.txt` de Admon y `requirements.txt` de
   WorkersAdmon con el mismo pin.
4. Desplegar cada app (Admon: `AdmonBuild`; WorkersAdmon: `WorkersBuild` +
   `run_container.ps1`).
5. `python tools/check_versiones.py` → debe salir OK en todas.
