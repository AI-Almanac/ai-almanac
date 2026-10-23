import type { AssistantStartup } from '$lib/api';

/** vLLM's startup stages in order, as the server reports them. */
export const STARTUP_STAGES: { stage: AssistantStartup['stage']; label: string; hint?: string }[] =
	[
		{ stage: 'starting', label: 'Launching the model server' },
		{ stage: 'loading_weights', label: 'Loading the model' },
		{
			stage: 'compiling',
			label: 'Compiling GPU kernels',
			hint: 'Several minutes the first time; much faster once they are cached.'
		},
		{ stage: 'capturing', label: 'Preparing the GPU' },
		{ stage: 'tuning', label: 'Tuning kernels for this GPU', hint: 'One pass per kernel shape.' },
		{ stage: 'finishing', label: 'Almost ready' }
	];

export type StartupStep = {
	label: string;
	state: 'done' | 'current' | 'pending';
	hint?: string;
	// e.g. "CUDA graphs · 4 of 7" or "fp8_gemm · pass 12 of about 46", current step only.
	detail?: string;
	// 0–1 through the work in hand (one tuning pass, the graph captures).
	fraction?: number;
};

export function startupSteps(progress: AssistantStartup): StartupStep[] {
	const current = Math.max(
		0,
		STARTUP_STAGES.findIndex((entry) => entry.stage === progress.stage)
	);
	return STARTUP_STAGES.map((entry, index) => {
		const state = index < current ? 'done' : index === current ? 'current' : 'pending';
		const step: StartupStep = { label: entry.label, state };
		if (state !== 'current') return step;
		step.hint = entry.hint;
		const parts = progress.detail ? [progress.detail] : [];
		if (progress.passes_done != null) {
			parts.push(
				progress.passes_expected
					? `pass ${progress.passes_done} of about ${progress.passes_expected}`
					: `pass ${progress.passes_done}`
			);
		} else if (progress.total) {
			parts.push(`${Math.min(progress.done ?? 0, progress.total)} of ${progress.total}`);
		}
		if (parts.length) step.detail = parts.join(' · ');
		if (progress.total)
			step.fraction = Math.min(progress.done ?? 0, progress.total) / progress.total;
		return step;
	});
}

export type StartupOverall = {
	fraction: number;
	// e.g. "About 3 min left", "Taking longer than last time", "Rough estimate".
	label: string;
};

/** The whole start's progress, and what it is measured against. */
export function startupOverall(progress: AssistantStartup): StartupOverall | null {
	if (progress.overall == null) return null;
	const fraction = Math.min(Math.max(progress.overall, 0), 1);
	if (progress.expected_seconds == null || progress.elapsed_seconds == null) {
		return { fraction, label: 'Rough estimate: the first start with these settings' };
	}
	const left = progress.expected_seconds - progress.elapsed_seconds;
	if (left <= 0) return { fraction, label: 'Taking longer than last time' };
	if (left < 60) return { fraction, label: 'Under a minute left' };
	return { fraction, label: `About ${Math.round(left / 60)} min left` };
}

/** `75` → `1:15`; `3725` → `1:02:05`. */
export function formatElapsed(seconds: number | null): string | null {
	if (seconds == null || !Number.isFinite(seconds)) return null;
	const total = Math.floor(seconds);
	const parts = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
	const [hours, minutes, secs] = parts;
	const pad = (value: number) => String(value).padStart(2, '0');
	return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}
