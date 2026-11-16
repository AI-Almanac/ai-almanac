import { describe, expect, it } from 'vitest';

import { ApiError, failureMessage } from '../src/lib/api/core';

describe('the message a failed request shows', () => {
	it('is the reason the server gave', () => {
		expect(failureMessage(new ApiError(400, 'Pick a model', 'POST /jobs failed (400)'))).toBe(
			'Pick a model'
		);
	});

	it('still says something when the response had no body', () => {
		expect(failureMessage(new ApiError(502, '', 'GET /jobs failed (502): '))).toBe(
			'The request failed (error 502). Try again.'
		);
	});
});
