import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount } from 'svelte';
import Dashboard from './Dashboard.svelte';
import Logs from './Logs.svelte';
import Eccsita from './Eccsita.svelte';
import Configuracion from './Configuracion.svelte';
import { navigate } from '../lib/router.js';

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
		expect(el.querySelector('svg.chart--bars title')?.textContent).toContain('Workers con error');
		expect(el.querySelector('a[href="/logs/aviso"]')).not.toBeNull();
	});

	it('abre las acciones del worker y llama a la API de control', async () => {
		const llamadas = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url, opciones = {}) => {
				llamadas.push([String(url), opciones]);
				return respuesta({
					kpis: [],
					series: { corriendo: [1], error: [0] },
					workers: {
						grupos: [
							{
								nombre: 'HUB',
								items: [
									{
										nombre: 'aviso',
										estado: 'RUNNING',
										latido: 'hace 0 s',
										corriendo: true,
										habilitado: true,
										protegido: false
									}
								]
							}
						]
					},
					avisos: []
				});
			})
		);

		const el = pintar(Dashboard);
		await esperar();

		const boton = el.querySelector('.wacc__toggle');
		expect(boton).not.toBeNull();
		boton.click();
		await esperar();

		const reiniciar = [...el.querySelectorAll('.wacc button')].find((b) =>
			b.textContent.includes('Reiniciar')
		);
		expect(reiniciar).toBeDefined();
		reiniciar.click();
		await esperar();

		const control = llamadas.find(([u]) => u.includes('/workers/aviso/restart'));
		expect(control).toBeDefined();
		expect(control[1].method).toBe('POST');
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

// API falsa del módulo ECCSita: una semana resumida con dos personas.
function apiEccsita({ grupo = true } = {}) {
	return vi.fn(async (url) => {
		const u = String(url);
		if (u.includes('/personas/7')) {
			return respuesta({
				usuario: { id: 7, nombre: 'Ana' },
				items: [
					{
						id: 9,
						texto: 'Ana y su termo eterno',
						autor: 'Beto',
						fecha: '2026-11-03T09:00:00',
						tipo: 'broma',
						aprobado: false
					},
					{
						id: 10,
						texto: 'Le dicen La Jefa',
						autor: 'Ceci',
						fecha: '2026-11-04T09:00:00',
						tipo: 'apodo',
						aprobado: true
					}
				]
			});
		}
		if (u.includes('/personas')) {
			return respuesta({
				items: [
					{ idUsuario: 7, nombre: 'Ana', perfiles: 2, ultimo: '2026-11-04T09:00:00' },
					{ idUsuario: 8, nombre: 'Beto', perfiles: 1, ultimo: '2026-11-02T10:00:00' }
				],
				total: 2
			});
		}
		if (u.includes('/semanas/2026/44')) {
			return respuesta({
				anio: 2026,
				semana: 44,
				inicio: '2026-11-02',
				fin: '2026-11-08',
				estado: 'RESUMIDA',
				generado_por: 'IA',
				resumen: 'Crónica de prueba de la semana.',
				temas: ['Los vales', { tema: 'El café', detalle: 'Siempre se acaba' }],
				personajes: [{ nombre: 'Ana', detalle: 'la que resuelve' }],
				momentos: [{ titulo: 'El meme del lunes', cita: '«otra vez»' }],
				mensajes: [
					{
						id: 'm1',
						autor: 'Ana',
						texto: 'hola a todos',
						tipo: 'text',
						fecha: '2026-11-02T10:00:00'
					},
					{
						id: 'm2',
						autor: 'Beto',
						texto: 'buenos días',
						tipo: 'text',
						fecha: '2026-11-02T10:05:00'
					}
				]
			});
		}
		if (u.includes('/semanas')) {
			return respuesta({
				items: [
					{
						anio: 2026,
						semana: 44,
						inicio: '2026-11-02',
						fin: '2026-11-08',
						mensajes: 2,
						participantes: 2,
						estado: 'RESUMIDA'
					}
				]
			});
		}
		if (u.endsWith('/grupo')) {
			return respuesta(
				grupo
					? { configurado: true, chat_id: '1203630@g.us', nombre: 'ECCSita' }
					: { configurado: false, chat_id: '', nombre: '' }
			);
		}
		if (u.endsWith('/grupos')) {
			return respuesta({ items: [{ id: '1203630@g.us', nombre: 'ECCSita', participantes: 9 }] });
		}
		return respuesta({});
	});
}

describe('Configuración', () => {
	const BOT = {
		sarcasmo: true,
		roast_grupo: true,
		volt: true,
		volt_cada: 12,
		ia: false,
		asistencia: true
	};

	function apiConfig() {
		return vi.fn(async (url, opciones = {}) => {
			const u = String(url);
			const metodo = opciones.method || 'GET';
			if (u.endsWith('/bot') && metodo === 'POST') return respuesta({ ok: true });
			if (u.endsWith('/bot')) return respuesta(BOT);
			if (u.endsWith('/canales')) return respuesta({});
			return respuesta({ workers: [] });
		});
	}

	it('pinta los seis controles del bot y guarda al cambiar', async () => {
		const fetchMock = apiConfig();
		vi.stubGlobal('fetch', fetchMock);

		const el = pintar(Configuracion);
		await esperar();

		expect(el.textContent).toContain('Bot de WhatsApp (Volt)');
		expect(el.querySelectorAll('input[name^="bot-"]').length).toBe(6);
		expect(el.querySelectorAll('input[type="checkbox"][name^="bot-"]').length).toBe(5);
		const cada = el.querySelector('input[type="number"][name="bot-volt_cada"]');
		expect(cada).not.toBeNull();
		expect(cada.value).toBe('12');

		const sarcasmo = el.querySelector('input[name="bot-sarcasmo"]');
		sarcasmo.checked = false;
		sarcasmo.dispatchEvent(new Event('change', { bubbles: true }));
		await esperar();

		const llamada = fetchMock.mock.calls.find(
			([url, opciones]) =>
				String(url).endsWith('/api/panel/bot') && opciones?.method === 'POST'
		);
		expect(llamada).toBeTruthy();
		expect(JSON.parse(llamada[1].body)).toEqual({ clave: 'sarcasmo', valor: 0 });
	});
});

describe('Eccsita', () => {
	it('pinta semanas, abre una semana y muestra los mensajes', async () => {
		navigate('/eccsita', { replace: true });
		vi.stubGlobal('fetch', apiEccsita());

		const el = pintar(Eccsita);
		await esperar();

		expect(el.textContent).toContain('Semana 44');
		expect(el.querySelector('.ecc-week')).not.toBeNull();
		expect(el.textContent).toContain('Resumida');
		expect(el.textContent).toContain('2 mensajes');
		expect(el.textContent).toContain('Grupo configurado');

		el.querySelector('.ecc-week').click();
		await esperar();

		expect(window.location.pathname).toBe('/eccsita/2026/44');
		expect(el.textContent).toContain('Crónica de prueba de la semana.');
		expect(el.textContent).toContain('El café');
		expect(el.textContent).toContain('Ana');
		expect(el.textContent).toContain('hola a todos');
		expect(el.textContent).toContain('buenos días');
		expect(el.querySelectorAll('.ecc-msg').length).toBe(2);
	});

	it('sin grupo muestra el aviso para elegirlo', async () => {
		navigate('/eccsita', { replace: true });
		vi.stubGlobal('fetch', apiEccsita({ grupo: false }));

		const el = pintar(Eccsita);
		await esperar();

		expect(el.textContent).toContain('Sin grupo');
		expect(el.textContent).toContain('Falta elegir el grupo de WhatsApp');
		expect(el.textContent).toContain('Elegir grupo');
	});

	it('construye las ligas de exportación Markdown y JSON', async () => {
		navigate('/eccsita/2026/44', { replace: true });
		vi.stubGlobal('fetch', apiEccsita());

		const el = pintar(Eccsita);
		await esperar();

		const md = el.querySelector('a[download][href*="formato=md"]');
		const json = el.querySelector('a[download][href*="formato=json"]');
		expect(md?.getAttribute('href')).toBe(
			'/api/panel/eccsita/semanas/2026/44/export?formato=md'
		);
		expect(json?.getAttribute('href')).toBe(
			'/api/panel/eccsita/semanas/2026/44/export?formato=json'
		);
	});

	it('pinta las personas de Roasts por persona con sus menciones', async () => {
		navigate('/eccsita', { replace: true });
		vi.stubGlobal('fetch', apiEccsita());

		const el = pintar(Eccsita);
		await esperar();

		expect(el.textContent).toContain('Roasts por persona');
		expect(el.querySelectorAll('.ecc-persona').length).toBe(2);
		expect(el.textContent).toContain('Ana');
		expect(el.textContent).toContain('2 mención(es)');

		el.querySelectorAll('.ecc-persona')[0].click();
		await esperar();

		expect(el.querySelectorAll('.ecc-mencion').length).toBe(2);
		expect(el.textContent).toContain('Ana y su termo eterno');
		expect(el.textContent).toContain('Le dicen La Jefa');
		expect(el.textContent).toContain('BROMA');
		expect(el.textContent).toContain('APODO');
		expect(el.textContent).toContain('Oculta');
	});

	it('aprobar manda el POST al perfil abierto', async () => {
		navigate('/eccsita', { replace: true });
		const fetchMock = apiEccsita();
		vi.stubGlobal('fetch', fetchMock);

		const el = pintar(Eccsita);
		await esperar();
		el.querySelectorAll('.ecc-persona')[0].click();
		await esperar();

		const aprobar = [...el.querySelectorAll('.ecc-mencion button')].find((b) =>
			b.textContent.includes('Aprobar')
		);
		aprobar.click();
		await esperar();

		const llamada = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/perfiles/9'));
		expect(llamada).toBeTruthy();
		expect(String(llamada[0])).toBe('/api/panel/eccsita/perfiles/9');
		expect(llamada[1].method).toBe('POST');
		expect(JSON.parse(llamada[1].body)).toEqual({ accion: 'aprobar' });
		expect(el.querySelector('.ecc-mencion').classList.contains('is-oculta')).toBe(false);
	});

	it('el buscador filtra las personas por nombre', async () => {
		navigate('/eccsita', { replace: true });
		vi.stubGlobal('fetch', apiEccsita());

		const el = pintar(Eccsita);
		await esperar();
		expect(el.querySelectorAll('.ecc-persona').length).toBe(2);

		const input = el.querySelector('input[name="buscar"]');
		input.value = 'beto';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await esperar();

		expect(el.querySelectorAll('.ecc-persona').length).toBe(1);
		expect(el.querySelector('.ecc-persona').textContent).toContain('Beto');
	});
});
