<script>
	import { onMount, tick } from 'svelte';
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

	// Las cinco sub-pestañas del módulo: cuatro de configuración y la bandeja.
	// Conexión, avisos y grupos son formularios; historial y conversaciones solo
	// leen.
	const VISTAS = [
		{ id: 'conexion', label: 'Conexión', icono: 'whatsapp' },
		{ id: 'avisos', label: 'Avisos', icono: 'bell' },
		{ id: 'grupos', label: 'Grupos', icono: 'users' },
		{ id: 'historial', label: 'Historial', icono: 'clock' },
		{ id: 'conversaciones', label: 'Conversaciones', icono: 'chat' }
	];

	const ESTADOS = {
		BOT: { texto: 'Bot', tone: 'ok' },
		HUMANO: { texto: 'Humano', tone: 'warn' },
		CERRADA: { texto: 'Cerrada', tone: 'info' }
	};

	let vista = $state('conexion');
	let cargando = $state(true);
	let guardando = $state(false);
	let datos = $state(null);
	let conexion = $state({ openwa_base_url: '', openwa_session_id: '', openwa_api_key: '' });
	let avisos = $state([]);
	let grupos = $state([]);
	let gruposError = $state('');
	let historial = $state([]);
	let mensaje = $state({ tipo: '', texto: '' });

	// Bandeja: lista, chat abierto e hilo.
	let conversaciones = $state([]);
	let cargandoLista = $state(false);
	let chat = $state('');
	let hilo = $state(null);
	let cargandoHilo = $state(false);
	let texto = $state('');
	let enviando = $state(false);
	let cajaMsgs = $state(null);

	function avisar(tipo, textoAviso) {
		mensaje = { tipo, texto: textoAviso };
	}

	const nombreEstado = (estado) => ESTADOS[estado] || { texto: estado || '—', tone: 'info' };

	function hora(iso) {
		if (!iso) return '';
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return '';
		const hoy = new Date();
		const mismoDia = d.toDateString() === hoy.toDateString();
		return mismoDia
			? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
			: d.toLocaleDateString('es-MX', { day: '2-digit', month: '2-digit' });
	}

	function fechaLarga(iso) {
		if (!iso) return '—';
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return String(iso);
		return d.toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' });
	}

	async function cargar(bloque = vista) {
		cargando = true;
		try {
			if (bloque === 'conversaciones') {
				await cargarConversaciones();
				return;
			}
			const res = await get(`/whatsapp?bloque=${bloque}`);
			datos = res;
			if (bloque === 'conexion') {
				conexion = {
					openwa_base_url: res.base_url || '',
					openwa_session_id: res.session_id || '',
					openwa_api_key: ''
				};
			} else if (bloque === 'avisos') {
				avisos = (res.items || []).map((ev) => ({ ...ev }));
			} else if (bloque === 'grupos') {
				grupos = res.items || [];
				gruposError = res.error || '';
			} else if (bloque === 'historial') {
				historial = res.items || [];
			}
		} catch (err) {
			datos = null;
			avisar('err', err?.message || 'No se pudo leer el módulo');
		} finally {
			cargando = false;
		}
	}

	function seleccionar(id) {
		if (id === vista) return;
		vista = id;
		chat = '';
		hilo = null;
		mensaje = { tipo: '', texto: '' };
		if (id === 'conversaciones') conversaciones = [];
		cargar(id);
	}

	async function guardar(e) {
		e.preventDefault();
		if (guardando) return;
		guardando = true;
		try {
			const res = await post(`/whatsapp/${vista}`, { accion: 'save', valores: conexion });
			avisar(res?.ok === false ? 'err' : 'ok', res?.mensaje || 'Guardado');
			await cargar();
		} catch (err) {
			avisar('err', err?.message || 'No se pudo guardar');
		} finally {
			guardando = false;
		}
	}

	async function guardarAviso(ev) {
		if (guardando) return;
		guardando = true;
		try {
			const res = await post('/whatsapp/avisos', {
				accion: 'save',
				valores: {
					id_evento: ev.id,
					plantilla: ev.plantilla,
					telefonos: ev.telefonos,
					adjuntar: ev.adjuntar,
					activo: ev.activo
				}
			});
			avisar(res?.ok === false ? 'err' : 'ok', res?.mensaje || 'Aviso guardado');
			await cargar('avisos');
		} catch (err) {
			avisar('err', err?.message || 'No se pudo guardar el aviso');
		} finally {
			guardando = false;
		}
	}

	// ── bandeja ────────────────────────────────────────────────────────────
	async function cargarConversaciones() {
		cargandoLista = true;
		try {
			const res = await get('/wa/conversaciones');
			conversaciones = res.items || [];
		} catch (err) {
			conversaciones = [];
			avisar('err', err?.message || 'No se pudo leer la bandeja');
		} finally {
			cargandoLista = false;
		}
	}

	async function abrir(chatId) {
		chat = chatId;
		cargandoHilo = true;
		try {
			hilo = await get(`/wa/conversaciones/${encodeURIComponent(chatId)}`);
			await tick();
			if (cajaMsgs) cajaMsgs.scrollTop = cajaMsgs.scrollHeight;
		} catch (err) {
			hilo = null;
			avisar('err', err?.message || 'No se pudo abrir la conversación');
		} finally {
			cargandoHilo = false;
		}
	}

	function volver() {
		chat = '';
		hilo = null;
	}

	async function enviar(e) {
		e.preventDefault();
		const limpio = texto.trim();
		if (!limpio || enviando || !chat) return;
		enviando = true;
		try {
			await post(`/wa/conversaciones/${encodeURIComponent(chat)}/enviar`, { texto: limpio });
			texto = '';
			await abrir(chat);
		} catch (err) {
			avisar('err', err?.message || 'No se pudo enviar');
		} finally {
			enviando = false;
		}
	}

	async function accion(nombre) {
		if (!chat) return;
		try {
			const res = await post(`/wa/conversaciones/${encodeURIComponent(chat)}/${nombre}`);
			avisar('ok', res?.mensaje || 'Listo');
			await abrir(chat);
			await cargarConversaciones();
		} catch (err) {
			avisar('err', err?.message || 'No se pudo cambiar el estado');
		}
	}

	onMount(() => {
		cargar();
	});
</script>

<div class="stack">
	<div class="segmented segmented--wrap" role="tablist" aria-label="WhatsApp">
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

	{#if vista === 'conversaciones'}
		<div class="wa-inbox" class:wa-inbox--detalle={!!chat}>
			<section class="wa-pane wa-pane--list" aria-label="Conversaciones">
				<div class="wa-pane__head">
					<span class="wa-pane__title"><Icon name="chat" size={18} /> Conversaciones</span>
					<Button variant="quiet" tam="sm" icono="refresh" onclick={cargarConversaciones}>
						<span class="hide-sm">Actualizar</span>
					</Button>
				</div>

				{#if cargandoLista && !conversaciones.length}
					<div class="stack wa-cargando">
						<Skeleton alto="20px" ancho="60%" />
						<Skeleton alto="14px" lineas={3} />
						<Skeleton alto="14px" lineas={3} />
					</div>
				{:else if !conversaciones.length}
					<Empty
						icono="chat"
						titulo="Sin conversaciones"
						texto="Aparecen cuando OpenWA entrega el primer mensaje del chat."
					/>
				{:else}
					<ul class="wa-list">
						{#each conversaciones as c (c.chat)}
							<li>
								<button
									type="button"
									class="wa-row"
									class:is-active={chat === c.chat}
									onclick={() => abrir(c.chat)}
								>
									<span class="wa-row__main">
										<span class="wa-row__name">{c.nombre}</span>
										{#if c.telefono && c.telefono !== c.nombre}
											<span class="wa-row__tel">{c.telefono}</span>
										{/if}
										<span class="wa-row__msg">{c.ultimo_texto || '(sin texto)'}</span>
									</span>
									<span class="wa-row__meta">
										<Badge tone={nombreEstado(c.estado).tone}>{nombreEstado(c.estado).texto}</Badge>
										<span class="wa-row__hora">{hora(c.ultima)}</span>
									</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="wa-pane wa-pane--detail" aria-label="Conversación">
				{#if !chat}
					<Empty
						icono="chat"
						titulo="Elige una conversación"
						texto="Selecciona un chat de la lista para leerlo y responder."
					/>
				{:else if cargandoHilo && !hilo}
					<div class="stack wa-cargando">
						<Skeleton alto="18px" ancho="40%" />
						<Skeleton alto="14px" lineas={5} />
					</div>
				{:else if !hilo}
					<Empty icono="alert" titulo="Chat no disponible" texto="Vuelve a la lista e inténtalo de nuevo." />
				{:else}
					<div class="wa-pane__head">
						<span class="wa-back">
							<Button variant="quiet" tam="sm" onclick={volver}>
								<Icon name="chevronleft" size={18} /> Volver
							</Button>
						</span>
						<span class="wa-pane__title">{hilo.nombre}</span>
						<Badge tone={nombreEstado(hilo.estado).tone}>{nombreEstado(hilo.estado).texto}</Badge>
						<p class="wa-pane__sub">
							{hilo.telefono || ''}
							{#if hilo.asignado}· la tiene {hilo.asignado}{/if}
						</p>
						<div class="wa-acciones">
							<Button variant="quiet" tam="sm" icono="check" onclick={() => accion('tomar')}>
								Tomar
							</Button>
							<Button variant="quiet" tam="sm" icono="refresh" onclick={() => accion('devolver')}>
								Devolver
							</Button>
							<Button variant="quiet" tam="sm" icono="x" onclick={() => accion('cerrar')}>
								Cerrar
							</Button>
						</div>
					</div>

					<div class="wa-msgs" bind:this={cajaMsgs}>
						{#if !hilo.mensajes?.length}
							<Empty icono="chat" titulo="Sin mensajes" texto="Este chat todavía no tiene mensajes." />
						{:else}
							{#each hilo.mensajes as m, i (i)}
								<div class="wa-msg {m.dir === 'OUT' ? 'wa-msg--out' : 'wa-msg--in'}">
									{m.texto}
									<span class="wa-msg__hora">{hora(m.fecha)}</span>
								</div>
							{/each}
						{/if}
					</div>

					<form class="wa-compose" onsubmit={enviar}>
						<textarea
							class="textarea"
							rows="1"
							placeholder="Responder…"
							bind:value={texto}
							aria-label={`Respuesta para ${hilo.nombre}`}
						></textarea>
						<Button tipo="submit" variant="primary" disabled={enviando || !texto.trim()} icono="send">
							<span class="sr-only">Enviar</span>
						</Button>
					</form>
				{/if}
			</section>
		</div>
	{:else if cargando && !datos}
		<Card titulo="Cargando…">
			<Skeleton alto="18px" ancho="42%" />
			<div class="skeleton-gap"></div>
			<Skeleton alto="14px" lineas={4} />
		</Card>
	{:else if vista === 'conexion'}
		<form onsubmit={guardar}>
			<Card titulo="Conexión · OpenWA (WhatsApp)" sub="Dentro de Docker se llama por hostname; OpenWA rechaza callbacks a IPs internas.">
				<div class="form-grid">
					<Field
						id="ow-base"
						label="Dirección de OpenWA"
						nombre="openwa_base_url"
						bind:valor={conexion.openwa_base_url}
						hint="Ej. http://openwa:2785 (nunca la IP interna)."
					/>
					<Field
						id="ow-sesion"
						label="ID de sesión"
						nombre="openwa_session_id"
						bind:valor={conexion.openwa_session_id}
						hint="La sesión de WhatsApp ya iniciada (la principal)."
					/>
					<Field
						id="ow-key"
						label="API key de OpenWA"
						tipo="password"
						nombre="openwa_api_key"
						autocomplete="new-password"
						placeholder={datos?.api_key?.tiene_valor ? 'Guardada — vacío = no cambiar' : 'Pega la API key'}
						bind:valor={conexion.openwa_api_key}
						hint="Vacío = no se toca. Escribe BORRAR para quitarla."
					/>
				</div>
				<div class="form-foot">
					<span class="form-foot__estado">
						<Badge tone={datos?.session_id ? 'ok' : 'warn'}>
							{datos?.session_id ? `Sesión ${datos.session_id}` : 'Sin sesión'}
						</Badge>
					</span>
					<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
						{guardando ? 'Guardando…' : 'Guardar'}
					</Button>
				</div>
			</Card>
		</form>
	{:else if vista === 'avisos'}
		{#if !avisos.length}
			<Card>
				<Empty icono="bell" titulo="Sin avisos" texto="Faltan los avisos configurados (migración 0043)." />
			</Card>
		{:else}
			{#each avisos as ev (ev.id)}
				<form onsubmit={(e) => { e.preventDefault(); guardarAviso(ev); }}>
					<Card titulo={ev.nombre} sub={`Acceso por ${ev.id}`}>
						<div class="form-foot form-foot--top">
							<span class="form-foot__estado">
								<Badge tone={ev.activo ? 'ok' : 'bad'}>{ev.activo ? 'Activo' : 'Inactivo'}</Badge>
								{#if !ev.destinatarios?.length}
									<Badge tone="warn">sin teléfonos: no se enviará nada</Badge>
								{:else}
									<Badge tone="ok">{plural(ev.destinatarios.length, 'destinatario', 'destinatarios')}</Badge>
								{/if}
								{#if ev.ultimo}
									<Badge tone={ev.ultimo.estado === 'ENVIADO' ? 'ok' : 'bad'}>
										{ev.ultimo.estado} · {fechaLarga(ev.ultimo.creado)}
									</Badge>
								{:else}
									<Badge>sin envíos</Badge>
								{/if}
							</span>
						</div>
						{#if ev.descripcion}<p class="help">{ev.descripcion}</p>{/if}
						{#if ev.rechazados?.length}
							<p class="help help--bad">
								No se usaron: {ev.rechazados.join(', ')} (no son números)
							</p>
						{/if}
						<div class="form-grid">
							<Field
								id={`ev-${ev.id}-texto`}
								label="Texto del mensaje"
								tipo="textarea"
								nombre="plantilla"
								filas={4}
								bind:valor={ev.plantilla}
								hint={'Placeholders: {Folio} {Fecha} {Nombre} {Cliente} {Kilometros}…'}
							/>
							<Field
								id={`ev-${ev.id}-tel`}
								label="Teléfonos destinatarios"
								tipo="textarea"
								nombre="telefonos"
								filas={3}
								bind:valor={ev.telefonos}
								hint="Separados por comas. Un grupo se elige en Grupos (id …@g.us)."
							/>
						</div>
						<label class="chk">
							<input type="checkbox" bind:checked={ev.adjuntar} />
							Adjuntar archivo
						</label>
						<label class="chk">
							<input type="checkbox" bind:checked={ev.activo} />
							Activo
						</label>
						<div class="form-foot">
							<Button tipo="submit" variant="primary" disabled={guardando} icono="check">
								{guardando ? 'Guardando…' : 'Guardar'}
							</Button>
						</div>
					</Card>
				</form>
			{/each}
		{/if}
	{:else if vista === 'grupos'}
		<Card titulo="Grupos de la sesión" sub="El id se agrega a los destinatarios de un aviso.">
			{#if gruposError}
				<Empty icono="alert" titulo="No se pudieron leer los grupos" texto={gruposError}>
					<p class="help">Revisa la conexión de OpenWA (API key y sesión) en Conexión.</p>
				</Empty>
			{:else if !grupos.length}
				<Empty icono="users" titulo="Sin grupos" texto="La sesión de WhatsApp no tiene grupos o la API no respondió." />
			{:else}
				<ul class="list">
					{#each grupos as g (g.id)}
						<li class="list__row">
							<span class="list__main">
								<b>{g.nombre || '(sin nombre)'}</b>
								<span class="help">{g.id}</span>
							</span>
							<span class="list__meta">{plural(g.participantes || 0, 'participante', 'participantes')}</span>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>
	{:else if vista === 'historial'}
		<Card titulo="Historial" sub="Últimos 100 mensajes de la cola (HUB_WhatsappQueue).">
			{#if !historial.length}
				<Empty icono="clock" titulo="Sin envíos" texto="Todavía no hay avisos por WhatsApp." />
			{:else}
				<div class="table-wrap tscroll">
					<table class="table">
						<thead>
							<tr>
								<th>#</th>
								<th>Aviso</th>
								<th class="hide-sm">Chat</th>
								<th>Estado</th>
								<th class="hide-sm">Intentos</th>
								<th>Creado</th>
								<th class="hide-sm">Error</th>
							</tr>
						</thead>
						<tbody>
							{#each historial as h (h.id)}
								<tr>
									<td>{h.id}</td>
									<td>{h.evento}</td>
									<td class="hide-sm"><code>{h.chat}</code></td>
									<td>
										<Badge tone={h.estado === 'ENVIADO' ? 'ok' : h.estado === 'FALLADO' ? 'bad' : 'warn'}>
											{h.estado || '—'}
										</Badge>
									</td>
									<td class="hide-sm">{h.intentos ?? ''}</td>
									<td>{fechaLarga(h.creado)}</td>
									<td class="hide-sm"><span class="help">{h.error}</span></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card>
	{/if}
</div>
