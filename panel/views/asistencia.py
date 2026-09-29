"""
panel.views.atencion — Pestaña "🕒 Asistencia" del panel
========================================================
La asistencia se infiere de la red: el escáner ve el MAC del teléfono o la
laptop en la red de la oficina y registra una ENTRADA o un SALIDA. Con el
escáner cada 3 minutos, lo que se sabe de una llegada NO es "llegó a las 9:02"
sino **que llegó entre las 8:57 y las 9:00**. Por eso aquí se muestra la
ventana y no un minuto suelto.

Cinco estados, y la diferencia entre ellos es lo importante:

  ✅ Normal            puntual, dentro de la tolerancia
  🟠 Tarde             se puede AFIRMAR que pasó la tolerancia
  🟡 Salida temprana   se puede afirmar que se fue antes
  🔍 Indeterminado     la ventana CRUZA la tolerancia: no se puede saber
  ⚪ Sin datos         el escáner no cubrió el día: NO es una falta
  ❌ Ausente           se escaneó y no hubo presencia del equipo
  ➖ No aplica         ese día no había turno configurado

Los dos últimos se separaron porque antes todo caía en "Ausente", y con el
escáner caído eso significaba marcar ausente a todo el planta.

Reglas de la vista:
  * Requiere el permiso `AccesoDeteccionRed` (la misma columna que usa el HUB).
  * "Calcular" es una acción POST con CSRF: escribir en la tabla de asistencia
    no es un GET.
  * La fecha por defecto es AYER: el día de hoy todavía no termina, así que
    casi siempre daría "sin salida registrada".
"""
import datetime

from .. import db
from ..templates import esc, page

_CSRF = {"token": ""}

# estado → (icono, etiqueta). El orden es el de "más grave a menos".
ESTADOS = {
    "TARDE": ("🟠", "Tarde"),
    "SALIDA_TEMPRANA": ("🟡", "Salida temprana"),
    "INDETERMINADO": ("🔍", "Indeterminado"),
    "AUSENTE": ("❌", "Ausente"),
    "SIN_DATOS": ("⚪", "Sin datos"),
    "NO_APLICA": ("➖", "No aplica"),
    "CALCULADA": ("✅", "Normal"),
}

# El nombre del día en español, a mano: `strftime("%A")` sale en el locale del
# contenedor (inglés) y un "Monday 28/09/2026" en una ui en español es un bug
# que se ve de inmediato.
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
        "domingo")


def _fecha_larga(fecha):
    """'lunes 28/09/2026'."""
    return f"{DIAS[fecha.weekday()]} {fecha.strftime('%d/%m/%Y')}"


ICONO_ESTADO = {
    "TARDE": "🟠", "SALIDA_TEMPRANA": "🟡", "INDETERMINADO": "🔍",
    "AUSENTE": "❌", "SIN_DATOS": "⚪", "NO_APLICA": "➖", "CALCULADA": "✅",
}

EXPLICACION = """
<div class="empty" style="text-align:left;padding-top:0">
La hora real es una <b>ventana</b>, no un minuto: el escáner ve la red cada
3 minutos, así que lo que se sabe es entre qué dos instantes ocurrió. Si esa
ventana <b>cruza</b> la tolerancia se marca 🔍 <b>Indeterminado</b> en vez de
acusar o exonerar, porque con esa evidencia no se puede afirmar ninguna de las
dos cosas.
<br><br>
⚪ <b>Sin datos</b> NO es una falta: es que el escáner no cubrió ese día (estuvo
caído, o el servidor no escaneó). ➖ <b>No aplica</b> es que ese día no había
turno configurado.
</div>
"""


def set_csrf(token):
    _CSRF["token"] = token or ""


def _csrf_field():
    return f'<input type="hidden" name="csrf" value="{esc(_CSRF["token"])}">'


def _fmt_hora(hora):
    import asistencia_core
    return asistencia_core.formatear_hora(hora)


def _fmt_ventana(piso, techo, respaldo=None):
    """
    '08:57–09:00 ±3 min'.

    `respaldo` es el minuto de las columnas viejas (HoraEntradaReal), para los
    registros anteriores a la migración, que no tienen ventana: se muestra con
    un '±?' en vez de fingir que se sabe el rango.
    """
    import asistencia_core
    if piso or techo:
        return asistencia_core.formatear_ventana(piso, techo)
    if respaldo:
        return asistencia_core.formatear_hora(respaldo) + " ±?"
    return "—"


def _estado_de(fila):
    """El estado de una fila, con el fallback para registros viejos.

    Los registros anteriores a la migración no tienen `Estado`: se deduje de
    los bits que sí tienen, que es lo único honesto que se puede hacer con
    ellos.
    """
    estado = (fila.get("Estado") or "").upper()
    if estado:
        return estado
    if fila.get("Ausente"):
        return "AUSENTE"
    if fila.get("EntradaTardia"):
        return "TARDE"
    if fila.get("SalidaTemprana"):
        return "SALIDA_TEMPRANA"
    return "CALCULADA"


def _fecha_de_query(valor):
    """'YYYY-MM-DD' → date, o ayer si no viene / no es válido."""
    if valor:
        try:
            return datetime.date.fromisoformat(valor)
        except ValueError:
            pass
    return datetime.date.today() - datetime.timedelta(days=1)


def _contar(estados):
    """Cuántos de cada estado, para las tarjetas de arriba."""
    n = {k: 0 for k in ESTADOS}
    for e in estados:
        n[e] = n.get(e, 0) + 1
    return n


def _tarjetas(estados):
    n = _contar(estados)
    afirmables = n.get("CALCULADA", 0) + n.get("TARDE", 0) + n.get("AUSENTE", 0)
    return f"""
  <div class="cards">
    <div class="card green"><div class="n">{afirmables}</div>
      <div class="l">Afirmables</div></div>
    <div class="card blue"><div class="n">{n.get('TARDE', 0)}</div>
      <div class="l">Tardanzas</div></div>
    <div class="card gray"><div class="n">{n.get('INDETERMINADO', 0)}</div>
      <div class="l">Indeterminados</div></div>
    <div class="card orange"><div class="n">{n.get('SIN_DATOS', 0)}</div>
      <div class="l">Sin datos</div></div>
    <div class="card red"><div class="n">{n.get('AUSENTE', 0)}</div>
      <div class="l">Ausentes</div></div>
  </div>"""


def _filtro(fecha, usuarios, usuario_sel):
    """Formulario GET: fecha y usuario. No escribe nada, por eso va por GET."""
    opts = ['<option value="">Todos</option>']
    for uid, nombre in usuarios:
        sel = " selected" if str(uid) == str(usuario_sel or "") else ""
        opts.append(f'<option value="{esc(uid)}"{sel}>{esc(nombre)}</option>')
    return f"""
  <div class="panel">
    <h2>🔎 Consultar</h2>
    <form method="get" action="/asistencia" class="cfg-save" style="padding:14px 18px">
      <label class="cfg-field" style="margin:0">
        <span>Fecha</span>
        <input class="input" type="date" name="fecha" value="{esc(fecha.isoformat())}">
      </label>
      <label class="cfg-field" style="margin:0">
        <span>Usuario</span>
        <select class="input sel-tipo" name="usuario">{''.join(opts)}</select>
      </label>
      <button class="btn btn-primary" type="submit">🔍 Consultar</button>
    </form>
  </div>"""


def _tabla(fecha, asistencias):
    if not asistencias:
        return ('<div class="empty">No hay asistencias calculadas para el '
                f'{esc(fecha.strftime("%d/%m/%Y"))}. Pulsa «Calcular y guardar» '
                'abajo.</div>')

    filas = []
    for a in asistencias:
        estado = _estado_de(a)
        icono = ICONO_ESTADO.get(estado, "•")
        tarde = a.get("MinutosTarde")
        antes = a.get("MinutosAntes")
        nota = a.get("Observaciones") or ""
        # La nota larga (por qué SIN DATOS) solo aparece en el bloque de
        # evidencia de abajo: en la tabla haría la fila ilegible en un
        # teléfono.
        filas.append(f"""<tr>
  <td>{icono}</td>
  <td>{esc(a.get("UsuarioNombre") or "")}</td>
  <td class="hide-sm">{esc(a.get("TurnoNombre") or "—")}</td>
  <td>{esc(_fmt_hora(a.get("HoraEntradaEsperada")))}</td>
  <td>{esc(_fmt_ventana(a.get("EntradaPiso"), a.get("EntradaTecho"),
                        a.get("HoraEntradaReal")))}</td>
  <td class="hide-sm">{esc(_fmt_hora(a.get("HoraSalidaEsperada")))}</td>
  <td>{esc(_fmt_ventana(a.get("SalidaPiso"), a.get("SalidaTecho"),
                        a.get("HoraSalidaReal")))}</td>
  <td>{esc(f"±{a['VentanaMin']}" if a.get("VentanaMin") is not None else "—")}</td>
  <td>{esc(f"{tarde} min" if tarde else "—")}</td>
  <td>{esc(f"{antes} min" if antes else "—")}</td>
  <td>{esc(ESTADOS.get(estado, (icono, estado))[1])}</td>
</tr>""")
    return f"""
  <div class="panel">
    <h2>📅 {esc(_fecha_larga(fecha))}
      <span class="badge badge-info">{len(asistencias)} personas</span></h2>
    <div class="tscroll"><table>
      <thead><tr>
        <th></th><th>Usuario</th><th class="hide-sm">Turno</th>
        <th>Entrada esp.</th><th>Entrada real</th>
        <th class="hide-sm">Salida esp.</th><th>Salida real</th>
        <th>Ancho</th><th>Tarde</th><th>Temprana</th><th>Estado</th>
      </tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table></div>
  </div>"""


def _detalle(fecha, asistencias):
    """El 'por qué' de cada fila: sin datos de evidencia no se puede defender
    un veredicto, y antes no se guardaba nada de eso."""
    filas = []
    for a in asistencias:
        estado = _estado_de(a)
        if not (a.get("Observaciones") or a.get("EscaneosDia") is not None
                or a.get("GapMaximoMin")):
            continue
        filas.append(f"""<tr>
  <td>{esc(a.get("UsuarioNombre") or "")}</td>
  <td>{esc(ESTADOS.get(estado, ("", estado))[1])}</td>
  <td>{esc(a.get("EscaneosDia") if a.get("EscaneosDia") is not None else "—")}</td>
  <td>{esc(a.get("GapMaximoMin") if a.get("GapMaximoMin") is not None else "—")}</td>
  <td class="muted">{esc(a.get("Observaciones") or "—")}</td>
</tr>""")
    if not filas:
        return ""
    return f"""
  <div class="panel">
    <h2>🔍 Evidencia <span class="badge badge-info">por qué cada veredicto</span></h2>
    <div class="card-desc">Cuántos escaneos hubo ese día (la señal de que el
    sistema estaba vivo), el hueco más largo entre ellos, y la nota del cálculo.
    Un hueco grande es una caída del escáner, no una ausencia.</div>
    <div class="tscroll"><table>
      <thead><tr><th>Usuario</th><th>Estado</th><th>Escaneos</th>
        <th>Hueco máx. (min)</th><th>Nota</th></tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table></div>
  </div>"""


def _calcular(fecha, csrf):
    """El botón de calcular. Es POST porque escribe en HUB_AsistenciaDiaria."""
    return f"""
  <div class="panel">
    <h2>🔄 Calcular</h2>
    <div class="card-desc">Recalcula el {esc(fecha.strftime("%d/%m/%Y"))} para
    todos los usuarios con turno, a partir de la evidencia de red que hay en la
    base. No borra nada: sobrescribe el registro de ese día.</div>
    <form method="post" action="/asistencia" class="cfg-save">
      {_csrf_field()}
      <input type="hidden" name="accion" value="calcular">
      <input type="hidden" name="fecha" value="{esc(fecha.isoformat())}">
      <button class="btn btn-primary" type="submit">🔄 Calcular y guardar</button>
    </form>
  </div>"""


def render(user, flash_ok="", flash_err="", csrf="", lugar="desconocido",
           ip="", fecha=None, usuario=None, resumen=None):
    """
    Devuelve el HTML de la pestaña Asistencia.

    `resumen` es el dict que devuelve `calcular_y_guardar_asistencias_fecha`
    cuando se acaba de calcular; None si no se acaba de pulsar el botón.
    """
    set_csrf(csrf)
    fecha = fecha or (datetime.date.today() - datetime.timedelta(days=1))

    try:
        uid = int(usuario) if usuario else None
    except (TypeError, ValueError):
        uid = None

    asistencias = db.get_asistencia_fecha(fecha, uid)
    # Solo usuarios con turno: la lista completa son 40+ nombres y el filtro
    # se usa sobre gente que tiene turno asignado.
    #
    # OJO con el nombre de la columna: get_all_usuario_turnos() devuelve
    # `UsuarioNombre` (el alias del JOIN con HUB_Users), no `NombreUsuario`. Con
    # el nombre equivocado el comprehension se comía todas las filas y el
    # filtro de usuario salía VACÍO, sin error ni aviso: se ve bien en las
    # pruebas si el fake usa el mismo nombre mal escrito que la vista.
    usuarios = sorted(
        {(u["IdUsuario"], u.get("UsuarioNombre") or "")
         for u in db.get_all_usuario_turnos() if u.get("IdUsuario")},
        key=lambda par: par[1].lower())

    cuerpo = _tarjetas([_estado_de(a) for a in asistencias])
    cuerpo += EXPLICACION
    cuerpo += _filtro(fecha, usuarios, uid)
    cuerpo += _tabla(fecha, asistencias)
    cuerpo += _detalle(fecha, asistencias)
    cuerpo += _calcular(fecha, csrf)

    if resumen:
        por_estado = resumen.get("por_estado") or {}
        lineas = "".join(
            f'<li>{ICONO_ESTADO.get(e, "•")} {esc(ESTADOS.get(e, ("", e))[1])}: '
            f'<b>{n}</b></li>' for e, n in sorted(por_estado.items(),
                                                  key=lambda kv: -kv[1]))
        cuerpo += (f'<div class="panel"><h2>📊 Resultado del cálculo</h2>'
                   f'<div class="card-desc">Se guardaron '
                   f'<b>{esc(resumen.get("guardados"))}</b> registros.'
                   f'</div><ul class="p desc" style="padding-left:1.4rem">'
                   f'{lineas}</ul></div>')
        if por_estado.get("SIN_DATOS"):
            cuerpo += ('<div class="callout bad">⚠️ Hubo días <b>SIN DATOS</b>: '
                       'el escáner no cubrió esas ventanas. No son faltas; '
                       'revisa el worker del escáner antes de usar esto para '
                       'nómina.</div>')

    return page("asistencia", cuerpo, user=user, flash_ok=flash_ok,
                flash_err=flash_err, lugar=lugar, ip=ip)
