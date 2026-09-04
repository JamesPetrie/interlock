// Upstream ships adapter-cloudflare. The interlock demo is served by the Spark's
// own demo_server off the local network with no internet, so this build uses
// adapter-static in SPA mode instead. Set ADAPTER=cloudflare to get upstream's.
import cloudflare from '@sveltejs/adapter-cloudflare';
import staticAdapter from '@sveltejs/adapter-static';

const adapter =
	process.env.ADAPTER === 'cloudflare'
		? cloudflare
		: () => staticAdapter({ fallback: 'index.html', strict: false });

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter: adapter(),
		env: {
			publicPrefix: '',
			dir: '..'
		}
	}
};

export default config;
