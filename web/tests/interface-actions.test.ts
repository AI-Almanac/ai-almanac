import { describe, expect, it, vi } from 'vitest';

import {
	actionFromToolCall,
	actionFromToolResult,
	actionLabel,
	parseAction,
	dispatchAction,
	type InterfaceAction
} from '../src/lib/interface-actions/actions';
import { opened } from '../src/lib/workspace/panels';

describe('reading an action the assistant sent', () => {
	it('takes a setup panel with the fields that were filled in, and nothing else', () => {
		const action = parseAction({
			kind: 'open_panel',
			panel: 'blend_setup',
			draft: {
				name: 'Kiremt blend',
				model_ids: ['aifs', 7, 'fuxi'],
				training_years: '',
				threshold_mm: 'twenty',
				focus_area: { level: 'adm2', units: ['Awi'] },
				onclick: 'alert(1)'
			}
		});

		expect(action).toEqual({
			kind: 'open_panel',
			panel: 'blend_setup',
			draft: {
				name: 'Kiremt blend',
				model_ids: ['aifs', 'fuxi'],
				focus_area: { level: 'adm2', units: ['Awi'] }
			}
		});
	});

	it('takes an area given as a box only when all four edges are numbers', () => {
		const box = { lat_min: 9, lat_max: 13, lon_min: 36, lon_max: 40 };
		const draftOf = (focus_area: unknown) =>
			parseAction({ kind: 'open_panel', panel: 'benchmark_setup', draft: { focus_area } });

		expect(draftOf(box)).toMatchObject({ draft: { focus_area: box } });
		expect(draftOf({ ...box, lon_max: 'east' })).toMatchObject({ draft: { model_ids: [] } });
		expect(draftOf({ ...box, lon_max: 'east' })?.kind === 'open_panel').toBe(true);
	});

	it('takes a run or a file to show', () => {
		expect(parseAction({ kind: 'show', target: { kind: 'run', job_ids: ['j1', 'j2'] } })).toEqual({
			kind: 'show',
			target: { kind: 'run', jobIds: ['j1', 'j2'] }
		});
		expect(parseAction({ kind: 'show', target: { kind: 'file', artifact_id: 'a1' } })).toEqual({
			kind: 'show',
			target: { kind: 'artifact', ref: 'a1' }
		});
	});

	it('is nothing for a kind, a panel or a target this interface does not know', () => {
		expect(parseAction({ kind: 'navigate', url: 'https://example.com' })).toBeNull();
		expect(parseAction({ kind: 'open_panel', panel: 'admin', draft: {} })).toBeNull();
		expect(parseAction({ kind: 'show', target: { kind: 'run', job_ids: [] } })).toBeNull();
		expect(parseAction('open_panel')).toBeNull();
		expect(parseAction(null)).toBeNull();
	});

	it('finds the action in a tool result, and none in a result that is an error', () => {
		const result = {
			interface_action: { kind: 'open_panel', panel: 'forecast_setup', draft: { blend_id: 'b1' } },
			offered: 'The form is offered.'
		};

		expect(actionFromToolResult(result)).toEqual({
			kind: 'open_panel',
			panel: 'forecast_setup',
			draft: { blend_id: 'b1', models: [] }
		});
		expect(actionFromToolResult({ error: 'No model x.' })).toBeNull();
		expect(actionFromToolResult(undefined)).toBeNull();
	});
});

describe('which tool results are actions', () => {
	const result = { interface_action: { kind: 'show', target: { kind: 'run', job_ids: ['j1'] } } };

	it('reads one from the tools that offer actions', () => {
		expect(actionFromToolCall({ name: 'show', result })).not.toBeNull();
	});

	it('reads none from any other tool, whatever its result holds', () => {
		expect(actionFromToolCall({ name: 'run_code', result })).toBeNull();
		expect(actionFromToolCall({ name: 'fetch', result })).toBeNull();
	});
});

describe('the button that repeats an action', () => {
	it('says what it brings up', () => {
		expect(
			actionLabel({ kind: 'open_panel', panel: 'blend_setup', draft: { model_ids: [] } })
		).toBe('Open the blend setup');
		expect(actionLabel({ kind: 'show', target: { kind: 'run', jobIds: ['j1'] } })).toBe(
			'Show the run'
		);
	});
});

describe('handing an action to a page', () => {
	const show: InterfaceAction = { kind: 'show', target: { kind: 'run', jobIds: ['j1'] } };

	it('goes to the page’s handler for that kind', () => {
		const handler = vi.fn();

		expect(dispatchAction(show, { show: handler })).toBe(true);
		expect(handler).toHaveBeenCalledWith(show);
	});

	it('does nothing on a page that has no handler for it', () => {
		expect(dispatchAction(show, { open_panel: vi.fn() })).toBe(false);
	});
});

describe('opening a panel', () => {
	it('counts each opening, so the same panel asked for again is shown afresh', () => {
		const first = opened({ panel: 'blend_setup' }, null);
		const second = opened({ panel: 'blend_setup' }, first);

		expect(second.opening).toBeGreaterThan(first.opening);
	});
});
