import { writable } from 'svelte/store';

// Estado de la sesión del panel. El token NO vive aquí: viaja en la cookie
// HttpOnly `ecsa_token`. Solo se guarda el usuario y el CSRF que devuelve
// /api/panel/me (o el login) para el header X-CSRF-Token.
export const sesion = writable({ user: null, csrf: '' });

// Token CSRF aparte para que api.js lo lea sin acoplarse al objeto completo.
export const csrf = writable('');

export function guardarSesion(user, token) {
	const valor = token || '';
	sesion.set({ user: user || null, csrf: valor });
	csrf.set(valor);
}

export function limpiarSesion() {
	sesion.set({ user: null, csrf: '' });
	csrf.set('');
}
