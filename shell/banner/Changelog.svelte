<script>
	/* ECCSA-Shell · popup de novedades (changelog). ESTÁNDAR.
	   ------------------------------------------------------------------
	   Salta SOLO la primera vez que se ve una versión nueva (el "¿ya te
	  ISHED mostré esta?" queda en localStorage) y después se vuelve a abrir
	   con el botón 📋 de la barra de acciones. Nació del CHANGELOG.md que ya
	   tenía Field; los de Admon y el panel se generaron después.

	   Mismo reparto que el banner y la barra de acciones: el shell pone el
	   modal, la lógica de "solo una vez" y los estilos; la app pone QUÉ
	   cambió. El contenido llega como prop para que no haya que ir a buscar un
	   archivo ni parsear markdown en el cliente.

	   Montar este componente UNA vez en el layout (no en cada página) es lo
	   que hace que el popup salte aunque se entre por cualquier ruta.

	   Props:
	     appId    string    'field' | 'admon' — la clave en localStorage
	     appName  string    'Field' — para el título
	     version  string    la versión de la app (normalmente $lib/shell.js)
	     cambios  string[]  qué cambió en esta versión
	     url      string    alternativa a `cambios`: una URL de JSON con
	                       {app, version, cambios}. Se pide al montar, así el
	                       texto se puede editar sin recompilar la app.
	*/
	import { onMount } from 'svelte';
	import {
		changelogAbierto,
		cerrarChangelog,
		versionYaVista,
		marcarVersionVista
	} from '$lib/changelog.js';

	let {
		appId = 'app',
		appName = '',
		version = '',
		cambios = [],
		url = ''
	} = $props();

	let lista = $state(Array.isArray(cambios) ? cambios.filter(Boolean) : []);
	/* El prop `version` es de solo lectura (viene de $props), así que si el
	   JSON trae su propia versión se guarda aparte y manda esa. */
	let versionLocal = $state(version);

	onMount(async () => {
		if (url) {
			try {
				const r = await fetch(url, { credentials: 'same-origin' });
				if (r.ok) {
					const d = await r.json();
					lista = (Array.isArray(d.cambios) ? d.cambios : []).filter(Boolean);
					// El JSON manda: si trae versión, es la que decide si saltamos.
					if (d.version) versionLocal = d.version;
				}
			} catch (e) {
				/* fail silent: sin changelog no hay popup, la app sigue igual */
			}
		}
		// Si no hay versión o no hay nada que contar, no molestamos al usuario.
		if (!versionLocal || !lista.length) return;
		// ¿Ya le mostramos esta versión? Entonces no saltamos solo: el popup
		// sigue disponible con el botón 📋, que es lo que espera el usuario.
		if (versionYaVista(appId) === versionLocal) return;
		changelogAbierto.set(true);
		// Se marca AL MOSTRARSE, no al cerrarse: si lo cierra sin leer, ya lo
		// vio y no tiene sentido insistir en la próxima carga.
		marcarVersionVista(appId, versionLocal);
	});

	function alTocarFuera(ev) {
		// Tocar el fondo cierra; tocar la tarjeta no.
		if (ev.target === ev.currentTarget) cerrarChangelog();
	}

	function alTeclear(ev) {
		if (ev.key === 'Escape') cerrarChangelog();
	}
</script>

<svelte:window onkeydown={alTeclear} />

{#if $changelogAbierto}
	<div class="shell-modal" role="dialog" aria-modal="true"
	     aria-label="Novedades de {appName}" onclick={alTocarFuera}>
		<div class="shell-modal-card">
			<div class="shell-modal-hd">
				<h2>📋 Novedades</h2>
				{#if versionLocal}<span class="shell-modal-ver">v{versionLocal}</span>{/if}
			</div>
			<p class="shell-modal-sub">
				{#if appName}Cambios de {appName}{:else}Cambios{/if}
			</p>

			{#if lista.length}
				<ul class="shell-modal-list">
					{#each lista as cambio, i (i)}
						<li>{cambio}</li>
					{/each}
				</ul>
			{:else}
				<p class="shell-modal-empty">No hay novedades registradas todavía.</p>
			{/if}

			<button class="btn btn-primary btn-block" onclick={cerrarChangelog}>
				Entendido
			</button>
		</div>
	</div>
{/if}
