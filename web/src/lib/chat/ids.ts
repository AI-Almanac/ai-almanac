import type { ChatMessage } from '$lib/api';

/*
 * The assistant's tools hand it IDs so it can make further calls, and the
 * ruleset tells it never to show them. Smaller models show them anyway, so
 * the transcript enforces it: IDs the tools returned are shown as the names
 * the tools returned beside them, and any other ID is dropped.
 */

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const QUOTED_UUID = `\`?(${UUID})\`?`;

const NAME_KEYS = ['name', 'display_name', 'model_name', 'label'];

function nameOf(record: Record<string, unknown>): string | null {
	for (const key of NAME_KEYS) {
		const value = record[key];
		if (typeof value === 'string' && value.trim()) return value.trim();
	}
	return null;
}

function collect(value: unknown, names: Map<string, string>): void {
	if (typeof value === 'string') {
		// Tools that return JSON text arrive unparsed when the result was not an object.
		if (/^\s*[[{]/.test(value)) {
			try {
				collect(JSON.parse(value), names);
			} catch {
				// Not JSON after all; nothing to learn from it.
			}
		}
		return;
	}
	if (Array.isArray(value)) {
		for (const item of value) collect(item, names);
		return;
	}
	if (!value || typeof value !== 'object') return;
	const record = value as Record<string, unknown>;
	const name = nameOf(record);
	if (typeof record.id === 'string' && name) names.set(record.id.toLowerCase(), name);
	for (const [key, item] of Object.entries(record)) {
		// dataset_id beside dataset_name, model_ids beside model_names.
		const single = key.match(/^(.*)_id$/);
		const plural = key.match(/^(.*)_ids$/);
		if (single && typeof item === 'string') {
			const paired = record[`${single[1]}_name`];
			if (typeof paired === 'string' && paired.trim()) names.set(item.toLowerCase(), paired.trim());
		} else if (plural && Array.isArray(item)) {
			const paired = record[`${plural[1]}_names`];
			if (Array.isArray(paired) && paired.length === item.length) {
				item.forEach((id, i) => {
					if (typeof id === 'string' && typeof paired[i] === 'string')
						names.set(id.toLowerCase(), paired[i]);
				});
			}
		}
		collect(item, names);
	}
}

/** The name of every ID the assistant's tools returned in these turns. */
export function namesById(turns: ChatMessage[]): Map<string, string> {
	const names = new Map<string, string>();
	for (const turn of turns) {
		for (const toolCall of turn.tool_calls ?? []) collect(toolCall.result, names);
	}
	return names;
}

function plainText(text: string): string {
	return text.replace(/[*_`]/g, '').trim();
}

function hideInline(text: string, names: Map<string, string>): string {
	const nameFor = (id: string) => names.get(id.toLowerCase()) ?? '';
	let out = text;
	// "AIFSv2 (9aa0…)" or "Chirps (dataset ID `ac2e…`)": the name is usually already there.
	out = out.replace(
		new RegExp(`\\s*\\(\\s*(?:[a-z]+\\s+)?(?:ids?:?\\s*)?${QUOTED_UUID}\\s*\\)`, 'gi'),
		(_match, id: string, offset: number, whole: string) => {
			const name = nameFor(id);
			return name && !plainText(whole.slice(0, offset)).endsWith(name) ? ` (${name})` : '';
		}
	);
	// "ac2e… (Chirps)": the label the assistant gave it is the name.
	out = out.replace(
		new RegExp(`${QUOTED_UUID}\\s*\\(([^()\\n]{1,80})\\)`, 'gi'),
		(_match, _id: string, label: string) => label.trim()
	);
	// "Model IDs: …" → "Models: …", "dataset ID …" → "dataset …", "ID: …" → "…".
	const before = `(?=${QUOTED_UUID})`;
	out = out.replace(new RegExp(`\\b([a-z]+)\\s+ID(s?)(:?)\\s+${before}`, 'gi'), '$1$2$3 ');
	out = out.replace(new RegExp(`\\bIDs?:?\\s+${before}`, 'gi'), '');
	out = out.replace(new RegExp(QUOTED_UUID, 'gi'), (_match, id: string) => nameFor(id));
	if (out === text) return text;
	// Tidy what dropping an ID leaves behind.
	return out
		.replace(/\(\s*\)/g, '')
		.replace(/(?:,\s*){2,}/g, ', ')
		.replace(/(\S) {2,}(?=\S)/g, '$1 ')
		.replace(/ +([,.;:])/g, '$1');
}

/** "Model ID" → "Model", "Model IDs" → "Models", "ID" → "Name". */
function withoutIdWord(label: string): string {
	return label.replace(/^(\W*)(.*?)\s*\bID(s?)\b(\W*)$/i, (_m, pre, rest, s, post) =>
		rest ? `${pre}${rest}${s}${post}` : `${pre}Name${post}`
	);
}

const ID_LABEL = /^\W*([a-z][\w ]*?\s+)?IDs?\W*$/i;
const SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function cells(row: string): string[] {
	return row
		.trim()
		.replace(/^\|/, '')
		.replace(/\|$/, '')
		.split('|')
		.map((cell) => cell.trim());
}

function hideInTable(rows: string[], names: Map<string, string>): string[] {
	const [header, separator, ...body] = rows.map(cells);
	const replaced = body.map((row) =>
		row.map((cell) => hideInline(cell, names).replace(/^,\s*|,\s*$/g, ''))
	);
	const keep = header.map((label, column) => {
		if (!ID_LABEL.test(label)) return true;
		// An ID column whose names repeat another column says nothing new.
		const values = replaced.map((row) => plainText(row[column] ?? ''));
		return !header.some(
			(_other, otherColumn) =>
				otherColumn !== column &&
				replaced.every((row, r) => plainText(row[otherColumn] ?? '') === values[r])
		);
	});
	const row = (values: string[]) => `| ${values.filter((_v, i) => keep[i] ?? true).join(' | ')} |`;
	return [
		row(header.map((label) => (ID_LABEL.test(label) ? withoutIdWord(label) : label))),
		row(separator),
		// A field/value table labels its rows in the first column.
		...replaced.map((values) =>
			row(
				values.map((value, i) => (i === 0 && ID_LABEL.test(value) ? withoutIdWord(value) : value))
			)
		)
	];
}

/** The assistant's markdown with IDs shown as names, or dropped where no name is known. */
export function hideIds(text: string, names: Map<string, string>): string {
	if (!/[0-9a-f]{8}-/i.test(text)) return text;
	// An ID still streaming in shows once it is whole, not hex by hex.
	const settled = text.replace(/`?\b[0-9a-f]{8}-[0-9a-f-]{0,27}$/i, (match) =>
		new RegExp(`${UUID}\`?$`, 'i').test(match) ? match : ''
	);
	const lines = settled.split('\n');
	const out: string[] = [];
	let fenced = false;
	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];
		if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
		if (fenced || /^\s*(```|~~~)/.test(line)) {
			out.push(line);
			continue;
		}
		const next = lines[i + 1] ?? '';
		if (line.includes('|') && next.includes('|') && SEPARATOR.test(next)) {
			let end = i + 2;
			while (end < lines.length && lines[end].includes('|') && lines[end].trim()) end++;
			out.push(...hideInTable(lines.slice(i, end), names));
			i = end - 1;
			continue;
		}
		out.push(hideInline(line, names));
	}
	return out.join('\n');
}
