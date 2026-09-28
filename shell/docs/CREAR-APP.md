# Cómo dar de alta una app nueva (patrón ECCSA-Shell)

## 1. Repo
`hector1516/<AppName>` privado. Copia `src/` de Admon (Svelte + Tailwind) o
`panel/` de WorkersAdmon (Python sin build) según el stack.

Después **dale de alta en el shell** (si es una app nueva, en `tools/sync_shell.py`):
`APPS` (variante + ruta del CSS), `CANDIDATES` (dónde está el clon), y en
`tools/propagate.py` el `REPOS` con su repo y rama. Sin eso la app no recibe el
CSS en los pushes del shell ni entra en los chequeos.

## 2. Shell (lo importante para que se vea igual)
```bash
git clone https://github.com/hector1516/ECCSA-Shell
python ECCSA-Shell/tools/sync_shell.py --target /ruta/de/tu/app --variant t4
```
Eso copia el CSS canónico y crea `ECCSA_SHELL_VERSION` + `src/lib/shell.js`
(versiones para el banner). Si usas Tailwind v3: `--variant t3`. Sin build:
`--variant plain` (destino `panel/shell.css`, cargado desde `templates.py`).

Añade el banner: copia `banner/SyncHeader.svelte` (Svelte) o
`banner/banner.py.html` (HTML) y expón `GET /api/shell/state`
(ver `docs/CONTRATO.md`).

## 3. Datos
- BD: `ECCSA_Admon` con `pymssql` (la imagen necesita `freetds-dev`).
- Usuarios/permisos: tabla de usuarios de `ECCSA_Admon` (hoy `HUB_Users`) con
  columnas `Acceso<Modulo>`.
- Passkeys: registra siempre con el **RP raíz `ecc-sa.com.mx`** para que la
  passkey sirva en cualquier subdominio.

## 4. Contenedor (ServerVM)
- Puerto host `81xx` → puerto interno (Field 8100→80, Admon 8103→8000,
  panel 8200→8080). El siguiente libre: 8101/8104.
- Subdominio `*.ecc-sa.com.mx` apuntando al host.
- Deploy: copia el patrón de `WorkersAdmon/deploy/` — `build.bat` lanzado con
  `schtasks /run` (SSH mata los procesos hijos), `run_container.ps1` con
  `--env-file` y credenciales **fuera** del repo (`deploy/env.local` gitignored).

## 5. Workers en background
No van en la app: van al contenedor `workersadmon` (como los 4 de Field).
- copia el script a `/app/`, crea `docker/conf.d.available/<nombre>.conf`,
- actívalo con `enable_worker <nombre>` (queda en `/data/workers_enabled.txt`).

## 5b. Si la app es una PANTALLA (kiosco)
El kiosco de la oficina (`hector1516/Dashboard`) es el caso raro: se abre sin
sesión, se ve a 3 metros y no la toca nadie. Además de lo de arriba:
- `GET /api/shell/state` **sin** `require_user`, con `user: null` y `sync.estado`
  = salud del último refresco (§2b del contrato).
- Tipografía self-hosted (sin CDN): si se cae la red, `system-ui` no es el
  diseño de ECCSA.
- Cero llamadas a internet en runtime: datos desde un snapshot en disco.

## 6. Checklist antes de publicar
- [ ] Probado en **iPhone instalado** (safe-areas, zoom, tab bar).
- [ ] Service worker: **solo intercepta GET** (nunca POST con FormData).
- [ ] Manifest + iconos (120/152/167/180/192/512 + maskable) + apple-touch.
- [ ] `python ECCSA-Shell/tools/sync_shell.py --target . --check` → al día.
- [ ] Tests y `GET /api/shell/state` responde 200 con sesión.
