<script lang="ts">
	import type { ChatJobEvent } from '$lib/api';
	import ChatNotice from '$lib/components/ChatNotice.svelte';
	import { jobEventText, type JobFollowUp } from '$lib/chat/conversation';

	interface Props {
		event: ChatJobEvent;
		followUp: JobFollowUp | null;
		onFollowUp: (prompt: string) => void;
	}

	const { event, followUp, onFollowUp }: Props = $props();

	const TONES = { complete: 'success', failed: 'failed', canceled: 'neutral' } as const;
	const ICONS = { complete: '✓', failed: '!', canceled: '–' } as const;
</script>

<ChatNotice
	tone={TONES[event.status]}
	icon={ICONS[event.status]}
	title={jobEventText(event)}
	detail={new Date(event.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
	actions={followUp ? [{ label: followUp.label, onclick: () => onFollowUp(followUp.prompt) }] : []}
/>
