// Shared onset-forecast domain helpers: window labels, the color ramps, and
// pure formatting/derivation used by both the map and the cell inspector.
// Keeping the ramps here means the map fill, the legend, and the inspector
// heatmap all draw from one source of truth.

export const WEEKS = ['week1', 'week2', 'week3', 'week4', 'later'] as const;
export type Week = (typeof WEEKS)[number];

export const WEEK_LABELS: Record<Week, string> = {
	week1: 'Week 1',
	week2: 'Week 2',
	week3: 'Week 3',
	week4: 'Week 4',
	later: 'Later'
};

// Short labels for tight grids (the inset column headers).
export const WEEK_SHORT: Record<Week, string> = {
	week1: 'W1',
	week2: 'W2',
	week3: 'W3',
	week4: 'W4',
	later: 'Later'
};

// Onset probability is magnitude → one sequential blue, dim→vivid, not a
// rainbow. Two constraints share this ramp: dots sit on the near-black basemap
// (low must read as a dim dot, not a hole) and the legend now sits on a light
// glass panel (no end may go near-white or it vanishes). So both ends stay
// saturated — low is a dim steel blue, high a vivid royal blue that still pops
// on dark yet holds contrast on white.
export const PROB_RAMP: [number, string][] = [
	[0, '#173f6e'],
	[0.25, '#255a9e'],
	[0.5, '#3277cc'],
	[0.75, '#4a90e2'],
	[1, '#63a6f0']
];

export const legendGradient = `linear-gradient(to right, ${PROB_RAMP.map(
	([v, c]) => `${c} ${v * 100}%`
).join(', ')})`;

// "Which window" is ordinal (soonest→latest): one hue, monotone lightness,
// indexed week1..later to match WEEKS.
export const WINDOW_RAMP = ['#cde2fb', '#86b6ef', '#3987e5', '#256abf', '#184f95'];

// Light-surface variant (inspector heatmap). A single blue hue collapses the
// low-mid range into near-identical pale tints on a light panel, so this is the
// multi-hue ColorBrewer YlGnBu scheme instead: probability climbs through
// yellow → green → teal → blue → navy, discriminating magnitude by hue *and*
// lightness. The teal midpoint echoes the app accent; high reads darkest so the
// most-likely window carries the most ink.
export const PROB_RAMP_LIGHT: [number, string][] = [
	[0, '#ffffcc'],
	[0.25, '#a1dab4'],
	[0.5, '#41b6c4'],
	[0.75, '#2c7fb8'],
	[1, '#253494']
];

function hexToRgb(h: string): [number, number, number] {
	return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}

// Interpolate a sequential ramp in RGB for a magnitude in [0, 1].
function interpRamp(ramp: [number, string][], v: number): string {
	const t = Math.max(0, Math.min(1, v));
	for (let i = 1; i < ramp.length; i++) {
		const [v1, c1] = ramp[i - 1];
		const [v2, c2] = ramp[i];
		if (t <= v2) {
			const f = v2 === v1 ? 0 : (t - v1) / (v2 - v1);
			const a = hexToRgb(c1);
			const b = hexToRgb(c2);
			const m = a.map((x, k) => Math.round(x + (b[k] - x) * f));
			return `rgb(${m[0]}, ${m[1]}, ${m[2]})`;
		}
	}
	return ramp[ramp.length - 1][1];
}

// Dark-basemap ramp (map fill + legend) and its light-panel counterpart.
export function rampColor(v: number): string {
	return interpRamp(PROB_RAMP, v);
}

export function rampColorLight(v: number): string {
	return interpRamp(PROB_RAMP_LIGHT, v);
}

export function argmax(arr: number[]): number {
	let idx = 0;
	for (let i = 1; i < arr.length; i++) if (arr[i] > arr[idx]) idx = i;
	return idx;
}

export function fmtProb(v: number): string {
	return `${(v * 100).toFixed(0)}%`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function fmtDate(iso: string): string {
	const parts = iso.split('-');
	return `${MONTHS[parseInt(parts[1]) - 1]} ${parseInt(parts[2])}`;
}

export function monthLabel(iso: string): string {
	return MONTHS[parseInt(iso.slice(5, 7)) - 1];
}

// ---- Absolute calendar dates for the onset windows -------------------------
//
// The CSV has no absolute onset dates — only the forecast issue date (`time`)
// and probabilities binned into 7-day lead windows. Per onset_blending's
// `connect_utils.sum_week_probs`, the per-lead-day onset probabilities
// `p_onset_day_0..27` are summed into weeks with day_0 in Week 1, and day_0 is
// the issue date (lead 0). So:
//   Week 1 = issue+0..+6, Week 2 = +7..+13, Week 3 = +14..+20, Week 4 = +21..+27,
//   Later  = issue+28 onward (open-ended; onset beyond the 4-week horizon).
// Isolated here so a single edit corrects every date shown if that binning changes.
export const ONSET_WINDOW_DAYS = 7;
export const LATER_START_DAY = ONSET_WINDOW_DAYS * 4; // issue + 28
const MS_PER_DAY = 86_400_000;

// Whole days since the Unix epoch for a 'YYYY-MM-DD' string (UTC, no tz drift).
export function isoToDay(iso: string): number {
	const [y, m, d] = iso.split('-').map(Number);
	return Date.UTC(y, m - 1, d) / MS_PER_DAY;
}

export function dayToIso(day: number): string {
	return new Date(day * MS_PER_DAY).toISOString().slice(0, 10);
}

// Absolute [start, end] day-numbers for a bounded window (weekIndex 0..3).
// The "later" bucket (index 4) has no bounded end and is handled separately.
export function windowDayRange(issueIso: string, weekIndex: number): { start: number; end: number } {
	const start = isoToDay(issueIso) + ONSET_WINDOW_DAYS * weekIndex;
	return { start, end: start + ONSET_WINDOW_DAYS - 1 };
}
