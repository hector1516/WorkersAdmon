<script>
	import { onMount } from 'svelte';
	import { get } from '../lib/api.js';
	import Button from '../lib/components/Button.svelte';
	import Icon from '../lib/components/Icon.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// Log crudo de un worker: GET /api/panel/logs/{nombre}. La página NO
	// scrollea; el desplazamiento vive dentro del <pre class="log"> (app.css:
	// .content:has(.dashx__log), .dashx__log pre.log).
	let { params = {} } = $props();
	let nombre = $derived(params.nombre || '');

	let cargando = $state(true);
	let error = $state('');
	let texto = $state('');

	onMount(cargar);

	async function cargar() {
		if (!nombre) {
			cargando = false;
			error = 'Falta el nombre del worker.';
			return;
		}
		cargando = true;
		error = '';
		try {
			const r = await get(`/logs/${encodeURIComponent(nombre)}`);
			texto = r.texto || '';
		} catch (e) {
			error = e.message || 'No se pudo leer el log.';
			texto = '';
		} finally {
			cargando = false;
		}
	}
</script>

<div class="card dashx__log">
	<div class="dashx__loghead">
		<h2><Icon name="logs" size={20} /> {nombre || 'Log'}</h2>
		<Button href="/logs" variant="quiet" tam="sm" icono="chevronleft">Volver a Logs</Button>
	</div>

	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
		<div>
			<Button variant="quiet" icono="refresh" onclick={cargar}>Reintentar</Button>
		</div>
	{:else if cargando}
		<Skeleton alto="16px" lineas={8} />
	{:else}
		<pre class="log">{texto || '(log vacío)'}</pre>
	{/if}
</div>
