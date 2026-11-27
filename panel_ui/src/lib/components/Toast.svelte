<script>
	import Icon from './Icon.svelte';

	// Aviso en el flujo o flotante. `tipo`: 'ok' | 'err' | 'info'.
	let { tipo = 'info', mensaje = '', oncerrar = null } = $props();
</script>

{#if mensaje}
	<div
		class="toast {tipo === 'ok' ? 'toast--ok' : tipo === 'err' ? 'toast--err' : ''}"
		role={tipo === 'err' ? 'alert' : 'status'}
	>
		<Icon name={tipo === 'err' ? 'alert' : tipo === 'ok' ? 'check' : 'inbox'} size={18} />
		<span>{mensaje}</span>
		{#if oncerrar}
			<button class="btn btn--quiet btn--sm toast__cerrar" type="button" onclick={oncerrar} aria-label="Cerrar aviso">
				<Icon name="x" size={16} />
			</button>
		{/if}
	</div>
{/if}
