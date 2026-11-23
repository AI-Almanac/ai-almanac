/**
 * The words on the workspace summary strip: each fact as a headline the eye lands
 * on and a quieter detail beneath it, from the server's typed summary.
 */
import type { ForecastSummary, SeasonSummary, SetupSummary, WorkspaceSkill } from '$lib/api';
import { formatDay } from '$lib/format/dates';
import { plural } from '$lib/format/plural';
import { modelDisplayName } from '$lib/model-names';

export type Fact = { headline: string; detail: string | null };

/** The season's name carries its year, so the start date is said without one. */
function startsFact(name: string, start: string, days: number | null): Fact {
	return {
		headline: `${name} · onset search starts ${formatDay(start, false)}`,
		detail: days === null || days < 1 ? null : `in ${plural(days, 'day')}`
	};
}

/** Where the season stands, in the year the workspace works on: once it has ended it
 * says so, and the year only moves on when the user chooses another. */
export function seasonFact(season: SeasonSummary): Fact {
	const name = `${season.name} ${season.year}`;
	if (season.phase === 'upcoming') {
		return startsFact(name, season.first_issue_date, season.days_until_start);
	}
	if (season.phase === 'in_season') {
		return {
			headline: `${name} is under way`,
			detail: `issued through ${formatDay(season.issued_through ?? season.first_issue_date, false)} · last issue date ${formatDay(season.last_issue_date, false)}`
		};
	}
	return { headline: `${name} ended ${formatDay(season.last_issue_date, false)}`, detail: null };
}

export function forecastFact(forecast: ForecastSummary | null): Fact {
	if (!forecast) {
		return {
			headline: 'No forecast yet',
			detail: 'Ask the assistant to run this season’s forecast'
		};
	}
	if (forecast.status !== 'complete') {
		return { headline: `Forecast ${forecast.status}`, detail: forecast.blend_name ?? null };
	}
	if (!forecast.blended) {
		return { headline: 'Raw model forecast', detail: 'each model’s own rainfall, no blend' };
	}
	const from = forecast.blend_name ? ` · from ${forecast.blend_name}` : '';
	if (!forecast.first_issue_date || !forecast.last_issue_date) {
		return { headline: 'Blended forecast', detail: `no issue dates yet${from}` };
	}
	return {
		headline: `Issued ${formatDay(forecast.first_issue_date, false)} – ${formatDay(forecast.last_issue_date)}`,
		detail: `${plural(forecast.issue_dates, 'issue date')}${from}`
	};
}

export function canUpdateForecast(forecast: ForecastSummary | null): boolean {
	return forecast?.status === 'complete' && forecast.blended;
}

/** What the Update button says to the assistant; the approval card guards the cost. */
export function updateForecastPrompt(forecast: ForecastSummary): string {
	const which = forecast.blend_name ? `the ${forecast.blend_name} forecast` : 'the latest forecast';
	return `Update ${which} with any new issue dates, and tell me what changed since the last issue.`;
}

export function skillFact(skill: WorkspaceSkill | null): Fact {
	if (!skill) {
		return { headline: 'Not benchmarked yet', detail: 'Run a benchmark to see skill here' };
	}
	if (skill.kind === 'blend') {
		const heldOut = skill.holdout_years ? ` · held out ${skill.holdout_years}` : '';
		return {
			headline: `RPSS ${skill.rps_skill.toFixed(2)} vs climatology`,
			detail: `${skill.name}${heldOut}`
		};
	}
	const climatology =
		skill.climatology_mae_days === null ? '' : ` · climatology ${skill.climatology_mae_days} d`;
	return {
		headline: `${modelDisplayName(skill.model)} · ${skill.mae_days} d onset error`,
		detail: `best model, days ${skill.window.replace('-', '–')}${climatology}`
	};
}

/** Region, observations and models as short chips; whatever is unknown is left out. */
export function setupChips(setup: SetupSummary): string[] {
	const models = setup.models.map(modelDisplayName).join(', ');
	return [setup.region, setup.observations, models || null].filter((chip): chip is string =>
		Boolean(chip)
	);
}
