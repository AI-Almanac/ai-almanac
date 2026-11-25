/**
 * One workspace as the server has it, kept current from its event stream, and
 * the changes made to it from the page.
 */
import {
	cancelFollowUp,
	deleteJob,
	describeWorkspace,
	failureMessage,
	getWorkspace,
	getWorkspaceSummary,
	hideWorkspaceAnalysis,
	linkWorkspaceJob,
	renameWorkspaceAnalysis,
	renameWorkspaceArtifact,
	unhideWorkspaceAnalysis,
	watchWorkspace,
	type FollowUp,
	type RunOrigin,
	type WorkspaceArtifact,
	type WorkspaceDescription,
	type WorkspaceDetail,
	type WorkspaceEvent,
	type WorkspaceSummary
} from '$lib/api';
import {
	getEffectiveWorkspaceSettings,
	updateWorkspaceSettings,
	type SeasonWindow,
	type WorkspaceSettings
} from '$lib/api/workspace-settings';
import { byVisibility, withAnalysisName, withHidden, type Analysis } from './analyses';
import { applyWorkspaceEvent, newestArtifactAt } from './live-updates';
import { groupByRun, type WorkspaceResults } from './runs';

/** How often a snapshot is fetched again when an update overtakes it. */
const SNAPSHOT_ATTEMPTS = 3;

const NO_RESULTS: WorkspaceResults = { runs: [], analyses: [] };

export class WorkspaceSession {
	readonly id: string;
	workspace = $state<WorkspaceDetail | null>(null);
	/** The headline facts; null when they could not be read, which leaves the strip off. */
	summary = $state<WorkspaceSummary | null>(null);
	followUps = $state<FollowUp[]>([]);
	/** Why the workspace could not be loaded. */
	error = $state<string | null>(null);

	readonly #grouped = $derived(
		this.workspace ? groupByRun(this.workspace.jobs, this.workspace.artifacts) : NO_RESULTS
	);
	readonly #analyses = $derived(
		byVisibility(this.#grouped.analyses, this.workspace?.hidden_analyses ?? [])
	);
	/** What the products list and the stage show: everything but the analyses the user hid. */
	readonly results: WorkspaceResults = $derived({
		...this.#grouped,
		analyses: this.#analyses.shown
	});
	readonly hiddenAnalyses: Analysis[] = $derived(this.#analyses.hidden);

	// Updates applied from the event stream, so a snapshot fetched before one of
	// them landed is not laid over it.
	#eventsApplied = 0;

	constructor(id: string) {
		this.id = id;
	}

	/** Loads the workspace, then keeps it current until `signal` aborts. */
	async open(signal: AbortSignal): Promise<void> {
		const loaded = this.refresh();
		void this.refreshSummary();
		await loaded;
		await watchWorkspace(
			this.id,
			() => newestArtifactAt(this.workspace?.artifacts ?? []),
			(event) => this.#apply(event),
			signal
		);
	}

	async refresh(): Promise<WorkspaceDetail | null> {
		try {
			const next = await this.#currentSnapshot();
			this.workspace = next;
			this.error = null;
			return next;
		} catch (e) {
			this.error = failureMessage(e);
			return null;
		}
	}

	async refreshSummary() {
		try {
			this.summary = await getWorkspaceSummary(this.id);
		} catch {
			this.summary = null;
		}
	}

	/** Puts runs into the workspace. */
	async attach(jobIds: string[], origin: RunOrigin) {
		for (const jobId of jobIds) await linkWorkspaceJob(this.id, jobId, origin);
		await this.refresh();
		void this.refreshSummary();
	}

	/**
	 * Deletes jobs outright, so they leave every workspace that holds them. The workspace
	 * is reread either way: when some are refused, the rest are still gone.
	 */
	async deleteJobs(jobIds: string[]) {
		const outcomes = await Promise.allSettled(jobIds.map((jobId) => deleteJob(jobId)));
		await this.refresh();
		void this.refreshSummary();
		const refused = outcomes.find((outcome) => outcome.status === 'rejected');
		if (refused) throw refused.reason;
	}

	/** The season in force: the workspace's own, else the platform default. */
	async seasonInForce(): Promise<SeasonWindow> {
		const season =
			this.workspace?.settings.season ?? (await getEffectiveWorkspaceSettings(this.id)).season;
		if (!season) throw new Error('No season to edit');
		return season;
	}

	/** The year of the season the workspace works on: its own choice, else the year the
	 * summary is about, which is this calendar year. */
	yearInForce(): number {
		return (
			this.workspace?.settings.season_year ?? this.summary?.season.year ?? new Date().getFullYear()
		);
	}

	/** Changes the settings the patch names; `null` returns one to the platform default. */
	async saveSettings(patch: WorkspaceSettings) {
		const settings = await updateWorkspaceSettings(this.id, patch);
		if (this.workspace) this.workspace = { ...this.workspace, settings };
		await this.refreshSummary();
	}

	/** Renames the workspace or rewrites what it is for. */
	async describe(change: WorkspaceDescription) {
		const described = await describeWorkspace(this.id, change);
		if (this.workspace) this.workspace = { ...this.workspace, ...described };
	}

	async renameFile(file: WorkspaceArtifact, title: string | null) {
		const renamed = await renameWorkspaceArtifact(this.id, file.id, title);
		if (this.workspace) {
			this.workspace = applyWorkspaceEvent(this.workspace, {
				type: 'artifacts',
				artifacts: [renamed]
			});
		}
	}

	async renameAnalysis(turnId: string, title: string | null) {
		const named = await renameWorkspaceAnalysis(this.id, turnId, title);
		if (this.workspace) {
			const analyses = withAnalysisName(this.workspace.analyses, turnId, named);
			this.workspace = { ...this.workspace, analyses };
		}
	}

	/** Hides an analysis from the products list, or (`hide` false) shows it again. */
	async setAnalysisHidden(turnId: string, hide: boolean) {
		await (hide ? hideWorkspaceAnalysis : unhideWorkspaceAnalysis)(this.id, turnId);
		if (this.workspace) {
			const hidden_analyses = withHidden(this.workspace.hidden_analyses, turnId, hide);
			this.workspace = { ...this.workspace, hidden_analyses };
		}
	}

	async cancelFollowUp(followUpId: string) {
		await cancelFollowUp(this.id, followUpId);
		this.followUps = this.followUps.map((followUp) =>
			followUp.id === followUpId ? { ...followUp, status: 'canceled' } : followUp
		);
	}

	async #currentSnapshot(): Promise<WorkspaceDetail> {
		for (let attempt = 1; ; attempt++) {
			const before = this.#eventsApplied;
			const snapshot = await getWorkspace(this.id);
			if (this.#eventsApplied === before || attempt === SNAPSHOT_ATTEMPTS) return snapshot;
		}
	}

	#apply(event: WorkspaceEvent) {
		if (event.type === 'follow_ups') this.followUps = event.follow_ups;
		else if (this.workspace) {
			this.workspace = applyWorkspaceEvent(this.workspace, event);
			this.#eventsApplied += 1;
		}
		// A run finishing or being linked changes the headline facts.
		if (event.type === 'jobs') void this.refreshSummary();
	}
}
