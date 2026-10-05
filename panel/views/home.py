"""
panel.views.home — La home del panel: la grilla de módulos.

Es el mismo patrón que la home de Field (docs/CONTRATO.md, sección 5): al
entrar se ve una grilla de tarjetas, una por módulo, con ícono, título y
descripción. El panel antes entraba directo al estado de los workers, que es
una tabla larga: no había forma de "volver al menú" ni de ver de un vistazo
qué hay adentro.

Cada tarjeta sale de config.TABS, así que agregar un módulo es agregar una
línea ahi y nada más. Un módulo sin permiso sale apagado (.module-card off):
se ve que existe y que te falta algo, en vez de desaparecer.
"""

from .. import auth as _auth
from .. import config
from ..templates import esc, page

# Ícono grande de cada módulo. El de la barra inferior (TABS["label"]) es el
# mismo emoji; se separa acá para poder usar uno más grande en la tarjeta.
ICONOS = {
    "estado": "📊",
    "logs": "📄",
    "config": "⚙️",
    "notificaciones": "🔔",
    "apps": "📦",
    "asistencia": "🕒",
    "correo": "✉️",
}


def _tarjeta(tab, user):
    """Una tarjeta del menú. `<a>` y no `<button>`: es navegación, y sin JS."""
    icono = ICONOS.get(tab["id"], "📦")
    perm = tab.get("perm")
    if perm and user is not None and not _auth.has_perm(user, perm):
        return (f'<span class="module-card off" title="Requiere el permiso {esc(perm)}">'
                f'<span class="module-icon">{icono}</span>'
                f'<span class="module-title">{esc(tab.get("modulo") or tab["label"])}</span>'
                f'<span class="module-desc">{esc(tab.get("desc", ""))}</span>'
                f'</span>')
    return (f'<a class="module-card" href="{esc(tab["href"])}">'
            f'<span class="module-icon">{icono}</span>'
            f'<span class="module-title">{esc(tab.get("modulo") or tab["label"])}</span>'
            f'<span class="module-desc">{esc(tab.get("desc", ""))}</span>'
            f'</a>')


def render(user, flash_ok="", flash_err="", conn=None,
           lugar="desconocido", ip=""):
    """La home: grilla de módulos."""
    tarjetas = "".join(_tarjeta(t, user) for t in config.TABS)
    body = f'<div class="module-grid">{tarjetas}</div>'
    # Sin subtitle: MODULO_INICIO ya describe el módulo ("Elegí un módulo para
    # empezar.") y el segundo renglón repetía la misma idea con otras palabras.
    return page("inicio", body, user=user, flash_ok=flash_ok, flash_err=flash_err,
                conn=conn, lugar=lugar, ip=ip)
