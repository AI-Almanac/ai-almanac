/** What saving a name field asks for: nothing, or a new name (`null` restores the default). */
export type NameEdit = { kind: 'unchanged' } | { kind: 'rename'; title: string | null };

export function nameEdit(draft: string, current: string): NameEdit {
	const title = draft.trim();
	if (title === current.trim()) return { kind: 'unchanged' };
	return { kind: 'rename', title: title || null };
}
