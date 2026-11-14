import { describe, expect, it } from 'vitest';
import { transcriptText } from '../src/lib/chat/column';

describe('the conversation column', () => {
	it('copies the conversation as who said what', () => {
		expect(
			transcriptText([
				{ role: 'user', content: 'Which model is best?' },
				{ role: 'assistant', content: 'AIFS.' }
			])
		).toBe('You: Which model is best?\n\nAI: AIFS.');
	});
});
