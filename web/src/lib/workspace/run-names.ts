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

/** What a run is called among its siblings: what sets it apart, then what it shares with them. */
export type ListName = { lead: string; context: string | null };

/** One part of a run's name, keyed so the same part of two runs' names can be compared. */
type NamePart = { key: string; text: string };

function part(key: string, text: string | null | undefined): NamePart[] {
	return text ? [{ key, text }] : [];
}

function unique<T>(values: T[]): T[] {
	return [...new Set(values)];
}

/** A setting as the run has it: its own change, else the default a sibling's change reports. */
function settingOf(
	onset: OnsetDefinition,
	label: string,
	sameRule: OnsetDefinition[]
): OnsetChange {
	const own = onset.changes.find((change) => change.label === label);
	if (own) return own;
	const changed = sameRule.flatMap((each) => each.changes).find((each) => each.label === label)!;
	return { ...changed, value: changed.default };
}

function settingText(setting: OnsetChange, short: boolean): string {
	const name = onsetChangeName(setting, short);
	return setting.value === setting.default ? `${name} (default)` : name;
}

/** The definition's name sets siblings apart unless every one of them uses the default's. */
function namesDefinition(onsets: OnsetDefinition[]): boolean {
	return unique(onsets.map((onset) => onset.name)).length > 1 || !onsets.some((o) => o.is_default);
}

/**
 * The run's onset definition among its siblings': its name where that tells them
 * apart, and every setting a sibling of the same definition changed, by its value
 * here, so a run that kept the default is named by it rather than by nothing.
 */
function onsetParts(
	onset: OnsetDefinition | null,
	onsets: OnsetDefinition[],
	short: boolean
): NamePart[] {
	if (!onset) return [];
	const sameRule = onsets.filter((each) => each.name === onset.name);
	const labels = unique(sameRule.flatMap((each) => each.changes.map((change) => change.label)));
	const settings = labels.map((label) => ({
		key: `onset:${label}`,
		text: settingText(settingOf(onset, label, sameRule), short)
	}));
	if (!namesDefinition(onsets)) return settings;
	const marked = onset.is_default && settings.length === 0;
	return [...part('onset', marked ? `${onset.name} (default)` : onset.name), ...settings];
}

/** In a title's order: own name, area (else region), years, onset definition, models. */
function siblingParts(
	naming: RunNaming,
	onsets: OnsetDefinition[],
	detail: NameDetail
): NamePart[] {
	const { scope } = naming;
	return [
		...part('own', naming.ownName),
		...part('area', areaName(naming.areas, detail.areas) ?? scope.place),
		...part('years', scope.years && spanName(scope.years)),
		...onsetParts(naming.onset, onsets, detail.shortLabels),
		...part('models', naming.models.join(', '))
	];
}

function joined(parts: NamePart[]): string {
	return parts.map((each) => each.text).join(' · ');
}

/** Leads with the parts that vary; a run whose every part is shared leads with those. */
function listName(parts: NamePart[], varies: (key: string) => boolean): ListName {
	const lead = parts.filter((each) => varies(each.key));
	const shared = parts.filter((each) => !varies(each.key));
	if (lead.length === 0) return { lead: joined(shared), context: null };
	return { lead: joined(lead), context: joined(shared) || null };
}

/** Each sibling named by the parts of its name that differ from the others', the rest as context. */
export function siblingNames(namings: RunNaming[], detail: NameDetail): ListName[] {
	const onsets = namings.flatMap((naming) => naming.onset ?? []);
	const parts = namings.map((naming) => siblingParts(naming, onsets, detail));
	const textOf = (own: NamePart[], key: string) => own.find((each) => each.key === key)?.text;
	const varying = new Set(
		unique(parts.flat().map((each) => each.key)).filter(
			(key) => unique(parts.map((own) => textOf(own, key))).length > 1
		)
	);
	return parts.map((own) => listName(own, (key) => varying.has(key)));
}
