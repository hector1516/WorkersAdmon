# Historial de cambios — WorkersAdmon

> Contenedor `workersadmon`: los workers en background, el servidor
> MCP/passkeys/push y el panel de control. Versión y novedades visibles en
> `static/changelog.json` y en el popup 📋 del shell.

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
