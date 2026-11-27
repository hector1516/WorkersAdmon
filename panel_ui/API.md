# Panel WorkersAdmon — frontend Svelte + API

El panel pasa a **Svelte 5 + Vite** (igual que Field y Admon) conservando el
diseño Apple de `panel/apple.css`. Python deja de generar HTML: queda como API
JSON y sirve el `dist/`.

## Reglas

- Misma sesión: cookie `ecsa_token` (HttpOnly) y tabla `HUB_Sessions`. El
  frontend **no** guarda tokens.
- CSRF: `GET /api/panel/me` devuelve `csrf`; todo POST lo manda en
  `X-CSRF-Token`. El servidor compara en tiempo constante.
- Errores: `{ "error": "texto para la persona" }` con el código HTTP correcto.
- Fechas y montos se formatean en el cliente (`Intl`), no en el servidor.
- Nada de secretos en el JSON: los campos `tipo: "secret"` viajan como
  `{valor: "", tiene_valor: true}`.
- El diseño vive en `panel_ui/src/app.css` (tokens y componentes copiados de
  `panel/apple.css`); `apple.css` deja de servirse cuando el SPA entra.

## Endpoints

| Método | Ruta | Devuelve |
|---|---|---|
| GET | `/api/panel/me` | `{ user: {id,nombre,email,permisos[]}, csrf }` o 401 |
| POST | `/api/panel/login` | `{email,password}` → set-cookie + `{user,csrf}` |
| POST | `/api/panel/logout` | 204 |
| GET | `/api/panel/estado` | KPIs, workers, avisos e historial |
| POST | `/api/panel/workers/{nombre}/{accion}` | `start|stop|restart|enable|disable` |
| GET | `/api/panel/logs` | índice de logs |
| GET | `/api/panel/logs/{nombre}` | `{nombre, texto}` |
| GET | `/api/panel/config` | config por worker |
| POST | `/api/panel/config` | guarda claves |
| GET | `/api/panel/apps` | catálogo + valores |
| POST | `/api/panel/apps` | `accion: save|edit|del|add|clasificar` |
| GET | `/api/panel/avisos` | avisos y contexto |
| GET | `/api/panel/notificaciones?bloque=` | `conexion|telegram|push|correo|ia` |
| POST | `/api/panel/notificaciones/{bloque}` | guarda / prueba |
| GET | `/api/panel/whatsapp?bloque=` | `conexion|avisos|grupos|historial` |
| GET | `/api/panel/wa/conversaciones` | lista de chats |
| GET | `/api/panel/wa/conversaciones/{chat}` | historial + estado |
| POST | `/api/panel/wa/conversaciones/{chat}/{accion}` | `enviar|tomar|devolver|cerrar` |
| GET | `/api/panel/asistencia` | resumen y filas |
| GET | `/api/panel/correo` | cuentas y estado |
| GET | `/api/panel/triaje` | pendientes |

## Formas

```jsonc
// estado
{
  "kpis": [{"label":"Corriendo","value":23,"hint":"de 27","tone":"ok","icon":"play"}],
  "series": {"corriendo": [22,23,23], "error": [1,0,1]},
  "workers": {"grupos":[{"nombre":"HUB","items":[
      {"nombre":"aviso","estado":"RUNNING","latido":"hace 0 s","viejo":false,"descripcion":"..."}]}]},
  "avisos": [{"titulo":"Shell","detalle":"...","ok":true}]
}

// logs índice
{"items":[{"nombre":"aviso","estado":"RUNNING","ultimo":"2026-11-27 10:00","descripcion":"..."}]}

// conversación
{"chat":"5218...@lid","nombre":"Hector","estado":"BOT","asignado":null,
 "mensajes":[{"dir":"IN","texto":"hola","fecha":"2026-11-27T10:00:00"}]}
```

## SPA

- Rutas del navegador: `/`, `/logs`, `/logs/:nombre`, `/configuracion`,
  `/apps`, `/avisos`, `/notificaciones`, `/whatsapp`, `/asistencia`,
  `/correo`, `/triaje`, `/login`.
- Python sirve `panel_ui/dist` y devuelve `index.html` para cualquier ruta que
  no empiece por `/api/`, `/static/`, `/sw.js`, `/manifest.webmanifest` ni
  `/icons/`.
- Carga: `shell` con splash corto (≤600 ms) y esqueletos por tarjeta; nunca
  pantalla en blanco.
- Gráficos del dashboard: SVG propio (líneas y barras) sin librerías.
- Iconos: `lucide-svelte` (mismos nombres del contrato: `LayoutDashboard`,
  `ScrollText`, `Settings`, `Boxes`, `Bell`, `Clock`, `MessageCircle`,
  `Mail`, `Ticket`, `TriangleAlert`, etc.).
