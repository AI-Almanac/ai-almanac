/** What an exported onset forecast map says: its title, and the colours as the map draws them. */
import { ONSET_PASSED_COLOR, fmtProb, rampColor, windowColor, type OnsetBins } from '$lib/onset';
import type { LegendSpec } from './exportImage';
import { titledMap, type MapTitle } from './exportTitle';

const RAMP_STEPS = [0, 0.25, 0.5, 0.75, 1];
const PASSED_NOTE = 'Grey: issued after onset likely began';
const UNCERTAIN_NOTE = 'Fainter where the timing is less certain; grey where onset likely began';

export type ForecastLegendParts = {
	/** Colour by the chance of onset in one window, or by each place's most likely window. */
	colorMode: 'window' | 'expected';
	daily: boolean;
	/** The palette runs the other way round. */
	reversed: boolean;
	bins: OnsetBins;
	/** The chance the top of the scale stands for, 0–1. */
	scaleMax: number;
	/** The first day a daily forecast can place the onset on, e.g. "Jun 9". */
	soonest: string;
};

export function forecastLegend(parts: ForecastLegendParts): LegendSpec {
	const { bins, reversed } = parts;
	if (parts.colorMode === 'window') {
		return {
			stops: RAMP_STEPS.map((step) => rampColor(step, reversed)),
			low: '0%',
			high: fmtProb(parts.scaleMax),
			note: PASSED_NOTE
		};
	}
	if (parts.daily) {
		return {
			stops: RAMP_STEPS.map((step) => rampColor(step, !reversed)),
			low: parts.soonest,
			high: 'Later',
			note: UNCERTAIN_NOTE
		};
	}
	return {
		stops: [],
		low: '',
		high: '',
		note: 'Fainter where the timing is less certain',
		swatches: [
			...bins.labels.map((label, i) => ({ color: windowColor(bins, i, reversed), label })),
			{ color: ONSET_PASSED_COLOR, label: 'Onset likely began' }
		]
	};
}

export type ForecastTitleParts = {
	/** What the colours mean, as the map's own legend says it. */
	what: string;
	/** The forecast's issue date in words. */
	issued: string;
	/** The areas the run was limited to, else its region. */
	area: string | null | undefined;
	/** Which blend is shown, when the forecast offers more than one. */
	blend: string | null;
};

export function forecastMapTitle(parts: ForecastTitleParts): MapTitle {
	return titledMap(parts.what.replace(/\.$/, ''), [
		`Forecast issued ${parts.issued}`,
		parts.area,
		parts.blend
	]);
}
