/**
 * Interface actions: things the interface knows how to do when asked, by the
 * assistant or by a control. An action is an intent with a payload (open this
 * panel, show this run), never a click.
 *
 * An action that arrives from the assistant is untrusted: it is parsed here into
 * these types, and a handler acts only on what its page has already loaded.
 * See docs/assistant-interface-actions-design.md.
 */
import type { FocusAreaValue } from '$lib/api/jobs';

/** What can be shown: a workspace file by its id or label, or the run the jobs belong to. */
export type WorkspaceTarget = { kind: 'artifact'; ref: string } | { kind: 'run'; jobIds: string[] };

export type BenchmarkDraft = {
	region_id?: string;
	dataset_id?: string;
	model_ids: string[];
	focus_area?: FocusAreaValue;
};

export type BlendDraft = {
	name?: string;
	obs_dataset_id?: string;
	model_ids: string[];
	training_years?: string;
	cv_holdout_years?: string;
	forecast_years?: string;
	true_holdout_years?: string;
	formula_text?: string;
	focus_area?: FocusAreaValue;
	threshold_mm?: number;
	cutoff_month_day?: string;
	ref_onset_month_day?: string;
};

export type ForecastDraft = { blend_id?: string; models: string[] };

/** Each setup panel an action can open, and the draft it is filled in with. */
export type SetupDrafts = {
	benchmark_setup: BenchmarkDraft;
	blend_setup: BlendDraft;
	forecast_setup: ForecastDraft;
};

export type SetupPanel = keyof SetupDrafts;

/** What each setup panel sets up, in the user's words. */
export const SETUP_SUBJECTS: Record<SetupPanel, string> = {
	benchmark_setup: 'benchmark',
	blend_setup: 'blend',
	forecast_setup: 'forecast'
};

export type OpenPanelAction = {
	[Panel in SetupPanel]: { kind: 'open_panel'; panel: Panel; draft: SetupDrafts[Panel] };
}[SetupPanel];

export type ShowAction = { kind: 'show'; target: WorkspaceTarget };

export type InterfaceAction = OpenPanelAction | ShowAction;

/** The key a tool result carries its action under. */
const ACTION_KEY = 'interface_action';

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields | null {
	return typeof value === 'object' && value !== null && !Array.isArray(value)
		? (value as Fields)
		: null;
}

function text(value: unknown): string | undefined {
	return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function texts(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [];
}

function finite(value: unknown): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/** A box or a list of named areas; anything else is no area at all. */
function focusArea(value: unknown): FocusAreaValue | undefined {
	const area = fields(value);
	if (!area) return undefined;
	const level = text(area.level);
	const units = texts(area.units);
	if (level && units.length > 0) return { level, units } as FocusAreaValue;
	const [latMin, latMax, lonMin, lonMax] = [
		finite(area.lat_min),
		finite(area.lat_max),
		finite(area.lon_min),
		finite(area.lon_max)
	];
	if ([latMin, latMax, lonMin, lonMax].some((edge) => edge === undefined)) return undefined;
	return { lat_min: latMin, lat_max: latMax, lon_min: lonMin, lon_max: lonMax } as FocusAreaValue;
}

/** Only the keys that were given, so a draft never overwrites a field with nothing. */
function given<T extends object>(draft: T): T {
	return Object.fromEntries(Object.entries(draft).filter(([, value]) => value !== undefined)) as T;
}

function benchmarkDraft(draft: Fields): BenchmarkDraft {
	return given({
		region_id: text(draft.region_id),
		dataset_id: text(draft.dataset_id),
		model_ids: texts(draft.model_ids),
		focus_area: focusArea(draft.focus_area)
	});
}

function blendDraft(draft: Fields): BlendDraft {
	return given({
		name: text(draft.name),
		obs_dataset_id: text(draft.obs_dataset_id),
		model_ids: texts(draft.model_ids),
		training_years: text(draft.training_years),
		cv_holdout_years: text(draft.cv_holdout_years),
		forecast_years: text(draft.forecast_years),
		true_holdout_years: text(draft.true_holdout_years),
		formula_text: text(draft.formula_text),
		focus_area: focusArea(draft.focus_area),
		threshold_mm: finite(draft.threshold_mm),
		cutoff_month_day: text(draft.cutoff_month_day),
		ref_onset_month_day: text(draft.ref_onset_month_day)
	});
}

function forecastDraft(draft: Fields): ForecastDraft {
	return given({ blend_id: text(draft.blend_id), models: texts(draft.models) });
}

function openPanel(action: Fields): OpenPanelAction | null {
	const draft = fields(action.draft) ?? {};
	if (action.panel === 'benchmark_setup') {
		return { kind: 'open_panel', panel: 'benchmark_setup', draft: benchmarkDraft(draft) };
	}
	if (action.panel === 'blend_setup') {
		return { kind: 'open_panel', panel: 'blend_setup', draft: blendDraft(draft) };
	}
	if (action.panel === 'forecast_setup') {
		return { kind: 'open_panel', panel: 'forecast_setup', draft: forecastDraft(draft) };
	}
	return null;
}

function show(action: Fields): ShowAction | null {
	const target = fields(action.target);
	if (target?.kind === 'run') {
		const jobIds = texts(target.job_ids);
		return jobIds.length > 0 ? { kind: 'show', target: { kind: 'run', jobIds } } : null;
	}
	const ref = target?.kind === 'file' ? text(target.artifact_id) : undefined;
	return ref ? { kind: 'show', target: { kind: 'artifact', ref } } : null;
}

/** The action in this value, or null when it is not one this interface knows. */
export function parseAction(value: unknown): InterfaceAction | null {
	const action = fields(value);
	if (action?.kind === 'open_panel') return openPanel(action);
	if (action?.kind === 'show') return show(action);
	return null;
}

/** The action a finished tool call asks for, if its result carries one. */
export function actionFromToolResult(result: unknown): InterfaceAction | null {
	return parseAction(fields(result)?.[ACTION_KEY]);
}

/** What the button that repeats an action says. */
export function actionLabel(action: InterfaceAction): string {
	if (action.kind === 'open_panel') return `Open the ${SETUP_SUBJECTS[action.panel]} setup`;
	return action.target.kind === 'run' ? 'Show the run' : 'Show the file';
}

/** The assistant's tools whose results are actions; no other tool's result is read as one. */
const ACTION_TOOLS = new Set(['open_panel', 'show']);

/** The action a finished call asks for; null for any tool that is not an action tool. */
export function actionFromToolCall(call: {
	name: string;
	result?: unknown;
}): InterfaceAction | null {
	return ACTION_TOOLS.has(call.name) ? actionFromToolResult(call.result) : null;
}

/** Who asked: the assistant, in a reply as it streams, or the user, by pressing a button. */
export type ActionSource = 'assistant' | 'user';

/** What a page does for each kind of action; a kind it leaves out is one it cannot do. */
export type ActionHandlers = {
	[Kind in InterfaceAction['kind']]?: (action: Extract<InterfaceAction, { kind: Kind }>) => void;
};

/** Hand an action to the page; false when the page has nothing that does it. */
export function dispatchAction(action: InterfaceAction, handlers: ActionHandlers): boolean {
	const handler = handlers[action.kind] as ((action: InterfaceAction) => void) | undefined;
	if (!handler) return false;
	handler(action);
	return true;
}
