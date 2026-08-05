import { discardComparison, voteOnComparison, type CompareEvent, type RevealedArm } from '$lib/api';

/**
 * Why the A/B button is unavailable, or null when it will run.
 *
 * `text` is the message that would be compared: what the user typed, or their
 * last question when the box is empty — comparing the answer already on screen
 * is the most obvious moment to want a second opinion.
 */
export function compareBlocker(ready: boolean, text: string): string | null {
	if (!ready) return 'Ask an administrator to publish two assistant styles to compare';
	if (!text.trim()) return 'Type a question first, or ask one and then compare the answer';
	return null;
}

export type ComparisonArm = {
	label: string;
	sessionId: string;
};

export type ArmAnswer = {
	text: string;
	tools: string[];
	cautions: string[];
	error: string | null;
};

export type ComparisonRound = {
	message: string;
	/** Index-aligned with `arms`. */
	answers: ArmAnswer[];
};

/**
 * Folds comparison SSE streams into a side-by-side dialogue — arms across,
 * rounds down — and owns the vote/discard lifecycle. Labeled (admin) and blind
 * (user) comparisons are the same state machine: a blind variant arrives
 * without a ruleset name, so its arm is labeled "Answer A"/"Answer B" and
 * `revealedArms` fills in after the vote. One vote covers every round.
 */
export class ComparisonState {
	arms = $state<ComparisonArm[]>([]);
	rounds = $state<ComparisonRound[]>([]);
	comparisonId = $state<string | null>(null);
	running = $state(false);
	voted = $state(false);
	revealedArms = $state<RevealedArm[]>([]);
	error = $state<string | null>(null);

	/** Maps the active stream's variant index onto an `arms` index; each
	 * stream declares its own ordering in `comparison_started`. */
	private variantToArm: number[] = [];

	async start(message: string, events: AsyncGenerator<CompareEvent>) {
		if (this.running) return;
		// Discard the previous scratch conversations, not the ratings: the turn
		// log keeps every vote already recorded against a ruleset version.
		if (this.comparisonId) await discardComparison(this.comparisonId).catch(() => undefined);
		this.arms = [];
		this.rounds = [];
		this.comparisonId = null;
		this.voted = false;
		this.revealedArms = [];
		await this.streamRound(message, events);
	}

	async followUp(message: string, events: AsyncGenerator<CompareEvent>) {
		if (this.running || !this.comparisonId) return;
		await this.streamRound(message, events);
	}

	private async streamRound(message: string, events: AsyncGenerator<CompareEvent>) {
		this.running = true;
		this.error = null;
		this.rounds.push({ message, answers: [] });
		try {
			for await (const event of events) this.apply(event);
		} catch (e) {
			this.error = (e as Error).message;
		} finally {
			this.running = false;
		}
	}

	private apply(event: CompareEvent) {
		const round = this.rounds[this.rounds.length - 1];
		if (!round) return;
		if (event.type === 'comparison_started') {
			this.comparisonId = event.comparison_id;
			if (this.arms.length === 0) {
				this.arms = event.variants.map((variant, i) => ({
					label: variant.ruleset_name
						? `${variant.ruleset_name} v${variant.ruleset_version}${
								variant.model ? ` · ${variant.model}` : ''
							}`
						: `Answer ${String.fromCharCode(65 + i)}`,
					sessionId: variant.session_id
				}));
			}
			this.variantToArm = event.variants.map((variant) =>
				this.arms.findIndex((arm) => arm.sessionId === variant.session_id)
			);
			round.answers = this.arms.map(() => ({ text: '', tools: [], cautions: [], error: null }));
			return;
		}
		if (event.type === 'comparison_complete') return;
		const answer = round.answers[this.variantToArm[event.variant] ?? -1];
		if (!answer) return;
		if (event.type === 'text_delta') answer.text += event.content ?? '';
		if (event.type === 'tool_call') answer.tools.push(event.tool_call.name);
		if (event.type === 'guardrail') {
			// Every tool call re-emits the current validation, so one turn that
			// updates and then validates a config carries the same caution twice —
			// and the board keys cautions by their text.
			for (const caution of [...(event.errors ?? []), ...(event.warnings ?? [])]) {
				if (!answer.cautions.includes(caution)) answer.cautions.push(caution);
			}
		}
		if (event.type === 'error') answer.error = event.message ?? 'Failed';
		if (event.type === 'done') answer.text = event.turn.content;
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
		this.arms = [];
		this.rounds = [];
		this.comparisonId = null;
		this.voted = false;
		this.revealedArms = [];
	}
}
