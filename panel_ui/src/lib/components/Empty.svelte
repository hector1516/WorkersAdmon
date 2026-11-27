<script>
	import Icon from './Icon.svelte';

	let { icono = 'inbox', titulo = 'Sin datos', texto = '', children } = $props();
</script>

<div class="empty">
	<Icon name={icono} size={32} />
	<strong>{titulo}</strong>
	{#if texto}<p>{texto}</p>{/if}
	{@render children?.()}
</div>
