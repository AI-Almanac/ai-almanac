/**
 * A random (version 4) UUID.
 *
 * `crypto.randomUUID()` exists only in secure contexts (HTTPS or localhost), so on
 * a plain-HTTP address, such as a machine's name on a private network, it is
 * missing and calling it throws, which leaves a page blank. `crypto.getRandomValues()`
 * is available everywhere, so the same kind of UUID is built from it there.
 */
export function randomId(): string {
	if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
	const bytes = crypto.getRandomValues(new Uint8Array(16));
	bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
	bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
	const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
