// ---- Filesystem browser -----------------------------------------------------
import { request } from './core';

export interface FsEntry {
	name: string;
	kind: 'file' | 'dir';
	size: number | null;
	is_hidden: boolean;
}

export interface FsListing {
	path: string;
	parent: string | null;
	entries: FsEntry[];
}

export interface QuickPath {
	label: string;
	path: string;
}

export async function fsList(path = '', includeHidden = false): Promise<FsListing> {
	const params = new URLSearchParams();
	if (path) params.set('path', path);
	if (includeHidden) params.set('include_hidden', 'true');
	const q = params.toString();
	return request<FsListing>(`/fs/list${q ? '?' + q : ''}`);
}

export async function fsQuickPaths(): Promise<QuickPath[]> {
	return request<QuickPath[]>('/fs/quick-paths');
}
