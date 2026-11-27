<script>
	let {
		id = '',
		label = '',
		tipo = 'text',
		nombre = '',
		valor = $bindable(''),
		placeholder = '',
		hint = '',
		autocomplete = 'off',
		required = false,
		disabled = false,
		opciones = [],
		filas = 3
	} = $props();

	const campoId = $derived(id || `campo-${nombre || 'valor'}`);
	const descId = $derived(hint ? `${campoId}-hint` : undefined);
</script>

<div class="field">
	{#if label}<label for={campoId}>{label}</label>{/if}

	{#if tipo === 'textarea'}
		<textarea
			class="textarea"
			id={campoId}
			name={nombre || campoId}
			bind:value={valor}
			{placeholder}
			{required}
			{disabled}
			rows={filas}
			aria-describedby={descId}
		></textarea>
	{:else if tipo === 'select'}
		<select
			class="select"
			id={campoId}
			name={nombre || campoId}
			bind:value={valor}
			{required}
			{disabled}
			aria-describedby={descId}
		>
			{#each opciones as o (o.valor)}
				<option value={o.valor}>{o.texto}</option>
			{/each}
		</select>
	{:else}
		<input
			class="input"
			id={campoId}
			name={nombre || campoId}
			type={tipo}
			bind:value={valor}
			{placeholder}
			{autocomplete}
			{required}
			{disabled}
			aria-describedby={descId}
		/>
	{/if}

	{#if hint}<p class="hint" id={descId}>{hint}</p>{/if}
</div>
