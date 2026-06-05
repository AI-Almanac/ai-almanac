// No-op auth store — the app has no built-in auth, so `isAuthenticated` is
// always true and the existing gated routes stay accessible.

import { readable } from 'svelte/store';

export const isAuthenticated = readable<boolean>(true);

export const currentUser = readable<{ name: string; email: string | null }>({
	name: 'You',
	email: null
});
