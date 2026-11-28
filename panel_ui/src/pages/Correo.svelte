<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { plural } from '../lib/texto.js';
	import Badge from '../lib/components/Badge.svelte';
	import Button from '../lib/components/Button.svelte';
	import Card from '../lib/components/Card.svelte';
	import Empty from '../lib/components/Empty.svelte';
	import Field from '../lib/components/Field.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';

	const TONOS_BUZON = { ACTIVA: 'ok', PENDIENTE: 'warn', ERROR: 'bad', PAUSADA: '' };

	let cuentas = $state([]);
	let config = $state({
		smtp_server: '',
		port: 465,
		username: '',
		password: '',
		use_ssl: true,
		use_tls: false,
		require_auth: true,
		tiene_password: false
	});
	let buzones = $state([]);
	let cargando = $state(true);
	let guardando = $state(false);
	let probando = $state(false);
	let destino = $state('');
	let error = $state('');
	let aviso = $state('');

	async function cargar() {
		cargando = true;
		error = '';
		try {
			const datos = await get('/correo');
			cuentas = datos.cuentas || [];
			buzones = datos.buzones || [];
			config = {
				...datos.config,
				// La contraseña nunca llega: el campo siempre arranca en blanco y
				// en blanco significa "no cambiar".
				password: '',
				tiene_password: Boolean(datos.config?.password?.tiene_valor)
			};
		} catch (e) {
			error = e.message;
		} finally {
			cargando = false;
		}
	}

	async function guardar() {
		guardando = true;
		error = '';
		aviso = '';
		try {
			const r = await post('/correo', { accion: 'guardar', ...config });
			aviso = r.mensaje || 'Configuración guardada.';
			await cargar();
		} catch (e) {
			error = e.message;
		} finally {
			guardando = false;
		}
	}

	async function probar() {
		probando = true;
		error = '';
		aviso = '';
		try {
			const r = await post('/correo', { accion: 'probar', destino });
			if (r.ok) aviso = r.mensaje || 'Prueba enviada.';
			else error = r.mensaje || 'La prueba falló.';
		} catch (e) {
			error = e.message;
		} finally {
			probando = false;
		}
	}

	onMount(cargar);
</script>

<div class="stack">
	<Toast tipo="err" mensaje={error} oncerrar={() => (error = '')} />
	<Toast tipo="ok" mensaje={aviso} oncerrar={() => (aviso = '')} />

	{#if cargando}
		<Card>
			<Skeleton alto="18px" ancho="35%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={3} />
		</Card>
	{:else}
		{#each cuentas as c (c.nombre)}
			<Card titulo={c.nombre}>
				<div class="row-meta">
					<Badge tone={c.tone}>{c.estado}</Badge>
					<span class="hint">{c.detalle}</span>
				</div>
			</Card>
		{/each}

		<Card
			titulo="Configuración SMTP"
			sub="La contraseña se guarda cifrada. En blanco = no cambiar la que ya está."
		>
			<div class="form-grid">
				<Field nombre="smtp_server" label="Servidor SMTP" bind:valor={config.smtp_server} placeholder="smtp.gmail.com" />
				<Field nombre="port" label="Puerto" tipo="number" bind:valor={config.port} hint="465 con SSL; 587 con STARTTLS." />
				<Field nombre="username" label="Usuario" bind:valor={config.username} />
				<Field
					nombre="password"
					label="Contraseña"
					tipo="password"
					bind:valor={config.password}
					placeholder={config.tiene_password ? '(sin cambios)' : 'Sin contraseña guardada'}
					autocomplete="new-password"
				/>
			</div>

			<div class="acciones-fila">
				<label class="chk">
					<input type="checkbox" bind:checked={config.use_ssl} />
					SSL
				</label>
				<label class="chk">
					<input type="checkbox" bind:checked={config.use_tls} />
					TLS
				</label>
				<label class="chk">
					<input type="checkbox" bind:checked={config.require_auth} />
					Requiere autenticación
				</label>
			</div>

			<div class="acciones-fila">
				<Button variant="primary" icono="check" onclick={guardar} disabled={guardando}>
					{guardando ? 'Guardando…' : 'Guardar'}
				</Button>
				<Field nombre="destino" label="Probar con" tipo="email" bind:valor={destino} placeholder="para@correo.com" />
				<Button variant="quiet" icono="send" onclick={probar} disabled={probando || !destino}>
					{probando ? 'Enviando…' : 'Enviar prueba'}
				</Button>
			</div>
		</Card>

		<Card titulo="Buzones de Mailbox" sub="Solo los ve quien tenga la cuenta asignada; la credencial no se muestra.">
			{#if buzones.length === 0}
				<Empty
					icono="mail"
					titulo="Sin buzones"
					texto="Las cuentas de Mailbox se dan de alta desde esta misma tarjeta."
				/>
			{:else}
				<div class="list">
					{#each buzones as b (b.id)}
						<div class="list__row">
							<div class="list__main">
								<strong>{b.nombre}</strong>
								<div class="hint">{b.email}</div>
								<div class="hint">{b.detalle}</div>
								{#if b.error}
									<div class="hint">Último error: {b.error}</div>
								{/if}
							</div>
							<div class="list__meta row-meta">
								<Badge tone={TONOS_BUZON[b.estado] ?? ''}>{b.estado}</Badge>
								<Badge>{plural(b.usuarios, 'usuario', 'usuarios')}</Badge>
								<Badge>{b.sin_leer} sin leer</Badge>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</Card>
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
	.form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 0 16px;
	}
</style>
