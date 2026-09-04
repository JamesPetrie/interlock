import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// DEMO_SERVER is where demo_server.py is listening. `pnpm dev` proxies the
// socket.io handshake there so the dev server behaves like the built bundle,
// which is served by demo_server itself and is therefore same-origin.
const DEMO_SERVER = process.env.DEMO_SERVER ?? 'http://127.0.0.1:8770';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		host: true,
		proxy: { '/socket.io': { target: DEMO_SERVER, ws: true, changeOrigin: true } }
	}
});
