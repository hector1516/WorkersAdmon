<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Card from '../lib/components/Card.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// El worker ya entrega solo lo pendiente; el filtro es de lectura y no
	// vuelve a consultar.
	const FILTROS = [
		{ id: 'todos', texto: 'Todos' },
		{ id: 'huerfano', texto: 'Huérfanos' }
	];

	let pendientes = $state([]);
	let resumen = $state({});
	let filtro = $state('todos');
	let cargando = $state(true);
	let ocupado = $state(0);
	let error = $state('');
	let aviso = $state('');

	const visibles = $derived(
		filtro === 'todos' ? pendientes : pendientes.filter((p) => p.categoria === filtro)
	);

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const datos = await get('/triaje');
			pendientes = datos.pendientes || [];
			resumen = datos.resumen || {};
		} catch (e) {
			error = e.message;
		} finally {
			cargando = false;
		}
	}

	async function actuar(p, accion) {
		if (accion === 'aprobar' && !confirm(`¿Aprobar y enviar la respuesta a ${p.de}?`)) return;
		ocupado = p.id;
		error = '';
		aviso = '';
		try {
			const r = await post('/triaje', { accion, id: p.id });
			aviso = r.mensaje || 'Listo.';
			await cargar();
		} catch (e) {
			error = e.message;
		} finally {
			ocupado = 0;
		}
	}

	onMount(cargar);
</script>

<div class="stack">
	<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	<Toast tipo="ok" mensaje={aviso} oncerrar={() => (aviso = '')} />

	<Card titulo="Resumen">
		<div class="row-meta">
			<Badge tone="warn">{resumen.nueva ?? 0} por revisar</Badge>
			<Badge tone="ok">{resumen.aprobada ?? 0} aprobadas</Badge>
			<Badge>{resumen.rechazada ?? 0} rechazadas</Badge>
			<Badge>{resumen.huerfanos_mes ?? 0} huérfanos (30 d)</Badge>
			<Badge>{resumen.bloqueos ?? 0} remitentes bloqueados</Badge>
		</div>
	</Card>

	<div class="segmented" role="group" aria-label="Filtro de pendientes">
		{#each FILTROS as f (f.id)}
			<button
				type="button"
				class:is-active={filtro === f.id}
				aria-pressed={filtro === f.id}
				onclick={() => (filtro = f.id)}
			>
				{f.texto}
			</button>
		{/each}
	</div>

	{#if cargando}
		<Card>
			<Skeleton alto="18px" ancho="45%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={4} />
		</Card>
	{:else if visibles.length === 0}
		<Card>
			<Empty
				icono="check"
				titulo="Nada por revisar"
				texto="El worker clasifica cada mensaje nuevo en su próximo ciclo. Nada se envía sin aprobación."
			/>
		</Card>
	{:else}
		{#each visibles as p (p.id)}
			<Card titulo={p.de} sub={p.asunto}>
				<div class="row-meta">
					<Badge tone={p.categoria === 'huerfano' ? 'warn' : 'info'}>{p.categoria || 'sin categoría'}</Badge>
					{#if p.confianza !== null && p.confianza !== undefined}
						<Badge>conf. {Number(p.confianza).toFixed(2)}</Badge>
					{/if}
					<Badge>{p.cuenta}</Badge>
				</div>

				<div class="hint">
					Destinatario huérfano: <b>{p.destinatario || '—'}</b> · se sugiere
					<b>{p.destino || '—'}</b>
				</div>
				<div class="hint">{p.motivo}</div>
				<div class="hint">{p.fecha}</div>

				{#if p.borrador}
					<details class="field campo-suelto">
						<summary class="hint">Ver el borrador de respuesta</summary>
						<pre class="pre">{p.borrador}</pre>
					</details>
				{/if}

				<div class="acciones-fila">
					<Button variant="primary" tam="sm" icono="check" onclick={() => actuar(p, 'aprobar')} disabled={ocupado === p.id}>
						Aprobar y enviar
					</Button>
					<Button variant="quiet" tam="sm" onclick={() => actuar(p, 'rechazar')} disabled={ocupado === p.id}>
						Rechazar
					</Button>
					<Button tam="sm" onclick={() => actuar(p, 'bloquear_remitente')} disabled={ocupado === p.id}>
						Bloquear remitente
					</Button>
					<Button tam="sm" onclick={() => actuar(p, 'bloquear_dominio')} disabled={ocupado === p.id}>
						Bloquear dominio
					</Button>
				</div>
			</Card>
		{/each}
	{/if}
</div>

<style>
	.row-meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
	}
	.acciones-fila {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		align-items: center;
		margin-top: 10px;
	}
	.campo-suelto {
		margin: 0;
	}
	.pre {
		margin: 8px 0 0;
		padding: 12px;
		background: var(--surface-2);
		border: 1px solid var(--separator);
		border-radius: var(--radius-control);
		font-family: var(--mono);
		font-size: 13px;
		line-height: 1.5;
		white-space: pre-wrap;
		word-break: break-word;
	}
</style>
