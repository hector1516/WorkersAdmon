<script>
	/* ECCSA-Shell · banner común (referencia Svelte). ESTE ES EL ESTÁNDAR.
	   ------------------------------------------------------------------
	   Nació del banner de Field (que ya tenía el `.sync-header` con estado de
	   sync, usuario, lugar y versiones) y es lo que deben pintar Field, Admon
	   y cualquier app Svelte nueva. Ver docs/CONTRATO.md.

	   Reparto de responsabilidades (para que no se duplique nada):
	     · El SHELL manda: este markup, los textos y el CSS — que vive en
	       src/body.css como `.sync-header`, NO en un <style> acá, porque las
	       apps no compilan el scope del componente y el panel (Python sin
	       build) tiene que compartir el mismo CSS.
	     · La APP manda: sus stores. Acá no se importa nada del proyecto;
	       el estado llega por props y el clic sale por `onsync`.

	   Deliberadamente en JS plano con JSDoc (y no lang="ts"): Admon compila con
	   vite+svelte sin preprocesador de TypeScript, y un lang="ts" le rompe el
	   build. Field corre svelte-check igual.

	   Props:
	     estado        'offline'|'syncing'|'error'|'pending'|'idle'
	     pendientes    number   (solo se usa con estado 'pending')
	     usuario       string   ('' = sin sesión → no se pinta la línea de usuario)
	     lugar         'oficina'|'remoto'|'desconocido'  (si se pasa, pisa el endpoint)
	     ip            string   (si se pasa, pisa el endpoint; va en el title)
	     appVersion    string   (normalmente de $lib/shell.js)
	     shellVersion  string
	     endpoint      string   (default '/api/shell/state')
	     fetcher       (url) => Promise   ver nota de autenticación abajo
	     onsync        () => any  (click = disparar la sincronización de la app)

	   AUTENTICACIÓN — por qué existe `fetcher`:
	   el endpoint exige sesión, y no todas las apps autentican igual. Field y
	   Admon mandan `Authorization: Bearer <token>`; el panel usa cookie. Un
	   fetch() peludo desde acá solo funcionaría en el panel, así que la app le
	   pasa su propio fetcher cuando lo necesita. Si no se pasa, se usa un fetch
	   same-origin peludo (que es justo lo que necesita el panel).
	*/
	let {
		estado = 'idle',
		pendientes = 0,
		usuario = '',
		lugar = null,
		ip = '',
		appVersion = '',
		shellVersion = '',
		endpoint = '/api/shell/state',
		fetcher = null,
		onsync = null
	} = $props();

	/* Lo que solo sabe el servidor: si estás en la oficina o en remoto.
	   Fail silent a propósito: si el endpoint no responde el banner se sigue
	   viendo igual, solo que con 📍 — (regla 4 del contrato). */
	let lugarApi = $state('desconocido');
	let ipApi = $state('');
	let usuarioApi = $state('');
	let versionApi = $state('');

	$effect(() => {
		let vivo = true;
		(async () => {
			try {
				/* La app manda su propio fetch si autentica con Bearer; si no,
				   el de aquí (same-origin) que es lo que Sirve el panel. */
				const pedir = fetcher
					|| ((url) => fetch(url, { credentials: 'same-origin' }));
				const r = await pedir(endpoint);
				if (!r.ok) return;
				const d = await r.json();
				if (!vivo) return;
				lugarApi = d.lugar?.modo || 'desconocido';
				ipApi = d.lugar?.ip || '';
				usuarioApi = d.user?.nombre || '';
				versionApi = d.shell?.version || '';
			} catch (e) {
				/* fail silent: el shell no puede tumbar una app */
			}
		})();
		return () => { vivo = false; };
	});

	/* El prop pisa al endpoint cuando la app ya lo sabe. */
	const modo = $derived(lugar || lugarApi);
	const ipFinal = $derived(ip || ipApi);
	const nombre = $derived(usuario || usuarioApi);
	const vApp = $derived(appVersion || '?');
	const vShell = $derived(shellVersion || versionApi || '?');

	const LUGAR = {
		oficina: { icono: '🏢', texto: 'Oficina' },
		remoto: { icono: '🏠', texto: 'Remoto' },
		desconocido: { icono: '📍', texto: '—' }
	};
	const lug = $derived(LUGAR[modo] || LUGAR.desconocido);

	/* Orden de precedencia: sin red > sincronizando > falló > hay pendientes > al día. */
	const effective = $derived(
		estado === 'offline' || estado === 'syncing' || estado === 'error'
			? estado
			: pendientes > 0 ? 'pending' : 'idle'
	);

	const PUNTO = {
		offline: 'offline',
		syncing: 'syncing',
		error: 'error',
		pending: 'pending',
		idle: 'ok'
	};

	function texto() {
		switch (effective) {
			case 'offline': return 'Sin conexión — modo offline';
			case 'syncing': return 'Sincronizando...';
			case 'error': return 'Error al sincronizar — reintentando';
			case 'pending':
				return `${pendientes} pendiente${pendientes > 1 ? 's' : ''} — toca para sincronizar`;
			default: return 'Todo sincronizado';
		}
	}
</script>

<button
	class="sync-header"
	class:offline={effective === 'offline'}
	class:syncing={effective === 'syncing'}
	class:has-items={effective === 'pending'}
	onclick={() => onsync && onsync()}
>
	<span class="dot {PUNTO[effective] || 'ok'}"></span><span>{texto()}</span>
	{#if nombre}
		<span class="who">
			👤 {nombre}
			<span class="lugar {modo}" title={ipFinal}>{lug.icono} {lug.texto}</span>
		</span>
	{/if}
	<span class="vers">v{vApp} · shell {vShell}</span>
</button>
