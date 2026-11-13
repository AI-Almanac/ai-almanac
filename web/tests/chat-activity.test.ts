import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import ChatActivity from '../src/lib/components/ChatActivity.svelte';
import { technicalDetails } from '../src/lib/chat/technical-details';
import type { ChatArtifact, ChatToolCall } from '../src/lib/api';
import type { ActivityGroup } from '../src/lib/chat/timeline';
import { call } from './fixtures/chat';

vi.mock('../src/lib/components/FigureCard.svelte', async () => ({
	default: (await import('./fixtures/FigureCardStub.svelte')).default
}));

const figure: ChatArtifact = {
	id: 'fig-1',
	kind: 'figure',
	url: '/figure.webp',
	label: 'Onset error by zone',
	created_at: ''
};

function group(...toolCalls: ChatToolCall[]): ActivityGroup {
	return {
		kind: 'activity',
		key: 'activity-0',
		steps: toolCalls.map((toolCall) => ({ kind: 'tool', toolCall })),
		figures: toolCalls.flatMap((toolCall) => toolCall.artifacts ?? [])
	};
}

async function openedTrace(
	activity: ActivityGroup,
	props: { drawFigures?: boolean; showTechnicalDetails?: boolean } = {}
) {
	const onOpenArtifact = vi.fn();
	const view = render(ChatActivity, {
		group: activity,
		live: false,
		onOpenArtifact,
		showTechnicalDetails: true,
		...props
	});
	await fireEvent.click(view.container.querySelector('.activity-header')!);
	return { ...view, onOpenArtifact };
}

describe('technical details of a step', () => {
	it('names the tool and what it was passed', () => {
		const details = technicalDetails(
			call('plot_map', { input: { artifact: 'spatial_metrics_aifs_1-15', variable: 'mean_mae' } })
		);

		expect(details.tool).toBe('plot_map');
		expect(JSON.parse(details.arguments ?? '')).toEqual({
			artifact: 'spatial_metrics_aifs_1-15',
			variable: 'mean_mae'
		});
		expect(details.code).toBeNull();
	});

	it('keeps code apart from the other arguments', () => {
		const details = technicalDetails(
			call('run_code', { input: { code: 'def compute(inputs): ...', inputs: ['rain'] } })
		);

		expect(details.code).toBe('def compute(inputs): ...');
		expect(JSON.parse(details.arguments ?? '')).toEqual({ inputs: ['rain'] });
	});

	it('never shows an internal id among the arguments', () => {
		const details = technicalDetails(
			call('plot_map', {
				input: {
					job_id: 'job-1',
					run_ids: ['run-1'],
					workspace_id: 'ws-1',
					artifact_id: 'a-1',
					id: 'x',
					source: '0b9f4c1e-8a3d-4f6b-9c2e-7d5a1b3c4e6f',
					variable: 'mean_mae',
					options: { session_id: 's-1', levels: [1, 2] },
					files: ['0b9f4c1e-8a3d-4f6b-9c2e-7d5a1b3c4e6f', 'rain.nc']
				}
			})
		);

		expect(JSON.parse(details.arguments ?? '')).toEqual({
			variable: 'mean_mae',
			options: { levels: [1, 2] },
			files: ['rain.nc']
		});
	});

	it('has nothing to show when every argument was an id', () => {
		expect(
			technicalDetails(call('get_job_metrics', { input: { job_id: 'job-1' } })).arguments
		).toBeNull();
	});

	it('has no arguments to show for a call that took none', () => {
		expect(technicalDetails(call('list_regions')).arguments).toBeNull();
	});
});

describe('the working trace', () => {
	it('lists each step in the same plain words as the collapsed line', async () => {
		const { container } = await openedTrace(
			group(call('list_regions'), call('plot_map', { input: { variable: 'mean_mae' } }))
		);

		const labels = [...container.querySelectorAll('.step-label')].map((el) => el.textContent);
		const header = container.querySelector('.activity-label')?.textContent ?? '';
		expect(labels).toHaveLength(2);
		for (const label of labels) {
			expect(header).toContain(label);
			expect(label).not.toMatch(/_/);
		}
	});

	it('keeps tool names and arguments under technical details that start closed', async () => {
		await openedTrace(group(call('plot_map', { input: { variable: 'mean_mae' } })));

		const toolName = screen.getByText('plot_map');
		const details = toolName.closest('details');
		expect(details).not.toBeNull();
		expect(details?.open).toBe(false);
		expect(details?.querySelector('summary')?.textContent).toBe('Technical details');
		expect(screen.getByText(/"variable": "mean_mae"/).closest('details')).toBe(details);
	});

	it('outside a workspace offers only the code of a code step, with no technical details', async () => {
		const { container } = await openedTrace(
			group(
				call('run_code', { input: { code: 'def compute(inputs): ...', job_id: 'job-1' } }),
				call('plot_map', { input: { job_id: 'job-1' } })
			),
			{ showTechnicalDetails: false }
		);

		expect(screen.queryByText('Technical details')).toBeNull();
		expect(container.textContent).not.toContain('plot_map');
		expect(container.textContent).not.toContain('job-1');
		const shown = screen.getAllByText('Show code');
		expect(shown).toHaveLength(1);
		const details = shown[0].closest('details')!;
		expect(details.open).toBe(false);

		await fireEvent.click(shown[0]);
		expect(details.open).toBe(true);
		expect(screen.getByText('def compute(inputs): ...').closest('details')).toBe(details);
		expect(screen.getByRole('button', { name: 'Copy' }).closest('details')).toBe(details);
	});

	it('shows nothing technical until the trace is opened', () => {
		render(ChatActivity, {
			group: group(call('plot_map')),
			live: false,
			onOpenArtifact: vi.fn()
		});

		expect(screen.queryByText('plot_map')).toBeNull();
		expect(screen.queryByText('Technical details')).toBeNull();
	});

	it('says why a step failed in the open, above the technical details', async () => {
		await openedTrace(
			group(call('plot_map', { status: 'failed', result: { error: 'No such file' } }))
		);

		expect(screen.getByText('No such file').closest('details')).toBeNull();
	});

	it('draws a step’s figures unless whatever hosts it shows them itself', async () => {
		const made = group(call('plot_map', { artifacts: [figure] }));

		const drawn = await openedTrace(made);
		await fireEvent.click(screen.getByText('Onset error by zone'));
		expect(drawn.onOpenArtifact).toHaveBeenCalledWith('fig-1');
		drawn.unmount();

		await openedTrace(made, { drawFigures: false });
		expect(screen.queryByText('Onset error by zone')).toBeNull();
	});
});
