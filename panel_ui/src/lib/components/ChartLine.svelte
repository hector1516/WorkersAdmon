<script>
	// Serie de líneas en SVG propio (sin librerías). Un punto por dato, con
	// `<title>` para lectores de pantalla.
	let { datos = [], etiqueta = 'Serie', tono = '', alto = '120px' } = $props();

	const W = 320;
	const H = 100;
	const M = 4;

	const valores = $derived((Array.isArray(datos) ? datos : []).map((v) => Number(v) || 0));
	const max = $derived(Math.max(1, ...valores));
	const min = $derived(Math.min(0, ...valores));
	const rango = $derived(max - min || 1);

	const puntos = $derived(
		valores.map((v, i) => ({
			v,
			x: valores.length <= 1 ? W / 2 : M + (i * (W - M * 2)) / (valores.length - 1),
			y: H - M - ((v - min) / rango) * (H - M * 2)
		}))
	);

	const linea = $derived(puntos.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '));
	const area = $derived(
		puntos.length
			? `${M},${H - M} ${linea} ${(W - M).toFixed(1)},${H - M}`
			: ''
	);
</script>

{#if puntos.length === 0}
	<p class="chart__vacio">Sin datos</p>
{:else}
	<svg
		class="chart chart--line {tono ? `chart--${tono}` : ''}"
		viewBox="0 0 {W} {H}"
		preserveAspectRatio="none"
		style={`height:${alto}`}
		role="img"
		aria-label={etiqueta}
	>
		<title>{etiqueta}: {valores.join(', ')}</title>
		<line class="chart__eje" x1={M} y1={H - M} x2={W - M} y2={H - M} />
		<polygon class="chart__area" points={area} />
		<polyline class="chart__linea" points={linea} />
		{#each puntos as p, i (i)}
			<circle class="chart__punto" cx={p.x} cy={p.y} r="2.5" />
		{/each}
	</svg>
{/if}
