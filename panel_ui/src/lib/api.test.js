import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { path } from './router.js';
import { sesion, guardarSesion, limpiarSesion } from './stores/session.js';
import { get as apiGet, post as apiPost } from './api.js';

function respuesta(status, cuerpo) {
	return {
		ok: status >= 200 && status < 300,
		status,
		text: async () => (cuerpo === undefined ? '' : JSON.stringify(cuerpo))
	};
}

describe('api', () => {
	beforeEach(() => {
		limpiarSesion();
		guardarSesion({ nombre: 'Hector' }, 'csrf-de-prueba');
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('GET usa cookie y la base /api/panel', async () => {
		const fetchMock = vi.fn().mockResolvedValue(respuesta(200, { user: { nombre: 'Hector' } }));
		vi.stubGlobal('fetch', fetchMock);

		const datos = await apiGet('/me');

		expect(datos.user.nombre).toBe('Hector');
		const [url, opciones] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/panel/me');
		expect(opciones.credentials).toBe('include');
		expect(opciones.method).toBe('GET');
		expect(opciones.headers['X-CSRF-Token']).toBeUndefined();
	});

	it('POST manda X-CSRF-Token y el cuerpo JSON', async () => {
		const fetchMock = vi.fn().mockResolvedValue(respuesta(200, { ok: true }));
		vi.stubGlobal('fetch', fetchMock);

		await apiPost('/config', { clave: 'valor' });

		const [, opciones] = fetchMock.mock.calls[0];
		expect(opciones.method).toBe('POST');
		expect(opciones.headers['X-CSRF-Token']).toBe('csrf-de-prueba');
		expect(opciones.headers['Content-Type']).toBe('application/json');
		expect(JSON.parse(opciones.body)).toEqual({ clave: 'valor' });
	});

	it('parsea {error} del servidor', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(respuesta(400, { error: 'Clave inválida' }))
		);

		await expect(apiPost('/config', {})).rejects.toThrow('Clave inválida');
	});

	it('en 401 limpia la sesión y redirige a login', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(respuesta(401, { error: 'Sesión expirada' }))
		);

		await expect(apiGet('/estado')).rejects.toThrow('La sesión expiró');
		expect(get(path)).toBe('/login');
		expect(get(sesion).user).toBeNull();
		expect(get(sesion).csrf).toBe('');
	});

	it('204 devuelve null sin intentar parsear', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respuesta(204)));
		await expect(apiPost('/logout')).resolves.toBeNull();
	});
});
