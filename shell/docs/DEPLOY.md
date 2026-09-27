# ECCSA-Shell · despliegue

Este repo es la **fuente** del shell. Las 3 apps (Field, Admon y el panel de
WorkersAdmon) guardan una copia de lo que les toca y se despliegan por su
propio lado. Ver `README.md` para qué hay en cada carpeta y `docs/CONTRATO.md`
para el contrato del banner.

## 1. Cambiar el diseño (el flujo normal)

```bash
vim tokens.css            # o src/body.css, o banner/

python tools/build_shell.py             # regenera dist/ (3 variantes)
python tools/sync_shell.py --all        # copia a las 3 apps + estampa versiones
python tools/build_shell.py --check && python tools/sync_shell.py --all --check
```

Si cambiaste el diseño, **sube `VERSION`**. La guarda te lo va a reclamar:

```
[BLOQ] field   el diseño cambió (sha a1b2c3… → d4e5f6…) pero VERSION sigue en 1.1.0
       → sube la VERSION del shell (editá VERSION) o usa --force
```

`--force` es solo para cuando 1.1.0 todavía no se publicó en ninguna app (por
ejemplo, si estás armando la versión antes de meterla).

## 2. Publicar

Un `git push` a `main` dispara **propagar shell a las apps**
(`.github/workflows/propagate.yml`), que empuja el CSS y los artefactos a los
3 repos. De ahí en adelante cada app se despliega sola con su propio workflow.

**Requisito (una sola vez):** en este repo, el secret
`SHELL_DEPLOY_TOKEN` — un PAT classic con alcance `repo` de un usuario con
acceso a los 3 repos de apps. Sin él, el workflow avisa y no hace nada (no
falla), y hay que correr `sync_shell.py --all` a mano.

## 3. Cómo se despliega cada app (gate de rebuild)

Las 3 apps tienen un workflow `deploy.yml` con `on: push` que decide entre dos
caminos mirando **qué archivos** cambió:

| Modo | Cuándo | Qué hace |
|---|---|---|
| `hotsync` | No cambió nada de la imagen | Compila lo estático y lo copia al contenedor que ya corre. **De minutos a segundos.** |
| `rebuild` | Cambió un insumo de la imagen | `docker build` + recrear el contenedor, con el rollback de siempre. |

Los archivos que **obligan** a reconstruir:

| App | Insumos de la imagen |
|---|---|
| Field | `Dockerfile`, `api/requirements.txt`, `docker/` |
| Admon | `Dockerfile`, `requirements.txt`, `api/requirements.txt` |
| WorkersAdmon | `Dockerfile`, `requirements.txt`, `docker/` |

Todo lo demás (`.svelte`, `.css`, `.py`, estáticos) va por `hotsync`. El
frontend se compila **dentro de un contenedor `node:20-alpine` descartable**,
así que el runner no necesita Node instalado.

El `rebuild` no se corre desde el checkout del runner: lanza la tarea
programada del ServerVM (`schtasks /run /tn FieldBuild | AdmonBuild |
WorkersBuild`), que trabaja sobre el clon canónico y conserva el rollback que
ya tenía `build.ps1`. Y va por `schtasks` y no por SSH porque Windows mata los
procesos hijos al cerrarse la sesión.

**El primer deploy con este workflow conviene vigilarlo.** Todo push anterior se
desplegaba a mano, así que el camino automático no se ha probado en producción.
Cada paso imprime lo que va a hacer, y `deploy/hotsync.sh --dry-run` imprime
los comandos sin ejecutar nada.

## 4. Chequeo diario

`tools/check_daily.py` corre dentro del contenedor `workersadmon` (que lleva
una copia verificada del shell en `/app/shell`) y escribe el resultado en
`/data/shell_check.json`, que el panel muestra. Verifica dos cosas: que `dist/`
esté generado desde las fuentes, y que la copia del panel (`panel/shell.css`)
sea idéntica a `dist/shell.plain.css` con la `VERSION` al día.

## 5. Requisitos

Las 3 apps comparten BD (`ECCSA_Admon`), passkeys, push, PDFs y el módulo de
Legends, así que sus versiones están mandadas por las de Field. Ver
`docs/VERSIONES.md`.
