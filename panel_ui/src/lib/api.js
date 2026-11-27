import { get as leerStore } from 'svelte/store';
import { csrf, limpiarSesion } from './stores/session.js';
import { navigate } from './router.js';

// Todas las rutas del backend cuelgan de /api/panel. Los helpers reciben la
// parte relativa ('/estado', '/logs/aviso').
const BASE = '/api/panel';

export class ApiError extends Error {
	constructor(mensaje, status = 0) {
		super(mensaje);
		this.name = 'ApiError';
		this.status = status;
	}
}

async function request(ruta, { method = 'GET', body } = {}) {
	const opciones = {
		method,
		credentials: 'include',
		headers: {}
	};

	if (body !== undefined) {
		opciones.headers['Content-Type'] = 'application/json';
		opciones.body = JSON.stringify(body);
	}

	// CSRF de doble envío en todo POST (el servidor lo compara en tiempo
	// constante). En GET no hace falta.
	if (method !== 'GET' && method !== 'HEAD') {
		const token = leerStore(csrf);
		if (token) opciones.headers['X-CSRF-Token'] = token;
	}

	let res;
	try {
		res = await fetch(`${BASE}${ruta}`, opciones);
	} catch {
		throw new ApiError('Sin conexión con el panel', 0);
	}

	if (res.status === 401) {
		limpiarSesion();
		navigate('/login', { replace: true });
		throw new ApiError('La sesión expiró. Vuelve a entrar.', 401);
	}

	let datos = null;
	if (res.status !== 204) {
		const texto = await res.text();
		if (texto) {
			try {
				datos = JSON.parse(texto);
			} catch {
				datos = null;
			}
		}
	}

	if (!res.ok) {
		throw new ApiError(datos?.error || `Error ${res.status}`, res.status);
	}

	return datos;
}

export function get(ruta) {
	return request(ruta);
}

export function post(ruta, body = {}) {
	return request(ruta, { method: 'POST', body });
}
