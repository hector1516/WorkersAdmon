<script>
	// Barras en SVG propio (sin librerías). Una barra por dato.
	let { datos = [], etiqueta = 'Barras', tono = '', alto = '120px' } = $props();

	const W = 320;
	const H = 100;
	const M = 4;

	const valores = $derived((Array.isArray(datos) ? datos : []).map((v) => Number(v) || 0));
	const max = $derived(Math.max(1, ...valores));
	const hueco = $derived(valores.length ? Math.min(6, (W - M * 2) / (valores.length * 2)) : 0);
	const ancho = $derived(
		valores.length ? (W - M * 2 - hueco * (valores.length - 1)) / valores.length : 0
	);

	const barras = $derived(
		valores.map((v, i) => {
			const h = (v / max) * (H - M * 2);
			return {
				v,
				x: M + i * (ancho + hueco),
				y: H - M - h,
				w: ancho,
				h: Math.max(h, v > 0 ? 1 : 0)
			};
		})
	);
</script>

{#if barras.length === 0}
	<p class="chart__vacio">Sin datos</p>
{:else}
	<svg
		class="chart chart--bars {tono ? `chart--${tono}` : ''}"
		viewBox="0 0 {W} {H}"
		preserveAspectRatio="none"
		style={`height:${alto}`}
		role="img"
		aria-label={etiqueta}
	>
		<title>{etiqueta}: {valores.join(', ')}</title>
		<line class="chart__eje" x1={M} y1={H - M} x2={W - M} y2={H - M} />
		{#each barras as b, i (i)}
			<rect class="chart__barra" x={b.x} y={b.y} width={b.w} height={b.h} rx="2"></rect>
		{/each}
	</svg>
{/if}
