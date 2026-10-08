# Reglas de actualización — workersadmon

Cómo se despliega **workersadmon**. Mismo proceso que las otras cinco apps: la imagen la
construye la CI **en la nube** y Arcane es el único que administra el contenedor.

## Las tres reglas

1. **Tú haces el código y el push. Nada más.**
2. **La CI construye y publica.** El servidor no compila.
3. **El operador da `Update` en Arcane.** Sin ese paso, la imagen nueva existe pero
   nadie la aplica.

## Cómo actualizar

```
1. Modificas el código
2. git commit + git push origin main
3. CI construye en ubuntu-latest y publica en ghcr.io/hector1516/workersadmon
4. Arcane → Projects → workersadmon → Updates → Update
```

## Dónde mirar

| | |
|---|---|
| Paquete | `workersadmon` |
| Imagen | `ghcr.io/hector1516/workersadmon` |
| Repositorio | [hector1516/WorkersAdmon](https://github.com/hector1516/WorkersAdmon) |
| Workflow | `.github/workflows/deploy.yml` |
| Jobs | desbloquear → build |
| Puertos | 8000 → 8000 y 8200 → 8080 |
| Salud | GET /healthz en 8080 interno |
| ¿Toca el servidor? | No |

## Comprobar que salió bien

Antes de ir a Arcane, mira que los jobs estén en verde:

```
https://github.com/hector1516/WorkersAdmon/actions
```

deploy.yml · desbloquear → build

Arcane: `https://docker.ecc-sa.com.mx` → **Projects → workersadmon → Updates**.
Ahí debe aparecer la imagen nueva con un digest distinto. Ahí es donde aplicas.

Si el job `deploy` aparece (cuando existe), el mensaje
`ATENCION: lo administra Arcane: no lo recreo` es **correcto**, no un error: el guard
impide que la CI toque el contenedor.

## Qué NO hacer

- **No** ejecutar `docker run`, `docker rm`, `docker restart` ni `docker cp` sobre el
  contenedor. Arcane es el dueño; si le quitas el contenedor, el Project queda roto.
- **No** correr `/opt/apps/_lib/run_app.sh workersadmon` — es el camino legacy y recrea por
  fuera de Arcane.
- **No** hacer `docker compose up -d` con el mismo directorio del Project.
- **No** cambiar el nombre del paquete a `${{ github.repository }}` si el repo y el
  paquete se llaman distinto (ver notas de workersadmon).
- **No** compilar en el servidor. Para eso está `ubuntu-latest`.

## Qué pasa si algo sale mal

**El workflow falla.** No toques el contenedor. Lee el log en GitHub Actions; el
servidor sigue con la imagen anterior y Arcane nunca vio nada.

**El contenedor no arranca tras el `Update`.** En Arcane: *Logs*. Para volver atrás,
revierte el commit en GitHub, deja que la CI publique la versión anterior y da
`Update` otra vez.

**Arcane no muestra actualización.** Comprueba en el server que la imagen se puede
bajar:

```bash
docker manifest inspect ghcr.io/hector1516/workersadmon:latest && echo OK || echo BLOQUEADO
```

Si sale `BLOQUEADO` o `unauthorized`, el paquete está **privado**. Es la causa más
frecuente. Ver la sección siguiente.

## Paquetes: dos cosas que hay que saber

**El paquete hereda la visibilidad del repo.** Nace privado si el repo es privado.
Cambiar el repo a público *después* no arregla un paquete ya creado.

**`GITHUB_TOKEN` solo publica a paquetes enlazados al repo.** Un paquete creado con
push manual nunca queda enlazado, y el push falla con `write_package denied` aunque
los permisos del YAML estén correctos. La cura: borrar el paquete y dejar que la CI
lo recree.

## Notas de workersadmon

Fue la última en estandarizarse y la que más cambió. Antes compilaba en un
runner **Windows** con PowerShell 5.1, mantenía un clon canónico en
`C:\WorkersAdmon` y llamaba a `deploy/build.ps1`. Ese flujo **no publicaba en
GHCR**, así que Arcane nunca veía actualizaciones de esta app.

El build conserva `--build-arg WITH_PLAYWRIGHT=1`, que es el que trae los
navegadores para los workers que los necesitan. Si se quita, se rompen esos workers.

`deploy/build.ps1`, `build.bat`, `run_container.ps1` y `register_task.ps1` quedaron
sin uso. No los borres sin confirmar que nadie los invoca.

Comparte volumen `mailbox_data` y red `openwa_default` con `mailbox` y `openwa-api`;
cualquier cambio de red o volumen afecta a los tres.

---

_estandarizado el 2026-10-08 junto a admon, mailbox, dashboard, colaboradores,
workersadmon y field._
