/**
 * One workspace as the server has it, kept current from its event stream, and
 * the changes made to it from the page.
 */
import {
	cancelFollowUp,
	failureMessage,
	getWorkspace,
	getWorkspaceSummary,
	linkWorkspaceJob,
	renameWorkspaceAnalysis,
	renameWorkspaceArtifact,
	watchWorkspace,
	type FollowUp,
	type RunOrigin,
	type WorkspaceArtifact,
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
import { withAnalysisName } from './analyses';
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

	readonly results = $derived(
		this.workspace ? groupByRun(this.workspace.jobs, this.workspace.artifacts) : NO_RESULTS
	);

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

	/** The season in force: the workspace's own, else the platform default. */
	async seasonInForce(): Promise<SeasonWindow> {
		const season =
			this.workspace?.settings.season ?? (await getEffectiveWorkspaceSettings(this.id)).season;
		if (!season) throw new Error('No season to edit');
		return season;
	}

	/** Changes the settings the patch names; `null` returns one to the platform default. */
	async saveSettings(patch: WorkspaceSettings) {
		const settings = await updateWorkspaceSettings(this.id, patch);
		if (this.workspace) this.workspace = { ...this.workspace, settings };
		await this.refreshSummary();
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
