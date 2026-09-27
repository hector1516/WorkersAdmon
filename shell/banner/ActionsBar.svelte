<script>
	/* ECCSA-Shell · barra de acciones (referencia Svelte). ESTÁNDAR.
	   ------------------------------------------------------------------
	   Los botones de siempre: 👥 usuarios en línea · 🔔 notificaciones ·
	   ⚙️ configuración · 🚪 salir. Nació del header de Field
	   (src/routes/+page.svelte), que los tenía copiados en el <style> de esa
	   sola página — por eso no había forma de salir desde /reportes, y Admon
	   no tenía 🔔. Acá los cuatro son parte del shell.

	   Mismo reparto que el banner: el shell pone markup, clases y textos; la
	   app pasa los números y los manejadores. No importa nada del proyecto.

	   Regla: un botón se pinta solo si su manejador está. Así cada app decide
	   qué expone —el panel, por ejemplo, ya tiene Configuración y Notificaciones
	   como pestañas, y ahí se pasan en null para no duplicarlas.

	   Props:
	     enLinea        number   cuántos usuarios hay en línea
	     notificaciones number   cuántos avisos/avisos sin leer
	     ononline       () => any   👥  (null = no se pinta)
	     onnotif        () => any   🔔  (null = no se pinta)
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
	{#if onnotif}
		<button class="btn btn-sm btn-secondary act-btn" onclick={onnotif} title="Notificaciones">
			🔔{#if notificaciones > 0}<span class="act-badge alert">{notificaciones}</span>{/if}
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
