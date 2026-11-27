<script>
	let { titulo = '', sub = '', clase = '', children, acciones } = $props();
</script>

<section class="card {clase}">
	{#if titulo || acciones}
		<header class="card__head">
			<div class="card__titles">
				{#if titulo}<h2 class="card__title">{titulo}</h2>{/if}
				{#if sub}<p class="card__sub">{sub}</p>{/if}
			</div>
			{#if acciones}<div class="card__actions">{@render acciones()}</div>{/if}
		</header>
	{/if}
	{@render children?.()}
</section>
