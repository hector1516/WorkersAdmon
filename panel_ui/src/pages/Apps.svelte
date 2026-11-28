<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { plural } from '../lib/texto.js';
	import Card from '../lib/components/Card.svelte';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Field from '../lib/components/Field.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Toast from '../lib/components/Toast.svelte';

	const TIPOS = [
		{ valor: 'text', texto: 'TEXTO' },
		{ valor: 'secret', texto: 'SECRETO' },
		{ valor: 'number', texto: 'NÚMERO' },
		{ valor: 'bool', texto: 'SÍ/NO' },
		{ valor: 'readonly', texto: 'SOLO LECTURA' }
	];

	let cargando = $state(true);
	let error = $state('');
	let aviso = $state(null);
	let migracion = $state(true);
	let apps = $state([]);
	let libres = $state([]);

	// Estado de edición por fila (Id del catálogo).
	let valores = $state({});
	let titulos = $state({});
	let tipos = $state({});
	let descs = $state({});
	let guardando = $state({});

	// Alta y clasificación.
	let nuevo = $state({
		app: '',
		clave: '',
		titulo: '',
		tipo: 'text',
		unidad: '',
		orden: 100,
		valor: '',
		descripcion: ''
	});
	let clasif = $state({});
	let agregando = $state(false);

	onMount(cargar);

	function llave(c) {
		return String(c.id ?? c.clave);
	}

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const r = await get('/apps');
			apps = r.apps || [];
			libres = r.sin_clasificar || [];
			migracion = r.migracion !== false;

			const v = {};
			const t = {};
			const ti = {};
			const d = {};
			for (const app of apps) {
				for (const c of app.claves || []) {
					const k = llave(c);
					v[k] = c.valor ?? '';
					t[k] = c.titulo ?? c.clave;
					ti[k] = c.tipo || 'text';
					d[k] = c.descripcion ?? '';
				}
			}
			valores = v;
			titulos = t;
			tipos = ti;
			descs = d;

			const cl = {};
			for (const s of libres) {
				cl[s.clave] = { app: '', titulo: s.titulo || s.clave, tipo: 'text' };
			}
			clasif = cl;
		} catch (e) {
			error = e.message || 'No se pudo cargar el catálogo de apps.';
		} finally {
			cargando = false;
		}
	}

	async function ejecutar(cuerpo, mensajeOk) {
		error = '';
		try {
			const r = await post('/apps', cuerpo);
			aviso = { tipo: 'ok', mensaje: r?.mensaje || mensajeOk };
			await cargar();
			return true;
		} catch (e) {
			aviso = { tipo: 'err', mensaje: e.message || 'No se pudo completar la operación.' };
			return false;
		}
	}

	async function guardarApp(app) {
		const envio = {};
		for (const c of app.claves || []) {
			if (c.tipo === 'readonly') continue;
			envio[c.clave] = valores[llave(c)] ?? '';
		}
		guardando = { ...guardando, [app.clave]: true };
		await ejecutar({ accion: 'save', app: app.clave, valores: envio },
			`Valores de ${app.clave} guardados.`);
		guardando = { ...guardando, [app.clave]: false };
	}

	async function editarFila(c) {
		const k = llave(c);
		await ejecutar({
			accion: 'edit',
			id: c.id,
			titulo: titulos[k],
			descripcion: descs[k],
			tipo: tipos[k],
			valor: valores[k] ?? ''
		}, `Clave '${c.clave}' guardada.`);
	}

	async function borrarFila(c) {
		if (!confirm(`¿Quitar la clave '${c.clave}' del catálogo? El valor en HUB_Config se conserva.`)) {
			return;
		}
		await ejecutar({ accion: 'del', id: c.id }, `Clave '${c.clave}' fuera del catálogo.`);
	}

	async function agregar() {
		agregando = true;
		const ok = await ejecutar({ accion: 'add', ...nuevo }, 'Clave agregada al catálogo.');
		if (ok) {
			nuevo = {
				app: nuevo.app,
				clave: '',
				titulo: '',
				tipo: 'text',
				unidad: '',
				orden: 100,
				valor: '',
				descripcion: ''
			};
		}
		agregando = false;
	}

	async function clasificar(s) {
		const datos = clasif[s.clave] || {};
		await ejecutar({
			accion: 'clasificar',
			clave: s.clave,
			app: datos.app,
			titulo: datos.titulo,
			tipo: datos.tipo
		}, `'${s.clave}' clasificada.`);
	}
</script>

<div>
	<p class="desc">
		Catálogo de metadatos en <code>HUB_ConfigCatalogo</code> y valores en
		<code>HUB_Config</code>: lo que guardes aquí lo ve al instante la app que
		lo lee. Los secretos en blanco no se modifican.
	</p>

	{#if aviso}
		<Toast tipo={aviso.tipo} mensaje={aviso.mensaje} oncerrar={() => (aviso = null)} />
	{/if}
	{#if error}
		<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	{/if}

	{#if cargando}
		<Card>
			<Skeleton alto="20px" ancho="30%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={5} />
		</Card>
	{:else if !migracion}
		<Card titulo="Falta la migración 0036">
			<Empty
				icono="alert"
				titulo="Sin catálogo"
				texto="La tabla HUB_ConfigCatalogo no existe en esta base. Aplica la migración desde el repo HUB."
			/>
		</Card>
	{:else if !apps.length && !libres.length}
		<Card>
			<Empty icono="apps" titulo="Catálogo vacío" texto="Todavía no hay claves catalogadas." />
		</Card>
	{:else}
		{#each apps as app (app.clave)}
			<Card titulo={app.clave} sub={plural(app.claves.length, 'clave', 'claves')}>
				<div class="table-wrap">
					<table class="table">
						<thead>
							<tr>
								<th>Título</th>
								<th>Clave</th>
								<th>Valor</th>
								<th>Tipo</th>
								<th class="hide-sm">Descripción</th>
								<th></th>
							</tr>
						</thead>
						<tbody>
							{#each app.claves as c (llave(c))}
								{@const k = llave(c)}
								<tr>
									<td>
										<input class="input" maxlength="120" bind:value={titulos[k]} />
									</td>
									<td>
										<code>{c.clave}</code>
										{#if c.unidad}<div class="muted nota--xs-72">{c.unidad}</div>{/if}
									</td>
									<td>
										{#if c.tipo === 'readonly'}
											<div class="ro">{valores[k] || '—'}</div>
										{:else}
											<input
												class="input"
												type={c.tipo === 'secret' ? 'password' : c.tipo === 'number' ? 'number' : 'text'}
												autocomplete={c.tipo === 'secret' ? 'new-password' : 'off'}
												placeholder={c.tipo === 'secret'
													? c.tiene_valor
														? '(guardado)'
														: '(sin valor)'
													: ''}
												bind:value={valores[k]}
											/>
										{/if}
									</td>
									<td>
										<select class="input" bind:value={tipos[k]}>
											{#each TIPOS as t (t.valor)}
												<option value={t.valor}>{t.texto}</option>
											{/each}
										</select>
									</td>
									<td class="hide-sm">
										<input class="input" maxlength="400" bind:value={descs[k]} />
									</td>
									<td class="actions-cell">
										<div class="actions">
											<Button tam="sm" variant="quiet" icono="check" onclick={() => editarFila(c)}>
												Guardar
											</Button>
											<Button tam="sm" variant="danger" icono="trash" onclick={() => borrarFila(c)}>
												Quitar
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<div class="actions">
					<Button
						variant="primary"
						icono="check"
						disabled={guardando[app.clave]}
						onclick={() => guardarApp(app)}
					>
						{guardando[app.clave] ? 'Guardando…' : 'Guardar valores'}
					</Button>
					<span class="muted nota--78">«Quitar» solo quita el catálogo</span>
				</div>
			</Card>
		{/each}

		{#if libres.length}
			<Card titulo="Sin clasificar" sub={`${plural(libres.length, 'clave', 'claves')} fuera del catálogo`}>
				<p class="help">
					Sus valores no se muestran hasta clasificarlas (pueden ser secretas).
				</p>
				<div class="table-wrap">
					<table class="table">
						<thead>
							<tr>
								<th>Clave</th>
								<th>App</th>
								<th>Título</th>
								<th>Tipo</th>
								<th></th>
							</tr>
						</thead>
						<tbody>
							{#each libres as s (s.clave)}
								<tr>
									<td>
										<code>{s.clave}</code>
										<div class="muted nota--xs-72">
											{s.tiene_valor ? 'con valor' : 'vacía'}
										</div>
									</td>
									<td>
										<input class="input" placeholder="HUB / Field / admon" list="apps-list"
											bind:value={clasif[s.clave].app} maxlength="40" />
									</td>
									<td>
										<input class="input" maxlength="120" bind:value={clasif[s.clave].titulo} />
									</td>
									<td>
										<select class="input" bind:value={clasif[s.clave].tipo}>
											{#each TIPOS as t (t.valor)}
												<option value={t.valor}>{t.texto}</option>
											{/each}
										</select>
									</td>
									<td class="actions-cell">
										<div class="actions">
											<Button tam="sm" variant="quiet" icono="plus" onclick={() => clasificar(s)}>
												Clasificar
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<datalist id="apps-list">
					{#each apps as app (app.clave)}
						<option value={app.clave}></option>
					{/each}
				</datalist>
			</Card>
		{/if}

		<Card titulo="Nueva clave" sub="Si la clave ya existe en HUB_Config solo se cataloga: su valor no se toca">
			<div class="grid2">
				<Field label="App" nombre="app" bind:valor={nuevo.app} placeholder="HUB / Field / admon" />
				<Field label="Clave (HUB_Config)" nombre="clave" bind:valor={nuevo.clave} placeholder="mi_clave" />
				<Field label="Título" nombre="titulo" bind:valor={nuevo.titulo} />
				<Field label="Tipo" nombre="tipo" tipo="select" bind:valor={nuevo.tipo} opciones={TIPOS} />
				<Field label="Unidad (opcional)" nombre="unidad" bind:valor={nuevo.unidad} placeholder="seg / min" />
				<Field label="Orden" nombre="orden" tipo="number" bind:valor={nuevo.orden} />
				<Field label="Valor inicial (opcional)" nombre="valor" bind:valor={nuevo.valor} />
				<Field label="Descripción" nombre="descripcion" bind:valor={nuevo.descripcion} />
			</div>
			<div class="actions">
				<Button variant="primary" icono="plus" disabled={agregando} onclick={agregar}>
					{agregando ? 'Agregando…' : 'Agregar al catálogo'}
				</Button>
			</div>
		</Card>
	{/if}
</div>
