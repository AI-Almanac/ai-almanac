import type { ForecastModel } from '$lib/api';

/**
 * Live forecast models an archive on `gridStep` can be linked to: those on the
 * same grid (the server rejects any other link), plus `current` so a saved link
 * always stays listed. Every model is offered while the grid is unknown.
 */
export function linkableModels(
	models: ForecastModel[],
	gridStep: unknown,
	current?: string | null
): ForecastModel[] {
	return models.filter(
		(model) =>
			model.id === current ||
			typeof gridStep !== 'number' ||
			Math.abs(model.resolution_deg - gridStep) < 1e-6
	);
}

/** `metadata` linked to `modelId`, or unlinked when it is empty. */
export function withForecastModel(
	metadata: Record<string, unknown>,
	modelId: string
): Record<string, unknown> {
	const linked = { ...metadata };
	if (modelId) linked.forecast_model_id = modelId;
	else delete linked.forecast_model_id;
	return linked;
}
