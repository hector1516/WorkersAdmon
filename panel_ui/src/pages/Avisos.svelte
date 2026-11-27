<script>
	import { onMount } from 'svelte';
	import { get } from '../lib/api.js';
	import Card from '../lib/components/Card.svelte';
	import Badge from '../lib/components/Badge.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Toast from '../lib/components/Toast.svelte';

	let cargando = $state(true);
	let error = $state('');
	let avisos = $state([]);
	let grupos = $state([]);
	let contexto = $state(null);

	onMount(cargar);

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const r = await get('/avisos');
			avisos = r.avisos || [];
			grupos = r.grupos || [];
			contexto = r.contexto || null;
		} catch (e) {
			error = e.message || 'No se pudieron cargar los avisos.';
		} finally {
			cargando = false;
		}
	}

	function cuando(iso) {
		if (!iso) return '—';
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
	<h2 class="page-title">📣 Avisos</h2>
	<p class="desc">
		Estado del ecosistema: workers, shell y versiones. Un aviso en rojo es
		algo que hay que revisar, no un simple registro.
	</p>

	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	{/if}

	{#if cargando}
		<Card>
			<Skeleton alto="20px" ancho="30%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={4} />
		</Card>
	{:else if !avisos.length}
		<Card>
			<Empty icono="bell" titulo="Sin avisos" texto="No hay avisos del ecosistema en este momento." />
		</Card>
	{:else}
		{#if contexto}
			<Card titulo="Contexto">
				<ul class="list">
					<li class="list__row">
						<span class="list__main">Actualizado</span>
						<span class="list__meta">{cuando(contexto.actualizado)}</span>
					</li>
					<li class="list__row">
						<span class="list__main">Zona horaria</span>
						<span class="list__meta">{contexto.zona || '—'}</span>
					</li>
					{#if contexto.totales}
						<li class="list__row">
							<span class="list__main">Workers</span>
							<span class="list__meta">
								{contexto.totales.running ?? 0} corriendo de {contexto.totales.total ?? 0}
							</span>
						</li>
					{/if}
				</ul>
			</Card>
		{/if}

		{#each avisos as a (a.titulo)}
			<Card titulo={a.titulo}>
				{#snippet acciones()}
					<Badge tone={a.ok ? 'ok' : 'bad'}>{a.ok ? 'OK' : 'Atención'}</Badge>
				{/snippet}
				<p class="desc">{a.detalle}</p>
			</Card>
		{/each}

		{#if grupos.length}
			{#each grupos as g (g.nombre)}
				<Card titulo={g.nombre} sub={`${g.items.length} worker(s)`}>
					<ul class="list">
						{#each g.items as w (w.nombre)}
							<li class="list__row">
								<span class="list__main">{w.nombre}</span>
								<span class="list__meta">
									<Badge tone={w.estado === 'RUNNING' ? 'ok' : w.estado === 'STOPPED' ? 'warn' : 'bad'}>
										{w.estado || 'sin estado'}
									</Badge>
									{#if w.latido}<span class="muted"> · {w.latido}</span>{/if}
								</span>
							</li>
						{/each}
					</ul>
				</Card>
			{/each}
		{/if}
	{/if}
</div>
