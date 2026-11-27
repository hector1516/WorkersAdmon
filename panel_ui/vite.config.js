import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// El panel se sirve desde la raíz del subdominio: Python entrega dist/index.html
// para cualquier ruta sin prefijo /api, /static, /sw.js, etc.
export default defineConfig({
	base: '/',
	plugins: [svelte()],
	// Vitest resuelve los módulos para Node por defecto; `svelte` tiene build
	// de servidor y build de cliente, y `mount()` solo existe en el de cliente.
	resolve: { conditions: ['browser'] },
	build: { outDir: 'dist' },
	test: { environment: 'jsdom', setupFiles: ['./src/test-setup.js'] }
});
