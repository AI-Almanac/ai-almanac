/**
 * What the blend setup form holds, where it comes from (blank or a handed-in
 * setup), and the request it sends.
 */
import type { BlendCreate, BlendRunSpec, DataSource, DayLevelSettings } from '$lib/api';
import { isFocusUnits, type FocusAreaValue } from '$lib/api/jobs';
import { overlaps } from '$lib/geo/bbox';
import { spatialBounds } from '$lib/source-coverage';
import { dayLevelParamsBody, type DayLevelInput } from './day-level-params';
import { onsetParamsBody, type OnsetParamsInput } from './onset-params';

/** Every field as typed; blank text means the workflow default. */
export type BlendFields = OnsetParamsInput & {
	name: string;
	obsDatasetId: string;
	modelIds: string[];
	trainingYears: string;
	cvHoldoutYears: string;
	forecastYears: string;
	trueHoldoutYears: string;
	formulaText: string;
	focusArea: FocusAreaValue | null;
	dayLevel: DayLevelInput;
	/** The years were set by hand or by a setup, so no split is suggested over them. */
	yearsChosen: boolean;
};

export function blankBlendFields(): BlendFields {
	return {
		name: '',
		obsDatasetId: '',
		modelIds: [],
		trainingYears: '',
		cvHoldoutYears: '',
		forecastYears: '',
		trueHoldoutYears: '',
		formulaText: '',
		focusArea: null,
		thresholdMm: '',
		cutoffMonthDay: '',
		refOnsetMonthDay: '',
		dayLevel: { yearsPerFold: '', trees: '', maxDepth: '', minSamplesLeaf: '', maxSamples: '' },
		yearsChosen: false
	};
}

/** A setup from elsewhere (the assistant), replacing every field; what it leaves out is blank. */
export function fieldsFromSpec(spec: BlendRunSpec): BlendFields {
	return {
		...blankBlendFields(),
		name: spec.name ?? '',
		obsDatasetId: spec.obs_dataset_id ?? '',
		modelIds: spec.model_ids ?? [],
		trainingYears: spec.training_years ?? '',
		cvHoldoutYears: spec.cv_holdout_years ?? '',
		forecastYears: spec.forecast_years ?? '',
		trueHoldoutYears: spec.true_holdout_years ?? '',
		formulaText: spec.formula_text ?? '',
		focusArea: spec.focus_area ?? null,
		thresholdMm: spec.threshold_mm != null ? String(spec.threshold_mm) : '',
		cutoffMonthDay: spec.cutoff_month_day ?? '',
		refOnsetMonthDay: spec.ref_onset_month_day ?? '',
		yearsChosen: Boolean(spec.training_years || spec.cv_holdout_years)
	};
}

/** Whether a drawn box still covers some of a source's data. Places, and unknown extents, always do. */
function areaFits(area: FocusAreaValue | null, source: DataSource | undefined): boolean {
	const data = spatialBounds(source);
	return !area || isFocusUnits(area) || !data || overlaps(area, data);
}

/** The fields with other observations chosen, dropping a box that misses their data. */
export function withObservations(fields: BlendFields, source: DataSource | undefined): BlendFields {
	return {
		...fields,
		obsDatasetId: source?.id ?? '',
		focusArea: areaFits(fields.focusArea, source) ? fields.focusArea : null
	};
}

export function toggleModel(fields: BlendFields, id: string): BlendFields {
	const modelIds = fields.modelIds.includes(id)
		? fields.modelIds.filter((m) => m !== id)
		: [...fields.modelIds, id];
	return { ...fields, modelIds };
}

/** Every field the server cannot default is filled in. */
export function hasRequiredFields(fields: BlendFields): boolean {
	return (
		fields.name.trim() !== '' &&
		fields.obsDatasetId !== '' &&
		fields.modelIds.length > 0 &&
		fields.trainingYears.trim() !== '' &&
		fields.cvHoldoutYears.trim() !== ''
	);
}

function optional<K extends string>(key: K, text: string): { [P in K]?: string } {
	const value = text.trim();
	return (value ? { [key]: value } : {}) as { [P in K]?: string };
}

function dayLevelBody(input: DayLevelInput, offered: boolean): { day_level?: DayLevelSettings } {
	const settings = offered ? dayLevelParamsBody(input) : undefined;
	return settings ? { day_level: settings } : {};
}

/** The request for these fields, leaving out every setting left at its default. */
export function blendRequest(fields: BlendFields, dayLevelOffered: boolean): BlendCreate {
	return {
		name: fields.name.trim(),
		obs_dataset_id: fields.obsDatasetId,
		model_ids: fields.modelIds,
		params: {
			training_years: fields.trainingYears.trim(),
			cv_holdout_years: fields.cvHoldoutYears.trim(),
			...optional('forecast_years', fields.forecastYears),
			...optional('true_holdout_years', fields.trueHoldoutYears),
			...optional('formula_text', fields.formulaText),
			...(fields.focusArea ? { focus_area: fields.focusArea } : {}),
			...onsetParamsBody(fields),
			...dayLevelBody(fields.dayLevel, dayLevelOffered)
		}
	};
}
