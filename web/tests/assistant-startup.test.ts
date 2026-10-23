import { describe, expect, it } from 'vitest';
import type { AssistantStartup } from '../src/lib/api';
import { formatElapsed, startupSteps } from '../src/lib/chat/startup';

function progress(overrides: Partial<AssistantStartup>): AssistantStartup {
	return {
		stage: 'starting',
		elapsed_seconds: null,
		detail: null,
		done: null,
		total: null,
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
			progress({ stage: 'tuning', detail: 'fp8_gemm', done: 11, total: 16 })
		);
		const tuning = steps.find((step) => step.state === 'current');

		expect(tuning?.detail).toBe('fp8_gemm · 11 of 16');
		expect(tuning?.fraction).toBeCloseTo(11 / 16);
		expect(steps.filter((step) => step.fraction !== undefined)).toHaveLength(1);
	});

	it('formats elapsed time as m:ss, adding hours past an hour', () => {
		expect(formatElapsed(null)).toBeNull();
		expect(formatElapsed(7.9)).toBe('0:07');
		expect(formatElapsed(75)).toBe('1:15');
		expect(formatElapsed(3725)).toBe('1:02:05');
	});
});
