/**
 * Everything the conversation says about each assistant tool, in one place: what a
 * step reads as, what its approval card asks, and how its result is shown.
 */
import type { ChatToolCall } from '$lib/api';
import type { StepState } from './timeline';
import {
	blendSkillTable,
	isRecord,
	skillScoresTable,
	spatialMetricsTable,
	type ResultTable
} from './tool-results';

/** The card a step's result is drawn as, beyond its one-line label. */
export type ResultKind = 'interface-action' | 'submitted-jobs' | 'table';

/** What an approval card for the step says, and its go-ahead button. */
export type ApprovalCopy = { title: string; action: string };

type Tool = {
	active: string;
	done: string;
	/** What a step that needs approval reads as when the user says not yet. */
	declined?: string;
	approval?: ApprovalCopy;
	result?: Exclude<ResultKind, 'table'>;
	/** The headline numbers of a results tool. */
	table?: (result: Record<string, unknown>) => ResultTable | null;
	/** The step runs code the assistant wrote, passed as `input.code`. */
	writesCode?: boolean;
};

const TOOLS: Record<string, Tool> = {
	open_panel: {
		active: 'Setting up the form',
		done: 'Set up the form',
		result: 'interface-action'
	},
	show: { active: 'Bringing it up', done: 'Brought it up', result: 'interface-action' },
	list_regions: { active: 'Checking regions', done: 'Checked regions' },
	list_datasets: { active: 'Checking datasets', done: 'Checked datasets' },
	list_models: { active: 'Checking models', done: 'Checked models' },
	get_benchmark_config: { active: 'Reading the benchmark plan', done: 'Read the benchmark plan' },
	update_benchmark_config: {
		active: 'Updating the benchmark plan',
		done: 'Updated the benchmark plan'
	},
	validate_benchmark_config: {
		active: 'Checking the benchmark plan',
		done: 'Checked the benchmark plan'
	},
	submit_benchmark: {
		active: 'Submitting the benchmark',
		done: 'Submitted the benchmark',
		declined: 'Benchmark not submitted',
		approval: { title: 'Ready to run', action: 'Run benchmark' },
		result: 'submitted-jobs'
	},
	list_blend_models: { active: 'Checking blendable models', done: 'Checked blendable models' },
	get_blend_config: { active: 'Reading the blend plan', done: 'Read the blend plan' },
	update_blend_config: { active: 'Updating the blend plan', done: 'Updated the blend plan' },
	validate_blend_config: { active: 'Checking the blend plan', done: 'Checked the blend plan' },
	submit_blend: {
		active: 'Submitting the blend',
		done: 'Submitted the blend',
		declined: 'Blend not submitted',
		approval: { title: 'Ready to train', action: 'Train blend' },
		result: 'submitted-jobs'
	},
	get_blend_results: {
		active: 'Reading blend results',
		done: 'Read blend results',
		table: blendSkillTable
	},
	open_setup: { active: 'Preparing the setup link', done: 'Prepared the setup link' },
	list_jobs: { active: 'Listing runs', done: 'Listed runs' },
	list_failed_jobs: { active: 'Checking failed runs', done: 'Checked failed runs' },
	get_job_info: { active: 'Reading run details', done: 'Read run details' },
	get_job_logs: { active: 'Reading run logs', done: 'Read run logs' },
	rerun_job: {
		active: 'Rerunning',
		done: 'Reran',
		declined: 'Rerun not started',
		approval: { title: 'Ready to rerun', action: 'Rerun job' }
	},
	cancel_run: { active: 'Canceling the run', done: 'Canceled the run' },
	get_job_metrics: {
		active: 'Loading metrics',
		done: 'Loaded metrics',
		table: spatialMetricsTable
	},
	get_skill_scores: {
		active: 'Loading skill scores',
		done: 'Loaded skill scores',
		table: skillScoresTable
	},
	get_spatial_summary: {
		active: 'Loading the spatial summary',
		done: 'Loaded the spatial summary'
	},
	run_code_sandbox: {
		active: 'Running a computation',
		done: 'Ran a computation',
		writesCode: true
	},
	run_code: { active: 'Running custom analysis', done: 'Ran custom analysis', writesCode: true },
	list_artifacts: { active: 'Checking workspace files', done: 'Checked workspace files' },
	inspect_artifact: { active: 'Reading a workspace file', done: 'Read a workspace file' },
	describe_provenance: {
		active: 'Tracing where a file came from',
		done: 'Traced where a file came from'
	},
	verify_provenance: {
		active: 'Checking where a file came from',
		done: 'Checked where a file came from'
	},
	list_capabilities: { active: 'Checking what I can do', done: 'Checked what I can do' },
	resolve_region: { active: 'Finding the region', done: 'Found the region' },
	list_sources: { active: 'Checking data sources', done: 'Checked data sources' },
	plan_fetch: { active: 'Planning a data download', done: 'Planned a data download' },
	find_runs: { active: 'Looking through your runs', done: 'Looked through your runs' },
	add_job: { active: 'Adding a run to the workspace', done: 'Added a run to the workspace' },
	continue_after_jobs: {
		active: 'Scheduling a follow-up',
		done: 'Scheduled a follow-up for when the runs finish'
	},
	set_workspace_season: {
		active: 'Updating the workspace season',
		done: 'Updated the workspace season'
	},
	set_workspace_area: {
		active: 'Updating the workspace area',
		done: 'Updated the workspace area'
	},
	find_country: { active: 'Looking up a country', done: 'Looked up a country' },
	validate_benchmark: { active: 'Checking the benchmark plan', done: 'Checked the benchmark plan' },
	validate_blend: { active: 'Checking the blend plan', done: 'Checked the blend plan' },
	run_forecast: {
		active: 'Running a forecast',
		done: 'Ran a forecast',
		declined: 'Forecast not run',
		approval: { title: 'Ready to forecast', action: 'Run forecast' }
	},
	check_forecast: {
		active: 'Checking the season and what a forecast costs',
		done: 'Checked the season and what a forecast costs'
	},
	update_forecast: {
		active: 'Updating a forecast',
		done: 'Updated a forecast',
		declined: 'Forecast not updated',
		approval: { title: 'Ready to update the forecast', action: 'Update forecast' }
	},
	fetch: {
		active: 'Fetching data',
		done: 'Fetched data',
		declined: 'Data not fetched',
		approval: { title: 'Ready to fetch data', action: 'Fetch data' }
	},
	list_areas: { active: 'Looking up area names', done: 'Looked up area names' },
	open_source: { active: 'Opening a dataset', done: 'Opened a dataset' },
	get_forecast_results: { active: 'Reading forecast results', done: 'Read forecast results' },
	summarize_skill_by_area: {
		active: 'Averaging skill over each area',
		done: 'Averaged skill over each area'
	},
	summarize_by_area: {
		active: 'Summarizing the forecast by area',
		done: 'Summarized the forecast by area'
	},
	plot_area_map: { active: 'Drawing an area map', done: 'Drew an area map' },
	detect_onset: {
		active: 'Reading onset from the forecast',
		done: 'Read onset from the forecast'
	},
	plot_map: { active: 'Drawing a map', done: 'Drew a map' },
	plot_maps: { active: 'Drawing maps', done: 'Drew maps' }
};

/** A tool this list does not know yet still reads as words: its name, spaced and capitalised. */
function toolFor(name: string): Tool {
	const known = TOOLS[name];
	if (known) return known;
	const words = name.replace(/_/g, ' ');
	const label = words.charAt(0).toUpperCase() + words.slice(1);
	return { active: label, done: label };
}

const WAITING_FOR_APPROVAL = 'Waiting for your approval';
const DECLINED = 'Step not run';
const DEFAULT_APPROVAL: ApprovalCopy = { title: 'Ready for the next step', action: 'Go ahead' };

type LabelledCall = Pick<ChatToolCall, 'name'> & Partial<Pick<ChatToolCall, 'status'>>;

/**
 * What a step is doing, or did, in the user's terms. An ordinary failure still
 * names the step; what went wrong is shown beside it.
 */
export function toolLabel(call: LabelledCall, state: StepState): string {
	const tool = toolFor(call.name);
	switch (state) {
		case 'waiting':
			return WAITING_FOR_APPROVAL;
		case 'declined':
			return tool.declined ?? DECLINED;
		case 'active':
			return call.status === 'pending' && tool.writesCode ? 'Writing analysis code' : tool.active;
		default:
			return tool.done;
	}
}

export function approvalCopy(toolName: string): ApprovalCopy {
	return toolFor(toolName).approval ?? DEFAULT_APPROVAL;
}

export function resultKind(toolName: string): ResultKind | null {
	const tool = toolFor(toolName);
	return tool.result ?? (tool.table ? 'table' : null);
}

/** The headline numbers from a results tool, or null when there is nothing to show. */
export function resultTable(toolName: string, result: unknown): ResultTable | null {
	const build = toolFor(toolName).table;
	if (!build || !isRecord(result) || result.error) return null;
	return build(result);
}

/** The code a code step ran, or null for any other step. */
export function codeForToolCall(call: Pick<ChatToolCall, 'name' | 'input'>): string | null {
	if (!toolFor(call.name).writesCode) return null;
	const code = call.input.code;
	return typeof code === 'string' && code.length > 0 ? code : null;
}
