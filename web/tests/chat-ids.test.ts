import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '../src/lib/api';
import { hideIds, namesById } from '../src/lib/chat/ids';

const ENS = '290b5843-89c3-4070-9d1d-19fc4ff67ec3';
const AIFS = '9aa0415a-802c-4509-9bcd-eda04c7b3878';
const AURORA = '4629ef23-4e97-4b0d-86a1-fe2153e64e0e';
const CHIRPS = 'ac2e2f7d-12e5-4841-946b-6e3c97b6c811';
const UNKNOWN = 'd36e37d3-0000-4000-8000-000000000000';

function turn(results: unknown[]): ChatMessage {
	return {
		id: 't',
		role: 'assistant',
		content: '',
		created_at: '2026-10-23T00:00:00Z',
		tool_calls: results.map((result, i) => ({
			id: `call-${i}`,
			name: 'tool',
			status: 'completed',
			input: {},
			artifacts: [],
			result
		}))
	} as ChatMessage;
}

const names = namesById([
	turn([
		[
			{ id: ENS, display_name: 'AIFS‑ENSv2' },
			{ id: AIFS, display_name: 'AIFSv2' }
		],
		JSON.stringify([{ id: CHIRPS, name: 'Chirps' }]),
		{ blend_config: { model_ids: [AURORA], model_names: ['Aurora 1.5'] } }
	])
]);

describe('namesById', () => {
	it('learns names from records, paired fields and JSON text', () => {
		expect(names.get(ENS)).toBe('AIFS‑ENSv2');
		expect(names.get(CHIRPS)).toBe('Chirps');
		expect(names.get(AURORA)).toBe('Aurora 1.5');
	});
});

describe('hideIds', () => {
	it('drops an ID column that only repeats the names beside it', () => {
		const table = [
			'| Model ID | Name | Training years |',
			'|---|---|---|',
			`| ${ENS} | AIFS‑ENSv2 | 1979‑2022 |`,
			`| \`${AIFS}\` | AIFSv2 | 1979‑2022 |`
		].join('\n');

		expect(hideIds(table, names)).toBe(
			[
				'| Name | Training years |',
				'| --- | --- |',
				'| AIFS‑ENSv2 | 1979‑2022 |',
				'| AIFSv2 | 1979‑2022 |'
			].join('\n')
		);
	});

	it('shows names in a field/value table and relabels the field', () => {
		const table = [
			'| Field | Value |',
			'|---|---|',
			`| Observation dataset | ${CHIRPS} (Chirps) |`,
			`| Model IDs | ${ENS}, ${AIFS}, ${AURORA} |`
		].join('\n');

		expect(hideIds(table, names).split('\n').slice(2)).toEqual([
			'| Observation dataset | Chirps |',
			'| Models | AIFS‑ENSv2, AIFSv2, Aurora 1.5 |'
		]);
	});

	it('keeps an ID column it cannot drop, showing names under a plain header', () => {
		const table = ['| Model ID | Years |', '|---|---|', `| ${AIFS} | 1979‑2022 |`].join('\n');

		expect(hideIds(table, names).split('\n')).toEqual([
			'| Model | Years |',
			'| --- | --- |',
			'| AIFSv2 | 1979‑2022 |'
		]);
	});

	it.each([
		[`AIFSv2 (\`${AIFS}\`) scored best.`, 'AIFSv2 scored best.'],
		[`Chirps (dataset ID ${CHIRPS}) covers Ethiopia.`, 'Chirps covers Ethiopia.'],
		[`The dataset (${CHIRPS}) covers Ethiopia.`, 'The dataset (Chirps) covers Ethiopia.'],
		[`Use model ${AIFS} next.`, 'Use model AIFSv2 next.'],
		[`Model IDs: ${ENS}, ${AIFS}`, 'Models: AIFS‑ENSv2, AIFSv2'],
		[`Run ${UNKNOWN} failed.`, 'Run failed.'],
		[`Run (ID: ${UNKNOWN}) failed.`, 'Run failed.']
	])('rewrites %s', (text, expected) => {
		expect(hideIds(text, names)).toBe(expected);
	});

	it('leaves code blocks and ID-free text alone', () => {
		const text = ['Results for 2019-2024:', '```', `id = "${AIFS}"`, '```'].join('\n');
		expect(hideIds(text, names)).toBe(text);
	});

	it('holds back an ID until it has finished streaming', () => {
		expect(hideIds('Running AIFSv2 (9aa0415a-802c', names)).toBe('Running AIFSv2 (');
		expect(hideIds(`Running ${AIFS}`, names)).toBe('Running AIFSv2');
	});
});
