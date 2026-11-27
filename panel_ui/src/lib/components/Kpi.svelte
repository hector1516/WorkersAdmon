<script>
	import Icon from './Icon.svelte';

	let { label = '', value = 0, hint = '', tone = '', icono = '' } = $props();
</script>

<div class="kpi {tone ? `kpi--${tone}` : ''}">
	<div class="kpi__label">
		{#if icono}<Icon name={icono} size={16} />{/if}
		{label}
	</div>
	<div class="kpi__value">{value}</div>
	{#if hint}<div class="kpi__hint">{hint}</div>{/if}
</div>
