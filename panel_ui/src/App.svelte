<script>
	import { onMount } from 'svelte';
	import { path, matchRoute, navigate } from './lib/router.js';
	import { auth, init, logout } from './lib/stores/auth.js';
	import Splash from './lib/components/Splash.svelte';
	import AppShell from './lib/components/AppShell.svelte';
	import Empty from './lib/components/Empty.svelte';
	import Dashboard from './pages/Dashboard.svelte';
	import Logs from './pages/Logs.svelte';
	import LogDetalle from './pages/LogDetalle.svelte';
	import Configuracion from './pages/Configuracion.svelte';
	import Apps from './pages/Apps.svelte';
	import Avisos from './pages/Avisos.svelte';
	import Notificaciones from './pages/Notificaciones.svelte';
	import WhatsApp from './pages/WhatsApp.svelte';
	import Asistencia from './pages/Asistencia.svelte';
	import Correo from './pages/Correo.svelte';
	import Triaje from './pages/Triaje.svelte';
	import Login from './pages/Login.svelte';

	// Las rutas con parámetro comparten componente: Logs y LogDetalle.
	const PAGINAS = {
		dashboard: Dashboard,
		logs: Logs,
		configuracion: Configuracion,
		apps: Apps,
		avisos: Avisos,
		notificaciones: Notificaciones,
		whatsapp: WhatsApp,
		asistencia: Asistencia,
		correo: Correo,
		triaje: Triaje
	};

	const ESPERA_MINIMA = 350; // el splash nunca pasa de ~600 ms sin red

	let listo = $state(false);
	let ruta = $derived(matchRoute($path));
	let Pagina = $derived(
		ruta.id === 'logs' && ruta.params.nombre ? LogDetalle : (PAGINAS[ruta.id] ?? null)
	);

	onMount(() => {
		const espera = new Promise((r) => setTimeout(r, ESPERA_MINIMA));
		Promise.all([init(), espera]).finally(() => {
			listo = true;
		});
	});

	// Sin sesión no se pinta el shell; con sesión, /login vuelve a la raíz.
	$effect(() => {
		if (!listo) return;
		if (!$auth.user && ruta.id !== 'login') navigate('/login', { replace: true });
		else if ($auth.user && ruta.id === 'login') navigate('/', { replace: true });
	});

	async function cerrarSesion() {
		try {
			await logout();
		} catch {
			// api.js ya limpió la sesión local en 401/error de red.
		}
		navigate('/login', { replace: true });
	}

	// `auth` pinta la tarjeta de login centrada (sin banner ni rail).
	$effect(() => {
		if (typeof document === 'undefined') return;
		document.body.classList.toggle('auth', !$auth.user);
	});
</script>

{#if !listo}
	<Splash />
{:else if !$auth.user}
	<Login />
{:else if $auth.user && ruta.id === 'login'}
	<Splash texto="Volviendo al panel…" />
{:else}
	<AppShell
		titulo={ruta.titulo}
		sub={ruta.sub}
		activo={ruta.id}
		usuario={$auth.user.nombre || $auth.user.email || ''}
		onlogout={cerrarSesion}
	>
		{#if Pagina}
			<Pagina params={ruta.params} />
		{:else}
			<Empty
				icono="alert"
				titulo="No encontrada"
				texto="La ruta no existe en el panel."
			/>
		{/if}
	</AppShell>
{/if}
