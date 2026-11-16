/**
 * What a run is called: the settings that tell it from its neighbours, in the
 * order a forecaster compares runs by. The same parts make the stage's full
 * title and the shorter label the products list has room for.
 */
import type { WorkspaceJob } from '$lib/api/workspaces';

type OnsetDefinition = NonNullable<WorkspaceJob['onset_definition']>;
type OnsetChange = OnsetDefinition['changes'][number];

/** How much of a run's settings a name spells out before it summarises them. */
export type NameDetail = {
	/** Areas named one by one; a longer list is counted and trails off. */
	areas: number;
	/** Changed onset settings spelled out; more than this are counted. */
	onsetChanges: number;
	leadDays: boolean;
	/** Changed onset settings go by their short names, where they have one. */
	shortLabels: boolean;
};

export const LIST_NAME: NameDetail = {
	areas: 1,
	onsetChanges: 1,
	leadDays: false,
	shortLabels: true
};
export const TITLE_NAME: NameDetail = {
	areas: 3,
	onsetChanges: 3,
	leadDays: true,
	shortLabels: false
};
/** Everything spelled out, for telling apart two runs whose shorter names match. */
export const FULL_NAME: NameDetail = {
	areas: Infinity,
	onsetChanges: Infinity,
	leadDays: true,
	shortLabels: false
};

export type RunScope = WorkspaceJob['scope'];
type Span = NonNullable<RunScope['years']>;

/** A run's scope when none of its jobs' settings say anything about it. */
export const NO_SCOPE: RunScope = { place: null, years: null, lead_days: null };

/** "1998–2024", or one year (or day) as itself. */
function spanName({ start, end }: Span): string {
	return start === end ? `${start}` : `${start}–${end}`;
}

function leadDaysName(days: Span | null): string | null {
	return days ? `days ${spanName(days)}` : null;
}

/** "North Gondar, South Gondar", or "12 areas: North Gondar, South Gondar, …" past `limit`. */
function areaName(areas: string[] | null | undefined, limit: number): string | null {
	if (!areas?.length) return null;
	// Counting pays off only when it hides more than one name.
	if (areas.length <= limit + 1) return areas.join(', ');
	return `${areas.length} areas: ${areas.slice(0, limit).join(', ')}, …`;
}

function lowerFirst(text: string): string {
	return text.charAt(0).toLowerCase() + text.slice(1);
}

/** One changed onset setting, value first: "20 mm wet spell rain total". */
function onsetChangeName(change: OnsetChange, short = false): string {
	const label = lowerFirst((short && change.short_label) || change.label);
	if (change.value === null || change.value === false) return `no ${label}`;
	if (change.value === true) return `with ${label}`;
	return [change.value, change.unit, label].filter((part) => part !== null).join(' ');
}

/** Null for the default definition, which tells no run from another. */
export function onsetName(
	onset: OnsetDefinition | null | undefined,
	limit: number,
	short = false
): string | null {
	if (!onset || onset.is_default) return null;
	const { changes } = onset;
	if (changes.length === 0) return onset.name;
	if (changes.length > limit) return `${onset.name}, ${changes.length} changed settings`;
	return `${onset.name}, ${changes.map((change) => onsetChangeName(change, short)).join(', ')}`;
}

export type RunNaming = {
	/** The name a blend was given, which leads its title; null for other runs. */
	ownName: string | null;
	scope: RunScope;
	areas: string[] | null;
	onset: OnsetDefinition | null;
	models: string[];
};

/** Area (else region), years, onset definition when not the default, models, lead days. */
export function nameParts(naming: RunNaming, detail: NameDetail): string[] {
	const { scope } = naming;
	return [
		naming.ownName,
		areaName(naming.areas, detail.areas) ?? scope.place,
		scope.years && spanName(scope.years),
		onsetName(naming.onset, detail.onsetChanges, detail.shortLabels),
		naming.models.join(', '),
		detail.leadDays ? leadDaysName(scope.lead_days) : null
	].filter((part): part is string => Boolean(part));
}
