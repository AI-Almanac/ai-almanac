// How current a season's forecast data is, from the issue dates it covers.

function toDay(iso: string): number {
	return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
}

// ponytail: no "behind" judgement — trajectory sets are model-scoped, so there's
// no single issue schedule to compare against; add one when sets carry their
// expected schedule.
export function latestIssueDate(issueDates: string[]): string | null {
	const days = issueDates.map(toDay).filter((d) => !Number.isNaN(d));
	if (days.length === 0) return null;
	return new Date(Math.max(...days)).toISOString().slice(0, 10);
}

export function formatIssueDate(iso: string): string {
	return new Date(toDay(iso)).toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		year: 'numeric',
		timeZone: 'UTC'
	});
}
