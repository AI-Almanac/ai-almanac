import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { ChatToolCall } from '../src/lib/api';
import InterfaceActionCard from '../src/lib/components/chat/InterfaceActionCard.svelte';

function call(result: unknown): ChatToolCall {
	return {
		id: 'call-1',
		name: 'open_panel',
		status: 'completed',
		input: {},
		result,
		artifacts: []
	};
}

const offered = {
	interface_action: { kind: 'open_panel', panel: 'blend_setup', draft: { name: 'Kiremt blend' } },
	offered: 'The form is offered.'
};

describe('InterfaceActionCard', () => {
	it('brings up again what the assistant offered', async () => {
		const onInterfaceAction = vi.fn();
		render(InterfaceActionCard, {
			props: { call: call(offered), context: { onInterfaceAction } }
		});

		await fireEvent.click(screen.getByRole('button', { name: 'Open the blend setup' }));

		expect(onInterfaceAction).toHaveBeenCalledWith(
			{ kind: 'open_panel', panel: 'blend_setup', draft: { name: 'Kiremt blend', model_ids: [] } },
			'user'
		);
	});

	it('shows nothing for a call the assistant got wrong, or where nothing can act on it', () => {
		render(InterfaceActionCard, {
			props: { call: call({ error: 'No model x.' }), context: { onInterfaceAction: vi.fn() } }
		});
		render(InterfaceActionCard, { props: { call: call(offered), context: {} } });

		expect(screen.queryByRole('button')).toBeNull();
	});
});
