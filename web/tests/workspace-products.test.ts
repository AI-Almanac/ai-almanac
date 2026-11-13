import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceArtifact, WorkspaceJob } from '../src/lib/api';
import ProductsList from '../src/lib/components/workspace/ProductsList.svelte';
import ProductsStrip from '../src/lib/components/workspace/ProductsStrip.svelte';
import type { Analysis } from '../src/lib/workspace/analyses';
import { OVERVIEW } from '../src/lib/workspace/canvas-view';
import {
	allProducts,
	analysisOnStage,
	findProducts,
	newestArrival,
	productGroups,
	productView,
	seenProducts,
	shownProductKey,
	stageView
} from '../src/lib/workspace/products';
import { groupByRun } from '../src/lib/workspace/runs';
import { workspaceArtifact, workspaceJob } from './fixtures/workspace';

function job(id: string, overrides: Partial<WorkspaceJob> = {}): WorkspaceJob {
	return workspaceJob({
		id,
		run_id: id,
		scope: { place: `Onset ${id}`, years: null, lead_days: null },
		...overrides
	});
}

function file(id: string, overrides: Partial<WorkspaceArtifact> = {}): WorkspaceArtifact {
	return workspaceArtifact({
		id,
		group: 'Figures',
		step_id: `step-${id}`,
		turn_id: 'turn-1',
		created_at: '2026-09-23T00:00:00Z',
		...overrides
	});
}

const headingOf = (analysis: Analysis) => ({ text: `Analysis ${analysis.key}`, question: null });

function workspaceOf(jobs: WorkspaceJob[], files: WorkspaceArtifact[]) {
	const results = groupByRun(jobs, files);
	const groups = productGroups(results, headingOf);
	return { results, groups, products: allProducts(groups) };
}

const jobs = [job('older'), job('newer', { created_at: '2026-09-24T00:00:00Z' })];
const files = [
	file('map'),
	file('outlines', { supporting: true }),
	file('table', { turn_id: 'turn-2', created_at: '2026-09-25T00:00:00Z' })
];

describe('products', () => {
	it('groups products by kind, leaving out kinds with nothing in them', () => {
		const blend = job('blend', { job_type: 'blend' });

		expect(
			workspaceOf(jobs, files).groups.map((group) => [group.title, group.products.length])
		).toEqual([
			['Benchmarks', 2],
			['Analyses', 2]
		]);
		expect(workspaceOf([...jobs, blend], []).groups.map((group) => group.title)).toEqual([
			'Benchmarks',
			'Blends'
		]);
	});

	it('finds products by part of their name, whatever its case', () => {
		const { groups } = workspaceOf(jobs, files);

		const found = findProducts(groups, '  ONSET new ');

		expect(found.map((group) => group.title)).toEqual(['Benchmarks']);
		expect(found[0].products).toHaveLength(1);
		expect(found[0].products[0].name).toContain('Onset newer');
		expect(findProducts(groups, '   ')).toEqual(groups);
		expect(findProducts(groups, 'no such thing')).toEqual([]);
	});

	it('finds a run by a setting its list label has no room for', () => {
		const { groups } = workspaceOf(
			[
				job('amhara', {
					scope: { place: 'Ethiopia', years: { start: 1998, end: 2024 }, lead_days: null },
					area_of_interest: ['North Gondar', 'South Gondar', 'Wag Hemra']
				})
			],
			[]
		);

		expect(findProducts(groups, 'south gondar')[0].products).toHaveLength(1);
	});

	it('opens a run on its own view and an analysis on its first result', () => {
		const { products } = workspaceOf(jobs, files);
		const run = products.find((product) => product.key === 'run:newer')!;
		const analysis = products.find((product) => product.key === 'analysis:turn-1')!;

		expect(productView(run)).toEqual({ kind: 'run', runKey: 'newer' });
		expect(productView(analysis)).toEqual({ kind: 'file', artifactId: 'map', runKey: null });
	});

	it('opens an analysis on a supporting file when it holds nothing else', () => {
		const { products } = workspaceOf([], [file('outlines', { supporting: true })]);

		expect(productView(products[0])).toEqual({
			kind: 'file',
			artifactId: 'outlines',
			runKey: null
		});
	});
});

describe('what the stage shows', () => {
	it('picks out the newest product to arrive, and nothing when none did', () => {
		const before = workspaceOf([job('older')], [file('map')]);
		const after = workspaceOf(jobs, files);
		const known = new Set(before.products.map((product) => product.key));

		expect(newestArrival(known, after.products)?.key).toBe('analysis:turn-2');
		expect(newestArrival(known, before.products)).toBeNull();
	});

	it('treats the first result of an analysis as its arrival, not the supporting file before it', () => {
		const supporting = file('rain', { supporting: true, turn_id: 'turn-9' });
		const figure = file('onset-map', { turn_id: 'turn-9' });
		const early = workspaceOf([], [supporting]).products;
		const later = workspaceOf([], [supporting, figure]).products;

		const known = seenProducts(early);

		expect(newestArrival(new Set(), early)?.key).toBe('analysis:turn-9');
		expect(newestArrival(known, early)).toBeNull();
		expect(productView(newestArrival(known, later)!)).toEqual({
			kind: 'file',
			artifactId: 'onset-map',
			runKey: null
		});
		expect(newestArrival(seenProducts(later), later)).toBeNull();
	});

	it('keeps what was opened by hand over anything that arrives', () => {
		const { results, products } = workspaceOf(jobs, files);
		const opened = { kind: 'run', runKey: 'older' } as const;

		expect(stageView(opened, { kind: 'run', runKey: 'newer' }, results, products)).toEqual(opened);
	});

	it('follows an arrival while nothing is opened, else stays on its default', () => {
		const { results, products } = workspaceOf(jobs, files);
		const arrived = { kind: 'run', runKey: 'newer' } as const;

		expect(stageView(OVERVIEW, arrived, results, products)).toEqual(arrived);
		expect(stageView(OVERVIEW, null, results, products)).toEqual(OVERVIEW);
	});

	it('lands on the newest analysis when the workspace has no run', () => {
		const { results, products } = workspaceOf([], files);

		expect(stageView(OVERVIEW, null, results, products)).toEqual({
			kind: 'file',
			artifactId: 'table',
			runKey: null
		});
	});

	it('names the shown product: the run opened or landed on, or the analysis a file is from', () => {
		const { results } = workspaceOf(jobs, files);

		expect(shownProductKey(OVERVIEW, results)).toBe('run:newer');
		expect(shownProductKey({ kind: 'run', runKey: 'older' }, results)).toBe('run:older');
		expect(shownProductKey({ kind: 'file', artifactId: 'map', runKey: 'older' }, results)).toBe(
			'run:older'
		);
		expect(shownProductKey({ kind: 'file', artifactId: 'outlines', runKey: null }, results)).toBe(
			'analysis:turn-1'
		);
		expect(shownProductKey({ kind: 'file', artifactId: 'gone', runKey: null }, results)).toBeNull();
	});

	it('knows the analysis behind a file unless the file was opened from a run', () => {
		const { results } = workspaceOf(jobs, files);

		expect(analysisOnStage({ kind: 'file', artifactId: 'map', runKey: null }, results)?.key).toBe(
			'turn-1'
		);
		expect(
			analysisOnStage({ kind: 'file', artifactId: 'map', runKey: 'older' }, results)
		).toBeNull();
		expect(analysisOnStage(OVERVIEW, results)).toBeNull();
	});
});

describe('ProductsList', () => {
	const { groups, products } = workspaceOf(jobs, files);
	const list = () => within(screen.getByRole('navigation', { name: 'Products' }));

	it('shows each kind with its count and no files', () => {
		render(ProductsList, { groups, onOpen: vi.fn(), onCollapse: vi.fn() });

		expect(list().getByRole('heading', { name: 'Benchmarks 2' })).toBeTruthy();
		expect(list().getByRole('heading', { name: 'Analyses 2' })).toBeTruthy();
		expect(list().queryByText('map')).toBeNull();
		expect(list().queryByText('outlines')).toBeNull();
	});

	it('narrows to the products whose name matches the find box', async () => {
		render(ProductsList, { groups, onOpen: vi.fn(), onCollapse: vi.fn() });

		await fireEvent.input(list().getByRole('searchbox', { name: 'Find a product' }), {
			target: { value: 'turn-2' }
		});

		expect(list().getByText('Analysis turn-2')).toBeTruthy();
		expect(list().queryByText('Analysis turn-1')).toBeNull();
		expect(list().queryByRole('heading', { name: /Benchmarks/ })).toBeNull();
		expect(list().getByRole('heading', { name: 'Analyses 1' })).toBeTruthy();
	});

	it('says so when no product matches', async () => {
		render(ProductsList, { groups, onOpen: vi.fn(), onCollapse: vi.fn() });

		await fireEvent.input(list().getByRole('searchbox'), { target: { value: 'zzz' } });

		expect(list().getByText('No product has “zzz” in its name.')).toBeTruthy();
	});

	it('marks the product the stage is showing and opens the one pressed', async () => {
		const onOpen = vi.fn();
		render(ProductsList, {
			groups,
			shownKey: 'analysis:turn-1',
			onOpen,
			onCollapse: vi.fn()
		});

		expect(list().getByRole('button', { current: true }).textContent).toContain('Analysis turn-1');

		await fireEvent.click(list().getByText('Analysis turn-2'));
		expect(onOpen).toHaveBeenCalledWith(products.find((p) => p.key === 'analysis:turn-2'));
	});
});

describe('ProductsStrip', () => {
	it('shows one button per kind with its count, opening the list at that kind', async () => {
		const { groups } = workspaceOf(jobs, files);
		const onExpand = vi.fn();
		render(ProductsStrip, { groups, onExpand });

		expect(screen.getAllByRole('button')).toHaveLength(2);
		const analyses = screen.getByRole('button', { name: /^Analyses: 2\./ });
		expect(analyses.textContent?.trim()).toBe('2');

		await fireEvent.click(analyses);
		expect(onExpand).toHaveBeenCalledWith('analysis');
	});
});
