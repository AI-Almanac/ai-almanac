/**
 * The panels a workspace can open over its stage, by name. The "New" menu, a
 * button in the conversation and the assistant all open a panel the same way, so
 * bringing another part of the platform into the workspace means adding it here.
 */
import { SETUP_SUBJECTS, type SetupDrafts, type SetupPanel } from '$lib/interface-actions/actions';

export type PanelName = SetupPanel | 'add_existing_run';

/** A panel to open, with what to fill it in with when something supplied a draft. */
export type PanelRequest =
	| { [Panel in SetupPanel]: { panel: Panel; draft?: SetupDrafts[Panel] } }[SetupPanel]
	| { panel: 'add_existing_run' };

export function panelTitle(panel: PanelName): string {
	return panel === 'add_existing_run' ? 'Add an existing run' : `New ${SETUP_SUBJECTS[panel]}`;
}

/** The panel as it is shown: opening the same panel again shows it afresh. */
export type OpenPanel = { request: PanelRequest; opening: number };

export function opened(request: PanelRequest, previous: OpenPanel | null): OpenPanel {
	return { request, opening: (previous?.opening ?? 0) + 1 };
}
