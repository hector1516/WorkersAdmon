<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { plural } from '../lib/texto.js';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Card from '../lib/components/Card.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Icon from '../lib/components/Icon.svelte';
	import Kpi from '../lib/components/Kpi.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// La fecha por defecto es AYER: hoy todavía no termina y casi siempre daría
	// "sin salida registrada". Se arma con la fecha LOCAL (nunca `toISOString`,
	// que devuelve UTC y de noche adelanta el día).
	function ayerLocal() {
		const d = new Date();
		d.setDate(d.getDate() - 1);
		return (
			`${d.getFullYear()}-` +
			`${String(d.getMonth() + 1).padStart(2, '0')}-` +
			`${String(d.getDate()).padStart(2, '0')}`
		);
	}

	let dia = $state(ayerLocal());
	let datos = $state(null);
	let cargando = $state(true);
	let calculando = $state(false);
	let error = $state('');
	let aviso = $state('');

	const diaLargo = $derived(
		datos?.dia
			? new Date(`${datos.dia}T00:00:00`).toLocaleDateString('es-MX', {
					weekday: 'long',
					day: '2-digit',
					month: '2-digit',
					year: 'numeric'
				})
			: ''
	);

	async function cargar() {
		cargando = true;
		error = '';
		aviso = '';
		try {
			datos = await get(`/asistencia?dia=${encodeURIComponent(dia)}`);
		} catch (e) {
			error = e.message;
			datos = null;
		} finally {
			cargando = false;
		}
	}

	async function calcular() {
		calculando = true;
		error = '';
		aviso = '';
		try {
			const r = await post('/asistencia', { accion: 'calcular', dia });
			aviso = `Se guardaron ${r.guardados} registros del ${r.dia}.`;
			await cargar();
		} catch (e) {
			error = e.message;
		} finally {
			calculando = false;
		}
	}

	onMount(cargar);
</script>

<div class="stack">
	<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	<Toast tipo="ok" mensaje={aviso} oncerrar={() => (aviso = '')} />

	<Card titulo="Día" sub="El escáner ve la red cada 3 minutos: la hora es una ventana, no un minuto.">
		<div class="acciones-fila">
			<div class="field campo-suelto">
				<label for="as-dia">Fecha</label>
				<input
					id="as-dia"
					class="input"
					type="date"
					bind:value={dia}
					onchange={cargar}
					autocomplete="off"
				/>
			</div>
			<Button variant="primary" icono="refresh" onclick={calcular} disabled={calculando}>
				{calculando ? 'Calculando…' : 'Calcular y guardar'}
			</Button>
		</div>
	</Card>

	{#if cargando}
		<div class="grid" aria-hidden="true">
			{#each [1, 2, 3, 4] as n (n)}
				<div class="kpi"><Skeleton alto="52px" /></div>
			{/each}
		</div>
		<Card>
			<Skeleton alto="18px" ancho="40%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={5} />
		</Card>
	{:else if datos}
		<div class="grid">
			{#each datos.kpis as k (k.label)}
				<Kpi label={k.label} value={k.value} hint={k.hint} tone={k.tone} icono={k.icon} />
			{/each}
		</div>

		<Card titulo={diaLargo || 'Asistencias'} sub={plural(datos.filas.length, 'persona', 'personas')}>
			{#if datos.filas.length === 0}
				<Empty
					icono="clock"
					titulo="Sin asistencias calculadas"
					texto="Pulsa «Calcular y guardar» para procesar ese día a partir de la evidencia de red."
				/>
			{:else}
				<div class="list">
					{#each datos.filas as f, i (i)}
						<div class="list__row">
							<div class="list__main">
								<strong>{f.persona}</strong>
								<div class="hint">
									Entrada {f.entrada} · Salida {f.salida}
								</div>
								<div class="hint">{f.detalle}</div>
							</div>
							<div class="list__meta">
								<Badge tone={f.tone}>{f.estado}</Badge>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</Card>

		<Card>
			<div class="hint">
				<Icon name="alert" size={16} /> «Sin datos» no es una falta: es que el escáner no
				cubrió ese día. «Indeterminado» significa que la ventana cruza la tolerancia y no se
				puede afirmar ni una cosa ni la otra.
			</div>
		</Card>
	{/if}
</div>

<style>
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
</style>
