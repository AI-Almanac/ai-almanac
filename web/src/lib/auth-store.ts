import { browser } from '$app/environment';
import { readable } from 'svelte/store';
import { getManager, hasApiAccessToken } from './auth';

export const isAuthenticated = readable<boolean>(false, (set) => {
	if (!browser) return;

	const manager = getManager();
	if (!manager) return;

	set(manager.authenticated && hasApiAccessToken());
	return manager.events.authenticated.addListener(({ isAuthenticated }) => {
		set(isAuthenticated && hasApiAccessToken());
	});
});
