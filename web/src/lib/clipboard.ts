/**
 * Copies text to the clipboard.
 *
 * `navigator.clipboard` exists only in secure contexts (HTTPS or localhost). On a
 * plain-HTTP address it is missing, so the older copy command, which still works
 * there, copies it from a hidden text area instead.
 */
export async function copyText(text: string): Promise<void> {
	if (navigator.clipboard?.writeText) {
		await navigator.clipboard.writeText(text);
		return;
	}
	const area = document.createElement('textarea');
	area.value = text;
	area.setAttribute('readonly', '');
	area.style.position = 'fixed';
	area.style.opacity = '0';
	document.body.appendChild(area);
	area.focus();
	area.select();
	try {
		if (!document.execCommand('copy')) throw new Error('The browser did not copy the text');
	} finally {
		area.remove();
	}
}
