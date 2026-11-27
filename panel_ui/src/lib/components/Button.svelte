<script>
	import Icon from './Icon.svelte';

	let {
		variant = '',
		tam = '',
		tipo = 'button',
		href = '',
		disabled = false,
		icono = '',
		onclick,
		children
	} = $props();

	let clase = $derived(
		['btn', variant && `btn--${variant}`, tam && `btn--${tam}`].filter(Boolean).join(' ')
	);
</script>

{#if href}
	<a class={clase} {href} onclick={onclick}>
		{#if icono}<Icon name={icono} size={18} />{/if}
		{@render children?.()}
	</a>
{:else}
	<button class={clase} type={tipo} {disabled} onclick={onclick}>
		{#if icono}<Icon name={icono} size={18} />{/if}
		{@render children?.()}
	</button>
{/if}
