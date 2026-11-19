import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	MGA_API_ENDPOINT: {
		description: 'Archetype AI API endpoint.',
		schema: (value) => value || 'https://platform.u1.archetypeai.app/api'
	},
	MGA_API_KEY: {
		description: 'Optional API key. A key pasted in the app takes precedence.',
		schema: (value) => value?.trim() || ''
	}
});
