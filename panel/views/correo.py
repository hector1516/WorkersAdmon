"""
panel.views.correo — Pestaña "Correo" del panel (cuentas de Mailbox)
========================================================================
Dónde se da de alta una cuenta de correo. La app de Mailbox **no** la da de
alta: el usuario solo ve las cuentas que alguien le asignó, por decisión de diseño
—una credencial de correo no se escribe en el cliente, llega por un formulario
que corre en el navegador de quien la está creando.

Esta vista hace cuatro cosas, en este orden de importancia:

  1. **Da de alta una cuenta** con su credencial, que se cifra antes de tocar la
     base. El panel nunca la vuelve a leer.
  2. **La asigna a usuarios.** Sin asignación nadie la ve; el usuario no puede
     autoasignarse.
  3. **Muestra su estado real**, incluido el último error del worker. Un
     `ERROR` con el motivo es lo único que separa "no me llegan correos" de "no
     me llega nada y no sé por qué".
  4. **La pausa.** Una cuenta dada de baja (un jefe que sale) se pausa en vez de
     borrarse: borrarla se lleva el índice por CASCADE.

─── POR QUÉ EL ESTADO LO PONE EL WORKER Y NO ESTA VISTA ───────────────────────

Al dar de alta una cuenta se pone `PENDIENTE`, nunca `ACTIVA`. Esta vista no tiene
las credenciales descifradas —no debe— y por lo tanto **no puede saber** si el
servidor de correo acepta el login. El `mailbox_worker` sí puede: en su ciclo
prueba la conexión, y si funciona promueve la cuenta a `ACTIVA`, y si no la deja
en `ERROR` con el motivo.

Declararla `ACTIVA` desde acá mentiría en doslugares a la vez: el panel la
mostraría en verde, y el worker intentaría sincronizar 20 veces por hora una
cuenta que nunca va a funcionar.
"""
import datetime

from .. import db_correo as _db
from ..icons import icono
from ..templates import esc, page

# Los mismos colores de las apps del ecosistema, para que una tarjeta de cuenta
# en el panel se parezca a la que el usuario ve en Mailbox.
COLORES = ("#FF6B00", "#0EA5E9", "#22C55E", "#A855F7", "#EF4444", "#F59E0B")

# El borde de color de la tarjeta va por clase: un `style=""` con el color de la
# base sería el único estilo inline que quedaba en las vistas.
COLOR_CLASES = {
    "#FF6B00": "co-cuenta--naranja",
    "#0EA5E9": "co-cuenta--azul",
    "#22C55E": "co-cuenta--verde",
    "#A855F7": "co-cuenta--morado",
    "#EF4444": "co-cuenta--rojo",
    "#F59E0B": "co-cuenta--ambar",
}

# estado → (clase de badge, etiqueta). El color lo pone `.badge--*`, no un emoji.
ESTADOS_UI = {
    "ACTIVA":    ("badge--ok", "Activa"),
    "PENDIENTE": ("badge--warn", "Pendiente de validar"),
    "ERROR":     ("badge--bad", "Error"),
    "PAUSADA":   ("", "Pausada"),
}

# Un servidor IMAP sin TLS es una puerta abierta. El panel no ofrece otra cosa a
# propósito: si algún día hace falta, se agrega knowing what it costs.
IMAP_POR_DEFECTO = ("imap.gmail.com", 993)
SMTP_POR_DEFECTO = ("smtp.gmail.com", 587)


def _ahora():
    return datetime.datetime.now().strftime("%d/%m %H:%M")


def _sino(b):
    return "Sí" if b else "No"


def set_csrf(token):
    """El handler ya validó el CSRF; se pasa al template por `render`."""
    return token


# ── Formulario ────────────────────────────────────────────────────────────────

def _formulario(csrf, cuenta=None):
    """El formulario de alta/edición. `cuenta=None` es una alta."""
    editando = bool(cuenta)
    c = cuenta or {}
    v = lambda k, dflt="": esc(c.get(k) or dflt)      # noqa: E731

    def opt_color(txt, sel):
        chosen = " selected" if txt == sel else ""
        return '<option value="%s"%s>%s</option>' % (esc(txt), chosen, esc(txt))

    return f"""
<div class="card co-sec">
  <h2 class="co-h">
    {icono("plus")} {'Editar cuenta' if editando else 'Nueva cuenta de correo'}</h2>
  <form method="post" action="/correo" class="grid2" autocomplete="off">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <input type="hidden" name="accion" value="{'editar' if editando else 'crear'}">
    <input type="hidden" name="id_cuenta" value="{esc(c.get('Id', ''))}">

    <div class="field">
      <label for="co-alias">Alias</label>
      <input id="co-alias" name="alias" class="input" required maxlength="120"
             autocomplete="off" value="{v('Alias')}" placeholder="Ventas">
      <p class="hint">Cómo se ven las tarjetas en la app del usuario.</p>
    </div>

    <div class="field">
      <label for="co-email">Correo</label>
      <input id="co-email" name="email" type="email" class="input" required
             autocomplete="off" value="{v('Email')}"
             placeholder="ventas@ecc-sa.com.mx">
    </div>

    <div class="field">
      <label for="co-pass">Contraseña o clave de app</label>
      <input id="co-pass" name="password" type="password" class="input"
             {'placeholder="(sin cambios)"' if editando else 'required'}
             autocomplete="new-password">
      <p class="hint">
        {'Dejala vacía para NO cambiar la contraseña que ya está cifrada.' if editando
         else 'Se cifra antes de guardarse y el panel nunca vuelve a leerla. Para Gmail, usa una clave de aplicación: la contraseña de la cuenta no sirve con IMAP.'}
      </p>
    </div>

    <div class="field">
      <label for="co-imap">Servidor IMAP</label>
      <input id="co-imap" name="servidor_imap" class="input" required
             autocomplete="off" value="{v('ServidorIMAP', IMAP_POR_DEFECTO[0])}">
    </div>

    <div class="field">
      <label for="co-pimap">Puerto IMAP</label>
      <input id="co-pimap" name="puerto_imap" type="number" class="input" required
             min="1" max="65535" autocomplete="off"
             value="{v('PuertoIMAP', str(IMAP_POR_DEFECTO[1]))}">
      <p class="hint">993 con TLS. Sin TLS no hay soporte.</p>
    </div>

    <div class="field">
      <label for="co-smtp">Servidor SMTP</label>
      <input id="co-smtp" name="servidor_smtp" class="input" required
             autocomplete="off" value="{v('ServidorSMTP', SMTP_POR_DEFECTO[0])}">
    </div>

    <div class="field">
      <label for="co-psmtp">Puerto SMTP</label>
      <input id="co-psmtp" name="puerto_smtp" type="number" class="input" required
             min="1" max="65535" autocomplete="off"
             value="{v('PuertoSMTP', str(SMTP_POR_DEFECTO[1]))}">
      <p class="hint">587 con STARTTLS. 465 abre TLS directo.</p>
    </div>

    <div class="field">
      <label for="co-auth">Autenticación</label>
      <select id="co-auth" name="tipo_auth" class="input">
        <option value="PASSWORD"{' selected' if c.get('TipoAuth') != 'OAUTH2' else ''}>Contraseña</option>
        <option value="OAUTH2"{' selected' if c.get('TipoAuth') == 'OAUTH2' else ''}>OAuth2 (token)</option>
      </select>
    </div>

    <div class="field">
      <label for="co-carpeta">Carpeta de entrada</label>
      <input id="co-carpeta" name="carpeta_raiz" class="input" maxlength="200"
             autocomplete="off" value="{v('CarpetaRaiz', 'INBOX')}" placeholder="INBOX">
      <p class="hint">En Gmail es <code>INBOX</code>; en un Exchange, la carpeta del buzón.</p>
    </div>

    <div class="field">
      <label for="co-icono">Icono</label>
      <input id="co-icono" name="icono" class="input" maxlength="8"
             autocomplete="off" value="{v('Icono', '📮')}">
    </div>

    <div class="field">
      <label for="co-color">Color</label>
      <select id="co-color" name="color" class="input">
        {"".join(opt_color(x, c.get("Color") or COLORES[0]) for x in COLORES)}
      </select>
    </div>

    <div class="field">
      <label for="co-ventana">Retención (días)</label>
      <input id="co-ventana" name="ventana_dias" type="number" class="input"
             min="1" max="3650" autocomplete="off"
             value="{v('VentanaDias', '90')}">
    </div>

    <div class="field">
      <label for="co-max">Máximo de mensajes</label>
      <input id="co-max" name="max_mensajes" type="number" class="input"
             min="100" max="100000" autocomplete="off"
             value="{v('MaxMensajes', '5000')}">
    </div>

    <div class="field co-acciones">
      <button class="btn btn--primary" type="submit">
        {icono("check", 18)}
        {'Guardar cambios' if editando else 'Crear cuenta'}
      </button>
      {'<a class="btn btn--quiet" href="/correo">Cancelar</a>' if editando else ''}
    </div>
  </form>
</div>"""


# ── Lista de cuentas ──────────────────────────────────────────────────────────

def _tarjeta(cuenta, usuarios_asignados, todos):
    """Un buzón con su estado, su error y quién lo tiene."""
    estado = cuenta.get("Estado") or "PENDIENTE"
    badge, etiqueta = ESTADOS_UI.get(estado, ("", estado))
    color = cuenta.get("Color") or COLORES[0]
    color_clase = COLOR_CLASES.get(str(color).upper(), "co-cuenta")

    error = cuenta.get("UltimoError")
    # El error se muestra entero y en su propia caja porque es lo único que dice
    # por qué no llegan correos. Recortarlo a 500 en la base es suficiente; aquí
    # se muestra completo.
    banda_error = (f'<div class="callout bad">{icono("alert", 18)} '
                   f'<b>Último error:</b> {esc(error)}</div>') if error else ""

    checks = []
    for u in todos:
        marcado = " checked" if u["Id"] in usuarios_asignados else ""
        checks.append(
            '<label class="chk"><input type="checkbox" name="usuarios" value="%s"%s>'
            ' %s</label>'
            % (esc(u["Id"]), marcado, esc(u.get("Nombre") or u.get("Email"))))

    sin_sync = (f'<span class="hint">nunca sincronizada</span>'
                if not cuenta.get("UltimoSync")
                else f'<span class="hint">{esc(cuenta["UltimoSync"].strftime("%d/%m %H:%M"))}</span>')

    return f"""
<div class="card co-sec--tight co-cuenta {color_clase}">
  <div class="list">
    <div class="list__row co-fila">
      <div class="list__main">
        <strong>{esc(cuenta.get("Alias") or cuenta.get("Email"))}</strong>
        <div class="hint">{esc(cuenta.get("Email"))}</div>
      </div>
      <div class="list__meta co-meta">
        <span class="badge {badge}">{esc(etiqueta)}</span>
        <span class="badge">{esc(cuenta.get("CuantosUsan") or 0)} usuario(s)</span>
        <span class="badge">{esc(cuenta.get("NoLeidos") or 0)} sin leer</span>
      </div>
    </div>
    <div class="list__row co-fila">
      <div class="list__main hint">
        IMAP {esc(cuenta.get("ServidorIMAP"))}:{esc(cuenta.get("PuertoIMAP"))} ·
        SMTP {esc(cuenta.get("ServidorSMTP"))}:{esc(cuenta.get("PuertoSMTP"))} ·
        {esc(cuenta.get("TipoAuth"))} · retención {esc(cuenta.get("VentanaDias"))}d ·
        tope {esc(cuenta.get("MaxMensajes"))}
      </div>
      <div class="list__meta">último sync {sin_sync}</div>
    </div>
  </div>

  {banda_error}

  <details class="wdesc co-detalle">
    <summary>{icono("users", 18)} Quién tiene esta cuenta ({esc(len(usuarios_asignados))})</summary>
    <form method="post" action="/correo" class="chklist co-chklist">
      <input type="hidden" name="csrf" value="{esc(_CSRF['token'])}">
      <input type="hidden" name="accion" value="asignar">
      <input type="hidden" name="id_cuenta" value="{esc(cuenta["Id"])}">
      {"".join(checks) or '<p class="hint">No hay usuarios activos.</p>'}
      <div>
        <button class="btn btn--sm btn--primary" type="submit">
          {icono("check", 18)} Guardar asignación</button>
      </div>
    </form>
  </details>

  <div class="actions co-sep">
    <a class="btn btn--sm btn--quiet" href="/correo?editar={esc(cuenta["Id"])}">
      {icono("settings", 18)} Editar</a>
    <form method="post" action="/correo" class="inline">
      <input type="hidden" name="csrf" value="{esc(_CSRF['token'])}">
      <input type="hidden" name="accion" value="estado">
      <input type="hidden" name="id_cuenta" value="{esc(cuenta["Id"])}">
      <input type="hidden" name="estado" value="{'PAUSADA' if estado == 'ACTIVA' else 'ACTIVA'}">
      <button class="btn btn--sm" type="submit">
        {icono("pause" if estado == "ACTIVA" else "play", 18)}
        {'Pausar' if estado == 'ACTIVA' else 'Activar'}
      </button>
    </form>
    <form method="post" action="/correo" class="inline"
          onsubmit="return confirm('Se borra la cuenta y TODO su índice de mensajes. ¿Seguir?');">
      <input type="hidden" name="csrf" value="{esc(_CSRF['token'])}">
      <input type="hidden" name="accion" value="borrar">
      <input type="hidden" name="id_cuenta" value="{esc(cuenta["Id"])}">
      <button class="btn btn--sm btn--danger" type="submit">
        {icono("trash", 18)} Borrar</button>
    </form>
  </div>
</div>"""


_CSRF = {"token": ""}

EXPLICACION = """
<div class="card co-sec">
  <h2 class="co-h co-h--chico">
    """ + icono("mail") + """ Cuentas de correo de Mailbox</h2>
  <p class="hint co-h--chico">
    Lo que se da de alta acá es lo que la app de Mailbox muestra. El usuario ve
    únicamente las cuentas que le asignes; no puede crear cuentas ni ver
    credenciales. La contraseña se cifra antes de guardarse y ni esta vista ni el
    panel vuelven a leerla: solo el worker la descifra, porque es el único que abre
    una conexión IMAP.
  </p>
  <p class="hint co-vacio">
    Al crear una cuenta queda <b>PENDIENTE</b>, no activa. En su siguiente ciclo el
    worker prueba la conexión: si el servidor acepta, la pasa a
    <b>ACTIVA</b>; si no, la deja en <b>ERROR</b> con el motivo, que aparece en la
    tarjeta.
  </p>
</div>"""


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido",
           ip="", editando=None):
    """
    La pestaña Correo: alta de cuentas, estado y asignación.

    `editando` es el id de la cuenta abierta en el formulario, o None. Va por la
    URL porque el handler de GET no tiene estado entre peticiones.
    """
    _CSRF["token"] = csrf
    cuentas = _db.listar_cuentas()
    todos = _db.usuarios_activos()

    cuerpo = EXPLICACION

    if editando:
        cuenta = _db.obtener_cuenta(editando)
        if cuenta:
            cuerpo += _formulario(csrf, cuenta)
        else:
            cuerpo += ('<div class="callout bad">Esa cuenta ya no existe.</div>')

    if not cuentas:
        cuerpo += ('<div class="empty"><p>No hay ninguna cuenta de correo dada de '
                   'alta. Abajo está el formulario.</p></div>')
    else:
        cuerpo += (f'<h2 class="co-h co-h--grande">'
                   f'Cuentas ({len(cuentas)})</h2>')
        filas = "".join(_tarjeta(c, _db.usuarios_con_cuenta(c["Id"]), todos)
                        for c in cuentas)
        cuerpo += f'<div class="list co-sec">{filas}</div>'

    # El formulario de alta va UNA vez, al final. Ponerlo arriba de la lista
    # obligaría a pasar por un formulario largo antes de ver si hay cuentas.
    if not editando:
        cuerpo += _formulario(csrf, None)

    return page("correo", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip)
