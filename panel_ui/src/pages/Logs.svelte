<script>
	import { onMount } from 'svelte';
	import { get } from '../lib/api.js';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Card from '../lib/components/Card.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Icon from '../lib/components/Icon.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// Índice de logs: GET /api/panel/logs. Cada fila lleva al detalle del
	// worker; el detalle es el único que tiene scroll interno.
	let cargando = $state(true);
	let error = $state('');
	let items = $state([]);

	onMount(cargar);

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const r = await get('/logs');
			items = r.items || [];
		} catch (e) {
			error = e.message || 'No se pudo cargar el índice de logs.';
		} finally {
			cargando = false;
		}
	}

	function estadoTono(estado) {
		switch (estado) {
			case 'RUNNING':
				return 'ok';
			case 'STARTING':
			case 'UNKNOWN':
			case 'NO_HABILITADO':
				return 'info';
			case 'STOPPED':
			case 'EXITED':
				return 'warn';
			case 'BACKOFF':
			case 'FATAL':
				return 'bad';
			default:
				return '';
		}
	}

	function estadoTexto(estado) {
		switch (estado) {
			case 'RUNNING':
				return 'En ejecución';
			case 'STARTING':
				return 'Iniciando';
			case 'STOPPED':
				return 'Detenido';
			case 'EXITED':
				return 'Terminado';
			case 'BACKOFF':
				return 'Reintentando';
			case 'FATAL':
				return 'Fallo';
			case 'NO_HABILITADO':
				return 'No habilitado';
			default:
				return estado || 'Sin estado';
		}
	}

	function ultimo(iso) {
		if (!iso) return 'sin registro';
		try {
			return new Intl.DateTimeFormat('es-MX', {
				dateStyle: 'medium',
				timeStyle: 'short'
			}).format(new Date(iso));
		} catch {
			return iso;
		}
	}
</script>

<div>
	<h2 class="page-title"><Icon name="logs" size={20} /> Logs</h2>
	<p class="desc">Últimas líneas de cada worker. Abre uno para ver el log completo.</p>

	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	{/if}

	{#if cargando}
		<Card>
			<Skeleton alto="18px" ancho="30%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={6} />
		</Card>
	{:else if items.length === 0}
		<Card>
			<Empty icono="logs" titulo="Sin logs" texto="Todavía no hay workers en el contenedor.">
				<Button variant="quiet" icono="refresh" onclick={cargar}>Reintentar</Button>
			</Empty>
		</Card>
	{:else}
		<Card titulo="Workers" sub={`${items.length} programa(s)`}>
			<ul class="list" aria-label="Workers con log">
				{#each items as w (w.nombre)}
					<li class="list__row">
						<div class="list__main">
							<a href={`/logs/${w.nombre}`}>{w.nombre}</a>
							<div class="muted dashx__sub">
								{#if w.descripcion}{w.descripcion} · {/if}Último registro {ultimo(w.ultimo)}
							</div>
						</div>
						<div class="dashx__rowmeta">
							<Badge tone={estadoTono(w.estado)}>{estadoTexto(w.estado)}</Badge>
							<Button href={`/logs/${w.nombre}`} variant="quiet" tam="sm">Ver log</Button>
						</div>
					</li>
				{/each}
			</ul>
		</Card>
	{/if}
</div>
