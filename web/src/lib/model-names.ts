/**
 * Display names for ROMP model identifiers.
 *
 * Benchmarking's one climatology is built from historical onset frequency over
 * the climatology period and does not condition on onset having held off, so it
 * is the Traditional Climatology of the blend results view.
 */
const MODEL_LABELS: Record<string, string> = {
	fuxi: 'FuXi',
	aifs: 'AIFS',
	aifs_daily: 'AIFS Daily',
	fuxi_s2s: 'FuXi S2S',
	climatology: 'Traditional Climatology'
};

export function modelDisplayName(modelName: string): string {
	return MODEL_LABELS[modelName.toLowerCase()] ?? modelName;
}
