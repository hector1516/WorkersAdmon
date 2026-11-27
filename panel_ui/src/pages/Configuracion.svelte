<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import Card from '../lib/components/Card.svelte';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Field from '../lib/components/Field.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Toast from '../lib/components/Toast.svelte';

	// Canales del ecosistema. El backend los expone en /api/panel/canales; aquí
	// solo se consumen, con el nombre y la nota que se muestran siempre.
	const CANALES = [
		{ clave: 'telegram', nombre: 'Telegram', nota: 'Se retira; lo reemplaza WhatsApp' },
		{ clave: 'whatsapp', nombre: 'WhatsApp', nota: 'Avisos por OpenWA' },
		{ clave: 'push', nombre: 'PWA', nota: 'Avisos push en las apps' }
	];

	let cargando = $state(true);
	let error = $state('');
	let aviso = $state(null);
	let workers = $state([]);
	let valores = $state({});
	let guardando = $state({});

	let canales = $state(CANALES.map((c) => ({ ...c, activo: false })));
	let errorCanales = $state('');
	let guardandoCanal = $state({});

	onMount(() => {
		cargar();
		cargarCanales();
	});

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const datos = await get('/config');
			workers = datos.workers || [];
			const nuevos = {};
			for (const w of workers) {
				for (const g of w.grupos || []) {
					for (const c of g.campos || []) {
						if (c.tipo !== 'readonly') nuevos[llave(w.nombre, c.clave)] = c.valor ?? '';
					}
				}
			}
			valores = nuevos;
		} catch (e) {
			error = e.message || 'No se pudo cargar la configuración.';
		} finally {
			cargando = false;
		}
	}

	function llave(worker, campo) {
		return `${worker}:${campo}`;
	}

	function tipoCampo(c) {
		if (c.tipo === 'select' || c.tipo === 'bool') return 'select';
		if (c.tipo === 'number') return 'number';
		if (c.tipo === 'secret') return 'password';
		return 'text';
	}

	function opcionesCampo(c) {
		if (c.opciones && c.opciones.length) return c.opciones;
		if (c.tipo === 'bool') {
			return [
				{ valor: '1', texto: 'Sí' },
				{ valor: '0', texto: 'No' }
			];
		}
		return [];
	}

	function pista(c) {
		if (c.tipo !== 'secret') return c.ayuda || '';
		return (c.tiene_valor ? 'Hay un valor guardado; en blanco no cambia.' : 'Sin definir.') +
			(c.ayuda ? ` ${c.ayuda}` : '');
	}

	async function guardar(nombre) {
		const worker = workers.find((w) => w.nombre === nombre);
		if (!worker) return;
		const envio = {};
		for (const g of worker.grupos || []) {
			for (const c of g.campos || []) {
				if (c.tipo === 'readonly') continue;
				envio[c.clave] = valores[llave(nombre, c.clave)] ?? '';
			}
		}
		guardando = { ...guardando, [nombre]: true };
		try {
			const r = await post('/config', { worker: nombre, valores: envio });
			aviso = { tipo: 'ok', mensaje: r?.mensaje || `Configuración de ${nombre} guardada.` };
			await cargar();
		} catch (e) {
			aviso = { tipo: 'err', mensaje: e.message || `No se pudo guardar ${nombre}.` };
		} finally {
			guardando = { ...guardando, [nombre]: false };
		}
	}

	function activoDe(valor) {
		if (typeof valor === 'string') {
			return ['1', 'true', 'si', 'sí', 'on'].includes(valor.toLowerCase());
		}
		return !!valor;
	}

	function normalizarCanales(r) {
		const lista = Array.isArray(r?.canales) ? r.canales : Array.isArray(r) ? r : null;
		return CANALES.map((def) => {
			let fuente = null;
			if (lista) {
				fuente = lista.find((x) =>
					String(x?.clave ?? x?.id ?? x?.nombre ?? '')
						.toLowerCase()
						.includes(def.clave)
				);
			} else if (r && typeof r === 'object') {
				fuente = r[def.clave];
			}
			let activo = false;
			let nota = def.nota;
			if (fuente && typeof fuente === 'object') {
				activo = fuente.activo ?? fuente.encendido ?? fuente.on ?? fuente.valor ?? false;
				if (fuente.nota || fuente.descripcion) nota = fuente.nota || fuente.descripcion;
			} else if (fuente !== undefined && fuente !== null) {
				activo = fuente;
			}
			return { ...def, nota, activo: activoDe(activo) };
		});
	}

	async function cargarCanales() {
		errorCanales = '';
		try {
			canales = normalizarCanales(await get('/canales'));
		} catch (e) {
			errorCanales = e.message || 'No se pudieron leer los canales.';
		}
	}

	async function alternarCanal(canal) {
		guardandoCanal = { ...guardandoCanal, [canal.clave]: true };
		errorCanales = '';
		const nuevo = !canal.activo;
		try {
			await post('/canales', { canal: canal.clave, activo: nuevo });
			canales = canales.map((c) =>
				c.clave === canal.clave ? { ...c, activo: nuevo } : c
			);
			aviso = { tipo: 'ok', mensaje: `Canal ${canal.nombre} ${nuevo ? 'encendido' : 'apagado'}.` };
		} catch (e) {
			errorCanales = e.message || `No se pudo cambiar ${canal.nombre}.`;
		} finally {
			guardandoCanal = { ...guardandoCanal, [canal.clave]: false };
		}
	}
</script>

<div>
	<h2 class="page-title">⚙️ Configuración</h2>
	<p class="desc">
		Misma configuración que el HUB, por worker. <strong>ENTORNO</strong> se
		persiste en el volumen y <strong>HUB_CONFIG</strong> aplica al instante.
		Los secretos en blanco no se modifican.
	</p>

	{#if aviso}
		<Toast tipo={aviso.tipo} mensaje={aviso.mensaje} oncerrar={() => (aviso = null)} />
	{/if}
	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	{/if}

	<Card titulo="Canales" sub="Interruptores de los canales de aviso del ecosistema">
		{#if errorCanales}
			<p class="help"><Badge tone="bad">Error</Badge> {errorCanales}</p>
		{/if}
		<div class="grid2">
			{#each canales as canal (canal.clave)}
				<div class="field">
					<label>
						<input
							type="checkbox"
							checked={canal.activo}
							disabled={guardandoCanal[canal.clave]}
							onchange={() => alternarCanal(canal)}
						/>
						{canal.nombre}
					</label>
					<p class="help">{canal.nota}</p>
				</div>
			{/each}
		</div>
	</Card>

	{#if cargando}
		<Card>
			<Skeleton alto="20px" ancho="35%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={4} />
		</Card>
	{:else if !workers.length}
		<Card>
			<Empty icono="settings" titulo="Sin configuración" texto="No hay workers con catálogo de configuración." />
		</Card>
	{:else}
		{#each workers as w (w.nombre)}
			<Card titulo={w.nombre} sub={w.descripcion}>
				{#snippet acciones()}
					<Badge tone={w.estado === 'RUNNING' ? 'ok' : w.estado === 'NO_HABILITADO' ? '' : 'warn'}>
						{w.estado}
					</Badge>
				{/snippet}

				{#each w.grupos as grupo (grupo.titulo)}
					<h3 class="wgroup">{grupo.titulo}</h3>
					{#if grupo.nota}<p class="help">{grupo.nota}</p>{/if}
					<div class="grid2">
						{#each grupo.campos as c (c.clave)}
							{#if c.tipo === 'readonly'}
								<div class="field">
									<p class="muted nota-sm"><strong>{c.label}</strong></p>
									<div class="ro">{c.valor || '—'}</div>
									{#if c.ayuda}<p class="help">{c.ayuda}</p>{/if}
								</div>
							{:else}
								<Field
									label={c.label}
									nombre={llave(w.nombre, c.clave)}
									tipo={tipoCampo(c)}
									bind:valor={valores[llave(w.nombre, c.clave)]}
									opciones={opcionesCampo(c)}
									hint={pista(c)}
									autocomplete={c.tipo === 'secret' ? 'new-password' : 'off'}
									placeholder={c.tipo === 'secret'
										? c.tiene_valor
											? '(guardado)'
											: '(sin definir)'
										: ''}
								/>
							{/if}
						{/each}
					</div>
				{/each}

				<div class="actions">
					<Button
						variant="primary"
						icono="check"
						disabled={guardando[w.nombre]}
						onclick={() => guardar(w.nombre)}
					>
						{guardando[w.nombre] ? 'Guardando…' : 'Guardar'}
					</Button>
				</div>
			</Card>
		{/each}
	{/if}
</div>
