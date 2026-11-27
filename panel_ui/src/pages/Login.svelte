<script>
	import Icon from '../lib/components/Icon.svelte';
	import Field from '../lib/components/Field.svelte';
	import Button from '../lib/components/Button.svelte';
	import Toast from '../lib/components/Toast.svelte';
	import { login } from '../lib/stores/auth.js';
	import { navigate } from '../lib/router.js';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let enviando = $state(false);

	async function enviar(e) {
		e.preventDefault();
		error = '';
		enviando = true;
		try {
			await login(email, password);
			navigate('/', { replace: true });
		} catch (err) {
			error = err?.message || 'No se pudo entrar';
		} finally {
			enviando = false;
		}
	}
</script>

<div class="auth__wrap">
	<div class="auth__brand">
		<Icon name="dashboard" size={22} />
		<span>Workers ECCSA</span>
	</div>

	<div class="card auth__card">
		<h1 class="auth__title">Entrar</h1>
		<p class="auth__sub">Panel de control de los workers</p>

		{#if error}<Toast tipo="err" mensaje={error} />{/if}

		<form onsubmit={enviar} novalidate>
			<Field
				id="login-email"
				label="Correo"
				tipo="email"
				nombre="email"
				placeholder="tu@ecc-sa.com.mx"
				autocomplete="username"
				required
				bind:valor={email}
			/>
			<Field
				id="login-password"
				label="Contraseña"
				tipo="password"
				nombre="password"
				autocomplete="current-password"
				required
				bind:valor={password}
			/>
			<Button tipo="submit" variant="primary" disabled={enviando} icono="login">
				{enviando ? 'Entrando…' : 'Entrar'}
			</Button>
		</form>
	</div>

	<p class="auth__foot">Misma cuenta del HUB · sesión por cookie</p>
</div>
