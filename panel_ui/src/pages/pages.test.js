import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount } from 'svelte';
import Dashboard from './Dashboard.svelte';
import Logs from './Logs.svelte';

let componente = null;
let destino = null;

function respuesta(cuerpo) {
	return {
		ok: true,
		status: 200,
		text: async () => JSON.stringify(cuerpo)
	};
}

function pintar(Componente, props = {}) {
	destino = document.createElement('div');
	document.body.appendChild(destino);
	componente = mount(Componente, { target: destino, props });
	return destino;
}

async function esperar() {
	for (let i = 0; i < 5; i++) await Promise.resolve();
	await new Promise((r) => setTimeout(r, 10));
}

afterEach(() => {
	if (componente) unmount(componente);
	destino?.remove();
	componente = null;
	destino = null;
	vi.unstubAllGlobals();
});

describe('Dashboard', () => {
	it('pinta los 4 KPIs y los dos gráficos con datos falsos', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				respuesta({
					kpis: [
						{ label: 'Corriendo', value: 3, hint: 'de 4', tone: 'ok', icon: 'play' },
						{ label: 'Con error', value: 1, hint: 'FATAL', tone: 'bad', icon: 'alert' },
						{ label: 'Detenidos', value: 1, hint: '1 habilitado', tone: 'warn', icon: 'pause' },
						{ label: 'Latidos viejos', value: 0, hint: 'más de 24 h', tone: '', icon: 'clock' }
					],
					series: { corriendo: [2, 3, 3], error: [1, 0, 1] },
					workers: {
						grupos: [
							{
								nombre: 'HUB',
								items: [
									{
										nombre: 'aviso',
										estado: 'RUNNING',
										latido: 'hace 0 s',
										viejo: false,
										descripcion: 'Avisos'
									}
								]
							}
						]
					},
					avisos: [
						{ nivel: 'ok', titulo: 'Shell', detalle: 'al día', ok: true },
						{ nivel: 'bad', titulo: 'aviso', detalle: 'FALLO', ok: false }
					],
					actualizado: '2026-11-27T10:00:00'
				})
			)
		);

		const el = pintar(Dashboard);
		await esperar();

		const valores = [...el.querySelectorAll('.kpi__value')].map((n) => n.textContent.trim());
		expect(valores).toEqual(['3', '1', '1', '0']);
		expect(el.querySelectorAll('svg.chart--line').length).toBe(1);
		expect(el.querySelectorAll('svg.chart--bars').length).toBe(1);
		expect(el.querySelector('svg.chart--line title')?.textContent).toContain('Workers corriendo');
		expect(el.querySelector('svg.chart--bars title')?.textContent).toContain('Avisos por estado');
		expect(el.querySelector('a[href="/logs/aviso"]')).not.toBeNull();
	});

	it('muestra el error y el botón Reintentar', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 500,
				text: async () => JSON.stringify({ error: 'Base de datos caída' })
			})
		);

		const el = pintar(Dashboard);
		await esperar();

		expect(el.textContent).toContain('Base de datos caída');
		expect(el.textContent).toContain('Reintentar');
	});
});

describe('Logs', () => {
	it('lista los workers con su enlace al detalle', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				respuesta({
					items: [
						{
							nombre: 'aviso',
							estado: 'RUNNING',
							ultimo: '2026-11-27T10:00:00',
							descripcion: 'Avisos del ecosistema',
							log: true
						},
						{
							nombre: 'mailbox_worker',
							estado: 'STOPPED',
							ultimo: null,
							descripcion: 'Correo',
							log: false
						}
					]
				})
			)
		);

		const el = pintar(Logs);
		await esperar();

		expect(el.querySelectorAll('.list__row').length).toBe(2);
		expect(el.textContent).toContain('aviso');
		expect(el.textContent).toContain('mailbox_worker');
		expect(el.querySelector('a[href="/logs/aviso"]')).not.toBeNull();
		expect(el.querySelector('a[href="/logs/mailbox_worker"]')).not.toBeNull();
	});
});
