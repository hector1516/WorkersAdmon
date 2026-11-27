"""
panel/icons.py — Iconos SVG en línea (estilo Lucide)
====================================================
El panel no carga fuentes de iconos ni imágenes: cada icono es un SVG que se
pinta con `currentColor`, así hereda el color del texto y no depende de red.
El trazo es 1.75 (el mismo que usa Lucide) y el tamaño lo decide quien llama.

Contrato (ver panel/DESIGN.md):

    icono("dashboard")        -> '<svg ...>'
    icono("whatsapp", 18)     -> '<svg ...>' con width/height 18
    icono("no-existe")        -> ''

Nunca lanza: un nombre desconocido devuelve cadena vacía para que una vista
nueva no tumbe la página por un icono mal escrito.
"""

# Trazos de Lucide (24×24, sin relleno). Solo se guardan los hijos del <svg>;
# el envoltorio lo arma `icono()` para que todos salgan idénticos.
_TRAZOS = {
    "dashboard": (
        '<rect width="7" height="9" x="3" y="3" rx="1.5"/>'
        '<rect width="7" height="5" x="14" y="3" rx="1.5"/>'
        '<rect width="7" height="9" x="14" y="12" rx="1.5"/>'
        '<rect width="7" height="5" x="3" y="16" rx="1.5"/>'
    ),
    "logs": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
        '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25'
        'a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73'
        'l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73'
        'l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73'
        'V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25'
        'a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73'
        'l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73'
        'l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25'
        'a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "apps": (
        '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73'
        'l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/>'
        '<path d="M12 22V12"/><path d="m3.3 7 8.7 5 8.7-5"/>'
    ),
    "bell": (
        '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
    ),
    "clock": (
        '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'
    ),
    "whatsapp": (
        '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>'
        '<path d="M9 10.4c.2 2.6 2 4.4 4.6 4.6l1.2-1.4-2-1-.9.5'
        'c-.7-.4-1.1-.8-1.5-1.5l.5-.9-1-2z"/>'
    ),
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16'
        'a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M12 16v-4"/><path d="M12 8h.01"/>'
    ),
    "refresh": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "chevron": '<path d="m9 18 6-6-6-6"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "logout": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        '<polyline points="16 17 21 12 16 7"/><path d="M21 12H9"/>'
    ),
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
        '<path d="M10 11v6"/><path d="M14 11v6"/>'
    ),
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": (
        '<rect x="14" y="4" width="4" height="16" rx="1"/>'
        '<rect x="6" y="4" width="4" height="16" rx="1"/>'
    ),
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9'
        'L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "mail": (
        '<rect width="20" height="16" x="2" y="4" rx="2"/>'
        '<path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>'
    ),
    "car": (
        '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10'
        's-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9'
        'A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/>'
        '<circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>'
    ),
    "ticket": (
        '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2'
        'a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/>'
        '<path d="M13 5v2"/><path d="M13 11v2"/><path d="M13 17v2"/>'
    ),
}

# Nombres válidos, en el orden del contrato. Sirve para pruebas y para que un
# typo se detecte al revisar el catálogo, no en producción.
NOMBRES = tuple(_TRAZOS)

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="{t}" height="{t}" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true" focusable="false">{cuerpo}</svg>')


def icono(nombre, tam=20):
    """SVG en línea del icono `nombre`, o '' si el nombre no existe.

    `tam` se fuerza a entero: nunca se interpola texto libre en el HTML.
    """
    cuerpo = _TRAZOS.get((nombre or "").strip().lower())
    if not cuerpo:
        return ""
    try:
        lado = max(1, int(tam))
    except (TypeError, ValueError):
        lado = 20
    return _SVG.format(t=lado, cuerpo=cuerpo)
