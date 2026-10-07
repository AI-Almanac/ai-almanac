// Shared onset-forecast domain helpers: the onset bins, the color ramps, and
// pure formatting/derivation used by both the map and the cell inspector.
// Keeping the ramps here means the map fill, the legend, and the inspector
// heatmap all draw from one source of truth.

// The onset palette is matplotlib "plasma" — the ramp the science team uses in
// their published onset graphics. One palette drives the map fill, the legend,
// the tooltip, and the inspector so a hue means the same thing everywhere.
// Sampled from purple up (plasma's near-black low end is skipped so the dimmest
// dot still reads on the dark basemap; the trade is that the yellow end sits
// low-contrast on the light inspector panel — the block borders and ring carry
// it there).
//
// Continuous magnitude ramp (probability 0→1): low reads as dim purple, high as
// vivid yellow (hot = likely on the near-black basemap).
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

// Ordinal "which onset bin" color: the same plasma run yellow (soonest onset —
// reads hot/imminent) → purple (Later — recedes). This reverses the science
// team's static legend (purple = soonest); we flip it so the nearest onset pops
// and yellow stays "high signal" as in the magnitude ramp. `reversed` restores
// their direction.
export function windowColor(bins: OnsetBins, idx: number, reversed = false): string {
	return rampColor(1 - idx / bins.laterIndex, reversed);
}

// Legend gradient for the ordinal bin colors, soonest on the left.
export function windowGradient(reversed = false): string {
	return probGradient(!reversed);
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

// ---- Onset bins: how a forecast's probabilities map onto the calendar ------
//
// The CSVs carry no absolute onset dates — only the forecast issue date
// (`time`) and probabilities binned by lead day, where lead day d is the
// calendar date issue + d (onset_blending's `assign_lead_bin`; lead 0, the
// issue date itself, is never a bin). The pipeline's horizon is 28 lead days:
//   Day d   = issue + d                  (d = 1..28)
//   Week w  = issue + 7(w-1)+1 .. 7w     (Week 1 = issue+1..+7, Week 4 = +22..+28)
//   Later   = issue + 29 onward          (open-ended; onset beyond the horizon)
// Each binning is an `OnsetBins` descriptor, so a binning change touches only
// this block and every date the UI shows follows it.
export const ONSET_HORIZON_DAYS = 28;
export const LATER_START_DAY = ONSET_HORIZON_DAYS + 1; // issue + 29
const MS_PER_DAY = 86_400_000;

// Whole days since the Unix epoch for a 'YYYY-MM-DD' string (UTC, no tz drift).
export function isoToDay(iso: string): number {
	const [y, m, d] = iso.split('-').map(Number);
	return Date.UTC(y, m - 1, d) / MS_PER_DAY;
}

export function dayToIso(day: number): string {
	return new Date(day * MS_PER_DAY).toISOString().slice(0, 10);
}

export type OnsetResolution = 'weekly' | 'daily';

export type DayRange = { start: number; end: number };

export type OnsetBins = {
	resolution: OnsetResolution;
	// Every bin, the open-ended "Later" bin last.
	count: number;
	laterIndex: number;
	// Calendar days covered by each bounded bin.
	binDays: number;
	labels: readonly string[];
	shortLabels: readonly string[];
	// Absolute [start, end] day numbers of a bounded bin (idx < laterIndex).
	dayRange(issueIso: string, idx: number): DayRange;
};

function onsetBins(
	resolution: OnsetResolution,
	binDays: number,
	label: (n: number) => string,
	shortLabel: (n: number) => string
): OnsetBins {
	const bounded = Array.from({ length: ONSET_HORIZON_DAYS / binDays }, (_, i) => i + 1);
	return {
		resolution,
		count: bounded.length + 1,
		laterIndex: bounded.length,
		binDays,
		labels: [...bounded.map(label), 'Later'],
		shortLabels: [...bounded.map(shortLabel), 'Later'],
		dayRange(issueIso, idx) {
			const start = isoToDay(issueIso) + binDays * idx + 1;
			return { start, end: start + binDays - 1 };
		}
	};
}

export const WEEKLY_BINS = onsetBins(
	'weekly',
	7,
	(w) => `Week ${w}`,
	(w) => `W${w}`
);
export const DAILY_BINS = onsetBins(
	'daily',
	1,
	(d) => `Day ${d}`,
	(d) => `D${d}`
);

export function binsFor(resolution: OnsetResolution): OnsetBins {
	return resolution === 'daily' ? DAILY_BINS : WEEKLY_BINS;
}

// First calendar day of the open-ended "Later" bin.
export function laterStartDay(issueIso: string): number {
	return isoToDay(issueIso) + LATER_START_DAY;
}

// Calendar description of one bin for one forecast: "Jun 2–Jun 8", "Jun 5",
// or "after Jun 29" for Later.
export function binDateLabel(bins: OnsetBins, issueIso: string, idx: number): string {
	if (idx >= bins.laterIndex) return `after ${fmtDate(dayToIso(laterStartDay(issueIso) - 1))}`;
	const r = bins.dayRange(issueIso, idx);
	return r.start === r.end
		? fmtDate(dayToIso(r.start))
		: `${fmtDate(dayToIso(r.start))}–${fmtDate(dayToIso(r.end))}`;
}

// Top of the colour ramp. Weekly bins and "Later" use the full 0–100%; daily
// probabilities are small (mostly under 15%), so a 0–100% ramp would paint
// every day the same dim colour. They scale to the largest bounded-day
// probability in `rows`, rounded up to a whole percent so the legend reads true.
export function probScaleMax(bins: OnsetBins, rows: number[][], binIdx?: number): number {
	if (bins.resolution === 'weekly' || binIdx === bins.laterIndex) return 1;
	let peak = 0;
	for (const row of rows)
		for (let i = 0; i < bins.laterIndex; i++) if ((row[i] ?? 0) > peak) peak = row[i];
	return Math.min(1, Math.max(0.01, Math.ceil(peak * 100 - 1e-9) / 100));
}

// ---- "Peak onset window passed" --------------------------------------------
//
// Every forecast's bins are forward-looking (onset *begins* in bin N after
// issue), so once a forecast is issued past the bin when onset was most
// likely, it has no bin left to place real mass in and dumps it into "Later"
// — a misleading bright dot. We gray those cells out, matching the science
// team's static figures. This is NOT a claim that onset was observed: we have no
// observed onset date, so we estimate the most-likely onset per cell from the
// season's own forecasts and drain the color once the issue date runs past it.

// Neutral slate for a grayed cell; reads as inactive on the dark basemap.
export const ONSET_PASSED_COLOR = '#8b929c';

// Probability-weighted consensus onset day for a cell across the whole season,
// using only the bounded bins ("Later" carries no date). null if no forecast
// ever dated the onset. Forecasts that dump their mass into "Later" contribute
// nothing, so the estimate is driven by the forecasts that actually placed onset
// on the calendar — i.e. where the models agree.
export function consensusOnsetDay(
	bins: OnsetBins,
	issueDates: string[],
	probs: number[][]
): number | null {
	let wsum = 0;
	let dsum = 0;
	issueDates.forEach((iso, di) => {
		const row = probs[di] ?? [];
		for (let b = 0; b < bins.laterIndex; b++) {
			const p = row[b] ?? 0;
			if (p <= 0) continue;
			const r = bins.dayRange(iso, b);
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
