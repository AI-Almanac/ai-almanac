import { describe, expect, it } from 'vitest';
import type { ChatMessage, GuardrailNotice } from '$lib/api';
import { currentGuardrails } from '$lib/chat/guardrails';

function turn(id: string, guardrails: GuardrailNotice[]): ChatMessage {
	return { id, role: 'assistant', content: '', guardrails } as unknown as ChatMessage;
}

const blendError: GuardrailNotice = {
	config_kind: 'blend',
	errors: ['training_years must look like "2005:2010"'],
	warnings: []
};
const blendClean: GuardrailNotice = { config_kind: 'blend', errors: [], warnings: [] };
const benchmarkWarning: GuardrailNotice = {
	config_kind: 'benchmark',
	errors: [],
	warnings: ['Only 6 test years']
};

describe('currentGuardrails', () => {
	it('drops errors once a later validation of the same config is clean', () => {
		const shown = currentGuardrails([turn('a', [blendError, blendError, blendClean])]);
		expect(shown.size).toBe(0);
	});

	it('clears errors from an earlier turn when a later turn fixes the config', () => {
		const shown = currentGuardrails([turn('a', [blendError]), turn('b', [blendClean])]);
		expect(shown.size).toBe(0);
	});

	it('keeps only the latest finding for a config that is still invalid', () => {
		const latest = { ...blendError, errors: ['Climatology needs 10 years'] };
		const shown = currentGuardrails([turn('a', [blendError]), turn('b', [latest])]);
		expect([...shown.keys()]).toEqual(['b']);
		expect(shown.get('b')).toEqual([latest]);
	});

	it('tracks benchmark and blend configs independently', () => {
		const shown = currentGuardrails([turn('a', [benchmarkWarning]), turn('b', [blendClean])]);
		expect(shown.get('a')).toEqual([benchmarkWarning]);
	});
});
