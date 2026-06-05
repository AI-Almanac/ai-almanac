import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter({
			// Single-page-app mode: every route loads `index.html`, then
			// client-side routing takes over. Required because the FastAPI
			// backend serves the built bundle from any URL.
			fallback: 'index.html',
			pages: 'build',
			assets: 'build',
			precompress: false,
			strict: true
		})
	}
};

export default config;
