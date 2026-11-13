/**
 * What a live forecast is set up from: which of a blend's models can run, and
 * the request the setup form sends.
 */
import type { Blend } from '$lib/api/blends';
import type { ForecastCreate, ForecastModel } from '$lib/api/forecasts';

export type RunnableModel = {
	/** The blend's name for the model: what is submitted, since it keys the blend's formula. */
	name: string;
	/** The live forecast model its data is linked to. */
	model: ForecastModel;
};

/** The blend's models that are linked to a live forecast model, in the blend's order. */
export function runnableModels(
	blend: Blend | null,
	forecastModels: ForecastModel[]
): RunnableModel[] {
	if (!blend) return [];
	const linked = blend.forecast_models ?? {};
	return blend.model_names.flatMap((name) => {
		const model = forecastModels.find((candidate) => candidate.id === linked[name]);
		return model ? [{ name, model }] : [];
	});
}

/**
 * The blend's models with no live forecast model. A forecast applies the blend's
 * whole formula, so it cannot run while any are left.
 */
export function unlinkedModels(blend: Blend | null): string[] {
	if (!blend) return [];
	const linked = blend.forecast_models ?? {};
	return blend.model_names.filter((name) => !(name in linked));
}

export type ForecastFields = {
	blendId: string;
	modelNames: string[];
	initSource: string;
	/** Free text; blank means the latest conditions available. */
	initTime: string;
	/** Undefined or NaN when the field is empty: the whole season to date. */
	maxIssueDates: number | undefined;
};

export function canSubmit(fields: ForecastFields, blend: Blend | null): boolean {
	return (
		fields.blendId !== '' && fields.modelNames.length > 0 && unlinkedModels(blend).length === 0
	);
}

/** The request for these fields, leaving out every setting left at its default. */
export function forecastRequest(fields: ForecastFields): ForecastCreate {
	const initTime = fields.initTime.trim();
	const initSource = fields.initSource.trim();
	const params: ForecastCreate['params'] = {
		...(initTime ? { init_time: initTime } : {}),
		...(initSource ? { init_source: initSource } : {}),
		...(fields.maxIssueDates != null && !Number.isNaN(fields.maxIssueDates)
			? { max_issue_dates: fields.maxIssueDates }
			: {})
	};
	return {
		blend_id: fields.blendId,
		forecast_model_ids: fields.modelNames,
		...(Object.keys(params).length ? { params } : {})
	};
}
