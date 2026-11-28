<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { plural } from '../lib/texto.js';
	import Icon from '../lib/components/Icon.svelte';
	import Card from '../lib/components/Card.svelte';
	import Field from '../lib/components/Field.svelte';
	import Button from '../lib/components/Button.svelte';
	import Badge from '../lib/components/Badge.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';
	import Empty from '../lib/components/Empty.svelte';

	// Cuatro canales de salida. Cada uno tiene su permiso del HUB y su forma;
	// el API devuelve las claves de la vista y los secretos como
	// {valor: "", tiene_valor: true}.
	const VISTAS = [
		{ id: 'telegram', label: 'Telegram', icono: 'chat' },
		{ id: 'push', label: 'Push', icono: 'bell' },
		{ id: 'correo', label: 'Correo', icono: 'mail' },
		{ id: 'ia', label: 'IA', icono: 'apps' }
	];

	let vista = $state('telegram');
	let cargando = $state(true);
	let guardando = $state(false);
	let datos = $state(null);
	let valores = $state({});
	let mensaje = $state({ tipo: '', texto: '' });

	function avisar(tipo, texto) {
		mensaje = { tipo, texto };
	}

	function valoresDe(bloque) {
		if (!bloque) return {};
		if (bloque.bloque === 'telegram') {
			return { telegram_bot_token: '' };
		}
		if (bloque.bloque === 'push') {
			return {
				VapidPublicKey: bloque.vapid_publica || '',
				VapidPrivateKey: '',
				VapidEmail: bloque.email || ''
			};
		}
		if (bloque.bloque === 'correo') {
			return {
				smtp_server: bloque.smtp_server || '',
				port: String(bloque.port ?? 465),
				username: bloque.username || '',
				password: '',
				use_ssl: !!bloque.use_ssl,
				use_tls: !!bloque.use_tls,
				require_auth: !!bloque.require_auth
			};
		}
		return {
			provider: bloque.provider || 'google_gemini',
			api_key: '',
			model: bloque.model || ''
		};
	}

	async function cargar(bloque = vista) {
		cargando = true;
		try {
			const datosBloque = await get(`/notificaciones?bloque=${bloque}`);
			datos = datosBloque;
			valores = valoresDe(datosBloque);
		} catch (err) {
			datos = null;
			avisar('err', err?.message || 'No se pudo leer la configuración');
		} finally {
			cargando = false;
		}
	}

	function seleccionar(id) {
		if (id === vista) return;
		vista = id;
		mensaje = { tipo: '', texto: '' };
		cargar(id);
	}

	async function guardar(e) {
		e.preventDefault();
		if (guardando) return;
		guardando = true;
		try {
			const res = await post(`/notificaciones/${vista}`, {
				accion: 'save',
				valores
			});
			avisar(res?.ok === false ? 'err' : 'ok', res?.mensaje || 'Guardado');
			await cargar();
		} catch (err) {
			avisar('err', err?.message || 'No se pudo guardar');
		} finally {
			guardando = false;
		}
	}

	onMount(() => {
		cargar();
	});
</script>

<div class="stack">
	<div class="segmented segmented--wrap" role="tablist" aria-label="Canal de notificación">
		{#each VISTAS as v (v.id)}
			<button
				type="button"
				role="tab"
				aria-selected={vista === v.id}
				class:is-active={vista === v.id}
				aria-current={vista === v.id ? 'page' : undefined}
				onclick={() => seleccionar(v.id)}
			>
				<Icon name={v.icono} size={16} />
				{v.label}
			</button>
		{/each}
	</div>

	{#if mensaje.texto}
		<Toast
			tipo={mensaje.tipo}
			mensaje={mensaje.texto}
			oncerrar={() => (mensaje = { tipo: '', texto: '' })}
		/>
	{/if}

	{#if cargando && !datos}
		<Card titulo="Cargando…">
			<Skeleton alto="18px" ancho="42%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={4} />
		</Card>
	{:else if !datos}
		<Card>
			<Empty
				icono="alert"
				titulo="Sin datos"
				texto="No se pudo leer la configuración de este canal."
			/>
		</Card>
	{:else if datos.puede === false}
		<Card>
			<Empty
				icono="lock"
				titulo="Sin permiso"
				texto={`Requiere el permiso ${datos.permiso || 'del canal'} en el HUB. Pídeselo al administrador.`}
			/>
		</Card>
	{:else}
		<form onsubmit={guardar}>
			{#if vista === 'telegram'}
				<Card
					titulo="Conexión · Bot de Telegram"
					sub="El token se guarda en HUB_Config y lo usan todas las alertas."
				>
					<div class="form-grid">
						<Field
							id="tg-token"
							label="Token del bot (@BotFather)"
							tipo="password"
							nombre="telegram_bot_token"
							autocomplete="new-password"
							placeholder={datos.token?.tiene_valor ? 'Guardado — vacío = no cambiar' : 'Pega el token'}
							bind:valor={valores.telegram_bot_token}
						/>
					</div>
					<div class="kpis-row">
						<div class="ro">Vinculados: <b>{datos.resumen?.vinculados ?? 0}</b></div>
						<div class="ro">Eventos: <b>{datos.resumen?.eventos ?? 0}</b></div>
						<div class="ro">Destinatarios: <b>{datos.resumen?.destinatarios ?? 0}</b></div>
						<div class="ro">En cola: <b>{datos.resumen?.pendientes ?? 0}</b></div>
					</div>
					<div class="form-foot">
						<span class="form-foot__estado">
							<Badge tone={datos.token?.tiene_valor ? 'ok' : 'warn'}>
								{datos.token?.tiene_valor ? 'Configurado' : 'Falta el token'}
							</Badge>
						</span>
						<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
							{guardando ? 'Guardando…' : 'Guardar'}
						</Button>
					</div>
				</Card>
			{:else if vista === 'push'}
				<Card
					titulo="Push web (VAPID)"
					sub="Claves que firman los avisos al navegador. La privada nunca sale del servidor."
				>
					<div class="form-grid">
						<Field
							id="push-publica"
							label="Clave pública VAPID"
							nombre="VapidPublicKey"
							bind:valor={valores.VapidPublicKey}
							hint="Se entrega al navegador para suscribirse (no es secreta)."
						/>
						<Field
							id="push-privada"
							label="Clave privada VAPID"
							tipo="password"
							nombre="VapidPrivateKey"
							autocomplete="new-password"
							placeholder={datos.vapid_privada?.tiene_valor ? 'Guardada — vacío = no cambiar' : 'Pega la clave privada'}
							bind:valor={valores.VapidPrivateKey}
						/>
						<Field
							id="push-email"
							label="Correo de contacto"
							tipo="email"
							nombre="VapidEmail"
							bind:valor={valores.VapidEmail}
							hint="Claim mailto: de VAPID."
						/>
					</div>
					<div class="ro">
						{plural(datos.suscriptores ?? 0, 'navegador suscrito', 'navegadores suscritos')}
						{datos.actualizado ? ` · actualizado ${datos.actualizado}` : ''}
					</div>
					<div class="form-foot">
						<span class="form-foot__estado">
							<Badge tone="info">{datos.suscriptores ?? 0} suscriptor(es)</Badge>
						</span>
						<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
							{guardando ? 'Guardando…' : 'Guardar'}
						</Button>
					</div>
				</Card>
			{:else if vista === 'correo'}
				<Card
					titulo="Correo SMTP"
					sub="La contraseña en blanco deja la guardada; nunca se vuelve a pintar."
				>
					<div class="form-grid">
						<Field
							id="smtp-server"
							label="Servidor SMTP"
							nombre="smtp_server"
							bind:valor={valores.smtp_server}
						/>
						<Field
							id="smtp-port"
							label="Puerto"
							tipo="number"
							nombre="port"
							bind:valor={valores.port}
						/>
						<Field
							id="smtp-user"
							label="Usuario"
							nombre="username"
							autocomplete="username"
							bind:valor={valores.username}
						/>
						<Field
							id="smtp-pass"
							label="Contraseña"
							tipo="password"
							nombre="password"
							autocomplete="new-password"
							placeholder={datos.password?.tiene_valor ? 'Guardada — vacío = no cambiar' : ''}
							bind:valor={valores.password}
						/>
					</div>
					<label class="chk">
						<input type="checkbox" bind:checked={valores.use_ssl} />
						SSL <span class="muted">(el HUB usa SSL en el puerto 465)</span>
					</label>
					<label class="chk">
						<input type="checkbox" bind:checked={valores.use_tls} />
						TLS
					</label>
					<label class="chk">
						<input type="checkbox" bind:checked={valores.require_auth} />
						Requiere autenticación
					</label>
					<div class="form-foot">
						<span class="form-foot__estado">
							<Badge tone={datos.smtp_server ? 'ok' : 'warn'}>
								{datos.smtp_server ? 'Configurado' : 'Falta el servidor'}
							</Badge>
						</span>
						<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
							{guardando ? 'Guardando…' : 'Guardar'}
						</Button>
					</div>
				</Card>
			{:else}
				<Card
					titulo="IA (Gemini)"
					sub="La misma clave que usan Cotizaciones, Jarvis y Órdenes."
				>
					<div class="form-grid">
						<Field
							id="ia-provider"
							label="Proveedor"
							nombre="provider"
							bind:valor={valores.provider}
							hint="El HUB usa google_gemini."
						/>
						<Field
							id="ia-key"
							label="API key"
							tipo="password"
							nombre="api_key"
							autocomplete="new-password"
							placeholder={datos.api_key?.tiene_valor ? 'Guardada — vacío = no cambiar' : 'Pega la API key'}
							bind:valor={valores.api_key}
						/>
						<Field
							id="ia-model"
							label="Modelo"
							nombre="model"
							bind:valor={valores.model}
							hint="Debe existir en la cuenta; si fue retirado, la IA del HUB falla."
						/>
					</div>
					<div class="form-foot">
						<span class="form-foot__estado">
							<Badge tone="info">{datos.model || 'sin modelo'}</Badge>
						</span>
						<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
							{guardando ? 'Guardando…' : 'Guardar'}
						</Button>
					</div>
				</Card>
			{/if}
		</form>
	{/if}
</div>
