<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { plural } from '../lib/texto.js';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Card from '../lib/components/Card.svelte';
	import ChartBars from '../lib/components/ChartBars.svelte';
	import ChartLine from '../lib/components/ChartLine.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Icon from '../lib/components/Icon.svelte';
	import Kpi from '../lib/components/Kpi.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// Tablero de Estado. Consume GET /api/panel/estado. En escritorio la
	// rejilla ocupa el alto restante y solo las listas internas se desplazan;
	// en el teléfono fluye y la página desplaza (app.css: .content:has(.dash)).
	let cargando = $state(true);
	let error = $state('');
	let datos = $state(null);
	// Control de un worker: la fila abierta y la acción en curso. El tablero
	// recupera aquí las acciones que el panel clásico tenía en la tarjeta
	// Control (POST /api/panel/workers/{nombre}/{accion}).
	let abierto = $state('');
	let accionando = $state('');
	let aviso = $state(null);

	onMount(cargar);

	async function cargar() {
		cargando = true;
		error = '';
		try {
			datos = await get('/estado');
		} catch (e) {
			error = e.message || 'No se pudo cargar el estado del panel.';
		} finally {
			cargando = false;
		}
	}

	const kpis = $derived(datos?.kpis ?? []);
	const grupos = $derived(datos?.workers?.grupos ?? []);
	const avisos = $derived(datos?.avisos ?? []);
	const serieCorriendo = $derived(datos?.series?.corriendo ?? []);
	const serieError = $derived(datos?.series?.error ?? []);
	const pendientes = $derived(avisos.filter((a) => !a.ok).length);
	const totalWorkers = $derived(
		grupos.reduce((n, g) => n + (g.items?.length || 0), 0)
	);
	const ultimaCorrida = $derived(
		serieCorriendo.length ? serieCorriendo[serieCorriendo.length - 1] : 0
	);
	const ultimoError = $derived(
		serieError.length ? serieError[serieError.length - 1] : 0
	);

	async function accionWorker(w, accion) {
		if (accionando) return;
		if (accion === 'disable' && !confirm(`¿Deshabilitar ${w.nombre}?`)) return;
		accionando = w.nombre;
		aviso = null;
		try {
			const r = await post(`/workers/${encodeURIComponent(w.nombre)}/${accion}`);
			aviso = { tipo: 'ok', mensaje: r?.mensaje || `Worker ${w.nombre} actualizado.` };
			abierto = '';
			await cargar();
		} catch (e) {
			aviso = { tipo: 'err', mensaje: e.message || `No se pudo actualizar ${w.nombre}.` };
		} finally {
			accionando = '';
		}
	}

	function estadoTono(estado) {
		switch (estado) {
			case 'RUNNING':
				return 'ok';
			case 'STARTING':
			case 'NO_HABILITADO':
			case 'UNKNOWN':
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

	function hora(iso) {
		if (!iso) return '';
		try {
			return new Intl.DateTimeFormat('es-MX', {
				hour: '2-digit',
				minute: '2-digit'
			}).format(new Date(iso));
		} catch {
			return '';
		}
	}
</script>

{#if cargando}
	<div class="dash dash--estado" aria-busy="true">
		<div class="grid dashx__kpis dash__kpis" aria-hidden="true">
			{#each [1, 2, 3, 4] as n (n)}
				<div class="kpi"><Skeleton alto="48px" /></div>
			{/each}
		</div>
		<div class="wgrid" aria-hidden="true">
			<Card clase="wcard dashx__card">
				<Skeleton alto="18px" ancho="40%" />
				<div class="skeleton-gap"></div>
				<Skeleton alto="14px" lineas={6} />
			</Card>
			<Card clase="wcard dashx__card">
				<Skeleton alto="18px" ancho="35%" />
				<div class="skeleton-gap"></div>
				<Skeleton alto="72px" />
			</Card>
			<Card clase="wcard dashx__card">
				<Skeleton alto="18px" ancho="35%" />
				<div class="skeleton-gap"></div>
				<Skeleton alto="14px" lineas={3} />
			</Card>
		</div>
	</div>
{:else if !datos}
	<div class="dash dash--estado">
		{#if error}
			<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
			<Button variant="quiet" icono="refresh" onclick={cargar}>Reintentar</Button>
		{:else}
			<Empty
				icono="dashboard"
				titulo="Sin datos"
				texto="El panel no devolvió información de los workers."
			/>
		{/if}
	</div>
{:else}
	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	{/if}
	{#if aviso}
		<Toast tipo={aviso.tipo} mensaje={aviso.mensaje} oncerrar={() => (aviso = null)} />
	{/if}
	<div class="dash dash--estado">

		<div
			class="grid dashx__kpis dash__kpis"
			role="group"
			aria-label="Indicadores de los workers"
		>
			{#each kpis as k (k.label)}
				<Kpi label={k.label} value={k.value} hint={k.hint} tone={k.tone} icono={k.icon} />
			{/each}
		</div>

		<div class="wgrid">
			<Card
				clase="wcard dashx__card"
				titulo="Workers"
				sub={`${plural(totalWorkers, 'programa', 'programas')}${datos.actualizado ? ` · ${hora(datos.actualizado)}` : ''}`}
			>
				{#if grupos.length === 0}
					<Empty icono="users" titulo="Sin workers" texto="No hay programas en el contenedor." />
				{:else}
					<div class="dashx__scroll">
						{#each grupos as g (g.nombre)}
							<div class="wgroup">{g.nombre}</div>
							<ul class="list" aria-label={`Workers de ${g.nombre}`}>
								{#each g.items as w (w.nombre)}
									<li class="list__row" class:is-open={abierto === w.nombre}>
										<div class="list__main">
											<a href={`/logs/${w.nombre}`}>{w.nombre}</a>
											<div class="muted dashx__sub">
												{w.latido ? `Último latido ${w.latido}` : 'Sin latidos registrados'}
											</div>
										</div>
										<div class="dashx__rowmeta">
											<Badge tone={estadoTono(w.estado)}>{estadoTexto(w.estado)}</Badge>
											{#if !w.protegido}
												<button
													class="btn btn--quiet btn--sm wacc__toggle"
													type="button"
													aria-expanded={abierto === w.nombre}
													aria-label={`Acciones de ${w.nombre}`}
													title={`Acciones de ${w.nombre}`}
													disabled={accionando === w.nombre}
													onclick={() => (abierto = abierto === w.nombre ? '' : w.nombre)}
												>
													<Icon name="more" size={16} />
												</button>
											{:else}
												<Badge tone="info">Este panel</Badge>
											{/if}
										</div>
										{#if abierto === w.nombre}
											<div class="wacc">
												{#if w.habilitado}
													{#if w.corriendo}
														<Button
															variant="quiet"
															tam="sm"
															icono="refresh"
															disabled={!!accionando}
															onclick={() => accionWorker(w, 'restart')}
														>
															Reiniciar
														</Button>
													{:else}
														<Button
															variant="quiet"
															tam="sm"
															icono="play"
															disabled={!!accionando}
															onclick={() => accionWorker(w, 'start')}
														>
															Arrancar
														</Button>
													{/if}
												{/if}
												{#if w.habilitado}
													<Button
														variant="danger-quiet"
														tam="sm"
														icono="pause"
														disabled={!!accionando}
														onclick={() => accionWorker(w, 'disable')}
													>
														Deshabilitar
													</Button>
												{:else}
													<Button
														variant="primary"
														tam="sm"
														icono="play"
														disabled={!!accionando}
														onclick={() => accionWorker(w, 'enable')}
													>
														Activar
													</Button>
												{/if}
												<Button variant="quiet" tam="sm" icono="settings" href="/configuracion">
													Configuración
												</Button>
											</div>
										{/if}
									</li>
								{/each}
							</ul>
						{/each}
					</div>
				{/if}
			</Card>

			<Card clase="wcard dashx__card" titulo="Actividad">
				<div class="dashx__charts">
					<div class="dashx__chart">
						<h3>
							<span>Corriendo</span>
							<span>{ultimaCorrida}</span>
						</h3>
						<ChartLine
							datos={serieCorriendo}
							etiqueta="Workers corriendo en el tiempo"
							tono="ok"
							alto="clamp(36px, 5.5vh, 96px)"
						/>
						<p class="dashx__legend">
							{serieCorriendo.length === 1
								? 'Última lectura'
								: `Últimas ${serieCorriendo.length} lecturas`}
						</p>
					</div>
					<div class="dashx__chart">
						<h3>
							<span>Errores</span>
							<span>{ultimoError}</span>
						</h3>
						<ChartBars
							datos={serieError}
							etiqueta="Workers con error en el tiempo"
							tono={serieError.some((n) => n > 0) ? 'bad' : 'ok'}
							alto="clamp(36px, 5.5vh, 96px)"
						/>
						<p class="dashx__legend">
							{serieError.length === 1
								? 'Última lectura'
								: `Últimas ${serieError.length} lecturas`}
						</p>
					</div>
				</div>
			</Card>

			<Card
				clase="wcard dashx__card"
				titulo="Avisos"
				sub={pendientes ? `${plural(pendientes, 'aviso', 'avisos')} por revisar` : 'Todo en orden'}
			>
				{#snippet acciones()}
					<Badge tone={pendientes ? 'bad' : 'ok'}>{pendientes || 'OK'}</Badge>
				{/snippet}
				{#if avisos.length === 0}
					<Empty icono="bell" titulo="Sin avisos" texto="No hay avisos del ecosistema." />
				{:else}
					<ul class="list dashx__scroll" aria-label="Avisos del ecosistema">
						{#each avisos as a, i (i)}
							<li class="list__row">
								<div class="list__main">
									<strong>{a.titulo}</strong>
									<div class="muted dashx__sub">{a.detalle}</div>
								</div>
								<div class="dashx__rowmeta">
									<Badge tone={a.ok ? 'ok' : a.nivel === 'warn' ? 'warn' : 'bad'}>
										{a.ok ? 'OK' : a.nivel === 'warn' ? 'Atención' : 'Fallo'}
									</Badge>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</Card>
		</div>
	</div>
{/if}
