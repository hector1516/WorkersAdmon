<script>
	/* ECCSA-Shell · barra de acciones (referencia Svelte). ESTÁNDAR.
	   ------------------------------------------------------------------
	   Los botones de siempre: 👥 usuarios en línea · 📋 novedades ·
	   ⚙️ configuración · 🚪 salir. Nació del header de Field
	   (src/routes/+page.svelte), que los tenía copiados en el <style> de esa
	   sola página — por eso no había forma de salir desde /reportes.

	   Mismo reparto que el banner: el shell pone markup, clases y textos; la
	   app pasa los números y los manejadores. No importa nada del proyecto.

	   🔔 QUITADO del contrato (2026-09-27): ya no se usa. Las notificaciones
	   viven donde corresponde a cada app, no en la barra global; el panel
	   las tenía como pestaña y quedaba duplicado. La prop `onnotif` y el
	   badge `.act-badge.alert` quedan en el CSS por compatibilidad, pero el
	   shell ya no las emite. Si alguna app los necesita, que use su propio
	   markup, no el del shell.

	   Regla: un botón se pinta solo si su manejador está. Así cada app decide
	   qué expone —el panel, por ejemplo, ya tiene Configuración como pestaña, y
	   ahí se pasa en null para no duplicarla.

	   Props:
	     enLinea        number   cuántos usuarios hay en línea
	     notificaciones number   (deprecada: ya no se pinta)
	     ononline       () => any   👥  (null = no se pinta)
	     onnotif        () => any   🔔  (deprecada: ya no se pinta)
	     onchangelog    () => any   📋  (por defecto abre el popup de novedades)
	     onconfig       () => any   ⚙️  (null = no se pinta)
	     onlogout       () => any   🚪  (null = no se pinta)
	*/
	import { abrirChangelog } from '$lib/changelog.js';

	let {
		enLinea = 0,
		notificaciones = 0,
		ononline = null,
		onnotif = null,
		// El 📋 sí viene con manejador por defecto: si la app montó <Changelog>
		// en su layout, el botón funciona solo, sin cablearlo en cada página.
		onchangelog = abrirChangelog,
		onconfig = null,
		onlogout = null
	} = $props();
</script>

<div class="shell-actions">
	{#if ononline}
		<button class="btn btn-sm btn-secondary act-btn" onclick={ononline} title="Usuarios en línea">
			👥{#if enLinea > 0}<span class="act-badge">{enLinea}</span>{/if}
		</button>
	{/if}
	{#if onchangelog}
		<button class="btn btn-sm btn-secondary" onclick={onchangelog} title="Novedades">📋</button>
	{/if}
	{#if onconfig}
		<button class="btn btn-sm btn-secondary" onclick={onconfig} title="Configuración">⚙️</button>
	{/if}
	{#if onlogout}
		<button class="btn btn-sm btn-secondary" onclick={onlogout} title="Cerrar sesión">🚪 Salir</button>
	{/if}
</div>
