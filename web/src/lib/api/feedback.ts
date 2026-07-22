// ---- Feedback: user-submitted reports forwarded to GitHub issues ---------------
import type { Breadcrumb } from '../breadcrumbs';
import { request } from './core';

export type FeedbackCategory = 'bug' | 'idea' | 'other';

export type FeedbackSubmission = {
	message: string;
	category: FeedbackCategory;
	page: string;
	snapshot: Record<string, unknown>;
	breadcrumbs: Breadcrumb[];
};

export type FeedbackResult = {
	issue_url: string;
};

export async function submitFeedback(submission: FeedbackSubmission): Promise<FeedbackResult> {
	return request<FeedbackResult>('/feedback', {
		method: 'POST',
		body: JSON.stringify(submission)
	});
}

/** Whether this deployment has feedback configured (from runtime config). */
export function feedbackEnabled(): boolean {
	if (typeof window === 'undefined') return false;
	const config = window.__ALMANAC_CONFIG__;
	// Dev (no /config.js): show the widget; the backend answers 503 with a
	// clear message if the token is missing.
	if (!config) return true;
	return config.feedbackEnabled ?? false;
}
