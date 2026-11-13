import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkspacePage from '../src/routes/workspace/[id]/+page.svelte';
import {
	ApiError,
	type ArtifactProvenance,
	type WorkspaceArtifact,
	type WorkspaceDetail,
	type WorkspaceEvent
} from '../src/lib/api';
import { metricsFor, provenanceStep, workspaceArtifact, workspaceJob } from './fixtures/workspace';

const nav = vi.hoisted(() => ({ goto: vi.fn(), url: 'http://localhost/workspace/ws-1' }));

// Read nav.url per subscription so each test can start at its own URL.
vi.mock('$app/stores', () => ({
	page: {
		subscribe: (run: (value: unknown) => void) =>
			readable({ params: { id: 'ws-1' }, url: new URL(nav.url) }).subscribe(run)
	}
}));
vi.mock('$app/navigation', () => ({ goto: nav.goto }));

vi.mock('../src/lib/components/ChatPanel.svelte', async () => ({
	default: (await import('./fixtures/ChatPanelStub.svelte')).default
}));
vi.mock('../src/lib/components/SplitResizer.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/ResultsViewer.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/AllMetricsPanel.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/blend/BlendSkillPanel.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/workspace/NewBlendPanel.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/benchmark-setup/BenchmarkForm.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

const api = vi.hoisted(() => ({
	getWorkspace: vi.fn(),
	getWorkspaceSummary: vi.fn(),
	linkWorkspaceJob: vi.fn(),
	listAddableJobs: vi.fn(),
	getRegions: vi.fn(),
	getDatasets: vi.fn(),
	getRompDefaults: vi.fn(),
	getGuardrailThresholds: vi.fn(),
	getCapabilities: vi.fn(),
	getArtifactProvenance: vi.fn(),
	getJob: vi.fn(),
	getJobMetrics: vi.fn(),
	getMetricDefinitions: vi.fn(),
	watchWorkspace: vi.fn(),
	renameWorkspaceArtifact: vi.fn(),
	renameWorkspaceAnalysis: vi.fn()
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

const workspace: WorkspaceDetail = {
	id: 'ws-1',
	name: 'Ethiopia 2026',
	description: '',
	created_at: '2026-09-22T00:00:00Z',
	updated_at: '2026-09-22T00:00:00Z',
	settings: {},
	jobs: [
		workspaceJob({ id: 'job-1', run_id: 'run-1', completed_at: '2026-09-22T00:00:00Z' }),
		workspaceJob({ id: 'job-2', run_id: 'run-1', model_name: 'fuxi', status: 'running' })
	],
	artifacts: [
		workspaceArtifact({
			id: 'art-1',
			label: 'job1_spatial_metrics_AIFS_1-15',
			title: 'AIFS · days 1–15',
			group: 'Spatial metrics',
			job_id: 'job-1',
			step_id: 'step-1',
			run_job_ids: ['job-1'],
			kind: 'metrics',
			format: 'netcdf',
			breadcrumb: ['benchmark.job'],
			summary: {
				dims: { lat: 10, lon: 12 },
				variables: { far: { dims: ['lat', 'lon'], long_name: 'False alarm rate', units: '1' } }
			}
		}),
		workspaceArtifact({
			id: 'art-2',
			label: 'far_map',
			title: 'False alarm rate map',
			group: 'Figures',
			step_id: 'step-2',
			turn_id: 'turn-abcdef12',
			run_job_ids: ['job-1'],
			breadcrumb: ['benchmark.job', 'figure.plot_map']
		})
	],
	analyses: []
};

const areaOutlines: WorkspaceArtifact = {
	...workspace.artifacts[1],
	id: 'art-3',
	label: 'adm1_outlines',
	title: 'Region outlines',
	kind: 'table',
	format: 'geojson',
	run_job_ids: [],
	supporting: true
};

const laterRun: WorkspaceDetail['jobs'][number] = {
	...workspace.jobs[0],
	id: 'job-3',
	run_id: 'run-2',
	status: 'running',
	model_name: 'graphcast',
	created_at: '2026-09-23T00:00:00Z',
	completed_at: null,
	scope: { place: 'Kenya long rains onset', years: null, lead_days: null }
};

const provenance: ArtifactProvenance = {
	artifact: {
		id: 'art-2',
		label: 'far_map',
		filename: 'far_map.png',
		kind: 'figure',
		format: 'png',
		content_hash: 'abc',
		provenance_step_id: 'step-2'
	},
	artifacts: [],
	producers: ['step-2'],
	lineage: '',
	steps: [
		provenanceStep({
			id: 'step-2',
			completed_at: '2026-09-22T00:00:00Z',
			primitive: 'figure.plot_map',
			description: 'Plotted the false alarm rate map for AIFS, days 1–15.',
			params: { variable: 'false_alarm_rate', artifact: 'job1_spatial_metrics' },
			display_params: { variable: 'false_alarm_rate' },
			outputs: ['art-2'],
			turn_id: 'turn-abcdef12',
			model_name: 'claude-sonnet-4-6'
		})
	]
};

beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	// Wide enough for the products list to start open.
	window.innerWidth = 1440;
	nav.url = 'http://localhost/workspace/ws-1';
	api.getWorkspace.mockResolvedValue(workspace);
	// Most tests run without the header's summary, as when it cannot be read.
	api.getWorkspaceSummary.mockRejectedValue(new Error('no summary'));
	api.linkWorkspaceJob.mockResolvedValue([]);
	api.listAddableJobs.mockResolvedValue([]);
	api.getRegions.mockResolvedValue([]);
	api.getDatasets.mockResolvedValue([]);
	api.getRompDefaults.mockResolvedValue(null);
	api.getGuardrailThresholds.mockResolvedValue(null);
	api.getCapabilities.mockResolvedValue({ chat: false });
	api.getArtifactProvenance.mockResolvedValue(provenance);
	api.getJob.mockImplementation(async (id: string) => ({ id, status: 'complete' }));
	api.getMetricDefinitions.mockResolvedValue([]);
	api.getJobMetrics.mockImplementation(async (id: string) =>
		metricsFor(id, 'aifs', { '1-15': 6.4 }, { '1-15': 11.9 })
	);
	api.watchWorkspace.mockImplementation(
		async (_id: string, _since: unknown, onEvent: (event: WorkspaceEvent) => void) => {
			workspaceEvents.send = onEvent;
		}
	);
});

const workspaceEvents: { send: (event: WorkspaceEvent) => void } = { send: () => {} };

/** The products list; the stage beside it repeats the shown run's title and lists its files. */
const productsList = () => screen.getByRole('navigation', { name: 'Products' });

async function enterName(name: string, value: string, scope: () => HTMLElement = productsList) {
	await screen.findByRole('navigation', { name: 'Products' });
	await fireEvent.click(within(scope()).getByRole('button', { name: `Rename ${name}` }));
	const field = screen.getByRole('textbox', { name: /New name/ });
	await fireEvent.input(field, { target: { value } });
	await fireEvent.keyDown(field, { key: 'Enter' });
}

describe('workspace page', () => {
	it('shows the workspace season beside its name once one is set', async () => {
		api.getWorkspace.mockResolvedValue({
			...workspace,
			settings: {
				season: {
					name: 'Kiremt',
					onset_search_start: '05-01',
					reference_onset: '06-01',
					last_issue_date: '07-31'
				}
			}
		});
		render(WorkspacePage);

		expect(await screen.findByText('Kiremt · 1 May – 31 Jul')).toBeTruthy();
	});

	it('starts with the products collapsed to a strip on a narrower screen, and remembers how it was left', async () => {
		window.innerWidth = 1100;
		const first = render(WorkspacePage);

		const benchmarks = await screen.findByRole('button', { name: /^Benchmarks: 1\./ });
		expect(screen.queryByRole('searchbox', { name: 'Find a product' })).toBeNull();

		await fireEvent.click(benchmarks);
		expect(screen.getByRole('searchbox', { name: 'Find a product' })).toBeTruthy();

		first.unmount();
		const second = render(WorkspacePage);
		await fireEvent.click(
			await screen.findByRole('button', { name: 'Collapse the products list' })
		);
		expect(screen.getByRole('button', { name: /^Analyses: 1\./ })).toBeTruthy();
		// The stage is untouched by the list collapsing.
		expect(
			screen.getByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' })
		).toBeTruthy();

		second.unmount();
		render(WorkspacePage);
		expect(await screen.findByRole('button', { name: /^Analyses: 1\./ })).toBeTruthy();
	});

	it('lands the stage on the newest run, marked in the list with its status, with its finished results', async () => {
		render(WorkspacePage);

		expect(
			await screen.findByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' })
		).toBeTruthy();
		const shown = within(productsList()).getByRole('button', { current: true });
		expect(shown.textContent).toContain('Ethiopia Kiremt onset · AIFS, FuXi');
		expect(within(productsList()).getByText('Running · 1 of 2 done')).toBeTruthy();
		await waitFor(() => expect(api.getJob).toHaveBeenCalledWith('job-1'));
		expect(api.getJob).not.toHaveBeenCalledWith('job-2');
		expect(await screen.findByTestId('stub-component')).toBeTruthy();
		expect(screen.getByText(/1 model is still running/)).toBeTruthy();
	});

	it('opens a run from the list and records it in the URL', async () => {
		render(WorkspacePage);

		await screen.findByRole('navigation', { name: 'Products' });
		await fireEvent.click(within(productsList()).getByText('Ethiopia Kiremt onset · AIFS, FuXi'));

		expect(nav.goto).toHaveBeenCalledWith('?run=run-1', {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
		expect(
			screen.getByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' })
		).toBeTruthy();
	});

	it('restores a file from the URL, checks it against the record and shows how it was made', async () => {
		nav.url = 'http://localhost/workspace/ws-1?artifact=art-2';
		render(WorkspacePage);

		expect(await screen.findByRole('heading', { name: 'False alarm rate map' })).toBeTruthy();
		expect(await screen.findByText('Files match the record')).toBeTruthy();
		expect(api.getArtifactProvenance).toHaveBeenCalledWith('ws-1', 'art-2', true);

		await fireEvent.click(screen.getByRole('tab', { name: 'How this was made' }));
		expect(
			await screen.findByText('Plotted the false alarm rate map for AIFS, days 1–15.')
		).toBeTruthy();
		expect(screen.getAllByText(/Run by the assistant/).length).toBeGreaterThan(0);
		expect(
			screen.queryByText('Matches the record'),
			'only problems are called out per step'
		).toBeNull();
		expect(screen.queryByText(/This exact file was made/)).toBeNull();
		expect(screen.queryByText('Full trace')).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'Verify again' }));
		await waitFor(() => expect(api.getArtifactProvenance).toHaveBeenCalledTimes(2));
	});

	it.each([
		['hash_mismatch', 'Changed since it was recorded'],
		['input_missing', 'A source file is missing']
	])('warns inline when a check finds %s', async (verification, message) => {
		nav.url = 'http://localhost/workspace/ws-1?artifact=art-2';
		api.getArtifactProvenance.mockResolvedValue({
			...provenance,
			steps: [{ ...provenance.steps[0], verification }]
		});
		render(WorkspacePage);

		expect(await screen.findByText(message)).toBeTruthy();
		expect(screen.queryByText('Files match the record')).toBeNull();
	});

	it('shows a dataset file summary', async () => {
		nav.url = 'http://localhost/workspace/ws-1?artifact=art-1&run=run-1';
		render(WorkspacePage);

		expect(await screen.findByText('False alarm rate')).toBeTruthy();
		expect(screen.getByText('Variables (1)')).toBeTruthy();
		await fireEvent.click(screen.getByText('← Back to run'));
		expect(nav.goto).toHaveBeenLastCalledWith('?run=run-1', expect.anything());
	});

	it('heads an analysis with its name once the assistant names it', async () => {
		render(WorkspacePage);
		expect(await screen.findByText(/^Analysis from /)).toBeTruthy();

		workspaceEvents.send({
			type: 'analyses',
			analyses: [{ turn_id: 'turn-abcdef12', title: 'AIFS false alarms', named_by: 'assistant' }]
		});

		expect(await screen.findByText('AIFS false alarms')).toBeTruthy();
		expect(screen.queryByText(/^Analysis from /)).toBeNull();
	});

	it('opens an analysis on the stage, with its other files a press away there', async () => {
		api.getWorkspace.mockResolvedValue({
			...workspace,
			artifacts: [...workspace.artifacts, areaOutlines]
		});
		render(WorkspacePage);

		await screen.findByRole('navigation', { name: 'Products' });
		const entry = within(productsList()).getByText(/^Analysis from /);
		expect(within(productsList()).getByText('2 files')).toBeTruthy();
		await fireEvent.click(entry);

		expect(nav.goto).toHaveBeenLastCalledWith('?artifact=art-2', expect.anything());
		expect(await screen.findByRole('heading', { name: 'False alarm rate map' })).toBeTruthy();
		expect(within(productsList()).getByRole('button', { current: true }).textContent).toContain(
			'Analysis from'
		);

		const files = within(screen.getByRole('navigation', { name: 'Files in this analysis' }));
		await fireEvent.click(files.getByRole('button', { name: 'Region outlines' }));
		expect(nav.goto).toHaveBeenLastCalledWith('?artifact=art-3', expect.anything());
		expect(await screen.findByRole('heading', { name: 'Region outlines' })).toBeTruthy();
	});

	it('moves the stage to a product that arrives while nothing has been opened', async () => {
		render(WorkspacePage);
		await screen.findByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' });

		workspaceEvents.send({ type: 'jobs', jobs: [...workspace.jobs, laterRun] });

		expect(
			await screen.findByRole('heading', { name: 'Kenya long rains onset · GraphCast' })
		).toBeTruthy();
		expect(nav.goto).not.toHaveBeenCalled();
	});

	it('leaves the stage where it was opened when a product arrives', async () => {
		nav.url = 'http://localhost/workspace/ws-1?run=run-1';
		render(WorkspacePage);
		await screen.findByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' });

		workspaceEvents.send({ type: 'jobs', jobs: [...workspace.jobs, laterRun] });

		expect(
			await within(productsList()).findByText('Kenya long rains onset · GraphCast')
		).toBeTruthy();
		expect(
			screen.getByRole('heading', { name: 'Ethiopia Kiremt onset · AIFS, FuXi' })
		).toBeTruthy();
		expect(
			screen.queryByRole('heading', { name: 'Kenya long rains onset · GraphCast' })
		).toBeNull();
	});

	it('offers starting points instead of a products list while the workspace is empty', async () => {
		api.getWorkspace.mockResolvedValue({ ...workspace, jobs: [], artifacts: [] });
		render(WorkspacePage);

		expect(await screen.findByRole('heading', { name: 'Starting points' })).toBeTruthy();
		expect(screen.queryByRole('navigation', { name: 'Products' })).toBeNull();
		expect(screen.getByRole('button', { name: 'Run a benchmark' })).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Add an existing run' })).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Train a blend' })).toBeTruthy();
		expect(screen.queryByRole('link', { name: /Train a blend/ })).toBeNull();
		expect(screen.queryByRole('button', { name: /assistant/i })).toBeNull();
	});

	it.each([
		['Run a benchmark', 'New benchmark'],
		['Train a blend', 'New blend']
	])(
		'sets up from “%s” over the stage, without leaving the workspace or needing the assistant',
		async (start, panel) => {
			api.getWorkspace.mockResolvedValue({ ...workspace, jobs: [], artifacts: [] });
			render(WorkspacePage);

			await fireEvent.click(await screen.findByRole('button', { name: start }));

			expect(screen.getByRole('dialog', { name: panel })).toBeTruthy();

			expect(nav.goto).not.toHaveBeenCalled();
		}
	);

	it('opens the setup the assistant offers over the stage', async () => {
		api.getCapabilities.mockResolvedValue({ chat: true });
		render(WorkspacePage);

		await fireEvent.click(
			await screen.findByRole('button', { name: 'Assistant offers a blend setup' })
		);

		expect(await screen.findByRole('dialog', { name: 'New blend' })).toBeTruthy();
	});

	it('leaves a form the user has open alone when the assistant offers another', async () => {
		api.getCapabilities.mockResolvedValue({ chat: true });
		api.getWorkspace.mockResolvedValue({ ...workspace, jobs: [], artifacts: [] });
		render(WorkspacePage);
		await fireEvent.click(await screen.findByRole('button', { name: 'Run a benchmark' }));

		await fireEvent.click(screen.getByRole('button', { name: 'Assistant offers a blend setup' }));

		expect(screen.getByRole('dialog', { name: 'New benchmark' })).toBeTruthy();
		expect(screen.queryByRole('dialog', { name: 'New blend' })).toBeNull();
	});

	it('invites a question in an empty workspace without repeating the starter questions', async () => {
		api.getWorkspace.mockResolvedValue({ ...workspace, jobs: [], artifacts: [] });
		api.getCapabilities.mockResolvedValue({ chat: true });
		render(WorkspacePage);
		await fireEvent.click(await screen.findByRole('button', { name: 'Collapse the assistant' }));
		expect(screen.getByRole('button', { name: 'Open the assistant' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Set up my season and area' })).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'ask the assistant' }));

		expect(screen.queryByRole('button', { name: 'Open the assistant' })).toBeNull();
		expect(screen.getByRole('button', { name: 'Collapse the assistant' })).toBeTruthy();
	});

	it('opens the benchmark setup over the stage from New, and Cancel leaves the workspace as it was', async () => {
		render(WorkspacePage);
		await screen.findByRole('navigation', { name: 'Products' });

		await fireEvent.click(within(productsList()).getByRole('button', { name: /New/ }));
		await fireEvent.click(screen.getByRole('menuitem', { name: 'Benchmark' }));
		const panel = screen.getByRole('dialog', { name: 'New benchmark' });
		await fireEvent.click(within(panel).getByRole('button', { name: 'Cancel' }));

		expect(screen.queryByRole('dialog', { name: 'New benchmark' })).toBeNull();
		expect(api.linkWorkspaceJob).not.toHaveBeenCalled();
		expect(nav.goto).not.toHaveBeenCalled();
	});

	it('offers New from the collapsed products strip too', async () => {
		window.innerWidth = 1100;
		render(WorkspacePage);
		await screen.findByRole('navigation', { name: 'Products' });

		await fireEvent.click(within(productsList()).getByRole('button', { name: 'New' }));
		await fireEvent.click(screen.getByRole('menuitem', { name: 'Add an existing run' }));

		expect(screen.getByRole('dialog', { name: 'Add an existing run' })).toBeTruthy();
	});

	it('adds an existing run picked by name and shows it on the stage', async () => {
		const kenya = {
			...workspace.jobs[0],
			id: 'job-9',
			run_id: 'run-9',
			model_name: 'graphcast',
			scope: { place: 'Kenya long rains onset', years: null, lead_days: null }
		};
		api.listAddableJobs.mockResolvedValue([kenya]);
		render(WorkspacePage);
		await screen.findByRole('navigation', { name: 'Products' });
		await fireEvent.click(within(productsList()).getByRole('button', { name: /New/ }));
		await fireEvent.click(screen.getByRole('menuitem', { name: 'Add an existing run' }));
		const picker = within(screen.getByRole('dialog', { name: 'Add an existing run' }));

		api.getWorkspace.mockResolvedValue({ ...workspace, jobs: [...workspace.jobs, kenya] });
		await fireEvent.click(await picker.findByRole('button', { name: /Kenya long rains onset/ }));

		await waitFor(() => expect(nav.goto).toHaveBeenCalledWith('?run=run-9', expect.anything()));
		expect(api.linkWorkspaceJob).toHaveBeenCalledWith('ws-1', 'job-9', 'added');
		expect(screen.queryByRole('dialog', { name: 'Add an existing run' })).toBeNull();
		expect(within(productsList()).getByText(/Kenya long rains onset/)).toBeTruthy();
	});

	it('closes the panel over the stage when a product is opened, and on Escape, handing focus back', async () => {
		api.listAddableJobs.mockResolvedValue([]);
		render(WorkspacePage);
		await screen.findByRole('navigation', { name: 'Products' });
		const openPicker = async () => {
			await fireEvent.click(within(productsList()).getByRole('button', { name: /New/ }));
			await fireEvent.click(await screen.findByRole('menuitem', { name: 'Add an existing run' }));
			return screen.findByRole('dialog', { name: 'Add an existing run' });
		};

		const panel = await openPicker();
		expect(panel.contains(document.activeElement)).toBe(true);
		await fireEvent.click(within(productsList()).getByText('Ethiopia Kiremt onset · AIFS, FuXi'));
		expect(screen.queryByRole('dialog', { name: 'Add an existing run' })).toBeNull();

		await fireEvent.keyDown(await openPicker(), { key: 'Escape' });
		expect(screen.queryByRole('dialog', { name: 'Add an existing run' })).toBeNull();
		expect(document.activeElement).toBe(
			within(productsList()).getByRole('button', { name: /New/ })
		);
	});

	it('changes the season from the header without the assistant', async () => {
		const kiremt = {
			name: 'Kiremt',
			onset_search_start: '05-01',
			reference_onset: '06-01',
			last_issue_date: '07-31'
		};
		api.getWorkspaceSummary.mockResolvedValue({
			season: {
				name: 'Rainy season',
				year: 2026,
				phase: 'in_season',
				first_issue_date: '2026-05-01',
				last_issue_date: '2026-07-31',
				issued_through: null,
				days_until_start: null,
				next_season_start: null,
				is_default: true
			},
			setup: { region: null, observations: null, models: [] },
			latest_forecast: null,
			skill: null
		});
		settingsApi.getEffectiveWorkspaceSettings.mockResolvedValue({
			season: { ...kiremt, name: 'Rainy season' }
		});
		settingsApi.updateWorkspaceSettings.mockResolvedValue({ season: kiremt });
		render(WorkspacePage);

		await fireEvent.click(await screen.findByRole('button', { name: /Rainy season 2026/ }));
		const form = within(await screen.findByRole('dialog', { name: 'Season' }));
		await fireEvent.input(await form.findByRole('textbox', { name: 'Season name' }), {
			target: { value: 'Kiremt' }
		});
		await fireEvent.click(form.getByRole('button', { name: 'Save' }));

		await waitFor(() =>
			expect(settingsApi.updateWorkspaceSettings).toHaveBeenCalledWith('ws-1', {
				season: kiremt
			})
		);
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Season' })).toBeNull());
	});

	it('moves the keyboard to the composer on opening and back to the opener on collapsing', async () => {
		api.getCapabilities.mockResolvedValue({ chat: true });
		render(WorkspacePage);

		await fireEvent.click(await screen.findByRole('button', { name: 'Collapse the assistant' }));
		const opener = await screen.findByRole('button', { name: 'Open the assistant' });
		await waitFor(() => expect(document.activeElement).toBe(opener));

		await fireEvent.click(opener);
		await waitFor(() =>
			expect(document.activeElement).toBe(
				screen.getByRole('textbox', { name: 'Ask the assistant' })
			)
		);
	});

	it('lets the open products list be made wider or narrower, and offers no handle once it is collapsed', async () => {
		render(WorkspacePage);

		const handle = await screen.findByRole('separator', { name: /Resize the products list/ });
		const shell = handle.parentElement as HTMLElement;
		Object.defineProperty(shell, 'clientWidth', { value: 1400 });
		const width = () => parseFloat(shell.style.getPropertyValue('--products-width'));
		await fireEvent.keyDown(handle, { key: 'ArrowRight' });
		const narrow = width();
		await fireEvent.keyDown(handle, { key: 'ArrowRight' });
		const wide = width();
		await fireEvent.keyDown(handle, { key: 'ArrowLeft' });

		expect(wide).toBeGreaterThan(narrow);
		expect(width()).toBe(narrow);

		await fireEvent.click(screen.getByRole('button', { name: 'Collapse the products list' }));

		expect(screen.queryByRole('separator', { name: /Resize the products list/ })).toBeNull();
	});

	it('offers the assistant as a bar on a narrow screen, opening to the conversation and closing again without unmounting it', async () => {
		window.innerWidth = 1000;
		api.getCapabilities.mockResolvedValue({ chat: true });
		render(WorkspacePage);

		const bar = await screen.findByRole('button', { name: 'Open the assistant' });
		const mounted = () =>
			screen
				.getByRole('complementary', { name: 'Assistant' })
				.querySelector('[data-testid="stub-component"]');
		const conversation = mounted();
		expect(conversation).not.toBeNull();
		expect(screen.queryByRole('separator', { name: /Resize the assistant/ })).toBeNull();

		await fireEvent.click(bar);
		const close = await screen.findByRole('button', { name: 'Collapse the assistant' });
		await waitFor(() =>
			expect(document.activeElement).toBe(
				screen.getByRole('textbox', { name: 'Ask the assistant' })
			)
		);

		await fireEvent.click(close);
		const reopened = await screen.findByRole('button', { name: 'Open the assistant' });
		await waitFor(() => expect(document.activeElement).toBe(reopened));
		expect(mounted()).toBe(conversation);
	});

	it('keeps the assistant collapsed as it was left, still mounted behind its strip', async () => {
		api.getCapabilities.mockResolvedValue({ chat: true });
		const first = render(WorkspacePage);
		await fireEvent.click(await screen.findByRole('button', { name: 'Collapse the assistant' }));

		first.unmount();
		render(WorkspacePage);

		expect(await screen.findByRole('button', { name: 'Open the assistant' })).toBeTruthy();
		const column = screen.getByRole('complementary', { name: 'Assistant' });
		expect(column.querySelector('[data-testid="stub-component"]')).not.toBeNull();
	});

	it('has no assistant column or strip when no assistant is set up', async () => {
		render(WorkspacePage);

		await screen.findByRole('navigation', { name: 'Products' });
		expect(screen.queryByRole('complementary', { name: 'Assistant' })).toBeNull();
		expect(screen.queryByRole('button', { name: /assistant/i })).toBeNull();
	});

	it('renames a file on the stage, and an emptied name restores the original', async () => {
		nav.url = 'http://localhost/workspace/ws-1?artifact=art-2';
		api.renameWorkspaceArtifact.mockResolvedValue({ ...workspace.artifacts[1], title: 'far_map' });
		render(WorkspacePage);
		await screen.findByRole('heading', { name: 'False alarm rate map' });

		await enterName('False alarm rate map', '  ', () => document.body);

		expect(api.renameWorkspaceArtifact).toHaveBeenCalledWith('ws-1', 'art-2', null);
		expect(await screen.findByRole('heading', { name: 'far_map' })).toBeTruthy();
		expect(screen.queryByRole('textbox')).toBeNull();
	});

	it('names an analysis, and keeps the field open with the reason when a name is refused', async () => {
		api.renameWorkspaceAnalysis
			.mockRejectedValueOnce(
				new ApiError(400, 'Names can be at most 80 characters.', 'PATCH failed')
			)
			.mockResolvedValueOnce({ turn_id: 'turn-abcdef12', title: 'FAR map', named_by: 'user' });
		render(WorkspacePage);
		const heading = (await screen.findByText(/^Analysis from /)).textContent!;

		await enterName(heading, 'x'.repeat(90));
		expect(await screen.findByRole('alert')).toHaveProperty(
			'textContent',
			'Names can be at most 80 characters.'
		);

		const field = screen.getByRole('textbox', { name: /New name/ });
		await fireEvent.input(field, { target: { value: 'FAR map' } });
		await fireEvent.keyDown(field, { key: 'Enter' });

		expect(api.renameWorkspaceAnalysis).toHaveBeenLastCalledWith(
			'ws-1',
			'turn-abcdef12',
			'FAR map'
		);
		expect(await screen.findByText('FAR map')).toBeTruthy();
	});
});
