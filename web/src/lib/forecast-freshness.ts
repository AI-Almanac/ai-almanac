// How current a season's forecast data is, from the issue dates it covers.

export type Freshness = {
	latest: string;
	// The newest issue date is older than the season's own cadence suggests it
	// should be, so newer forecasts are probably missing.
	behind: boolean;
};

const DAY_MS = 86_400_000;
const MIN_ALLOWED_GAP_DAYS = 7;

function toDay(iso: string): number {
	return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
}

function medianGapDays(days: number[]): number {
	const gaps = days.slice(1).map((d, i) => (d - days[i]) / DAY_MS);
	if (gaps.length === 0) return 0;
	const sorted = [...gaps].sort((a, b) => a - b);
	return sorted[Math.floor(sorted.length / 2)];
}

// ponytail: the cadence is inferred from the covered dates themselves; use the
// source's declared issue schedule if this misfires on irregular calendars.
export function forecastFreshness(issueDates: string[], today: Date): Freshness | null {
	const days = issueDates
		.map(toDay)
		.filter((d) => !Number.isNaN(d))
		.sort((a, b) => a - b);
	if (days.length === 0) return null;
	const latestDay = days[days.length - 1];
	const latest = new Date(latestDay).toISOString().slice(0, 10);
	const todayDay = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
	const sameYear = new Date(latestDay).getUTCFullYear() === today.getUTCFullYear();
	const allowedGap = Math.max(MIN_ALLOWED_GAP_DAYS, 2 * medianGapDays(days));
	return { latest, behind: sameYear && (todayDay - latestDay) / DAY_MS > allowedGap };
}

export function formatIssueDate(iso: string): string {
	return new Date(toDay(iso)).toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		year: 'numeric',
		timeZone: 'UTC'
	});
}
