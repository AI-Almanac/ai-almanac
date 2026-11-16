/**
 * Dates as people read them. Each is read from its text rather than through
 * `Date`, so a day never shifts with the reader's time zone.
 */
export const MONTH_NAMES = [
	'January',
	'February',
	'March',
	'April',
	'May',
	'June',
	'July',
	'August',
	'September',
	'October',
	'November',
	'December'
];

/** `Jan` … `Dec`. */
export const MONTH_ABBREVIATIONS = MONTH_NAMES.map((name) => name.slice(0, 3));

/** `07-31` → `31 Jul`. */
export function dayMonth(monthDay: string): string {
	const [month, day] = monthDay.split('-').map(Number);
	return `${day} ${MONTH_ABBREVIATIONS[month - 1]}`;
}

/** `2026-07-09` → `9 Jul 2026`, or `9 Jul` without the year. */
export function formatDay(iso: string, withYear = true): string {
	const text = dayMonth(iso.slice(5, 10));
	return withYear ? `${text} ${iso.slice(0, 4)}` : text;
}

/** `2024-06-01T00:00:00.000000000` → `2024-06-01`; anything but a midnight timestamp is left as it is. */
export function midnightAsDate(value: string): string {
	const match = /^(\d{4}-\d{2}-\d{2})T00:00:00(\.0+)?$/.exec(value);
	return match ? match[1] : value;
}
