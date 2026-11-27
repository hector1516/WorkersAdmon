<script>
	import { onMount } from 'svelte';
	import Icon from '../lib/components/Icon.svelte';
	import Card from '../lib/components/Card.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';

	// Placeholder de las rutas del contrato mientras llega su ola. Muestra el
	// esqueleto en el primer render y el estado vacío en cuanto monta: la
	// página nunca queda en blanco.
	let { titulo = '', icono = 'inbox', descripcion = '', params = {}, contenedor = '' } = $props();

	let listo = $state(false);
	onMount(() => {
		listo = true;
	});
</script>

<div class={contenedor}>
<h2 class="page-title"><Icon name={icono} size={20} /> {titulo}</h2>

<Card>
	{#if listo}
		<Empty
			{icono}
			titulo="En construcción"
			texto={descripcion || 'Este módulo se conecta al API en la siguiente ola.'}
		/>
		{#if params && Object.keys(params).length}
			<p class="page-note">Parámetros: {JSON.stringify(params)}</p>
		{/if}
	{:else}
		<Skeleton alto="20px" ancho="40%" />
		<div class="skeleton-gap"></div>
		<Skeleton alto="14px" lineas={3} />
	{/if}
</Card>
</div>
