<script>
	import Icon from './Icon.svelte';
	import { navigate } from '../router.js';

	// Destinos del contrato (DESIGN.md). En el rail lateral se ven todos; la
	// barra inferior solo lleva cuatro + «Más».
	const DESTINOS = [
		{ id: 'dashboard', label: 'Estado', href: '/', icono: 'dashboard' },
		{ id: 'logs', label: 'Logs', href: '/logs', icono: 'logs' },
		{ id: 'whatsapp', label: 'WhatsApp', href: '/whatsapp', icono: 'chat' },
		{ id: 'eccsita', label: 'ECCSita', href: '/eccsita', icono: 'messagecircle' },
		{ id: 'notificaciones', label: 'Notificaciones', href: '/notificaciones', icono: 'bell' },
		{ id: 'avisos', label: 'Avisos', href: '/avisos', icono: 'megaphone' },
		{ id: 'configuracion', label: 'Configuración', href: '/configuracion', icono: 'settings' },
		{ id: 'apps', label: 'Apps', href: '/apps', icono: 'apps' },
		{ id: 'asistencia', label: 'Asistencia', href: '/asistencia', icono: 'clock' },
		{ id: 'correo', label: 'Correo', href: '/correo', icono: 'mail' },
		{ id: 'triaje', label: 'Triaje', href: '/triaje', icono: 'ticket' }
	];

	const PRINCIPALES = ['dashboard', 'logs', 'whatsapp', 'notificaciones'];
	const EN_BARRA = DESTINOS.filter((d) => PRINCIPALES.includes(d.id));
	const EN_HOJA = DESTINOS.filter((d) => !PRINCIPALES.includes(d.id));

	let { titulo = 'Panel', sub = '', usuario = '', activo = '', onlogout = () => {}, children } = $props();

	let mas = $state(false);

	function salir() {
		mas = false;
		onlogout();
	}
</script>

<div class="app">
	<aside class="rail" aria-label="Navegación principal">
		<a class="rail__brand" href="/" aria-label="Workers ECCSA, ir a Estado">
			<Icon name="dashboard" size={22} />
			<span class="rail__brand-name">Workers ECCSA</span>
		</a>
		<nav class="rail__nav" aria-label="Secciones">
			{#each DESTINOS as d (d.id)}
				<a
					class="nav-item"
					class:is-active={activo === d.id}
					href={d.href}
					aria-current={activo === d.id ? 'page' : undefined}
					aria-label={d.label}
				>
					<Icon name={d.icono} size={20} />
					<span class="nav-item__text">{d.label}</span>
				</a>
			{/each}
		</nav>
		<div class="rail__foot">
			<button class="btn btn--quiet btn--sm" type="button" onclick={salir} aria-label="Cerrar sesión">
				<Icon name="logout" size={18} />
				<span class="nav-item__text">Salir</span>
			</button>
		</div>
	</aside>

	<main class="main">
		<header class="topbar">
			<div class="topbar__titles">
				<h1 class="topbar__title">{titulo}</h1>
				{#if sub}<p class="topbar__sub">{sub}</p>{/if}
			</div>
			<div class="topbar__actions">
				{#if usuario}<span class="topbar__user">{usuario}</span>{/if}
				<button class="btn btn--quiet btn--sm" type="button" onclick={salir} aria-label="Cerrar sesión">
					<Icon name="logout" size={18} />
					<span class="hide-sm">Salir</span>
				</button>
			</div>
		</header>
		<div class="content">
			{@render children?.()}
		</div>
	</main>

	<nav class="rail--bottom" aria-label="Navegación principal">
		{#each EN_BARRA as d (d.id)}
			<a
				class="nav-item"
				class:is-active={activo === d.id}
				href={d.href}
				aria-current={activo === d.id ? 'page' : undefined}
				aria-label={d.label}
			>
				<Icon name={d.icono} size={22} />
				<span class="nav-item__text">{d.label}</span>
			</a>
		{/each}
		<details class="rail__more" bind:open={mas}>
			<summary class="nav-item" aria-label="Más secciones">
				<Icon name="more" size={22} />
				<span class="nav-item__text">Más</span>
			</summary>
			<div class="rail__sheet">
				{#each EN_HOJA as d (d.id)}
					<a
						class="nav-item"
						class:is-active={activo === d.id}
						href={d.href}
						aria-current={activo === d.id ? 'page' : undefined}
						onclick={() => (mas = false)}
					>
						<Icon name={d.icono} size={20} />
						<span class="nav-item__text">{d.label}</span>
					</a>
				{/each}
				<button class="nav-item" type="button" onclick={salir} aria-label="Cerrar sesión">
					<Icon name="logout" size={20} />
					<span class="nav-item__text">Salir</span>
				</button>
			</div>
		</details>
	</nav>
</div>
