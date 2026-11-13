import { describe, expect, it } from 'vitest';

import type { WorkspaceArtifact, WorkspaceDetail, WorkspaceJob } from '../src/lib/api';
import { applyWorkspaceEvent, newestArtifactAt } from '../src/lib/workspace/live-updates';
import { LIST_NAME, TITLE_NAME, onsetName } from '../src/lib/workspace/run-names';
import {
	defaultRun,
	findRunByJobs,
	groupByFamily,
	groupByRun,
	resolveArtifact,
	runListLabels,
	runTitle,
	runHeadline,
	metricsWindow,
	runView
} from '../src/lib/workspace/runs';
import { workspaceArtifact, workspaceJob } from './fixtures/workspace';

/** A job with no model or summary, so each test names only what it reads. */
function job(overrides: Partial<WorkspaceJob> & { id: string }): WorkspaceJob {
	return workspaceJob({
		model_name: null,
		scope: { place: null, years: null, lead_days: null },
		...overrides
	});
}

function file(overrides: Partial<WorkspaceArtifact> & { id: string }): WorkspaceArtifact {
	return workspaceArtifact({ kind: 'metrics', format: 'netcdf', ...overrides });
}

describe('groupByRun', () => {
	it('puts jobs sharing a run id together with the files they produced', () => {
		const jobs = [
			job({ id: 'a', run_id: 'run-1' }),
			job({ id: 'b', run_id: 'run-1' }),
			job({ id: 'c' })
		];
		const files = [file({ id: 'f1', job_id: 'b' }), file({ id: 'f2', job_id: 'c' })];

		const { runs } = groupByRun(jobs, files);

		const run1 = runs.find((run) => run.key === 'run-1')!;
		expect(run1.jobs.map((j) => j.id)).toEqual(['a', 'b']);
		expect(run1.files.map((f) => f.id)).toEqual(['f1']);
		expect(runs.find((run) => run.key === 'c')!.files.map((f) => f.id)).toEqual(['f2']);
	});

	it('lists files that no linked run made under analyses', () => {
		const { analyses } = groupByRun(
			[job({ id: 'a' })],
			[file({ id: 'figure' }), file({ id: 'orphan', job_id: 'gone', run_job_ids: ['gone'] })]
		);
		expect(analyses.flatMap((a) => a.files.map((f) => f.id))).toEqual(['figure', 'orphan']);
	});

	it('places a derived file under every run its inputs came from, and in analyses', () => {
		const jobs = [job({ id: 'a', run_id: 'run-1' }), job({ id: 'b', run_id: 'run-2' })];
		const diff = file({ id: 'diff', run_job_ids: ['a', 'b'], turn_id: 't1' });

		const { runs, analyses } = groupByRun(jobs, [diff]);

		expect(runs.map((run) => run.files.map((f) => f.id))).toEqual([['diff'], ['diff']]);
		expect(analyses.map((a) => [a.turnId, a.files.map((f) => f.id)])).toEqual([['t1', ['diff']]]);
	});

	it("keeps a run's own outputs out of analyses", () => {
		const { analyses } = groupByRun(
			[job({ id: 'a' })],
			[file({ id: 'metrics', job_id: 'a', run_job_ids: ['a'] })]
		);
		expect(analyses).toEqual([]);
	});

	it('shows the newest run first', () => {
		const { runs } = groupByRun(
			[
				job({ id: 'old', created_at: '2026-09-01T00:00:00Z' }),
				job({ id: 'new', created_at: '2026-09-20T00:00:00Z' })
			],
			[]
		);
		expect(runs.map((run) => run.key)).toEqual(['new', 'old']);
	});
});

describe('groupByFamily', () => {
	it('buckets files by group in first-seen order, falling back to the kind', () => {
		const families = groupByFamily([
			file({ id: '1', group: 'Spatial metrics' }),
			file({ id: '2', group: 'Figures' }),
			file({ id: '3', group: 'Spatial metrics' }),
			file({ id: '4', kind: 'document' })
		]);
		expect(families.map((f) => [f.name, f.files.length])).toEqual([
			['Spatial metrics', 2],
			['Figures', 1],
			['Document', 1]
		]);
	});
});

describe('runTitle', () => {
	it('joins the run summary with the display names of its models', () => {
		const [run] = groupByRun(
			[
				job({
					id: 'a',
					run_id: 'r',
					model_name: 'aifs',
					scope: { place: 'Ethiopia Kiremt onset', years: null, lead_days: null }
				}),
				job({
					id: 'b',
					run_id: 'r',
					model_name: 'fuxi',
					scope: { place: 'Ethiopia Kiremt onset', years: null, lead_days: null }
				}),
				job({
					id: 'c',
					run_id: 'r',
					model_name: 'aifs',
					scope: { place: 'Ethiopia Kiremt onset', years: null, lead_days: null }
				})
			],
			[]
		).runs;
		expect(runTitle(run)).toBe('Ethiopia Kiremt onset · AIFS, FuXi');
	});

	it('names the kind of run when there is no summary', () => {
		const [run] = groupByRun([job({ id: 'a', job_type: 'blend' })], []).runs;
		expect(runTitle(run)).toBe('Blend');
	});

	it('leads a blend with its own name, as given, then the summary', () => {
		const [run] = groupByRun(
			[
				job({
					id: 'b',
					job_type: 'blend',
					model_name: 'AIFS-FuXi Ethiopia Blend',
					scope: { place: 'Ethiopia', years: { start: 2008, end: 2024 }, lead_days: null }
				})
			],
			[]
		).runs;
		expect(runTitle(run)).toBe('AIFS-FuXi Ethiopia Blend · Ethiopia · 2008–2024');
	});
});

describe('naming a run by what sets it apart', () => {
	const SCOPE = {
		place: 'Ethiopia',
		years: { start: 1998, end: 2024 },
		lead_days: { start: 1, end: 30 }
	};
	const AMHARA = ['North Gondar', 'South Gondar', 'North Wollo', 'South Wollo', 'Wag Hemra'];
	const change = (label: string, value: number | boolean | null, unit: string | null = null) => ({
		label,
		short_label: label,
		value,
		unit
	});
	const onset = (
		overrides: Partial<NonNullable<WorkspaceJob['onset_definition']>> = {}
	): WorkspaceJob['onset_definition'] => ({
		name: 'Standard wet spell',
		description: null,
		is_default: false,
		changes: [],
		...overrides
	});
	const benchmark = (id: string, overrides: Partial<WorkspaceJob> = {}) => [
		job({ id: `${id}-fuxi`, run_id: id, model_name: 'fuxi', scope: SCOPE, ...overrides }),
		job({ id: `${id}-aifs`, run_id: id, model_name: 'aifs', scope: SCOPE, ...overrides })
	];
	const runsOf = (...jobs: WorkspaceJob[][]) => groupByRun(jobs.flat(), []).runs;
	const labelsOf = (...jobs: WorkspaceJob[][]) => runListLabels(runsOf(...jobs));

	it('titles a whole-region run with the default onset definition by region, years and models', () => {
		const [run] = runsOf(benchmark('r', { onset_definition: onset({ is_default: true }) }));

		expect(runTitle(run)).toBe('Ethiopia · 1998–2024 · FuXi, AIFS · days 1–30');
		expect(runListLabels([run])).toEqual(['Ethiopia · 1998–2024 · FuXi, AIFS']);
	});

	it('leads with the area of interest in place of the region', () => {
		const [run] = runsOf(benchmark('r', { area_of_interest: AMHARA }));

		expect(runTitle(run)).toBe(
			'5 areas: North Gondar, South Gondar, North Wollo, … · 1998–2024 · FuXi, AIFS · days 1–30'
		);
		expect(runListLabels([run])).toEqual(['5 areas: North Gondar, … · 1998–2024 · FuXi, AIFS']);
	});

	it('names a short list of areas, or one box, in full', () => {
		const [two] = runsOf(benchmark('r', { area_of_interest: ['Borena', 'Guji'] }));
		const [box] = runsOf(benchmark('r', { area_of_interest: ['9°N–13°N, 36°E–40°E'] }));

		expect(runListLabels([two])).toEqual(['Borena, Guji · 1998–2024 · FuXi, AIFS']);
		expect(runListLabels([box])).toEqual(['9°N–13°N, 36°E–40°E · 1998–2024 · FuXi, AIFS']);
	});

	it('names the onset definition and its changed settings only when it is not the default', () => {
		const changed = onset({ changes: [change('Onset threshold', 20, 'mm')] });
		const [run] = runsOf(benchmark('r', { onset_definition: changed }));

		expect(runTitle(run)).toBe(
			'Ethiopia · 1998–2024 · Standard wet spell, 20 mm onset threshold · FuXi, AIFS · days 1–30'
		);
	});

	it('names another onset definition on its own when its settings are untouched', () => {
		const [run] = runsOf(benchmark('r', { onset_definition: onset({ name: 'Two-stage' }) }));

		expect(runListLabels([run])).toEqual(['Ethiopia · 1998–2024 · Two-stage · FuXi, AIFS']);
	});

	it('counts changed onset settings once there are too many to read in the list', () => {
		const changes = [
			change('Onset threshold', 20, 'mm'),
			change('Wet spell length', 5, 'days'),
			change('Dry spell check', false)
		];
		const [run] = runsOf(benchmark('r', { onset_definition: onset({ changes }) }));

		expect(runListLabels([run])[0]).toContain('Standard wet spell, 3 changed settings');
		expect(runTitle(run)).toContain(
			'Standard wet spell, 20 mm onset threshold, 5 days wet spell length, no dry spell check'
		);
	});

	it('gives runs that differ only in area or onset definition different list labels', () => {
		const runs = runsOf(
			benchmark('whole'),
			benchmark('amhara', { area_of_interest: AMHARA }),
			benchmark('wetter', {
				onset_definition: onset({ changes: [change('Onset threshold', 20, 'mm')] })
			})
		);
		const labels = runListLabels(runs);
		const labelOf = (key: string) => labels[runs.findIndex((run) => run.key === key)];

		expect(labelOf('whole')).toBe('Ethiopia · 1998–2024 · FuXi, AIFS');
		expect(labelOf('amhara')).toBe('5 areas: North Gondar, … · 1998–2024 · FuXi, AIFS');
		expect(labelOf('wetter')).toBe(
			'Ethiopia · 1998–2024 · Standard wet spell, 20 mm onset threshold · FuXi, AIFS'
		);
	});

	it('uses the short name of a changed onset setting in the list and its full name in the title', () => {
		const wetSpell = onset({
			changes: [{ label: 'Wet spell rain total', short_label: 'Wet spell', value: 20, unit: 'mm' }]
		});

		expect(onsetName(wetSpell, LIST_NAME.onsetChanges, LIST_NAME.shortLabels)).toBe(
			'Standard wet spell, 20 mm wet spell'
		);
		expect(onsetName(wetSpell, TITLE_NAME.onsetChanges, TITLE_NAME.shortLabels)).toBe(
			'Standard wet spell, 20 mm wet spell rain total'
		);
	});

	it('spells out runs whose short labels would match', () => {
		const onsetWith = (threshold: number) =>
			onset({
				changes: [change('Onset threshold', threshold, 'mm'), change('Wet spell length', 5, 'days')]
			});
		const labels = labelsOf(
			benchmark('a', { area_of_interest: AMHARA }),
			benchmark('b', { area_of_interest: [...AMHARA.slice(0, 4), 'Oromia'] }),
			benchmark('c', { onset_definition: onsetWith(20) }),
			benchmark('d', { onset_definition: onsetWith(25) })
		);

		expect(new Set(labels).size).toBe(4);
		expect(labels.find((label) => label.includes('Oromia'))).toContain('Oromia · 1998–2024');
	});

	it('gives a blend its area and onset definition after its own name', () => {
		const [run] = runsOf([
			job({
				id: 'b',
				job_type: 'blend',
				model_name: 'Amhara blend',
				scope: { place: 'Ethiopia', years: { start: 2008, end: 2024 }, lead_days: null },
				area_of_interest: ['North Gondar'],
				onset_definition: onset({ name: 'Two-stage' })
			})
		]);

		expect(runTitle(run)).toBe('Amhara blend · North Gondar · 2008–2024 · Two-stage');
	});
});

describe('chat references', () => {
	it('finds the run containing any of the given jobs', () => {
		const { runs } = groupByRun([job({ id: 'a', run_id: 'r1' }), job({ id: 'b' })], []);
		expect(findRunByJobs(runs, ['zzz', 'a'])?.key).toBe('r1');
		expect(findRunByJobs(runs, ['zzz'])).toBeNull();
	});

	it('resolves a file by id before label', () => {
		const files = [file({ id: 'x', label: 'y' }), file({ id: 'y', label: 'other' })];
		expect(resolveArtifact(files, 'y')?.id).toBe('y');
		expect(resolveArtifact(files, 'other')?.id).toBe('y');
		expect(resolveArtifact(files, 'missing')).toBeNull();
	});
});

describe('applyWorkspaceEvent', () => {
	const base = {
		id: 'ws',
		jobs: [job({ id: 'a', status: 'running' })],
		artifacts: [file({ id: 'f1', title: 'Old title' })]
	} as unknown as WorkspaceDetail;

	it('replaces the job list with the latest one', () => {
		const next = applyWorkspaceEvent(base, {
			type: 'jobs',
			jobs: [job({ id: 'a', status: 'complete' }), job({ id: 'b', status: 'queued' })]
		});
		expect(next.jobs.map((j) => [j.id, j.status])).toEqual([
			['a', 'complete'],
			['b', 'queued']
		]);
	});

	it('adds new files and refreshes ones it already had without duplicating', () => {
		const next = applyWorkspaceEvent(base, {
			type: 'artifacts',
			artifacts: [file({ id: 'f1', title: 'New title' }), file({ id: 'f2' })]
		});
		expect(next.artifacts.map((a) => [a.id, a.title])).toEqual([
			['f1', 'New title'],
			['f2', expect.any(String)]
		]);
	});

	it('replaces analysis names with the latest ones', () => {
		const named = { ...base, analyses: [{ turn_id: 't1', title: 'Old', named_by: 'assistant' }] };
		const next = applyWorkspaceEvent(named as WorkspaceDetail, {
			type: 'analyses',
			analyses: [{ turn_id: 't2', title: 'Onset map', named_by: 'assistant' }]
		});
		expect(next.analyses).toEqual([{ turn_id: 't2', title: 'Onset map', named_by: 'assistant' }]);
		expect(next.artifacts).toBe(base.artifacts);
	});
});

describe('newestArtifactAt', () => {
	it('is the latest created_at, or null when there are no files', () => {
		expect(newestArtifactAt([])).toBeNull();
		expect(
			newestArtifactAt([
				file({ id: 'a', created_at: '2026-01-02T00:00:00' }),
				file({ id: 'b', created_at: '2026-01-03T00:00:00' }),
				file({ id: 'c', created_at: '2026-01-01T00:00:00' })
			])
		).toBe('2026-01-03T00:00:00');
	});
});

describe('runView', () => {
	const run = (...jobs: WorkspaceJob[]) => ({ key: 'r', jobs, files: [] });

	it('names the view a finished run opens to', () => {
		expect(runView(run(job({ id: 'b' })))).toBe('Skill maps');
		expect(runView(run(job({ id: 'b', job_type: 'blend' })))).toBe('Skill map');
		expect(runView(run(job({ id: 'f', job_type: 'forecast', blended: true })))).toBe('Onset map');
	});

	it('names none until a job finishes, or for a raw forecast', () => {
		expect(runView(run(job({ id: 'b', status: 'running' })))).toBeNull();
		expect(runView(run(job({ id: 'f', job_type: 'forecast', blended: false })))).toBeNull();
	});
});

describe('metricsWindow', () => {
	it('reads the lead-time window from a spatial metrics file', () => {
		expect(metricsWindow(file({ id: 'a', label: 'spatial_metrics_aifs_1-15' }))).toBe('1-15');
		expect(metricsWindow(file({ id: 'b', label: 'e2s_spatial_metrics_fuxi_16-30' }))).toBe('16-30');
		expect(metricsWindow(file({ id: 'c', label: 'spatial_metrics_aifs_all' }))).toBeNull();
		expect(metricsWindow(file({ id: 'd', label: 'summary_models_pooled' }))).toBeNull();
	});
});

describe('runHeadline', () => {
	const run = (...jobs: WorkspaceJob[]) => ({ key: 'r', jobs, files: [] });

	it('leads a run with whichever job carries the headline, in the words of the summary', () => {
		const blend = run(
			job({
				id: 'b',
				job_type: 'blend',
				headline: {
					kind: 'blend',
					job_id: 'b',
					name: 'AIFS-FuXi Ethiopia Blend',
					rps_skill: 0.126,
					brier_skill: 0.05,
					holdout_years: '2019–2024'
				}
			})
		);
		expect(runHeadline(blend)).toBe('RPSS 0.13 vs climatology');

		const benchmark = run(
			job({ id: 'a', run_id: 'r' }),
			job({
				id: 'f',
				run_id: 'r',
				headline: {
					kind: 'benchmark',
					job_id: 'f',
					model: 'FuXi',
					mae_days: 6.6,
					climatology_mae_days: 11.4,
					window: '1-15'
				}
			})
		);
		expect(runHeadline(benchmark)).toBe('FuXi · 6.6 d onset error');
		expect(runHeadline(run(job({ id: 'none' })))).toBeNull();
	});
});

describe('defaultRun', () => {
	it('lands on the newest run that has a view, else the newest run at all', () => {
		const withView = groupByRun(
			[
				job({ id: 'new-running', status: 'running', created_at: '2026-09-30T00:00:00Z' }),
				job({ id: 'done', created_at: '2026-09-20T00:00:00Z' }),
				job({ id: 'older-done', created_at: '2026-09-10T00:00:00Z' })
			],
			[]
		).runs;
		expect(defaultRun(withView)?.key).toBe('done');

		const onlyRunning = groupByRun([job({ id: 'queued', status: 'queued' })], []).runs;
		expect(defaultRun(onlyRunning)?.key).toBe('queued');
		expect(defaultRun([])).toBeNull();
	});
});
