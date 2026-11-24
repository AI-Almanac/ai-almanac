/**
 * Which workspace the Workspaces tab opens: the one last open in this browser,
 * so coming back lands where the user left off.
 */

type Listed = { id: string };

/** The last-opened workspace if it is still the user's, else the most recently updated one. */
export function workspaceToOpen(lastOpened: string | null, workspaces: Listed[]): string | null {
	if (lastOpened && workspaces.some((ws) => ws.id === lastOpened)) return lastOpened;
	return workspaces[0]?.id ?? null;
}

const storageKey = (userId: string | null) => `workspace.lastOpened.${userId ?? 'local'}`;

export function lastOpenedWorkspace(storage: Storage, userId: string | null): string | null {
	return storage.getItem(storageKey(userId));
}

export function rememberOpenedWorkspace(storage: Storage, userId: string | null, id: string) {
	storage.setItem(storageKey(userId), id);
}
