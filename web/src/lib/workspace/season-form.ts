/** A workspace's season: its dates as month and day, and the server's refusals in plain words. */
import type { SeasonWindow } from '$lib/api/workspace-settings';
import { dayMonth, MONTH_NAMES } from '$lib/format/dates';

/** The season a workspace works in, from the onset search start to the last issue date. */
export function seasonLabel(season: SeasonWindow): string {
	return `${season.name} · ${dayMonth(season.onset_search_start)} – ${dayMonth(season.last_issue_date)}`;
}

// A season's dates must exist in every year, so February stops at the 28th.
const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

export type MonthDay = { month: number; day: number };

export type SeasonDateField = 'onset_search_start' | 'reference_onset' | 'last_issue_date';
export type SeasonField = 'name' | SeasonDateField;

export const SEASON_DATE_FIELDS: { field: SeasonDateField; label: string }[] = [
	{ field: 'onset_search_start', label: 'Onset search starts' },
	{ field: 'reference_onset', label: 'Reference onset' },
	{ field: 'last_issue_date', label: 'Last issue date' }
];

export type SeasonDraft = { name: string } & Record<SeasonDateField, MonthDay>;

export function daysIn(month: number): number[] {
	return Array.from({ length: DAYS_IN_MONTH[month - 1] ?? 31 }, (_, index) => index + 1);
}

/** `05-01` → May 1. */
export function parseMonthDay(value: string): MonthDay {
	const [month, day] = value.split('-').map(Number);
	return { month, day };
}

/** May 1 → `05-01`; a day the month does not have becomes its last day. */
export function formatMonthDay({ month, day }: MonthDay): string {
	const held = Math.min(day, DAYS_IN_MONTH[month - 1] ?? 31);
	return `${String(month).padStart(2, '0')}-${String(held).padStart(2, '0')}`;
}

export function seasonDraft(season: SeasonWindow): SeasonDraft {
	return {
		name: season.name,
		onset_search_start: parseMonthDay(season.onset_search_start),
		reference_onset: parseMonthDay(season.reference_onset),
		last_issue_date: parseMonthDay(season.last_issue_date)
	};
}

export function seasonFromDraft(draft: SeasonDraft): SeasonWindow {
	return {
		name: draft.name.trim(),
		onset_search_start: formatMonthDay(draft.onset_search_start),
		reference_onset: formatMonthDay(draft.reference_onset),
		last_issue_date: formatMonthDay(draft.last_issue_date)
	};
}

export type SeasonProblems = {
	fields: Partial<Record<SeasonField, string>>;
	/** A refusal that belongs to no one field. */
	general: string | null;
};

export const NO_PROBLEMS: SeasonProblems = { fields: {}, general: null };

export function hasProblems(problems: SeasonProblems): boolean {
	return problems.general !== null || Object.keys(problems.fields).length > 0;
}

/** What can be told before asking the server. */
export function draftProblems(draft: SeasonDraft): SeasonProblems {
	return draft.name.trim()
		? NO_PROBLEMS
		: { fields: { name: 'Give the season a name.' }, general: null };
}

const SERVER_PREFIX = 'Invalid workspace settings: ';
const SEASON_FIELDS: SeasonField[] = [
	'name',
	'onset_search_start',
	'reference_onset',
	'last_issue_date'
];
// A rule about two dates is reported against the season; it reads as being about
// the date it names first.
const SUBJECT_OF_RULE: [RegExp, SeasonField][] = [
	[/^the reference onset/i, 'reference_onset'],
	[/^the last issue date/i, 'last_issue_date']
];

/** `05-01` → `1 May`, wherever a message quotes a date the way it is stored. */
function spokenDates(message: string): string {
	return message.replace(/\b(\d{2})-(\d{2})\b/g, (stored, month, day) => {
		const name = MONTH_NAMES[Number(month) - 1];
		return name ? `${Number(day)} ${name}` : stored;
	});
}

function plainWords(message: string): string {
	if (message.startsWith('must be a calendar date'))
		return 'Choose a date that exists in every year.';
	const spoken = spokenDates(message.trim());
	const sentence = spoken.charAt(0).toUpperCase() + spoken.slice(1);
	return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

function fieldOf(where: string, message: string): SeasonField | null {
	const named = SEASON_FIELDS.find((field) => where === `season.${field}`);
	if (named) return named;
	return SUBJECT_OF_RULE.find(([pattern]) => pattern.test(message))?.[1] ?? null;
}

/**
 * The server's refusal of a season, split by the field each part is about. It
 * arrives as one line, "Invalid workspace settings: season.name: …; season: …".
 */
export function seasonProblems(detail: string): SeasonProblems {
	if (!detail.startsWith(SERVER_PREFIX)) return { fields: {}, general: detail };
	const problems: SeasonProblems = { fields: {}, general: null };
	for (const part of detail.slice(SERVER_PREFIX.length).split(/; (?=season\b)/)) {
		const [, where = '', message = part] = part.match(/^(season(?:\.\w+)?): (.*)$/s) ?? [];
		const field = fieldOf(where, message);
		const words = plainWords(message);
		if (field) problems.fields[field] ??= words;
		else problems.general = [problems.general, words].filter(Boolean).join(' ');
	}
	return problems;
}
