/** Questions offered to start a conversation, chosen by what the workspace holds. */

export type StarterContext = {
	/** The workspace has at least one benchmark, blend, forecast or analysis. */
	hasProducts: boolean;
	/** The season the workspace works in, e.g. "Kiremt"; leave out the platform default. */
	seasonName?: string | null;
};

function trustQuestion(seasonName?: string | null): string {
	const season = seasonName?.trim();
	return season
		? `Which models should I trust for ${season} onset?`
		: 'Which models should I trust for onset here?';
}

/**
 * A forecaster's first questions. An empty workspace asks what to trust and how to
 * begin; one with results asks about what is there.
 */
export function starterQuestions(context: StarterContext): string[] {
	if (context.hasProducts) {
		return [
			'Which model did best, and where?',
			'Show skill zone by zone',
			'What changed since the last run?'
		];
	}
	return [trustQuestion(context.seasonName), 'Set up my season and area', 'What can I do here?'];
}
