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

// The onset palette is matplotlib "plasma" — the ramp the science team uses in
// their published onset graphics: soonest onset window = purple, latest =
// yellow. One palette drives the map fill, the legend, the tooltip, and the
// inspector so a hue means the same window everywhere. Sampled from purple up
// (plasma's near-black low end is skipped so the dimmest dot still reads on the
// dark basemap; the trade is that the yellow end sits low-contrast on the light
// inspector panel — the block borders and ring carry it there).

// Ordinal "which onset window" (week1..later), indexed to match WEEKS. Discrete
// plasma samples running yellow (soonest onset — reads hot/imminent) → purple
// (latest — recedes). Note: this reverses the science team's static legend
// (purple=soonest → yellow=latest); we flip it so the nearest onset pops and
// yellow stays "high signal" as in the magnitude ramp.
export const WINDOW_RAMP = ['#f0f921', '#fb9f3a', '#d9586a', '#9e199d', '#4903a0'];

// Continuous magnitude ramp (probability 0→1) built from the same plasma stops,
// so the "by window" dots and the legend bar stay in the same palette: low reads
// as dim purple, high as vivid yellow (hot = likely on the near-black basemap).
export const PROB_RAMP: [number, string][] = [
	[0, '#4903a0'],
	[0.25, '#9e199d'],
	[0.5, '#d9586a'],
	[0.75, '#fb9f3a'],
	[1, '#f0f921']
];

// Legend gradient for the magnitude ramp. `reversed` flips it so the vivid end
// is purple instead of yellow, tracking the soonest-onset color toggle.
export function probGradient(reversed = false): string {
	const ramp = reversed
		? [...PROB_RAMP].map(([v, c]) => [1 - v, c] as [number, string]).reverse()
		: PROB_RAMP;
	return `linear-gradient(to right, ${ramp.map(([v, c]) => `${c} ${v * 100}%`).join(', ')})`;
}

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

// Magnitude → plasma color for the map fill and inspector. `reversed` puts the
// vivid end at low probability instead of high, tracking the soonest-onset toggle.
export function rampColor(v: number, reversed = false): string {
	return interpRamp(PROB_RAMP, reversed ? 1 - v : v);
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

// ---- "Peak onset window passed" --------------------------------------------
//
// Every forecast's windows are forward-looking (onset *begins* in week N after
// issue), so once a forecast is issued past the window when onset was most
// likely, it has no window left to place real mass in and dumps it into "Later"
// — a misleading bright dot. We gray those cells out, matching the science
// team's static figures. This is NOT a claim that onset was observed: we have no
// observed onset date, so we estimate the most-likely onset per cell from the
// season's own forecasts and drain the color once the issue date runs past it.

// Neutral slate for a grayed cell; reads as inactive on the dark basemap.
export const ONSET_PASSED_COLOR = '#8b929c';

// Probability-weighted consensus onset day for a cell across the whole season,
// using only the bounded windows ("Later" carries no date). null if no forecast
// ever dated the onset. Forecasts that dump their mass into "Later" contribute
// nothing, so the estimate is driven by the forecasts that actually placed onset
// on the calendar — i.e. where the models agree.
export function consensusOnsetDay(issueDates: string[], probs: number[][]): number | null {
	let wsum = 0;
	let dsum = 0;
	issueDates.forEach((iso, di) => {
		const row = probs[di] ?? [];
		for (let w = 0; w < 4; w++) {
			const p = row[w] ?? 0;
			if (p <= 0) continue;
			const r = windowDayRange(iso, w);
			wsum += p;
			dsum += (p * (r.start + r.end)) / 2;
		}
	});
	return wsum > 0 ? dsum / wsum : null;
}

// Grace past the estimated onset before a cell counts as post-onset: once a
// forecast is issued beyond the consensus ±3d band, onset has begun and its
// forward outlook is stale.
export const ONSET_PASSED_GRACE_DAYS = 3;

export function onsetHasPassed(issueIso: string, consensusDay: number | null): boolean {
	return consensusDay != null && isoToDay(issueIso) > consensusDay + ONSET_PASSED_GRACE_DAYS;
}
