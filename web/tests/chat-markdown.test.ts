import { describe, expect, it } from 'vitest';
import { linkTarget, renderMarkdown } from '../src/lib/chat/markdown';

describe('a clicked workspace link', () => {
	const host = document.createElement('div');
	host.innerHTML = renderMarkdown('See [the map](artifact:onset-map) or [the run](run:job-7).', {
		openArtifactLinks: true
	});
	const [file, run] = host.querySelectorAll('button');

	it('opens the file or run it names', () => {
		expect(linkTarget(file)).toEqual({ kind: 'artifact', ref: 'onset-map' });
		expect(linkTarget(run)).toEqual({ kind: 'run', jobIds: ['job-7'] });
	});

	it('opens nothing for a click anywhere else', () => {
		expect(linkTarget(host)).toBeNull();
		expect(linkTarget(null)).toBeNull();
	});
});

const TABLE = ['| Zone | AIFS |', '| --- | --- |', '| Awi | 3.8 |'].join('\n');

function parsed(html: string): HTMLElement {
	const host = document.createElement('div');
	host.innerHTML = html;
	return host;
}

describe('markdown tables', () => {
	it('gives a table a container to scroll in when asked to', () => {
		const table = parsed(renderMarkdown(TABLE, { scrollTables: true })).querySelector('table');

		expect(table?.parentElement?.className).toBe('table-scroll');
		expect(table?.parentElement?.getAttribute('tabindex')).toBe('0');
		expect(table?.textContent).toContain('Awi');
	});

	it('leaves a table as it is otherwise', () => {
		const table = parsed(renderMarkdown(TABLE)).querySelector('table');

		expect(table).not.toBeNull();
		expect(table?.parentElement?.className).not.toBe('table-scroll');
	});

	it('gives each of several tables its own container', () => {
		const html = renderMarkdown(`${TABLE}\n\nBetween.\n\n${TABLE}`, { scrollTables: true });

		expect(parsed(html).querySelectorAll('.table-scroll > table')).toHaveLength(2);
	});

	// The container used to be added by rewriting the sanitised HTML, which turned
	// the word "<table" inside an attribute into live markup. Text that only looks
	// like a table must stay text, whatever it is wrapped in.
	it.each([
		'[x](http://a "<table style=position:fixed onmouseover=alert(1)// ")',
		'![x](http://a/b.png "<table onmouseover=alert(1)>")',
		'<a title="<table onmouseover=alert(1) ">x</a>',
		'`<table onmouseover=alert(1)>`'
	])('never makes a table out of text that mentions one: %s', (reply) => {
		const host = parsed(renderMarkdown(reply, { scrollTables: true }));

		expect(host.querySelector('table')).toBeNull();
		expect(host.querySelector('.table-scroll')).toBeNull();
		expect(host.querySelector('[onmouseover]')).toBeNull();
	});
});
