/**
 * The workspace's products as one list: runs and analyses, grouped by kind,
 * found by name, and mapped to what the stage shows for each.
 */
import type { AnalysisHeading, Analysis } from './analyses';
import { OVERVIEW, type CanvasView } from './canvas-view';
import {
	defaultRun,
	runListLabels,
	runsByKind,
	runTitle,
	type Run,
	type RunKind,
	type WorkspaceResults
} from './runs';
import { foldSupporting } from './supporting';

export type ProductKind = RunKind | 'analysis';

type ProductBase = {
	key: string;
	/** The full name, as the stage titles it. */
	name: string;
	/** The name the products list has room for. */
	label: string;
	createdAt: string;
};
export type RunProduct = ProductBase & { kind: RunKind; run: Run };
export type AnalysisProduct = ProductBase & {
	kind: 'analysis';
	analysis: Analysis;
	heading: AnalysisHeading;
};
export type Product = RunProduct | AnalysisProduct;

export type ProductGroup = { kind: ProductKind; title: string; products: Product[] };

const runProductKey = (runKey: string) => `run:${runKey}`;
const analysisProductKey = (analysisKey: string) => `analysis:${analysisKey}`;

function runCreatedAt(run: Run): string {
	return run.jobs.reduce((latest, job) => (job.created_at > latest ? job.created_at : latest), '');
}

function runProduct(run: Run, kind: RunKind, label: string): RunProduct {
	return {
		kind,
		key: runProductKey(run.key),
		name: runTitle(run),
		label,
		createdAt: runCreatedAt(run),
		run
	};
}

function analysisProduct(analysis: Analysis, heading: AnalysisHeading): AnalysisProduct {
	return {
		kind: 'analysis',
		key: analysisProductKey(analysis.key),
		name: heading.text,
		label: heading.text,
		createdAt: analysis.startedAt,
		analysis,
		heading
	};
}

/** Benchmarks, blends, forecasts, then analyses; kinds with nothing in them are left out. */
export function productGroups(
	results: WorkspaceResults,
	headingOf: (analysis: Analysis) => AnalysisHeading
): ProductGroup[] {
	const labels = runListLabels(results.runs);
	const labelOf = (run: Run) => labels[results.runs.indexOf(run)];
	const runGroups: ProductGroup[] = runsByKind(results.runs).map((section) => ({
		kind: section.kind,
		title: section.title,
		products: section.runs.map((run) => runProduct(run, section.kind, labelOf(run)))
	}));
	const analyses = results.analyses.map((analysis) =>
		analysisProduct(analysis, headingOf(analysis))
	);
	if (analyses.length === 0) return runGroups;
	return [...runGroups, { kind: 'analysis', title: 'Analyses', products: analyses }];
}

export function allProducts(groups: ProductGroup[]): Product[] {
	return groups.flatMap((group) => group.products);
}

function isCalled(product: Product, wanted: string): boolean {
	return [product.name, product.label].some((name) => name.toLowerCase().includes(wanted));
}

/** The groups narrowed to products whose name contains `query`, whatever its case. */
export function findProducts(groups: ProductGroup[], query: string): ProductGroup[] {
	const wanted = query.trim().toLowerCase();
	if (!wanted) return groups;
	return groups
		.map((group) => ({
			...group,
			products: group.products.filter((product) => isCalled(product, wanted))
		}))
		.filter((group) => group.products.length > 0);
}

/** The files an analysis is opened for, ahead of the ones they were made from. */
function analysisFiles(product: AnalysisProduct) {
	return foldSupporting(product.analysis.files).results;
}

/** What the stage shows for a product: a run's own view, or an analysis's first result. */
export function productView(product: Product): CanvasView | null {
	if (product.kind !== 'analysis') return { kind: 'run', runKey: product.run.key };
	const lead = analysisFiles(product)[0];
	return lead ? { kind: 'file', artifactId: lead.id, runKey: null } : null;
}

/**
 * What marks a product as seen; null while the stage has nothing to show for it.
 * An analysis led by a supporting file is marked apart, so the result that
 * replaces that file a moment later counts as an arrival of its own.
 */
function arrivalMark(product: Product): string | null {
	if (product.kind !== 'analysis') return product.key;
	const lead = analysisFiles(product)[0];
	if (!lead) return null;
	return lead.supporting ? `${product.key}:supporting` : product.key;
}

/** What the stage has already had the chance to follow. */
export function seenProducts(products: Product[]): Set<string> {
	return new Set(products.flatMap((product) => arrivalMark(product) ?? []));
}

/** The product that arrived since `known` was taken, newest first when several did. */
export function newestArrival(known: ReadonlySet<string>, products: Product[]): Product | null {
	const arrived = products.filter((product) => {
		const mark = arrivalMark(product);
		return mark !== null && !known.has(mark);
	});
	return arrived.reduce<Product | null>(
		(newest, product) => (newest && newest.createdAt >= product.createdAt ? newest : product),
		null
	);
}

/**
 * What the stage shows: what was opened by hand, else the product it followed
 * onto, else its default. With no run to land on, the default is the newest analysis.
 */
export function stageView(
	opened: CanvasView,
	followed: CanvasView | null,
	results: WorkspaceResults,
	products: Product[]
): CanvasView {
	if (opened.kind !== 'overview') return opened;
	if (followed) return followed;
	if (results.runs.length > 0) return OVERVIEW;
	const analysis = products.find((product) => product.kind === 'analysis');
	return (analysis && productView(analysis)) ?? OVERVIEW;
}

function analysisHolding(artifactId: string, results: WorkspaceResults): Analysis | null {
	return (
		results.analyses.find((analysis) => analysis.files.some((file) => file.id === artifactId)) ??
		null
	);
}

/** The analysis whose file the stage is showing, unless the file was opened from a run. */
export function analysisOnStage(view: CanvasView, results: WorkspaceResults): Analysis | null {
	if (view.kind !== 'file' || view.runKey) return null;
	return analysisHolding(view.artifactId, results);
}

function productKeyOfFile(artifactId: string, results: WorkspaceResults): string | null {
	const analysis = analysisHolding(artifactId, results);
	if (analysis) return analysisProductKey(analysis.key);
	const run = results.runs.find((each) => each.files.some((file) => file.id === artifactId));
	return run ? runProductKey(run.key) : null;
}

/** The product the stage is showing, for the list to highlight. */
export function shownProductKey(view: CanvasView, results: WorkspaceResults): string | null {
	if (view.kind === 'run') return runProductKey(view.runKey);
	if (view.kind === 'file') {
		return view.runKey ? runProductKey(view.runKey) : productKeyOfFile(view.artifactId, results);
	}
	const landed = defaultRun(results.runs);
	return landed ? runProductKey(landed.key) : null;
}
