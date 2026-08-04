import { discardComparison, voteOnComparison, type CompareEvent, type RevealedArm } from '$lib/api';

export type ComparisonColumn = {
	label: string;
	sessionId: string;
	text: string;
	tools: string[];
	cautions: string[];
	error: string | null;
};

/**
 * Folds a comparison SSE stream into side-by-side columns and owns the
 * vote/discard lifecycle. Labeled (admin) and blind (user) comparisons are the
 * same state machine — a blind variant simply arrives without a ruleset name,
 * so its column is labeled "Answer A"/"Answer B" and `revealedArms` fills in
 * after the vote.
 */
export class ComparisonState {
	columns = $state<ComparisonColumn[]>([]);
	comparisonId = $state<string | null>(null);
	running = $state(false);
	voted = $state(false);
	revealedArms = $state<RevealedArm[]>([]);
	error = $state<string | null>(null);

	async run(events: AsyncGenerator<CompareEvent>) {
		if (this.running) return;
		this.running = true;
		this.error = null;
		// Discard the previous scratch conversations, not the ratings: the turn
		// log keeps every vote already recorded against a ruleset version.
		if (this.comparisonId) await discardComparison(this.comparisonId).catch(() => undefined);
		this.comparisonId = null;
		this.columns = [];
		this.voted = false;
		this.revealedArms = [];
		try {
			for await (const event of events) this.apply(event);
		} catch (e) {
			this.error = (e as Error).message;
		} finally {
			this.running = false;
		}
	}

	private apply(event: CompareEvent) {
		if (event.type === 'comparison_started') {
			this.comparisonId = event.comparison_id;
			this.columns = event.variants.map((variant, i) => ({
				label: variant.ruleset_name
					? `${variant.ruleset_name} v${variant.ruleset_version}${
							variant.model ? ` · ${variant.model}` : ''
						}`
					: `Answer ${String.fromCharCode(65 + i)}`,
				sessionId: variant.session_id,
				text: '',
				tools: [],
				cautions: [],
				error: null
			}));
			return;
		}
		if (event.type === 'comparison_complete') return;
		const column = this.columns[event.variant ?? -1];
		if (!column) return;
		if (event.type === 'text_delta') column.text += event.content ?? '';
		if (event.type === 'tool_call') column.tools.push(event.tool_call.name);
		if (event.type === 'guardrail') {
			column.cautions.push(...(event.errors ?? []), ...(event.warnings ?? []));
		}
		if (event.type === 'error') column.error = event.message ?? 'Failed';
		if (event.type === 'done') column.text = event.turn.content;
	}

	async vote(winnerSessionId: string | null, note?: string): Promise<boolean> {
		if (!this.comparisonId) return false;
		try {
			const result = await voteOnComparison(this.comparisonId, winnerSessionId, note);
			this.voted = true;
			this.revealedArms = result.arms ?? [];
			return true;
		} catch (e) {
			this.error = (e as Error).message;
			return false;
		}
	}

	revealedName(sessionId: string): string | null {
		const arm = this.revealedArms.find((a) => a.session_id === sessionId);
		if (!arm) return null;
		const version = arm.ruleset_version != null ? ` v${arm.ruleset_version}` : '';
		return arm.ruleset_name ? `${arm.ruleset_name}${version}` : 'unknown';
	}

	async discard() {
		if (this.comparisonId) await discardComparison(this.comparisonId).catch(() => undefined);
		this.comparisonId = null;
		this.columns = [];
		this.voted = false;
		this.revealedArms = [];
	}
}
