import { describe, expect, it } from 'vitest';

import { starterQuestions } from '../src/lib/workspace/starter-prompts';

describe('starter questions', () => {
	it('asks what to trust and how to begin in an empty workspace', () => {
		expect(starterQuestions({ hasProducts: false, seasonName: 'Kiremt' })).toEqual([
			'Which models should I trust for Kiremt onset?',
			'Set up my season and area',
			'What can I do here?'
		]);
	});

	it('asks about onset here when the workspace has no season of its own', () => {
		const [first] = starterQuestions({ hasProducts: false, seasonName: '  ' });

		expect(first).toBe('Which models should I trust for onset here?');
	});

	it('asks about what is there once the workspace has results', () => {
		const questions = starterQuestions({ hasProducts: true, seasonName: 'Kiremt' });

		expect(questions).toEqual([
			'Which model did best, and where?',
			'Show skill zone by zone',
			'What changed since the last run?'
		]);
	});

	it('never offers a question about results to an empty workspace', () => {
		const empty = starterQuestions({ hasProducts: false });

		expect(empty.some((question) => /latest|last run|did best/.test(question))).toBe(false);
	});
});
