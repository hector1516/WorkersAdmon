<script>
	// Esqueleto de carga: nunca se ve una pantalla en blanco. Con
	// prefers-reduced-motion el latido se apaga desde app.css.
	let { ancho = '100%', alto = '16px', radio = '8px', lineas = 1 } = $props();
</script>

{#if lineas > 1}
	<div class="skeleton-stack" aria-hidden="true">
		{#each Array(lineas) as _, i (i)}
			<div
				class="skeleton"
				style={`width:${i === lineas - 1 ? ancho : '100%'};height:${alto};border-radius:${radio};margin-bottom:8px`}
			></div>
		{/each}
	</div>
{:else}
	<div
		class="skeleton"
		style={`width:${ancho};height:${alto};border-radius:${radio}`}
		aria-hidden="true"
	></div>
{/if}
