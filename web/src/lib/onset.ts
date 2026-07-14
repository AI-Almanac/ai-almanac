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

// Onset probability is magnitude → one sequential hue (blue), light→dark, not a
// rainbow. The floor is lifted off the near-black basemap (#0d1117) so a
// low-probability cell reads as a dim dot, not a hole — "low" must never be
// indistinguishable from "no data."
export const PROB_RAMP: [number, string][] = [
	[0, '#2b4a72'],
	[0.25, '#2f68b0'],
	[0.5, '#3987e5'],
	[0.75, '#86b6ef'],
	[1, '#cde2fb']
];

export const legendGradient = `linear-gradient(to right, ${PROB_RAMP.map(
	([v, c]) => `${c} ${v * 100}%`
).join(', ')})`;

// "Which window" is ordinal (soonest→latest): one hue, monotone lightness,
// indexed week1..later to match WEEKS.
export const WINDOW_RAMP = ['#cde2fb', '#86b6ef', '#3987e5', '#256abf', '#184f95'];

function hexToRgb(h: string): [number, number, number] {
	return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}

// Interpolate the sequential ramp in RGB for a magnitude in [0, 1].
export function rampColor(v: number): string {
	const t = Math.max(0, Math.min(1, v));
	for (let i = 1; i < PROB_RAMP.length; i++) {
		const [v1, c1] = PROB_RAMP[i - 1];
		const [v2, c2] = PROB_RAMP[i];
		if (t <= v2) {
			const f = v2 === v1 ? 0 : (t - v1) / (v2 - v1);
			const a = hexToRgb(c1);
			const b = hexToRgb(c2);
			const m = a.map((x, k) => Math.round(x + (b[k] - x) * f));
			return `rgb(${m[0]}, ${m[1]}, ${m[2]})`;
		}
	}
	return PROB_RAMP[PROB_RAMP.length - 1][1];
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
