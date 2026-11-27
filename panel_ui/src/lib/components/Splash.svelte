<script>
	import Icon from './Icon.svelte';

	let { texto = 'Cargando panel…' } = $props();
</script>

<div class="splash" role="status" aria-live="polite">
	<div class="splash__brand">
		<Icon name="dashboard" size={26} />
		<span>Workers ECCSA</span>
	</div>
	<div class="splash__bar" aria-hidden="true"></div>
	<p class="splash__note">{texto}</p>
</div>
