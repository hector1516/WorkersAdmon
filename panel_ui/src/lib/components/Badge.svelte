<script>
	let { tone = '', children } = $props();
</script>

<span class="badge {tone ? `badge--${tone}` : ''}">{@render children?.()}</span>
