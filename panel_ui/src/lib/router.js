import { writable } from 'svelte/store';

// Micro-router history-mode, igual que Admon: sin dependencias.
export const path = writable(
	typeof window !== 'undefined' ? window.location.pathname : '/'
);

export function navigate(to, { replace = false } = {}) {
	if (typeof window === 'undefined') return;
	if (replace) history.replaceState({}, '', to);
	else history.pushState({}, '', to);
	path.set(to);
	try {
		window.scrollTo(0, 0);
	} catch {
		// jsdom no implementa scrollTo; en el navegador sí.
	}
}

// Rutas del contrato (API.md). `id` marca el destino activo en la navegación.
export const RUTAS = [
	{ id: 'dashboard', patron: '/', titulo: 'Estado', icono: 'dashboard', sub: 'Workers, avisos y control' },
	{ id: 'dashboard', patron: '/estado', titulo: 'Estado', icono: 'dashboard', sub: 'Workers, avisos y control' },
	{ id: 'logs', patron: '/logs', titulo: 'Logs', icono: 'logs', sub: 'Índice de logs por worker' },
	{ id: 'logs', patron: '/logs/:nombre', titulo: 'Log', icono: 'logs', sub: 'Salida del worker' },
	{ id: 'configuracion', patron: '/configuracion', titulo: 'Configuración', icono: 'settings', sub: 'Variables por worker' },
	{ id: 'apps', patron: '/apps', titulo: 'Apps', icono: 'apps', sub: 'Catálogo de claves por app' },
	{ id: 'avisos', patron: '/avisos', titulo: 'Avisos', icono: 'bell', sub: 'Estado de los avisos del ecosistema' },
	{ id: 'notificaciones', patron: '/notificaciones', titulo: 'Notificaciones', icono: 'bell', sub: 'Conexión, eventos e historial' },
	{ id: 'whatsapp', patron: '/whatsapp', titulo: 'WhatsApp', icono: 'chat', sub: 'Bandeja de conversaciones' },
	{ id: 'eccsita', patron: '/eccsita', titulo: 'ECCSita', icono: 'chat', sub: 'Memoria semanal del grupo' },
	{ id: 'eccsita', patron: '/eccsita/:anio/:semana', titulo: 'ECCSita', icono: 'chat', sub: 'Crónica de la semana' },
	{ id: 'asistencia', patron: '/asistencia', titulo: 'Asistencia', icono: 'clock', sub: 'Resumen y registros' },
	{ id: 'correo', patron: '/correo', titulo: 'Correo', icono: 'mail', sub: 'Cuentas y estado' },
	{ id: 'triaje', patron: '/triaje', titulo: 'Triaje', icono: 'ticket', sub: 'Pendientes por clasificar' },
	{ id: 'login', patron: '/login', titulo: 'Entrar', icono: 'logout', sub: 'Acceso al panel' }
];

// Compara un patrón con la ruta actual. `/logs/:nombre` devuelve
// { nombre: 'aviso' } o null. Ignora la barra final.
export function matchPath(patron, actual) {
	const limpia = (p) => (p.length > 1 ? p.replace(/\/+$/, '') : p);
	const a = limpia(patron).split('/').filter(Boolean);
	const b = limpia(actual || '/').split('/').filter(Boolean);
	if (a.length !== b.length) return null;
	const params = {};
	for (let i = 0; i < a.length; i++) {
		if (a[i].startsWith(':')) {
			params[a[i].slice(1)] = decodeURIComponent(b[i]);
		} else if (a[i] !== b[i]) {
			return null;
		}
	}
	return params;
}

// Devuelve la definición de la primera ruta que casa, con sus parámetros.
export function matchRoute(actual) {
	for (const ruta of RUTAS) {
		const params = matchPath(ruta.patron, actual);
		if (params) return { ...ruta, params };
	}
	return { id: 'no-encontrada', patron: actual, titulo: 'No encontrada', icono: 'alert', sub: '', params: {} };
}

if (typeof window !== 'undefined') {
	window.addEventListener('popstate', () => path.set(window.location.pathname));
	// Interceptar clicks en enlaces internos <a href="/..."> (misma pestaña).
	document.addEventListener('click', (e) => {
		if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
		const t = e.target;
		const el = t instanceof Element ? t.closest('a[href]') : null;
		if (!el) return;
		if (el.target && el.target !== '_self') return;
		if (el.hasAttribute('download')) return;
		const href = el.getAttribute('href');
		if (!href || !href.startsWith('/') || href.startsWith('//')) return;
		e.preventDefault();
		if (href !== window.location.pathname) navigate(href);
	});
}
