import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// A long-running Node server: it spawns ffmpeg and keeps a serving session warm.
			adapter: adapter()
		})
	],
	resolve: {
		alias: [
			// tw-animate-css is exported only under the `style` condition, which
			// @tailwindcss/vite 4.3 does not pass in Vite 8's SSR build, so
			// `vite build` cannot resolve layout.css. Point at the file directly.
			{
				find: /^tw-animate-css$/,
				replacement: fileURLToPath(
					new URL('./node_modules/tw-animate-css/dist/tw-animate.css', import.meta.url)
				)
			}
		]
	},
	ssr: { external: ['pdfkit'] }
});
