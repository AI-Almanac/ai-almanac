import { describe, expect, it } from 'vitest';

import type { JobArtifact } from '../src/lib/api/jobs';
import {
	awaitingArtifacts,
	blendInProgress,
	blendStateChanged,
	dayLevelNote,
	dayLevelTraining
} from '../src/routes/blends/day-level';

const ACTIVE = ['queued', 'starting', 'running', 'canceling'];
const artifact = (filename: string) => ({ filename }) as JobArtifact;

describe('a blend whose day-level blend trains apart from it', () => {
	it('shows its week-level results while the day-level blend still trains', () => {
		const blend = { status: 'complete', day_level_status: 'running' } as const;

		expect(dayLevelTraining(blend)).toBe(true);
		expect(blendInProgress(blend, ACTIVE)).toBe(true);
		expect(dayLevelNote(blend)?.title).toBe('Day-level blend still training');
	});

	it('stops polling once both are done, and for blends that never trained apart', () => {
		expect(blendInProgress({ status: 'complete', day_level_status: 'complete' }, ACTIVE)).toBe(
			false
		);
		expect(blendInProgress({ status: 'complete', day_level_status: null }, ACTIVE)).toBe(false);
		expect(blendInProgress({ status: 'running', day_level_status: null }, ACTIVE)).toBe(true);
		expect(dayLevelNote({ status: 'complete', day_level_status: 'complete' })).toBeNull();
	});

	it('picks up a day-level change even when the blend status stays complete', () => {
		expect(
			blendStateChanged(
				{ status: 'complete', day_level_status: 'running' },
				{ status: 'complete', day_level_status: 'complete' }
			)
		).toBe(true);
		expect(
			blendStateChanged(
				{ status: 'complete', day_level_status: 'running' },
				{ status: 'complete', day_level_status: 'running' }
			)
		).toBe(false);
	});

	it('waits for the day-level files after the day-level blend finishes', () => {
		const weekly = [artifact('summary_models_pooled_x.csv')];
		const finished = { status: 'complete', day_level_status: 'complete' } as const;

		expect(awaitingArtifacts(finished, [])).toBe(true);
		expect(awaitingArtifacts(finished, weekly)).toBe(true);
		expect(awaitingArtifacts(finished, [...weekly, artifact('day_level_manifest.json')])).toBe(
			false
		);
		expect(awaitingArtifacts({ status: 'complete', day_level_status: 'running' }, weekly)).toBe(
			false
		);
	});

	it('names why the day-level blend has no results', () => {
		expect(
			dayLevelNote({ status: 'complete', day_level_status: 'failed', day_level_error: 'boom' })
		).toEqual({ kind: 'failed', title: 'Day-level blend failed', detail: 'boom' });
		expect(dayLevelNote({ status: 'complete', day_level_status: 'canceled' })?.kind).toBe(
			'stopped'
		);
		expect(dayLevelNote({ status: 'failed', day_level_status: 'canceled' })).toBeNull();
	});
});
