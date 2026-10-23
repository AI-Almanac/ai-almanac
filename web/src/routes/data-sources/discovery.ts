import type { DataSourceCreate, DiscoveredSource } from '$lib/api';
import { withForecastModel } from './live-links';

/** What the user can change about a proposed source before registering it. */
export type DiscoveryChoice = { name: string; region: string; forecastModelId: string };

/** A proposal can be registered once it validated and nothing points at it yet. */
export function canRegister(source: DiscoveredSource): boolean {
	return source.status === 'ready' && source.registered_as == null;
}

/** Paths ticked when a scan returns: registrable proposals that already have a region. */
export function initialSelection(sources: DiscoveredSource[]): Record<string, boolean> {
	return Object.fromEntries(
		sources.map((source) => [source.path, canRegister(source) && Boolean(source.region)])
	);
}

/** Starting values for what the user can change, including any suggested live model. */
export function initialChoices(sources: DiscoveredSource[]): Record<string, DiscoveryChoice> {
	return Object.fromEntries(
		sources.map((source) => [
			source.path,
			{
				name: source.name,
				region: source.region ?? '',
				forecastModelId: String(source.metadata.forecast_model_id ?? '')
			}
		])
	);
}

/** The create request for a proposal: its validated metadata, with the user's choices. */
export function registrationBody(
	source: DiscoveredSource,
	choice: DiscoveryChoice
): DataSourceCreate {
	return {
		kind: source.kind,
		name: choice.name.trim() || source.name,
		path: source.path,
		region: choice.region,
		metadata:
			source.kind === 'model'
				? withForecastModel(source.metadata, choice.forecastModelId)
				: source.metadata
	};
}

/** One line on what was found: `data_{}.nc · RAINFALL · 1901–2025`. */
export function proposalSummary(source: DiscoveredSource): string {
	const metadata = source.metadata;
	const pattern = source.kind === 'obs' ? metadata.obs_file_pattern : metadata.file_pattern;
	const variable = source.kind === 'obs' ? metadata.obs_var : metadata.model_var;
	const parts = [pattern, variable].filter(Boolean).map(String);
	if (metadata.start_year != null && metadata.end_year != null) {
		parts.push(`${metadata.start_year}–${metadata.end_year}`);
	}
	if (metadata.probabilistic === true) parts.push('ensemble');
	return parts.join(' · ');
}
