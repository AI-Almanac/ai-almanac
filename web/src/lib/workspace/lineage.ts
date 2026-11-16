import type { ArtifactProvenance, ProvenanceStep } from '$lib/api/workspaces';

export type VerificationTone = 'ok' | 'problem' | 'neutral';

const VERIFICATION_LABELS: Record<string, { label: string; tone: VerificationTone }> = {
	verified: { label: 'Matches the record', tone: 'ok' },
	hash_mismatch: { label: 'Changed since it was recorded', tone: 'problem' },
	input_missing: { label: 'A source file is missing', tone: 'problem' }
};

function verificationBadge(
	verification: string | null
): { label: string; tone: VerificationTone } | null {
	if (!verification) return null;
	return (
		VERIFICATION_LABELS[verification] ?? {
			label: verification.replaceAll('_', ' '),
			tone: 'neutral'
		}
	);
}

export type IntegrityStatus = { label: string; tone: VerificationTone };

/**
 * One status for the whole file, from the check of every step behind it.
 * The check compares files with the record; it does not re-run anything.
 */
export function integrityStatus(provenance: ArtifactProvenance | null): IntegrityStatus | null {
	const results = provenance?.steps.map((step) => step.verification) ?? [];
	if (results.length === 0 || results.some((result) => !result)) return null;
	if (results.includes('hash_mismatch'))
		return { label: 'Changed since it was recorded', tone: 'problem' };
	if (results.includes('input_missing'))
		return { label: 'A source file is missing', tone: 'problem' };
	if (results.every((result) => result === 'verified'))
		return { label: 'Files match the record', tone: 'ok' };
	return { label: 'Could not be checked', tone: 'neutral' };
}

function formatWhen(iso: string): string {
	return new Date(iso).toLocaleString();
}

function rulesetName(step: ProvenanceStep): string | null {
	if (!step.ruleset_id) return null;
	const version = step.ruleset_version ? ` v${step.ruleset_version}` : '';
	return `the ${step.ruleset_id} ruleset${version}`;
}

function assistantAttribution(step: ProvenanceStep): string {
	if (!step.approved_at) return 'Run by the assistant';
	const ruleset = rulesetName(step);
	const proposer = [
		`Proposed by ${step.model_name ?? 'the assistant'}`,
		ruleset ? `under ${ruleset}` : null
	]
		.filter(Boolean)
		.join(' ');
	return `${proposer}, approved by you at ${formatWhen(step.approved_at)}`;
}

/** Who decided this step happened, in the user's terms. */
export function stepAttribution(step: ProvenanceStep): string {
	if (step.actor_kind === 'assistant') return assistantAttribution(step);
	if (step.actor_kind === 'user') return 'Started by you';
	return 'Recorded by the platform';
}

/** When the step's run was made elsewhere and added to this workspace afterwards. */
function stepAdded(step: ProvenanceStep): string | null {
	if (!step.added_at) return null;
	const who = step.added_by === 'assistant' ? 'the assistant' : 'you';
	return `Added by ${who} at ${formatWhen(step.added_at)}`;
}

/** The steps that output this file's bytes, oldest first (identical output can be made more than once). */
export function producerSteps(provenance: ArtifactProvenance): ProvenanceStep[] {
	const byId = new Map(provenance.steps.map((step) => [step.id, step]));
	return provenance.producers.flatMap((id) => byId.get(id) ?? []);
}

/** Prose for a step, falling back to the operation name for older records. */
function stepHeadline(step: ProvenanceStep): string {
	return step.description || step.primitive;
}

export function hasEntries(value: Record<string, unknown> | null | undefined): boolean {
	return !!value && Object.keys(value).length > 0;
}

/** One line of "How this was made": a step, or a run of identical steps in a row. */
export type LineageRow = {
	steps: ProvenanceStep[];
	headline: string;
	attribution: string;
	/** Set when the row's run was added to the workspace by hand rather than made in it. */
	added: string | null;
	/** The code a code step ran, when it is a different file from the one being viewed. */
	codeFileId: string | null;
	/** A check that failed on any of the row's steps; matching steps say nothing. */
	problem: { label: string; tone: VerificationTone } | null;
};

function codeFileOf(step: ProvenanceStep, provenance: ArtifactProvenance): string | null {
	const code = provenance.artifacts.find(
		(file) =>
			file.format === 'python' &&
			file.id !== provenance.artifact.id &&
			step.outputs.includes(file.id)
	);
	return code?.id ?? null;
}

function rowProblem(steps: ProvenanceStep[]): LineageRow['problem'] {
	const badges = steps.map((step) => verificationBadge(step.verification));
	return badges.find((badge) => badge && badge.tone !== 'ok') ?? null;
}

function sameRow(row: LineageRow, step: ProvenanceStep, provenance: ArtifactProvenance): boolean {
	return (
		row.headline === stepHeadline(step) &&
		row.attribution === stepAttribution(step) &&
		row.added === stepAdded(step) &&
		row.codeFileId === null &&
		codeFileOf(step, provenance) === null
	);
}

/** The chain's steps, oldest first, with repeats of the same step folded into one row. */
export function lineageRows(provenance: ArtifactProvenance): LineageRow[] {
	const rows: LineageRow[] = [];
	for (const step of provenance.steps) {
		const last = rows.at(-1);
		if (last && sameRow(last, step, provenance)) {
			last.steps.push(step);
			last.problem = rowProblem(last.steps);
			continue;
		}
		rows.push({
			steps: [step],
			headline: stepHeadline(step),
			attribution: stepAttribution(step),
			added: stepAdded(step),
			codeFileId: codeFileOf(step, provenance),
			problem: rowProblem([step])
		});
	}
	return rows;
}
