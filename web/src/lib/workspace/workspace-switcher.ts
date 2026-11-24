/** Finding a workspace to switch to among the user's own. */

type Listed = { name: string; description: string };

/** Past this many workspaces the switcher offers a filter. */
export const FILTER_FROM = 8;

/** The workspaces whose name or description mention every word typed, in the order listed. */
export function matchingWorkspaces<T extends Listed>(workspaces: T[], query: string): T[] {
	const words = query.toLowerCase().split(/\s+/).filter(Boolean);
	if (words.length === 0) return workspaces;
	return workspaces.filter((ws) => {
		const text = `${ws.name} ${ws.description}`.toLowerCase();
		return words.every((word) => text.includes(word));
	});
}
