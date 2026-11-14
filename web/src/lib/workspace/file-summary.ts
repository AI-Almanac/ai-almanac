/**
 * Parses the server's shape-level `summary` of a workspace file (see
 * provenance.summarize) into what the Details tab shows. The summary is an
 * untyped JSON blob, so anything malformed is dropped rather than rendered.
 */

export type FileVariable = { name: string; description: string | null; units: string | null };
export type TimeRange = { coord: string; start: string; end: string; steps: number };

export type FileSummary = {
	dimensions: [string, number][];
	variables: FileVariable[];
	timeRange: TimeRange | null;
	imageSize: { width: number; height: number } | null;
	sizeBytes: number | null;
};

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown): string | null {
	return typeof value === 'string' && value ? value : null;
}

function parseDimensions(raw: unknown): [string, number][] {
	if (!isRecord(raw)) return [];
	return Object.entries(raw).flatMap(([name, size]) =>
		typeof size === 'number' ? [[name, size] as [string, number]] : []
	);
}

function parseVariables(raw: unknown): FileVariable[] {
	if (!isRecord(raw)) return [];
	return Object.entries(raw).map(([name, meta]) => ({
		name,
		description: isRecord(meta) ? stringOrNull(meta.long_name) : null,
		units: isRecord(meta) ? stringOrNull(meta.units) : null
	}));
}

const TIME_COORD = /time|date/i;

function parseTimeRange(raw: unknown): TimeRange | null {
	if (!isRecord(raw)) return null;
	const entry = Object.entries(raw).find(([name, meta]) => TIME_COORD.test(name) && isRecord(meta));
	if (!entry) return null;
	const [coord, meta] = entry as [string, Json];
	const start = stringOrNull(meta.min);
	const end = stringOrNull(meta.max);
	if (!start || !end) return null;
	return { coord, start, end, steps: typeof meta.size === 'number' ? meta.size : 0 };
}

function parseImageSize(raw: Json): FileSummary['imageSize'] {
	const { width, height } = raw;
	return typeof width === 'number' && typeof height === 'number' ? { width, height } : null;
}

export function parseFileSummary(raw: Record<string, unknown>): FileSummary {
	return {
		dimensions: parseDimensions(raw.dims),
		variables: parseVariables(raw.variables),
		timeRange: parseTimeRange(raw.coords),
		imageSize: parseImageSize(raw),
		sizeBytes: typeof raw.size_bytes === 'number' ? raw.size_bytes : null
	};
}
