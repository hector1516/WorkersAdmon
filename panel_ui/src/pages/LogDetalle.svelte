<script>
	import Placeholder from './Placeholder.svelte';

	let { params = {} } = $props();
</script>

<Placeholder
	titulo={params.nombre ? `Log · ${params.nombre}` : 'Log'}
	icono="logs"
	descripcion="Salida completa del worker, con desplazamiento interno."
	{params}
/>
