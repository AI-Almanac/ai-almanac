import type { ChatToolCall } from '$lib/api';
import type {
	ActionSource,
	InterfaceAction,
	WorkspaceTarget
} from '$lib/interface-actions/actions';

/** What a result card needs from the conversation around it. */
export type StepContext = {
	jobStatuses?: Record<string, string>;
	onOpenItem?: (target: WorkspaceTarget) => void;
	onInterfaceAction?: (action: InterfaceAction, source: ActionSource) => void;
};

export type ResultCardProps = { call: ChatToolCall; context: StepContext };
