import type { SettingsGroup } from '$lib/api';

export type SettingsLink = {
	label: string;
	href: string;
	/** Hidden from non-admins, who reach settings only for their own API keys. */
	adminOnly: boolean;
};

export type SettingsNavGroup = {
	heading: string;
	links: SettingsLink[];
};

export function sectionSlug(groupName: string): string {
	return groupName
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-|-$/g, '');
}

/** Config groups whose subject is the assistant, so the nav can file them with
 * the ruleset and feedback pages rather than with the platform settings. */
function isAssistantGroup(group: SettingsGroup): boolean {
	return /^(ai |assistant)/i.test(group.name);
}

/**
 * The side nav: two headed groups, so a reader scanning for one setting has a
 * short list to scan rather than a page to scroll. Config groups come from the
 * schema, so a new backend group appears here without a frontend change.
 */
export function settingsNav(
	groups: SettingsGroup[],
	{ comparisonsEnabled = true }: { comparisonsEnabled?: boolean } = {}
): SettingsNavGroup[] {
	const link = (group: SettingsGroup): SettingsLink => ({
		label: group.name,
		href: `/settings/${sectionSlug(group.name)}`,
		adminOnly: true
	});
	return [
		{
			heading: 'Platform',
			links: [
				{ label: 'Overview', href: '/settings', adminOnly: true },
				...groups.filter((group) => !isAssistantGroup(group)).map(link)
			]
		},
		{
			heading: 'Assistant',
			links: [
				{ label: 'Model & API keys', href: '/settings/ai', adminOnly: false },
				...groups.filter(isAssistantGroup).map(link),
				{ label: 'Rulesets', href: '/settings/assistant', adminOnly: true },
				// Hidden with the feature: its endpoints 404 when the flag is off.
				...(comparisonsEnabled
					? [
							{
								label: 'Compare rulesets',
								href: '/settings/assistant/comparisons',
								adminOnly: true
							}
						]
					: []),
				{ label: 'Feedback', href: '/settings/assistant/feedback', adminOnly: true }
			]
		}
	];
}
