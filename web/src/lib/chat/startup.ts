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
		{ stage: 'tuning', label: 'Tuning kernels for this GPU', hint: 'Usually a few minutes.' },
		{ stage: 'finishing', label: 'Almost ready' }
	];

export type StartupStep = {
	label: string;
	state: 'done' | 'current' | 'pending';
	hint?: string;
	// e.g. "fp8_gemm · 11 of 16", only on the current step.
	detail?: string;
	// 0–1 through `detail`, when the server reports a count.
	fraction?: number;
};

export function startupSteps(progress: AssistantStartup): StartupStep[] {
	const current = Math.max(
		0,
		STARTUP_STAGES.findIndex((entry) => entry.stage === progress.stage)
	);
	return STARTUP_STAGES.map((entry, index) => {
		const state = index < current ? 'done' : index === current ? 'current' : 'pending';
		const step: StartupStep = { label: entry.label, state, hint: entry.hint };
		if (state === 'current' && progress.total) {
			const done = Math.min(progress.done ?? 0, progress.total);
			step.detail = `${progress.detail ? `${progress.detail} · ` : ''}${done} of ${progress.total}`;
			step.fraction = done / progress.total;
		}
		if (state !== 'current') delete step.hint;
		return step;
	});
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
