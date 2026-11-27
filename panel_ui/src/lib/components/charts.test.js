import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import ChartLine from './ChartLine.svelte';
import ChartBars from './ChartBars.svelte';

let componente = null;
let destino = null;

function pintar(Componente, props) {
	destino = document.createElement('div');
	document.body.appendChild(destino);
	componente = mount(Componente, { target: destino, props });
	return destino;
}

afterEach(() => {
	if (componente) unmount(componente);
	destino?.remove();
	componente = null;
	destino = null;
});

describe('gráficos SVG', () => {
	it('ChartLine pinta un punto por dato y su título accesible', () => {
		const el = pintar(ChartLine, { datos: [22, 23, 21, 24], etiqueta: 'Corriendo' });
		expect(el.querySelectorAll('circle.chart__punto').length).toBe(4);
		expect(el.querySelectorAll('polyline.chart__linea').length).toBe(1);
		expect(el.querySelector('title')?.textContent).toContain('Corriendo');
	});

	it('ChartBars pinta una barra por dato', () => {
		const el = pintar(ChartBars, { datos: [1, 0, 3], etiqueta: 'Errores', tono: 'bad' });
		expect(el.querySelectorAll('rect.chart__barra').length).toBe(3);
		expect(el.querySelector('svg')?.classList.contains('chart--bad')).toBe(true);
		expect(el.querySelector('title')?.textContent).toContain('Errores');
	});

	it('sin datos no inventa puntos', () => {
		const el = pintar(ChartLine, { datos: [] });
		expect(el.querySelectorAll('circle').length).toBe(0);
		expect(el.textContent).toContain('Sin datos');
	});
});
