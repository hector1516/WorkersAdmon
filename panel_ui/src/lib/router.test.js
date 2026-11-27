import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { path, navigate, matchPath, matchRoute } from './router.js';

describe('router', () => {
	beforeEach(() => {
		navigate('/', { replace: true });
	});

	it('navega y actualiza el store y la URL', () => {
		navigate('/logs');
		expect(get(path)).toBe('/logs');
		expect(window.location.pathname).toBe('/logs');
	});

	it('extrae el parámetro de /logs/:nombre', () => {
		expect(matchPath('/logs/:nombre', '/logs/aviso')).toEqual({ nombre: 'aviso' });
		expect(matchPath('/logs/:nombre', '/logs')).toBeNull();
	});

	it('resuelve la ruta con parámetros y conserva el destino activo', () => {
		const ruta = matchRoute('/logs/aviso-diario');
		expect(ruta.id).toBe('logs');
		expect(ruta.titulo).toBe('Log');
		expect(ruta.params).toEqual({ nombre: 'aviso-diario' });
	});

	it('la raíz es el tablero, no 404', () => {
		expect(matchRoute('/').id).toBe('dashboard');
		expect(matchRoute('/ruta-inexistente').id).toBe('no-encontrada');
	});

	it('responde al popstate', () => {
		history.pushState({}, '', '/configuracion');
		window.dispatchEvent(new PopStateEvent('popstate'));
		expect(get(path)).toBe('/configuracion');
	});
});
