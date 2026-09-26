/* WorkersAdmon Service Worker — espejo de Field/Admon (PWA del panel).
 *
 * REGLA DURA (lección de Field, docs/TROUBLESHOOTING.md §1): SOLO interceptar
 * GET. Nunca responder a un POST/PUT/PATCH con la petición de red tal cual:
 * el SW pierde el cuerpo y el backend recibe size=0 → 422.
 *
 * Estrategia:
 *   · Navegaciones (HTML) → SIEMPRE red; si no hay red → /offline.
 *     No se cachea HTML autenticado: evita servir contenido de otra sesión
 *     en una máquina compartida.
 *   · /api/status, /healthz, login y passkey → network-only (nunca en caché).
 *   · Estáticos (iconos, logo, manifest) → caché primero con revalidación.
 */
const CACHE = 'workers-v1';
const OFFLINE = '/offline';
const APP_SHELL = [
	OFFLINE,
	'/manifest.webmanifest',
	'/logo.png',
	'/icons/icon-192x192.png',
	'/icons/icon-512x512.png',
	'/icons/apple-touch-icon.png'
];

// Prefijos que jamás se cachean (datos vivos o con sesión/CSRF).
const NETWORK_ONLY = ['/api/status', '/healthz', '/login', '/logout', '/passkey/'];

self.addEventListener('install', (e) => {
	e.waitUntil(
		caches.open(CACHE).then((c) =>
			Promise.all(APP_SHELL.map((u) => c.add(u).catch(() => {})))
		).then(() => self.skipWaiting())
	);
});

self.addEventListener('activate', (e) => {
	e.waitUntil(
		caches.keys()
			.then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
			.then(() => self.clients.claim())
	);
});

self.addEventListener('message', (e) => {
	if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', (e) => {
	const { request } = e;

	// SOLO GET: todo lo demás pasa directo a la red (regla dura, ver cabecera).
	if (request.method !== 'GET') return;

	const url = new URL(request.url);
	if (url.origin !== self.location.origin) return;

	// API / login / passkey → siempre red, sin cachear.
	if (NETWORK_ONLY.some((p) => url.pathname === p || url.pathname.startsWith(p))) {
		return;
	}

	// Navegaciones: red primero; sin conexión → página offline estática.
	if (request.mode === 'navigate') {
		e.respondWith(
			fetch(request).catch(() =>
				caches.match(OFFLINE).then((r) => r || new Response(
					'Sin conexión', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }
				))
			)
		);
		return;
	}

	// Estáticos: caché primero y revalidación en segundo plano.
	e.respondWith(
		caches.match(request).then((cached) => {
			const network = fetch(request).then((res) => {
				if (res && res.ok) {
					const copy = res.clone();
					caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
				}
				return res;
			}).catch(() => cached);
			return cached || network;
		})
	);
});
