import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getArtifactProvenance: vi.fn() }));
vi.mock('../src/lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('../src/lib/api')>()),
	getArtifactProvenance: api.getArtifactProvenance
}));
vi.mock('../src/lib/components/workspace/FileContents.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/workspace/LineageView.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

import type { ArtifactProvenance } from '../src/lib/api';
import ArtifactDetail from '../src/lib/components/workspace/ArtifactDetail.svelte';
import { provenanceStep, workspaceArtifact } from './fixtures/workspace';

function provenance(verification: 'verified' | 'hash_mismatch'): ArtifactProvenance {
	return { steps: [provenanceStep({ verification })] } as unknown as ArtifactProvenance;
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
}

const props = (id: string) => ({
	workspaceId: 'ws-1',
	file: workspaceArtifact({ id, kind: 'table', format: 'csv' }),
	onDownload: () => {}
});

describe('a file’s integrity badge', () => {
	it('never shows a file that is no longer open, however late its check answers', async () => {
		const slow = deferred<ArtifactProvenance>();
		api.getArtifactProvenance
			.mockReturnValueOnce(slow.promise)
			.mockResolvedValueOnce(provenance('hash_mismatch'));
		const { rerender } = render(ArtifactDetail, props('art-a'));

		await rerender(props('art-b'));
		expect(await screen.findByText('Changed since it was recorded')).toBeTruthy();
		slow.resolve(provenance('verified'));

		await new Promise((done) => setTimeout(done, 0));
		expect(screen.queryByText('Files match the record')).toBeNull();
	});

	it('does not keep an earlier pass after checking again fails', async () => {
		api.getArtifactProvenance
			.mockResolvedValueOnce(provenance('verified'))
			.mockRejectedValueOnce(new Error('offline'));
		render(ArtifactDetail, props('art-a'));
		expect(await screen.findByText('Files match the record')).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: 'Verify again' }));

		await waitFor(() => expect(screen.queryByText('Files match the record')).toBeNull());
	});
});
