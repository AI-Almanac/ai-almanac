import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
	WorkspaceArtifact,
	WorkspaceDetail,
	WorkspaceEvent,
	WorkspaceJob
} from '../src/lib/api';

const api = vi.hoisted(() => ({
	getWorkspace: vi.fn(),
	getWorkspaceSummary: vi.fn(),
	hideWorkspaceAnalysis: vi.fn(),
	linkWorkspaceJob: vi.fn(),
	unhideWorkspaceAnalysis: vi.fn(),
	watchWorkspace: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

const settingsApi = vi.hoisted(() => ({
	getEffectiveWorkspaceSettings: vi.fn(),
	updateWorkspaceSettings: vi.fn()
}));
vi.mock('../src/lib/api/workspace-settings', () => settingsApi);

import { WorkspaceSession } from '../src/lib/workspace/workspace-session.svelte';

function job(id: string, status = 'complete'): WorkspaceJob {
	return { id, status, run_id: 'run-1', created_at: '2026-09-22T00:00:00Z' } as WorkspaceJob;
}

function snapshot(jobs: WorkspaceJob[]): WorkspaceDetail {
	return { id: 'ws-1', name: 'Kiremt', settings: {}, jobs, artifacts: [], analyses: [] } as never;
}

function analysisFile(id: string, turnId: string): WorkspaceArtifact {
	return { id, turn_id: turnId, job_id: null, created_at: '2026-09-22T00:00:00Z' } as never;
}

function withAnalyses(hidden: string[]): WorkspaceDetail {
	return {
		...snapshot([]),
		artifacts: [analysisFile('wrong-map', 'turn-wrong'), analysisFile('map', 'turn-right')],
		hidden_analyses: hidden
	};
}

const shownTurns = (session: WorkspaceSession) =>
	session.results.analyses.map((analysis) => analysis.turnId);
const hiddenTurns = (session: WorkspaceSession) =>
	session.hiddenAnalyses.map((analysis) => analysis.turnId);

/** Opens the session and hands back what the event stream would call with each update. */
async function opened(session: WorkspaceSession) {
	let send: (event: WorkspaceEvent) => void = () => {};
	api.watchWorkspace.mockImplementation(async (_id, _since, onEvent) => {
		send = onEvent;
	});
	await session.open(new AbortController().signal);
	return (event: WorkspaceEvent) => send(event);
}

describe('a workspace kept current', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		api.getWorkspace.mockResolvedValue(snapshot([job('a')]));
		api.getWorkspaceSummary.mockResolvedValue({ season: {} });
	});

	it('loads the workspace and its runs, then applies the updates that stream in', async () => {
		const session = new WorkspaceSession('ws-1');
		const send = await opened(session);

		expect(session.results.runs.map((run) => run.key)).toEqual(['run-1']);
		send({ type: 'jobs', jobs: [job('a'), { ...job('b'), run_id: 'run-2' }] });
		send({ type: 'follow_ups', follow_ups: [{ id: 'f1', status: 'waiting' }] } as never);

		expect(session.results.runs).toHaveLength(2);
		expect(session.followUps.map((followUp) => followUp.id)).toEqual(['f1']);
		// A run finishing changes the headline facts.
		expect(api.getWorkspaceSummary).toHaveBeenCalledTimes(2);
	});

	it('reads the workspace again when an update lands while it is being read', async () => {
		const session = new WorkspaceSession('ws-1');
		const send = await opened(session);
		let finishRead: (value: WorkspaceDetail) => void = () => {};
		api.getWorkspace
			.mockImplementationOnce(() => new Promise((resolve) => (finishRead = resolve)))
			.mockResolvedValueOnce(snapshot([job('a'), job('b')]));

		const refreshed = session.refresh();
		send({ type: 'jobs', jobs: [job('a'), job('b')] });
		finishRead(snapshot([job('a', 'running')]));
		await refreshed;

		expect(session.workspace?.jobs.map((each) => each.status)).toEqual(['complete', 'complete']);
	});

	it('says why the workspace could not be loaded', async () => {
		api.getWorkspace.mockRejectedValue(new Error('Workspace not found'));
		const session = new WorkspaceSession('ws-1');

		await opened(session);

		expect(session.workspace).toBeNull();
		expect(session.error).toBe('Workspace not found');
	});

	it('saves a setting and reads the headline facts again', async () => {
		const season = { name: 'Belg' };
		settingsApi.updateWorkspaceSettings.mockResolvedValue({ season });
		const session = new WorkspaceSession('ws-1');
		await opened(session);

		await session.saveSettings({ season } as never);

		expect(settingsApi.updateWorkspaceSettings).toHaveBeenCalledWith('ws-1', { season });
		expect(session.workspace?.settings).toEqual({ season });
		expect(api.getWorkspaceSummary).toHaveBeenCalledTimes(2);
	});

	it('edits the season in force, the platform’s when the workspace has none', async () => {
		settingsApi.getEffectiveWorkspaceSettings.mockResolvedValue({ season: { name: 'Kiremt' } });
		const session = new WorkspaceSession('ws-1');
		await opened(session);

		expect(await session.seasonInForce()).toEqual({ name: 'Kiremt' });
	});

	it('puts runs into the workspace one job at a time and reads it again', async () => {
		const session = new WorkspaceSession('ws-1');
		await opened(session);

		await session.attach(['x', 'y'], 'started');

		expect(api.linkWorkspaceJob.mock.calls).toEqual([
			['ws-1', 'x', 'started'],
			['ws-1', 'y', 'started']
		]);
		expect(api.getWorkspace).toHaveBeenCalledTimes(2);
	});

	it('leaves a hidden analysis out of the results until it is shown again', async () => {
		api.getWorkspace.mockResolvedValue(withAnalyses([]));
		const session = new WorkspaceSession('ws-1');
		await opened(session);

		await session.setAnalysisHidden('turn-wrong', true);
		const whileHidden = { shown: shownTurns(session), hidden: hiddenTurns(session) };
		await session.setAnalysisHidden('turn-wrong', false);

		expect(api.hideWorkspaceAnalysis).toHaveBeenCalledWith('ws-1', 'turn-wrong');
		expect(api.unhideWorkspaceAnalysis).toHaveBeenCalledWith('ws-1', 'turn-wrong');
		expect(whileHidden).toEqual({ shown: ['turn-right'], hidden: ['turn-wrong'] });
		expect(hiddenTurns(session)).toEqual([]);
		expect(shownTurns(session)).toContain('turn-wrong');
	});

	it('keeps an analysis listed when hiding it fails', async () => {
		api.getWorkspace.mockResolvedValue(withAnalyses([]));
		api.hideWorkspaceAnalysis.mockRejectedValueOnce(new Error('offline'));
		const session = new WorkspaceSession('ws-1');
		await opened(session);

		await expect(session.setAnalysisHidden('turn-wrong', true)).rejects.toThrow('offline');

		expect(hiddenTurns(session)).toEqual([]);
	});

	it('hides an analysis another page hid', async () => {
		api.getWorkspace.mockResolvedValue(withAnalyses([]));
		const session = new WorkspaceSession('ws-1');
		const send = await opened(session);

		send({ type: 'analyses', analyses: [], hidden_analyses: ['turn-wrong'] });

		expect(shownTurns(session)).toEqual(['turn-right']);
		expect(hiddenTurns(session)).toEqual(['turn-wrong']);
	});
});
