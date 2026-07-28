/** Display names for ROMP model identifiers. */
const MODEL_LABELS: Record<string, string> = {
	fuxi: 'FuXi',
	aifs: 'AIFS',
	aifs_daily: 'AIFS Daily',
	fuxi_s2s: 'FuXi S2S',
	climatology: 'Climatology'
};

export function modelDisplayName(modelName: string): string {
	return MODEL_LABELS[modelName.toLowerCase()] ?? modelName;
}
