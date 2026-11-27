<script>
	import { onMount } from 'svelte';
	import { get, post } from '../lib/api.js';
	import { path, matchPath, navigate } from '../lib/router.js';
	import Icon from '../lib/components/Icon.svelte';
	import Card from '../lib/components/Card.svelte';
	import Field from '../lib/components/Field.svelte';
	import Button from '../lib/components/Button.svelte';
	import Badge from '../lib/components/Badge.svelte';
	import Skeleton from '../lib/components/Skeleton.svelte';
	import Toast from '../lib/components/Toast.svelte';
	import Empty from '../lib/components/Empty.svelte';

	// Módulo ECCSita: memoria semanal del grupo de WhatsApp. El API vive en
	// panel/api_eccsita.py y cuelga de /api/panel/eccsita. La API entrega las
	// semanas, el detalle con la crónica y los mensajes crudos, y las acciones
	// de generar / marcar revisada.
	// `get`/`post` ya anteponen /api/panel, así que aquí la ruta va relativa.
	const API = '/eccsita';
	// Para hrefs directos (export) se usa la ruta absoluta del backend.
	const API_ABS = '/api/panel/eccsita';

	// Los estados de la tabla HUB_GrupoSemanas (migración del plan).
	const ESTADOS = {
		VACIA: { texto: 'Vacía', tone: '' },
		CAPTURADA: { texto: 'Capturada', tone: 'warn' },
		RESUMIDA: { texto: 'Resumida', tone: 'info' },
		REVISADA: { texto: 'Revisada', tone: 'ok' }
	};

	// ── estado ───────────────────────────────────────────────────────────────
	let grupo = $state(null);
	let cargandoGrupo = $state(true);
	let errorGrupo = $state('');

	let semanas = $state([]);
	let cargandoSemanas = $state(true);
	let errorSemanas = $state('');

	let detalle = $state(null);
	let cargandoSemana = $state(false);
	let errorSemana = $state('');

	let persona = $state('');
	let generando = $state(false);
	let revisando = $state(false);
	let aviso = $state({ tipo: '', texto: '' });

	let mostrandoGrupos = $state(false);
	let grupos = $state([]);
	let cargandoGrupos = $state(false);
	let errorGrupos = $state('');
	let chatSel = $state('');
	let guardandoGrupo = $state(false);

	// Roasts por persona: ECCSita agrupa sus menciones por persona y aquí se
	// moderan (aprobar, ocultar, borrar). El API las sirve en
	// /api/panel/eccsita/personas y /api/panel/eccsita/perfiles/{id}.
	let personasRoast = $state([]);
	let cargandoPersonas = $state(true);
	let errorPersonas = $state('');
	let buscarRoast = $state('');
	let roastSel = $state(null);
	let roastMenciones = $state([]);
	let cargandoMenciones = $state(false);
	let errorMenciones = $state('');
	let accionando = $state('');
	let confirmando = $state('');

	const TIPOS_MENCION = {
		BROMA: { texto: 'BROMA', tone: 'info' },
		APODO: { texto: 'APODO', tone: 'warn' },
		INSISTENCIA: { texto: 'INSISTENCIA', tone: '' }
	};

	const personasFiltradas = $derived.by(() => {
		const q = buscarRoast.trim().toLowerCase();
		if (!q) return personasRoast;
		return personasRoast.filter((p) => p.nombre.toLowerCase().includes(q));
	});

	// La ruta manda: /eccsita es la lista y /eccsita/:anio/:semana el detalle.
	// Se lee del store (no de los props) para que el botón de una semana y el
	// botón Atrás del navegador funcionen igual.
	const abierta = $derived(matchPath('/eccsita/:anio/:semana', $path));
	const anio = $derived(abierta ? Number(abierta.anio) : null);
	const semanaNum = $derived(abierta ? Number(abierta.semana) : null);

	const grupoConfigurado = $derived(
		!!grupo && (grupo.configurado === true || !!String(grupo.chat_id || '').trim())
	);

	const personas = $derived(
		[...new Set((detalle?.mensajes || []).map((m) => m.autor).filter(Boolean))]
	);
	const filtrados = $derived(
		persona ? (detalle?.mensajes || []).filter((m) => m.autor === persona) : detalle?.mensajes || []
	);
	const dias = $derived.by(() => {
		const mapa = new Map();
		for (const m of filtrados) {
			const clave = m.dia || (m.fecha ? String(m.fecha).slice(0, 10) : 'sin-fecha');
			if (!mapa.has(clave)) mapa.set(clave, { clave, mensajes: [] });
			mapa.get(clave).mensajes.push(m);
		}
		return [...mapa.values()];
	});

	const opcionesGrupos = $derived(
		grupos.map((g) => ({
			valor: String(g.id || g.chat_id || ''),
			texto: `${g.nombre || g.name || '(sin nombre)'}${
				g.participantes ? ` · ${g.participantes} participante(s)` : ''
			}`
		}))
	);

	// ── normalizadores ───────────────────────────────────────────────────────
	// El API puede devolver los campos como los declara el plan (minúsculas) o
	// como vienen de la tabla (PascalCase); aquí se aceptan las dos formas.
	const lista = (x) => (Array.isArray(x) ? x : x ? [x] : []);

	function normalizarSemana(w) {
		const item = w || {};
		return {
			anio: item.anio ?? item.Anio ?? null,
			semana: item.semana ?? item.Semana ?? null,
			inicio: item.inicio ?? item.Inicio ?? '',
			fin: item.fin ?? item.Fin ?? '',
			mensajes: Number(item.mensajes ?? item.Mensajes ?? 0) || 0,
			participantes: Number(item.participantes ?? item.Participantes ?? 0) || 0,
			estado: String(item.estado ?? item.Estado ?? '').toUpperCase(),
			generado_por: item.generado_por ?? item.generadoPor ?? item.GeneradoPor ?? '',
			fecha_generacion:
				item.fecha_generacion ?? item.fechaGeneracion ?? item.FechaGeneracion ?? ''
		};
	}

	function normalizarMensaje(m) {
		const item = m || {};
		const fecha = item.fecha ?? item.Fecha ?? '';
		return {
			id: item.id ?? item.Id ?? '',
			autor: item.autor ?? item.autor_nombre ?? item.AutorNombre ?? 'Sin nombre',
			texto: item.texto ?? item.Texto ?? '',
			tipo: item.tipo ?? item.Tipo ?? 'text',
			fecha,
			dia: item.dia ?? (fecha ? String(fecha).slice(0, 10) : '')
		};
	}

	function normalizarDetalle(d) {
		const base = normalizarSemana(d);
		const crudo = Array.isArray(d?.mensajes)
			? d.mensajes
			: d?.lista || d?.mensajesLista || d?.items || [];
		const mensajes = lista(crudo).map(normalizarMensaje);
		return {
			...base,
			mensajes,
			total: Number(
				d?.total ?? (Array.isArray(d?.mensajes) ? mensajes.length : base.mensajes)
			) || 0,
			resumen: d?.resumen ?? d?.cronica ?? d?.Cronica ?? d?.Resumen ?? '',
			temas: lista(d?.temas ?? d?.Temas),
			personajes: lista(d?.personajes ?? d?.Personajes),
			momentos: lista(d?.momentos ?? d?.Momentos)
		};
	}

	function normalizarGrupo(r) {
		const g = r?.grupo ?? r ?? {};
		const chat = String(g.chat_id ?? g.chatId ?? g.chat ?? '').trim();
		return {
			configurado: g.configurado === true || g.configured === true || !!chat,
			chat_id: chat,
			nombre: g.nombre ?? g.name ?? ''
		};
	}

	function normalizarPersonaRoast(p) {
		const item = p || {};
		const perfiles = item.perfiles ?? item.Perfiles ?? 0;
		const cuenta =
			item.menciones ??
			item.menciones_count ??
			item.total ??
			(Array.isArray(perfiles) ? perfiles.length : perfiles);
		return {
			idUsuario: String(item.idUsuario ?? item.id_usuario ?? item.id ?? ''),
			nombre: item.nombre ?? item.Nombre ?? 'Sin nombre',
			perfiles: Array.isArray(perfiles) ? perfiles : [],
			menciones: Number(cuenta) || 0,
			ultimo: item.ultimo ?? item.Ultimo ?? ''
		};
	}

	function normalizarMencion(m) {
		const item = m || {};
		const tipo = String(item.tipo ?? item.Tipo ?? '').toUpperCase();
		const aprobado = item.aprobado ?? item.Aprobado ?? true;
		return {
			id: String(item.id ?? item.Id ?? ''),
			texto: item.texto ?? item.Texto ?? '',
			autor: item.autor ?? item.Autor ?? 'Sin autor',
			fecha: item.fecha ?? item.Fecha ?? '',
			tipo: TIPOS_MENCION[tipo] ? tipo : '',
			aprobado: Boolean(aprobado)
		};
	}

	// Un tema, personaje o momento puede venir como texto o como objeto.
	function tituloItem(x) {
		if (x == null) return '';
		if (typeof x === 'string') return x;
		return x.tema ?? x.titulo ?? x.momento ?? x.nombre ?? x.quien ?? x.texto ?? '';
	}

	function detalleItem(x) {
		if (!x || typeof x === 'string') return '';
		const partes = [];
		const desc = x.detalle ?? x.descripcion ?? x.contexto ?? x.que ?? x.por_que ?? x.cita;
		if (desc) partes.push(desc);
		// Un momento del API llega como {autor, fecha, texto}: el texto es el
		// título y el autor con la fecha, el pie.
		if (x.autor) partes.push(x.autor);
		if (x.fecha) {
			const d = comoFecha(x.fecha);
			if (d) {
				partes.push(
					d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short' })
				);
			}
		}
		return partes.join(' · ');
	}

	const nombreEstado = (estado) =>
		ESTADOS[estado] || { texto: estado || 'Sin estado', tone: '' };

	// ── formato ──────────────────────────────────────────────────────────────
	function comoFecha(valor) {
		if (!valor) return null;
		const texto = String(valor);
		const d = new Date(texto.length === 10 ? `${texto}T12:00:00` : texto);
		return Number.isNaN(d.getTime()) ? null : d;
	}

	function fechaCorta(valor) {
		const d = comoFecha(valor);
		if (!d) return '—';
		return d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short' });
	}

	function rango(w) {
		if (!w?.inicio && !w?.fin) return 'Sin rango';
		return `${fechaCorta(w.inicio)} – ${fechaCorta(w.fin)}`;
	}

	function diaLargo(clave) {
		if (!clave || clave === 'sin-fecha') return 'Sin fecha';
		const d = comoFecha(clave);
		if (!d) return clave;
		return d.toLocaleDateString('es-MX', {
			weekday: 'long',
			day: '2-digit',
			month: 'long'
		});
	}

	function hora(fecha) {
		const d = comoFecha(fecha);
		if (!d) return '';
		return d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
	}

	function ligaExport(formato) {
		const a = detalle?.anio ?? anio;
		const s = detalle?.semana ?? semanaNum;
		return `${API_ABS}/semanas/${a}/${s}/export?formato=${formato}`;
	}

	// ── carga ────────────────────────────────────────────────────────────────
	function avisar(tipo, texto) {
		aviso = { tipo, texto };
	}

	async function cargarGrupo() {
		cargandoGrupo = true;
		errorGrupo = '';
		try {
			grupo = normalizarGrupo(await get(`${API}/grupo`));
		} catch (err) {
			grupo = null;
			errorGrupo = err?.message || 'No se pudo leer el grupo.';
		} finally {
			cargandoGrupo = false;
		}
	}

	async function cargarSemanas(silencioso = false) {
		if (!silencioso) cargandoSemanas = true;
		errorSemanas = '';
		try {
			const res = await get(`${API}/semanas`);
			const items = res?.items ?? res?.semanas ?? res ?? [];
			semanas = lista(items).map(normalizarSemana);
		} catch (err) {
			semanas = [];
			errorSemanas = err?.message || 'No se pudieron leer las semanas.';
		} finally {
			cargandoSemanas = false;
		}
	}

	async function cargarSemana(a, s) {
		cargandoSemana = true;
		errorSemana = '';
		persona = '';
		try {
			detalle = normalizarDetalle(await get(`${API}/semanas/${a}/${s}`));
		} catch (err) {
			detalle = null;
			errorSemana = err?.message || 'No se pudo abrir la semana.';
		} finally {
			cargandoSemana = false;
		}
	}

	// Al cambiar la ruta se carga (o se cierra) el detalle.
	$effect(() => {
		const a = anio;
		const s = semanaNum;
		if (a && s) cargarSemana(a, s);
		else detalle = null;
	});

	onMount(() => {
		cargarGrupo();
		cargarSemanas();
		cargarPersonas();
	});

	// ── acciones ─────────────────────────────────────────────────────────────
	async function generar() {
		if (generando || !anio || !semanaNum) return;
		generando = true;
		try {
			const res = await post(`${API}/semanas/${anio}/${semanaNum}/generar`, {});
			avisar('ok', res?.mensaje || 'Crónica generada.');
			await cargarSemana(anio, semanaNum);
			await cargarSemanas(true);
		} catch (err) {
			avisar('err', err?.message || 'No se pudo generar la crónica.');
		} finally {
			generando = false;
		}
	}

	async function marcarRevisada() {
		if (revisando || !anio || !semanaNum) return;
		revisando = true;
		try {
			const res = await post(`${API}/semanas/${anio}/${semanaNum}/revisar`, {});
			avisar('ok', res?.mensaje || 'Semana marcada como revisada.');
			await cargarSemana(anio, semanaNum);
			await cargarSemanas(true);
		} catch (err) {
			avisar('err', err?.message || 'No se pudo marcar como revisada.');
		} finally {
			revisando = false;
		}
	}

	async function cargarPersonas(silencioso = false) {
		if (!silencioso) cargandoPersonas = true;
		errorPersonas = '';
		try {
			const res = await get(`${API}/personas`);
			const items = res?.items ?? res?.personas ?? [];
			personasRoast = lista(items)
				.map(normalizarPersonaRoast)
				.filter((p) => p.idUsuario);
		} catch (err) {
			personasRoast = [];
			errorPersonas = err?.message || 'No se pudieron leer las personas.';
		} finally {
			cargandoPersonas = false;
		}
	}

	async function abrirPersona(p) {
		roastSel = p;
		roastMenciones = [];
		errorMenciones = '';
		cargandoMenciones = true;
		confirmando = '';
		try {
			const res = await get(`${API}/personas/${encodeURIComponent(p.idUsuario)}`);
			roastMenciones = lista(res?.items ?? res?.menciones ?? []).map(normalizarMencion);
		} catch (err) {
			errorMenciones = err?.message || 'No se pudieron leer las menciones.';
		} finally {
			cargandoMenciones = false;
		}
	}

	function cerrarPersona() {
		roastSel = null;
		roastMenciones = [];
		errorMenciones = '';
		confirmando = '';
	}

	// «Borrar» pide confirmación en el mismo botón: el primer clic solo arma
	// la confirmación, el segundo manda el POST. Cualquier otra acción la
	// desarma.
	async function accionMencion(m, accion) {
		if (accionando) return;
		if (accion === 'borrar' && confirmando !== m.id) {
			confirmando = m.id;
			return;
		}
		confirmando = '';
		accionando = m.id;
		try {
			await post(`${API}/perfiles/${encodeURIComponent(m.id)}`, { accion });
			if (accion === 'borrar') {
				roastMenciones = roastMenciones.filter((x) => x.id !== m.id);
				avisar('ok', 'Mención borrada.');
			} else {
				roastMenciones = roastMenciones.map((x) =>
					x.id === m.id ? { ...x, aprobado: accion === 'aprobar' } : x
				);
				avisar('ok', accion === 'aprobar' ? 'Mención aprobada.' : 'Mención ocultada.');
			}
			cargarPersonas(true);
		} catch (err) {
			avisar('err', err?.message || 'No se pudo actualizar la mención.');
		} finally {
			accionando = '';
		}
	}

	const tipoMencion = (tipo) =>
		TIPOS_MENCION[tipo] || { texto: tipo || 'SIN TIPO', tone: '' };

	async function cargarGrupos() {
		cargandoGrupos = true;
		errorGrupos = '';
		try {
			const res = await get(`${API}/grupos`);
			grupos = lista(res?.items ?? res?.grupos ?? res);
			if (!grupos.length && res?.error) errorGrupos = String(res.error);
			chatSel = grupoConfigurado ? grupo.chat_id : '';
		} catch (err) {
			grupos = [];
			errorGrupos = err?.message || 'No se pudieron leer los grupos.';
		} finally {
			cargandoGrupos = false;
		}
	}

	async function abrirGrupos() {
		mostrandoGrupos = true;
		if (!grupos.length) await cargarGrupos();
	}

	async function guardarGrupo() {
		if (guardandoGrupo || !chatSel) return;
		guardandoGrupo = true;
		try {
			const res = await post(`${API}/grupo`, { chatId: chatSel });
			avisar('ok', res?.mensaje || 'Grupo guardado.');
			await cargarGrupo();
			mostrandoGrupos = false;
		} catch (err) {
			avisar('err', err?.message || 'No se pudo guardar el grupo.');
		} finally {
			guardandoGrupo = false;
		}
	}

	function abrirSemana(w) {
		navigate(`/eccsita/${w.anio}/${w.semana}`);
	}

	function volver() {
		navigate('/eccsita');
	}
</script>

<div class="eccsita" class:eccsita--detalle={!!abierta}>
	<header class="ecc-head">
		<span class="ecc-head__title">
			<Icon name="messagecircle" size={20} />
			ECCSita
		</span>
		{#if cargandoGrupo}
			<Badge tone="info">Cargando grupo…</Badge>
		{:else if grupoConfigurado}
			<Badge tone="ok">Grupo configurado</Badge>
			<span class="ecc-head__grupo" title={grupo.chat_id}>
				{grupo.nombre || grupo.chat_id}
			</span>
		{:else}
			<Badge tone="warn">Sin grupo</Badge>
			<span class="ecc-head__grupo">Falta elegir el grupo de WhatsApp</span>
		{/if}
		{#if errorGrupo}<span class="ecc-head__error">{errorGrupo}</span>{/if}
		<Button variant="quiet" tam="sm" icono="users" onclick={abrirGrupos}>
			Elegir grupo
		</Button>
	</header>

	{#if aviso.texto}
		<div class="ecc-toast">
			<Toast
				tipo={aviso.tipo}
				mensaje={aviso.texto}
				oncerrar={() => (aviso = { tipo: '', texto: '' })}
			/>
		</div>
	{/if}

	{#if mostrandoGrupos}
		<div class="ecc-picker">
			<Card
				titulo="Grupos de WhatsApp"
				sub="Solo se captura el grupo elegido. El bot escucha y no responde."
			>
				{#if cargandoGrupos}
					<Skeleton alto="14px" lineas={4} />
				{:else if errorGrupos}
					<Empty icono="alert" titulo="No se pudieron leer los grupos" texto={errorGrupos}>
						<Button variant="quiet" icono="refresh" onclick={cargarGrupos}>Reintentar</Button>
					</Empty>
				{:else if !opcionesGrupos.length}
					<Empty
						icono="users"
						titulo="Sin grupos"
						texto="La sesión de WhatsApp no tiene grupos o la API no respondió."
					/>
				{:else}
					<Field
						id="ecc-grupo"
						label="Grupo a capturar"
						tipo="select"
						nombre="grupo"
						bind:valor={chatSel}
						opciones={opcionesGrupos}
					/>
					<div class="form-foot">
						<Button variant="quiet" onclick={() => (mostrandoGrupos = false)}>Cancelar</Button>
						<Button
							variant="primary"
							icono="check"
							onclick={guardarGrupo}
							disabled={guardandoGrupo || !chatSel}
						>
							{guardandoGrupo ? 'Guardando…' : 'Guardar'}
						</Button>
					</div>
				{/if}
			</Card>
		</div>
	{/if}

	<aside class="ecc-list" aria-label="Semanas capturadas">
		<div class="ecc-list__head">
			<span class="ecc-list__title">Semanas</span>
			<Button variant="quiet" tam="sm" icono="refresh" onclick={() => cargarSemanas()}>
				<span class="sr-only">Actualizar</span>
			</Button>
		</div>

		{#if cargandoSemanas}
			<div class="stack ecc-cargando">
				<Skeleton alto="18px" ancho="60%" />
				<Skeleton alto="14px" lineas={3} />
				<Skeleton alto="14px" lineas={3} />
			</div>
		{:else if errorSemanas}
			<Empty icono="alert" titulo="No se pudieron leer las semanas" texto={errorSemanas}>
				<Button variant="quiet" icono="refresh" onclick={() => cargarSemanas()}>Reintentar</Button>
			</Empty>
		{:else if !semanas.length}
			<Empty
				icono="clock"
				titulo="Sin semanas"
				texto="Aparecen cuando el bot capture los primeros mensajes del grupo."
			/>
		{:else}
			<ul class="ecc-weeks">
				{#each semanas as w (w.anio + '-' + w.semana)}
					{@const est = nombreEstado(w.estado)}
					<li>
						<button
							type="button"
							class="ecc-week"
							class:is-active={anio === Number(w.anio) && semanaNum === Number(w.semana)}
							onclick={() => abrirSemana(w)}
						>
							<span class="ecc-week__main">
								<span class="ecc-week__title">Semana {w.semana} · {w.anio}</span>
								<span class="ecc-week__rango">{rango(w)}</span>
								<span class="ecc-week__meta">
									{w.mensajes} mensaje(s) · {w.participantes} persona(s)
								</span>
							</span>
							<Badge tone={est.tone}>{est.texto}</Badge>
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</aside>

	<section class="ecc-detail" aria-label="Detalle de la semana">
		{#if cargandoSemana && !detalle}
			<div class="ecc-detail__body">
				<Skeleton alto="18px" ancho="40%" />
				<div class="skeleton-gap"></div>
				<Skeleton alto="14px" lineas={6} />
			</div>
		{:else if errorSemana && !detalle}
			<div class="ecc-detail__body">
				<Empty icono="alert" titulo="No se pudo abrir la semana" texto={errorSemana}>
					<Button variant="quiet" icono="refresh" onclick={() => cargarSemana(anio, semanaNum)}>
						Reintentar
					</Button>
				</Empty>
			</div>
		{:else if !detalle}
			<div class="ecc-detail__body">
				<Empty
					icono="messagecircle"
					titulo="Elige una semana"
					texto="Abre una semana de la lista para leer la crónica, los temas y los mensajes."
				/>
			</div>
		{:else}
			{@const est = nombreEstado(detalle.estado)}
			<div class="ecc-detail__head">
				<span class="ecc-back">
					<Button variant="quiet" tam="sm" onclick={volver}>
						<Icon name="chevronleft" size={18} /> Atrás
					</Button>
				</span>
				<div class="ecc-detail__titles">
					<h2 class="ecc-detail__title">Semana {detalle.semana} · {detalle.anio}</h2>
					<p class="ecc-detail__sub">
						{rango(detalle)} · {detalle.total} mensaje(s)
						{#if detalle.generado_por}· {detalle.generado_por}{/if}
					</p>
				</div>
				<Badge tone={est.tone}>{est.texto}</Badge>
				<div class="ecc-detail__actions">
					<Button variant="primary" tam="sm" icono="refresh" onclick={generar} disabled={generando}>
						{generando ? 'Generando…' : detalle.estado === 'VACIA' ? 'Generar' : 'Regenerar'}
					</Button>
					<Button
						variant="quiet"
						tam="sm"
						icono="check"
						onclick={marcarRevisada}
						disabled={revisando || detalle.estado === 'REVISADA'}
					>
						{revisando ? 'Guardando…' : 'Marcar revisada'}
					</Button>
					<a class="btn btn--quiet btn--sm" href={ligaExport('md')} download>
						<Icon name="logs" size={18} /> Exportar Markdown
					</a>
					<a class="btn btn--quiet btn--sm" href={ligaExport('json')} download>
						<Icon name="apps" size={18} /> Exportar JSON
					</a>
				</div>
			</div>

			<div class="ecc-detail__body">
				<Card titulo="Crónica" sub="Lo que se habló, en tono de anécdota interna.">
					{#if detalle.resumen}
						<p class="ecc-texto">{detalle.resumen}</p>
					{:else}
						<p class="hint">Todavía no hay crónica. Pulsa «Generar» cuando la semana tenga mensajes.</p>
					{/if}
				</Card>

				<div class="ecc-cards">
					<Card titulo="Temas" sub="Recurrentes y chistes internos">
						{#if !detalle.temas.length}
							<p class="hint">Sin temas todavía.</p>
						{:else}
							<ul class="ecc-items">
								{#each detalle.temas as t, i (i)}
									<li>
										<strong>{tituloItem(t)}</strong>
										{#if detalleItem(t)}<span>{detalleItem(t)}</span>{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</Card>

					<Card titulo="Personajes" sub="Quién la hace de oro y con qué">
						{#if !detalle.personajes.length}
							<p class="hint">Sin personajes todavía.</p>
						{:else}
							<ul class="ecc-items">
								{#each detalle.personajes as p, i (i)}
									<li>
										<strong>{tituloItem(p)}</strong>
										{#if detalleItem(p)}<span>{detalleItem(p)}</span>{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</Card>

					<Card titulo="Momentos" sub="Candidatos a meme o a segmento de podcast">
						{#if !detalle.momentos.length}
							<p class="hint">Sin momentos todavía.</p>
						{:else}
							<ul class="ecc-items">
								{#each detalle.momentos as m, i (i)}
									<li>
										<strong>{tituloItem(m)}</strong>
										{#if detalleItem(m)}<span>{detalleItem(m)}</span>{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</Card>
				</div>

				<Card titulo="Mensajes" sub={`${detalle.total} en la semana`}>
					{#if personas.length > 1}
						<div class="segmented segmented--wrap ecc-filtro" role="tablist" aria-label="Filtrar por persona">
							<button
								type="button"
								role="tab"
								aria-selected={persona === ''}
								class:is-active={persona === ''}
								onclick={() => (persona = '')}
							>
								Todos
							</button>
							{#each personas as p (p)}
								<button
									type="button"
									role="tab"
									aria-selected={persona === p}
									class:is-active={persona === p}
									onclick={() => (persona = p)}
								>
									{p}
								</button>
							{/each}
						</div>
					{/if}

					{#if !filtrados.length}
						<Empty
							icono="chat"
							titulo="Sin mensajes"
							texto={persona ? 'Esa persona no tiene mensajes en la semana.' : 'La semana no tiene mensajes capturados.'}
						/>
					{:else}
						<div class="ecc-days">
							{#each dias as d (d.clave)}
								<div class="ecc-day">
									<h3 class="ecc-day__title">{diaLargo(d.clave)}</h3>
									<ul class="ecc-msgs">
										{#each d.mensajes as m, i (m.id || i)}
											<li class="ecc-msg">
												<span class="ecc-msg__head">
													<strong>{m.autor}</strong>
													<span class="ecc-msg__hora">{hora(m.fecha)}</span>
												</span>
												<span class="ecc-msg__texto">
													{m.texto || (m.tipo !== 'text' ? `(${m.tipo})` : '')}
												</span>
											</li>
										{/each}
									</ul>
								</div>
							{/each}
						</div>
					{/if}
				</Card>
			</div>
		{/if}
	</section>

	<section
		class="ecc-roasts"
		class:ecc-roasts--abierto={!!roastSel}
		aria-label="Roasts por persona"
	>
		<div class="ecc-roasts__list">
			<div class="ecc-list__head">
				<span class="ecc-list__title">Roasts por persona</span>
				<Button variant="quiet" tam="sm" icono="refresh" onclick={() => cargarPersonas()}>
					<span class="sr-only">Actualizar</span>
				</Button>
			</div>

			<div class="ecc-roasts__buscar">
				<Field
					id="roast-buscar"
					label="Buscar persona"
					nombre="buscar"
					bind:valor={buscarRoast}
					placeholder="Nombre"
				/>
			</div>

			{#if cargandoPersonas}
				<div class="ecc-roasts__cargando">
					<Skeleton alto="16px" lineas={4} />
				</div>
			{:else if errorPersonas}
				<Empty icono="alert" titulo="No se pudieron leer las personas" texto={errorPersonas}>
					<Button variant="quiet" icono="refresh" onclick={() => cargarPersonas()}>
						Reintentar
					</Button>
				</Empty>
			{:else if !personasRoast.length}
				<Empty
					icono="users"
					titulo="Sin roasts"
					texto="Aparecen cuando ECCSita mencione a alguien del grupo."
				/>
			{:else if !personasFiltradas.length}
				<Empty
					icono="search"
					titulo="Sin coincidencias"
					texto={`Ninguna persona se llama «${buscarRoast.trim()}».`}
				/>
			{:else}
				<ul class="ecc-roasts__personas">
					{#each personasFiltradas as p (p.idUsuario)}
						<li>
							<button
								type="button"
								class="ecc-persona"
								class:is-active={roastSel?.idUsuario === p.idUsuario}
								onclick={() => abrirPersona(p)}
							>
								<span class="ecc-persona__main">
									<span class="ecc-persona__nombre">{p.nombre}</span>
									<span class="ecc-persona__meta">
										{p.menciones} mención(es) · {fechaCorta(p.ultimo)}
									</span>
								</span>
								<Icon name="chevronright" size={18} />
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		<div class="ecc-roasts__menciones">
			{#if !roastSel}
				<div class="ecc-roasts__body">
					<Empty
						icono="messagecircle"
						titulo="Elige una persona"
						texto="Abre una persona para ver y moderar lo que ECCSita dijo de ella."
					/>
				</div>
			{:else}
				<div class="ecc-detail__head">
					<span class="ecc-back">
						<Button variant="quiet" tam="sm" onclick={cerrarPersona}>
							<Icon name="chevronleft" size={18} /> Atrás
						</Button>
					</span>
					<div class="ecc-detail__titles">
						<h2 class="ecc-detail__title">{roastSel.nombre}</h2>
						<p class="ecc-detail__sub">
							{roastSel.menciones} mención(es) · última {fechaCorta(roastSel.ultimo)}
						</p>
					</div>
				</div>

				<div class="ecc-roasts__body">
					{#if cargandoMenciones}
						<Skeleton alto="16px" lineas={4} />
					{:else if errorMenciones}
						<Empty icono="alert" titulo="No se pudieron leer las menciones" texto={errorMenciones}>
							<Button variant="quiet" icono="refresh" onclick={() => abrirPersona(roastSel)}>
								Reintentar
							</Button>
						</Empty>
					{:else if !roastMenciones.length}
						<Empty
							icono="chat"
							titulo="Sin menciones"
							texto="Todavía no hay nada de esta persona en ECCSita."
						/>
					{:else}
						<ul class="ecc-menciones">
							{#each roastMenciones as m (m.id)}
								{@const et = tipoMencion(m.tipo)}
								<li class="ecc-mencion" class:is-oculta={!m.aprobado}>
									<div class="ecc-mencion__head">
										<Badge tone={et.tone}>{et.texto}</Badge>
										{#if !m.aprobado}<Badge>Oculta</Badge>{/if}
										<span class="ecc-mencion__meta">
											{m.autor} · {fechaCorta(m.fecha)}{#if hora(m.fecha)}· {hora(m.fecha)}{/if}
										</span>
									</div>
									<p class="ecc-mencion__texto">{m.texto}</p>
									<div class="ecc-mencion__acciones">
										<Button
											variant="quiet"
											tam="sm"
											icono="check"
											disabled={m.aprobado || accionando === m.id}
											onclick={() => accionMencion(m, 'aprobar')}
										>
											Aprobar
										</Button>
										<Button
											variant="quiet"
											tam="sm"
											disabled={!m.aprobado || accionando === m.id}
											onclick={() => accionMencion(m, 'ocultar')}
										>
											Ocultar
										</Button>
										<Button
											variant={confirmando === m.id ? 'danger' : 'quiet'}
											tam="sm"
											icono="trash"
											disabled={accionando === m.id}
											onclick={() => accionMencion(m, 'borrar')}
										>
											{confirmando === m.id ? 'Confirmar borrado' : 'Borrar'}
										</Button>
									</div>
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}
		</div>
	</section>
</div>

<style>
	/* El módulo no scrollea la página: la lista de semanas y el detalle
	   scrollean por su cuenta (ver las reglas .content:has(.eccsita) en
	   app.css, que fijan el alto del contenedor). */
	.eccsita {
		flex: 1 1 auto;
		min-height: 0;
		display: grid;
		grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
		grid-template-rows: auto minmax(0, 1fr) minmax(0, 300px);
		gap: 12px;
	}

	.ecc-head,
	.ecc-toast,
	.ecc-picker,
	.ecc-roasts {
		grid-column: 1 / -1;
	}

	.ecc-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 12px;
		padding: 12px 16px;
		background: var(--surface);
		border: 1px solid var(--separator);
		border-radius: var(--radius);
	}
	.ecc-head__title {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		font-size: 17px;
		font-weight: 590;
	}
	.ecc-head__grupo {
		font-size: 15px;
		color: var(--text-2);
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.ecc-head__error { color: var(--danger-text); font-size: 13px; }
	.ecc-head > :global(.btn) { margin-left: auto; }

	.ecc-list,
	.ecc-detail {
		min-height: 0;
		display: flex;
		flex-direction: column;
		background: var(--surface);
		border: 1px solid var(--separator);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.ecc-list__head,
	.ecc-detail__head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		padding: 12px;
		border-bottom: 1px solid var(--separator);
	}
	.ecc-list__title { font-size: 15px; font-weight: 590; }
	.ecc-list__head > :global(.btn) { margin-left: auto; }

	.ecc-cargando { padding: 12px; }

	.ecc-weeks {
		flex: 1 1 auto;
		min-height: 0;
		margin: 0;
		padding: 0;
		list-style: none;
		overflow-y: auto;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
	}
	.ecc-week {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		width: 100%;
		padding: 10px 12px;
		border: none;
		border-bottom: 1px solid var(--separator);
		background: transparent;
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.ecc-week:hover { background: var(--fill); }
	.ecc-week.is-active { background: var(--fill-strong); }
	.ecc-week__main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
	.ecc-week__title { font-weight: 590; }
	.ecc-week__rango { font-size: 13px; color: var(--text-2); }
	.ecc-week__meta { font-size: 12px; color: var(--text-2); }

	.ecc-detail__head { align-items: flex-start; }
	.ecc-detail__titles { min-width: 0; flex: 1 1 auto; }
	.ecc-detail__title { margin: 0; font-size: 17px; font-weight: 590; }
	.ecc-detail__sub { margin: 2px 0 0; font-size: 13px; color: var(--text-2); }
	.ecc-detail__actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		flex-basis: 100%;
	}
	.ecc-back { display: none; }

	.ecc-detail__body {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 12px;
		background: var(--surface-2);
	}

	.ecc-texto {
		margin: 0;
		font-size: 15px;
		line-height: 1.55;
		white-space: pre-wrap;
	}

	.ecc-cards {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 12px;
	}

	.ecc-items {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ecc-items li { display: flex; flex-direction: column; gap: 2px; }
	.ecc-items span { font-size: 13px; color: var(--text-2); }

	.ecc-filtro { margin: 0 0 12px; }

	.ecc-days { display: flex; flex-direction: column; gap: 12px; }
	.ecc-day__title {
		margin: 0 0 6px;
		font-size: 13px;
		font-weight: 590;
		color: var(--text-2);
		text-transform: capitalize;
	}
	.ecc-msgs {
		margin: 0;
		padding: 0;
		list-style: none;
		border-left: 2px solid var(--separator);
	}
	.ecc-msg {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 6px 0 6px 10px;
	}
	.ecc-msg__head { display: flex; align-items: baseline; gap: 8px; }
	.ecc-msg__head strong { font-size: 13px; }
	.ecc-msg__hora { font-size: 11px; color: var(--text-2); font-variant-numeric: tabular-nums; }
	.ecc-msg__texto { font-size: 15px; line-height: 1.4; white-space: pre-wrap; word-break: break-word; }

	/* Roasts por persona: dos paneles como las semanas; cada uno scrollea
	   por su cuenta, así la página nunca crece. */
	.ecc-roasts {
		min-height: 0;
		display: grid;
		grid-template-columns: minmax(0, 340px) minmax(0, 1fr);
		background: var(--surface);
		border: 1px solid var(--separator);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.ecc-roasts__list,
	.ecc-roasts__menciones {
		min-height: 0;
		display: flex;
		flex-direction: column;
	}
	.ecc-roasts__list { border-right: 1px solid var(--separator); }

	.ecc-roasts__buscar { padding: 10px 12px 0; }
	.ecc-roasts__buscar :global(.field) { margin: 0; }

	.ecc-roasts__cargando { padding: 12px; }

	.ecc-roasts__personas {
		flex: 1 1 auto;
		min-height: 0;
		margin: 0;
		padding: 0;
		list-style: none;
		overflow-y: auto;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
	}
	.ecc-persona {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		width: 100%;
		padding: 8px 12px;
		border: none;
		border-bottom: 1px solid var(--separator);
		background: transparent;
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.ecc-persona:hover { background: var(--fill); }
	.ecc-persona.is-active { background: var(--fill-strong); }
	.ecc-persona__main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
	.ecc-persona__nombre {
		font-weight: 590;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.ecc-persona__meta { font-size: 12px; color: var(--text-2); }

	.ecc-roasts__body {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 12px;
		background: var(--surface-2);
	}

	.ecc-menciones {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ecc-mencion {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 10px 12px;
		background: var(--surface);
		border: 1px solid var(--separator);
		border-radius: var(--radius);
	}
	.ecc-mencion.is-oculta { opacity: .55; }
	.ecc-mencion__head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
	.ecc-mencion__meta { font-size: 12px; color: var(--text-2); }
	.ecc-mencion__texto {
		margin: 0;
		font-size: 15px;
		line-height: 1.4;
		white-space: pre-wrap;
		word-break: break-word;
	}
	.ecc-mencion__acciones { display: flex; flex-wrap: wrap; gap: 6px; }

	/* Teléfono: una sola columna y, al abrir una semana, el detalle ocupa el
	   lugar de la lista (con Atrás), como la bandeja de WhatsApp. */
	@media (max-width: 899px) {
		.eccsita { grid-template-columns: minmax(0, 1fr); }
		.ecc-detail { display: none; }
		.eccsita--detalle .ecc-list { display: none; }
		.eccsita--detalle .ecc-detail { display: flex; }
		.ecc-back { display: inline-flex; }
		.ecc-head > :global(.btn) { margin-left: 0; }

		/* Roasts en una columna: al abrir una persona, sus menciones
		   reemplazan la lista (con Atrás). */
		.ecc-roasts { grid-template-columns: minmax(0, 1fr); }
		.ecc-roasts__list { border-right: none; }
		.ecc-roasts__menciones { display: none; }
		.ecc-roasts--abierto .ecc-roasts__list { display: none; }
		.ecc-roasts--abierto .ecc-roasts__menciones { display: flex; }
	}
</style>
