import { get } from 'svelte/store';
import { sesion, guardarSesion, limpiarSesion } from './session.js';
import { get as getJson, post } from '../api.js';

// Reexporta el store de sesión para que la UI haga `$auth.user`.
export const auth = sesion;

// Pide /me al arrancar. Si no hay cookie válida, deja la sesión vacía.
export async function init() {
	try {
		const datos = await getJson('/me');
		guardarSesion(datos?.user, datos?.csrf);
	} catch {
		limpiarSesion();
	}
	return get(sesion);
}

export async function login(email, password) {
	const datos = await post('/login', { email, password });
	guardarSesion(datos?.user, datos?.csrf);
	return datos?.user;
}

export async function logout() {
	try {
		await post('/logout');
	} finally {
		limpiarSesion();
	}
}

export function isLoggedIn() {
	return get(sesion).user !== null;
}
