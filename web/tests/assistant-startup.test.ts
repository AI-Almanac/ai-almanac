import { describe, expect, it } from 'vitest';
import type { AssistantStartup } from '../src/lib/api';
import { formatElapsed, startupOverall, startupSteps } from '../src/lib/chat/startup';

function progress(overrides: Partial<AssistantStartup>): AssistantStartup {
	return {
		stage: 'starting',
		elapsed_seconds: null,
		detail: null,
		done: null,
		total: null,
		passes_done: null,
		passes_expected: null,
		overall: null,
		expected_seconds: null,
		previous_failure: null,
		...overrides
	};
}

describe('assistant startup progress', () => {
	it('marks earlier stages done, the reported one current, and later ones pending', () => {
		const steps = startupSteps(progress({ stage: 'compiling' }));

		expect(steps.map((step) => step.state)).toEqual([
			'done',
			'done',
			'current',
			'pending',
			'pending',
			'pending'
		]);
		expect(steps[2].hint).toMatch(/first time/);
		expect(steps.filter((step) => step.hint)).toHaveLength(1);
	});

	it('shows the count and fraction of the work in hand on the current stage only', () => {
		const steps = startupSteps(
			progress({ stage: 'capturing', detail: 'CUDA graphs', done: 4, total: 7 })
		);
		const capturing = steps.find((step) => step.state === 'current');

		expect(capturing?.detail).toBe('CUDA graphs · 4 of 7');
		expect(capturing?.fraction).toBeCloseTo(4 / 7);
		expect(steps.filter((step) => step.fraction !== undefined)).toHaveLength(1);
	});

	it('shows the current pass with its own bar and the pass count against the last start', () => {
		const tuning = startupSteps(
			progress({
				stage: 'tuning',
				detail: 'fp8_gemm',
				done: 11,
				total: 16,
				passes_done: 12,
				passes_expected: 46
			})
		).find((step) => step.state === 'current');

		expect(tuning?.detail).toBe('fp8_gemm · pass 12 of about 46');
		expect(tuning?.fraction).toBeCloseTo(11 / 16);
	});

	it('counts passes without a total when no earlier start is on record', () => {
		const tuning = startupSteps(
			progress({ stage: 'tuning', detail: 'fp8_gemm', done: 3, total: 16, passes_done: 2 })
		).find((step) => step.state === 'current');

		expect(tuning?.detail).toBe('fp8_gemm · pass 2');
	});

	it('labels overall progress by what it is measured against', () => {
		expect(startupOverall(progress({}))).toBeNull();
		expect(startupOverall(progress({ overall: 0.5 }))).toEqual({
			fraction: 0.5,
			label: 'Rough estimate: the first start with these settings'
		});
		expect(
			startupOverall(progress({ overall: 0.5, elapsed_seconds: 60, expected_seconds: 240 }))
		).toEqual({ fraction: 0.5, label: 'About 3 min left' });
		expect(
			startupOverall(progress({ overall: 0.9, elapsed_seconds: 200, expected_seconds: 230 }))?.label
		).toBe('Under a minute left');
		expect(
			startupOverall(progress({ overall: 0.99, elapsed_seconds: 300, expected_seconds: 240 }))
				?.label
		).toBe('Taking longer than last time');
	});

	it('formats elapsed time as m:ss, adding hours past an hour', () => {
		expect(formatElapsed(null)).toBeNull();
		expect(formatElapsed(7.9)).toBe('0:07');
		expect(formatElapsed(75)).toBe('1:15');
		expect(formatElapsed(3725)).toBe('1:02:05');
	});
});
