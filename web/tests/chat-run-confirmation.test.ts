import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import ChatRunConfirmation from '../src/lib/components/ChatRunConfirmation.svelte';
import { approvalFromRequest, type PendingApproval } from '../src/lib/chat/approval';
import { call } from './fixtures/chat';

const obsId = 'f1fe0000-obs';
const modelIds = ['a5c90000-aifs', 'db950000-fuxi'];

/** The card a step's approval request asks for, with the facts the server wrote. */
function stepApproval(toolName: string, facts: unknown[]): PendingApproval {
	return approvalFromRequest({
		type: 'tool_approval_request',
		turn_id: 'turn-1',
		tool_call: call(toolName, { id: 'call-1', status: 'running' }),
		metadata: {
			params: { spec: { name: 'Kiremt blend', obs_dataset_id: obsId, model_ids: modelIds } },
			display: { facts }
		}
	});
}

function blendStep() {
	return stepApproval('submit_blend', [
		{ label: 'Observations', value: 'CHIRPS (1981–2024)' },
		{ label: 'Models', value: 'AIFS, FuXi' },
		{ label: 'Training years', value: '2000–2019' }
	]);
}

function renderCard(approval = blendStep()) {
	return render(ChatRunConfirmation, { approval, onApprove: vi.fn(), onDecline: vi.fn() });
}

describe('ChatRunConfirmation for a workspace step', () => {
	it('names the data the step will use', () => {
		renderCard();

		expect(screen.getByText('Ready to train')).toBeTruthy();
		expect(screen.getByText('CHIRPS (1981–2024)')).toBeTruthy();
		expect(screen.getByText('AIFS, FuXi')).toBeTruthy();
		expect(screen.getByText('2000–2019')).toBeTruthy();
	});

	it('shows no ids, only the facts in the user’s words', () => {
		const { container } = renderCard();

		const shown = container.textContent ?? '';
		expect(shown).not.toContain(obsId);
		expect(modelIds.some((id) => shown.includes(id))).toBe(false);
		expect(screen.queryByText('Show exact request')).toBeNull();
	});

	it('lists what will run before the buttons that start it', () => {
		const { container } = renderCard();

		const shown = container.textContent ?? '';
		expect(shown.indexOf('Ready to train')).toBeLessThan(shown.indexOf('Training years'));
		expect(shown.indexOf('Training years')).toBeLessThan(shown.indexOf('Train blend'));
		expect(shown.indexOf('Train blend')).toBeLessThan(shown.indexOf('Not yet'));
	});

	it('says the years, the onset definition, where it runs and how long it should take', () => {
		renderCard(
			stepApproval('submit_benchmark', [
				{ label: 'Evaluation years', value: '2000–2023' },
				{ label: 'Onset definition', value: 'Standard wet spell' },
				{ label: 'Runs on', value: 'This computer' },
				{ label: 'Expected time', value: 'About 3 minutes per model' }
			])
		);

		for (const text of [
			'Evaluation years',
			'2000–2023',
			'Standard wet spell',
			'This computer',
			'About 3 minutes per model'
		]) {
			expect(screen.getByText(text)).toBeTruthy();
		}
		expect(screen.getByRole('button', { name: 'Run benchmark' })).toBeTruthy();
	});

	it('warns about the plan above the buttons too', () => {
		const { container } = render(ChatRunConfirmation, {
			approval: {
				kind: 'benchmark' as const,
				toolCallId: 'call-1',
				config: { model_names: ['AIFS'], region_name: 'Ethiopia' } as never,
				validation: { warnings: ['Only 8 evaluation years'] } as never
			},
			onApprove: vi.fn(),
			onDecline: vi.fn()
		});

		const shown = container.textContent ?? '';
		expect(shown).toContain('AIFS · Ethiopia · Days 1–30');
		expect(shown.indexOf('Only 8 evaluation years')).toBeLessThan(shown.indexOf('Run benchmark'));
	});

	it('answers through the buttons', async () => {
		const onApprove = vi.fn();
		const onDecline = vi.fn();
		render(ChatRunConfirmation, { approval: blendStep(), onApprove, onDecline });

		await fireEvent.click(screen.getByRole('button', { name: 'Train blend' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Not yet' }));

		expect(onApprove).toHaveBeenCalledOnce();
		expect(onDecline).toHaveBeenCalledOnce();
	});

	it('asks to forecast in the forecaster’s words', () => {
		renderCard(stepApproval('run_forecast', []));

		expect(screen.getByText('Ready to forecast')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Run forecast' })).toBeTruthy();
	});

	it('leaves out facts that are not label and value text', () => {
		const { container } = renderCard(
			stepApproval('submit_blend', [{ label: 'Models', value: 'AIFS' }, { label: 'Days' }, null])
		);

		expect(screen.getByText('AIFS')).toBeTruthy();
		expect(container.textContent).not.toContain('Days');
	});
});
