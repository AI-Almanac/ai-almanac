// Parses the blend's pooled per-model summary CSV into the skill series the
// radar chart renders. Only the small pooled summary is read client-side; the
// large combined pickle is never loaded into the browser.

export const SKILL_AXES = ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Later'];
const AUC_COLUMNS = ['auc_week1', 'auc_week2', 'auc_week3', 'auc_week4', 'auc_later'];
const BLEND_MODEL = 'blended_model';

export type SkillRow = {
	model: string;
	label: string;
	isBlend: boolean;
	auc: number;
	brierSkill: number | null;
	aucByLead: number[];
};

// Turn a raw model id into a reader-friendly label.
//   blended_model                 -> "Blend"
//   clim_raw                      -> "Climatology"
//   unc_clim_raw                  -> "Climatology (uncalibrated)"
//   aifs_clim_mok_date_raw        -> "AIFS (raw)"
//   aifs_calibrated_clim_mok_date -> "AIFS (calibrated)"
function prettyModel(model: string): string {
	if (model === BLEND_MODEL) return 'Blend';
	if (model === 'clim_raw') return 'Climatology';
	if (model === 'unc_clim_raw') return 'Climatology (uncalibrated)';

	const calibrated = /calibrated/.test(model);
	const name = model
		.replace(/_calibrated/g, '')
		.replace(/_clim_mok_date/g, '')
		.replace(/_raw$/, '')
		.replace(/_/g, ' ')
		.trim()
		.toUpperCase();
	return `${name} (${calibrated ? 'calibrated' : 'raw'})`;
}

function finite(value: string | undefined): number | null {
	const n = Number(value);
	return Number.isFinite(n) ? n : null;
}

export function parsePooledSummary(csv: string): SkillRow[] {
	const lines = csv.trim().split(/\r?\n/);
	if (lines.length < 2) return [];
	const header = lines[0].split(',');
	const index = (name: string) => header.indexOf(name);
	const modelIdx = index('model');
	const aucIdx = index('auc');
	const skillIdx = index('brier_skill');
	const leadIdx = AUC_COLUMNS.map(index);
	if (modelIdx < 0 || leadIdx.some((i) => i < 0)) return [];

	const rows: SkillRow[] = [];
	for (const line of lines.slice(1)) {
		const cells = line.split(',');
		const model = cells[modelIdx];
		if (!model) continue;
		const aucByLead = leadIdx.map((i) => finite(cells[i]));
		if (aucByLead.some((v) => v === null)) continue;
		rows.push({
			model,
			label: prettyModel(model),
			isBlend: model === BLEND_MODEL,
			auc: finite(cells[aucIdx]) ?? 0,
			brierSkill: skillIdx >= 0 ? finite(cells[skillIdx]) : null,
			aucByLead: aucByLead as number[]
		});
	}
	// Blend first: it draws on top and leads the legend.
	return rows.sort((a, b) => Number(b.isBlend) - Number(a.isBlend));
}
