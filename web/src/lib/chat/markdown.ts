import { Marked, Renderer, type MarkedExtension } from 'marked';
import DOMPurify from 'dompurify';

const ARTIFACT_SCHEME = /^artifact:/i;
const RUN_SCHEME = /^run:/i;

function refFromHref(href: string, scheme: RegExp): string | null {
	if (!scheme.test(href)) return null;
	const ref = href.replace(scheme, '').trim();
	return ref.length > 0 ? ref : null;
}

/** The artifact label an `artifact:<label>` link points at, or null for any other link. */
export function artifactRefFromHref(href: string): string | null {
	return refFromHref(href, ARTIFACT_SCHEME);
}

/** The job a `run:<job id>` link opens the run of, or null for any other link. */
function runRefFromHref(href: string): string | null {
	return refFromHref(href, RUN_SCHEME);
}

/** The data attribute a workspace link's button carries, or null for an ordinary link. */
function workspaceLinkAttribute(href: string): string | null {
	const artifact = artifactRefFromHref(href);
	if (artifact !== null) return `data-artifact-ref="${escapeAttribute(artifact)}"`;
	const run = runRefFromHref(href);
	return run === null ? null : `data-run-ref="${escapeAttribute(run)}"`;
}

/** What a workspace link opens. */
export type LinkTarget = { kind: 'artifact'; ref: string } | { kind: 'run'; jobIds: string[] };

/** What the workspace link under a click opens, or null when the click was not on one. */
export function linkTarget(clicked: EventTarget | null): LinkTarget | null {
	const link =
		clicked instanceof Element ? clicked.closest('[data-artifact-ref], [data-run-ref]') : null;
	const ref = link?.getAttribute('data-artifact-ref');
	if (ref) return { kind: 'artifact', ref };
	const jobId = link?.getAttribute('data-run-ref');
	return jobId ? { kind: 'run', jobIds: [jobId] } : null;
}

function escapeAttribute(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;');
}

/**
 * File and run links become buttons carrying their reference in a data attribute,
 * so a delegated click handler opens them and no href ever reaches the DOM.
 * Without anywhere to open them they become plain text rather than dead links.
 */
function artifactLinks(openable: boolean): MarkedExtension {
	return {
		renderer: {
			link(token) {
				const attribute = workspaceLinkAttribute(token.href);
				if (attribute === null) return false;
				const text = this.parser.parseInline(token.tokens);
				if (!openable) return `<span class="artifact-link-text">${text}</span>`;
				return `<button type="button" class="artifact-link" ${attribute}>${text}</button>`;
			}
		}
	};
}

/**
 * Gives each markdown table a container of its own to scroll in, so a wide one
 * neither squeezes its columns nor pushes the conversation wider. The container
 * is written here, as the table is rendered, so that sanitising stays the last
 * thing done to the HTML: nothing may rewrite it afterwards.
 */
const scrollingTables: MarkedExtension = {
	renderer: {
		table(token) {
			const plain = new Renderer(this.options);
			plain.parser = this.parser;
			return `<div class="table-scroll" tabindex="0">${plain.table(token)}</div>`;
		}
	}
};

export type MarkdownOptions = {
	/** Render `artifact:` and `run:` links as buttons that open them in the workspace. */
	openArtifactLinks?: boolean;
	/** Put each table in a container that scrolls sideways. */
	scrollTables?: boolean;
};

function buildRenderer(options: Required<MarkdownOptions>): Marked {
	const extensions = [artifactLinks(options.openArtifactLinks)];
	return new Marked(...(options.scrollTables ? [...extensions, scrollingTables] : extensions));
}

const renderers = new Map<string, Marked>();

function rendererFor(options: MarkdownOptions): Marked {
	const resolved = {
		openArtifactLinks: options.openArtifactLinks ?? false,
		scrollTables: options.scrollTables ?? false
	};
	const key = `${resolved.openArtifactLinks}:${resolved.scrollTables}`;
	const renderer = renderers.get(key) ?? buildRenderer(resolved);
	renderers.set(key, renderer);
	return renderer;
}

/** Markdown as sanitised HTML. Sanitising is the final step; callers must not edit the result. */
export function renderMarkdown(text: string, options: MarkdownOptions = {}): string {
	return DOMPurify.sanitize(rendererFor(options).parse(text, { async: false }));
}
