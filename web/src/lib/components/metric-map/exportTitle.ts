/** The words an exported map carries, so it can be read away from the app. */

/** What an exported image says about the run beyond what the map itself knows. */
export type MapExportContext = { area: string; years: string | null };

/**
 * The same for a blend's or a forecast's map: the areas the run was limited to, or
 * null when it covered all of its data.
 */
export type AreaExportContext = { area: string | null };

const PLAIN_METRIC: Record<string, string> = {
	mean_mae: 'Onset error (mean absolute error, days)',
	false_alarm_rate: 'False alarms',
	miss_rate: 'Missed onsets'
};

export type MapTitleParts = {
	metric: string;
	metricLabel: string;
	model: string;
	window: string;
	/** What the model is set against when the map shows a comparison; null for plain values. */
	reference: string | null;
	area: string;
	years: string | null;
};

export type MapTitle = { title: string; subtitle: string };

/** What the map shows, for which model; then lead window, area and years. */
export function mapTitle(parts: MapTitleParts): MapTitle {
	const what = PLAIN_METRIC[parts.metric] ?? parts.metricLabel;
	const versus = parts.reference ? `, compared with ${parts.reference}` : '';
	return {
		title: `${what} — ${parts.model}${versus}`,
		subtitle: [parts.window, parts.area, parts.years].filter(Boolean).join(' · ')
	};
}

/** A title already in words, over the facts that place it; missing facts are left out. */
export function titledMap(title: string, facts: (string | null | undefined)[]): MapTitle {
	return { title, subtitle: facts.filter(Boolean).join(' · ') };
}

/** A file name a person can find again: the title in lower-case words. */
export function mapFileName(title: MapTitle): string {
	const words = `${title.title} ${title.subtitle}`
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
	return `${words.slice(0, 96).replace(/-+$/, '') || 'map'}.png`;
}
