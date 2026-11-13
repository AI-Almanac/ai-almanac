/**
 * What the file page shows of a workspace file's contents, and the parsing that
 * turns its downloaded text into something readable.
 */

export type PreviewKind = 'figure' | 'code' | 'table' | 'text';

/** Above this the file page offers the download instead of loading the file. */
export const PREVIEW_MAX_BYTES = 2_000_000;
const TABLE_PREVIEW_ROWS = 200;

const PREVIEW_BY_FORMAT: Record<string, PreviewKind> = {
	python: 'code',
	csv: 'table',
	json: 'text',
	geojson: 'text',
	text: 'text'
};

const FORMAT_NAMES: Record<string, string> = {
	python: 'Python code',
	csv: 'Table (CSV)',
	json: 'JSON',
	geojson: 'GeoJSON',
	text: 'Text',
	netcdf: 'NetCDF dataset',
	zarr: 'Zarr dataset',
	png: 'PNG image',
	webp: 'WebP image',
	jpeg: 'JPEG image'
};

export function previewKind(file: { kind: string; format: string }): PreviewKind | null {
	if (file.kind === 'figure') return 'figure';
	return PREVIEW_BY_FORMAT[file.format] ?? null;
}

export function formatName(format: string): string {
	return FORMAT_NAMES[format] ?? format;
}

export function formatBytes(bytes: number): string {
	const units = ['B', 'KB', 'MB', 'GB'];
	const exponent = Math.min(Math.floor(Math.log10(Math.max(bytes, 1)) / 3), units.length - 1);
	return `${(bytes / 1000 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`;
}

export type CsvTable = { columns: string[]; rows: string[][]; totalRows: number };

/** RFC 4180 records: quoted fields may hold commas, doubled quotes and line breaks. */
export function csvRecords(text: string): string[][] {
	const records: string[][] = [];
	let record: string[] = [];
	let field = '';
	let quoted = false;
	for (let i = 0; i < text.length; i++) {
		const char = text[i];
		if (quoted) {
			if (char === '"' && text[i + 1] === '"') {
				field += '"';
				i++;
			} else if (char === '"') quoted = false;
			else field += char;
		} else if (char === '"') quoted = true;
		else if (char === ',') {
			record.push(field);
			field = '';
		} else if (char === '\n' || char === '\r') {
			if (char === '\r' && text[i + 1] === '\n') i++;
			record.push(field);
			records.push(record);
			record = [];
			field = '';
		} else field += char;
	}
	if (field || record.length) records.push([...record, field]);
	return records;
}

/** The header and the first `maxRows` rows; `totalRows` counts every row. */
export function parseCsv(text: string, maxRows = TABLE_PREVIEW_ROWS): CsvTable {
	const [columns = [], ...rows] = csvRecords(text.replace(/^﻿/, ''));
	return { columns, rows: rows.slice(0, maxRows), totalRows: rows.length };
}

const NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?%?$/;

/** Columns whose every filled cell is a number, so the table can right-align them. */
export function numericColumns(table: CsvTable): boolean[] {
	return table.columns.map((_, index) => {
		const cells = table.rows.map((row) => row[index]?.trim() ?? '').filter(Boolean);
		return cells.length > 0 && cells.every((cell) => NUMBER.test(cell));
	});
}
