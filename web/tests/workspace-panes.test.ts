import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import AssistantStrip from '../src/lib/components/workspace/AssistantStrip.svelte';
import {
	assistantForm,
	assistantLimits,
	assistantShown,
	defaultZones,
	isUnreadReply,
	restoreZones,
	storedOpen,
	zoneWidths
} from '../src/lib/workspace/zones';

const unchosen = { products: null, assistant: null };

describe('workspace zones', () => {
	it('starts with the assistant open, and the products open only on a wide screen', () => {
		expect(defaultZones(1280)).toEqual({ productsOpen: true, assistantOpen: true });
		expect(defaultZones(1279)).toEqual({ productsOpen: false, assistantOpen: true });
	});

	it('restores each side as it was left, whatever the screen', () => {
		const left = { products: storedOpen(true), assistant: storedOpen(false) };
		expect(restoreZones(left, 800)).toEqual({ productsOpen: true, assistantOpen: false });

		const other = { products: storedOpen(false), assistant: storedOpen(true) };
		expect(restoreZones(other, 1600)).toEqual({ productsOpen: false, assistantOpen: true });
	});

	it('falls back to the default for a side with nothing usable remembered', () => {
		expect(restoreZones({ products: null, assistant: 'sideways' }, 1600)).toEqual(
			defaultZones(1600)
		);
		expect(restoreZones({ products: null, assistant: storedOpen(false) }, 900)).toEqual({
			productsOpen: false,
			assistantOpen: false
		});
	});

	it('gives an open side its width and a collapsed side a strip', () => {
		const open = { productsOpen: true, assistantOpen: true };
		const collapsed = { productsOpen: false, assistantOpen: false };

		expect(zoneWidths(open, unchosen, false)).toEqual({ products: '14rem', assistant: '28rem' });
		expect(zoneWidths(collapsed, { products: 300, assistant: 500 }, false)).toEqual({
			products: '3rem',
			assistant: '3rem'
		});
	});

	it('sizes the open products list from the width it was dragged to', () => {
		const open = { productsOpen: true, assistantOpen: true };

		expect(zoneWidths(open, { products: 300, assistant: null }, false).products).toBe('300px');
	});

	it('sizes the open assistant from the chosen width, held to the comparison floor', () => {
		const open = { productsOpen: true, assistantOpen: true };

		expect(zoneWidths(open, { products: null, assistant: 400 }, false).assistant).toBe('400px');
		expect(zoneWidths(open, { products: null, assistant: 400 }, true).assistant).toBe('640px');
		expect(zoneWidths(open, unchosen, true).assistant).toBe('40rem');
	});

	it('widens the assistant floor while two answers are compared', () => {
		expect(assistantLimits(false).min).toBe(320);
		expect(assistantLimits(true).min).toBe(640);
	});

	it('counts a reply as unread only when it lands on a collapsed assistant', () => {
		expect(isUnreadReply(2, 3, false)).toBe(true);
		expect(isUnreadReply(2, 3, true)).toBe(false);
		expect(isUnreadReply(2, 2, false)).toBe(false);
		// A conversation loading for the first time is not a reply.
		expect(isUnreadReply(0, 4, false)).toBe(false);
	});
});

describe('the assistant on a narrow screen', () => {
	const zones = { productsOpen: false, assistantOpen: true };

	it('is a sheet at 1050px and below, and a column above that', () => {
		expect(assistantForm(1000)).toBe('sheet');
		expect(assistantForm(1050)).toBe('sheet');
		expect(assistantForm(1051)).toBe('column');
	});

	it('starts as a bar however the column was left, and opens only when asked', () => {
		expect(assistantShown('sheet', zones, false)).toBe(false);
		expect(assistantShown('sheet', { ...zones, assistantOpen: false }, true)).toBe(true);
	});

	it('shows the column as it was left, whatever the sheet last did', () => {
		expect(assistantShown('column', zones, false)).toBe(true);
		expect(assistantShown('column', { ...zones, assistantOpen: false }, true)).toBe(false);
	});
});

describe('AssistantStrip', () => {
	it('opens the assistant when pressed', async () => {
		const onExpand = vi.fn();
		render(AssistantStrip, { onExpand });

		await fireEvent.click(screen.getByRole('button', { name: 'Open the assistant' }));

		expect(onExpand).toHaveBeenCalledOnce();
	});

	it('says when a reply is waiting', () => {
		render(AssistantStrip, { onExpand: vi.fn(), unread: true });
		expect(screen.getByRole('button', { name: 'Open the assistant (new reply)' })).toBeTruthy();
	});

	it('says when the assistant is working, ahead of an earlier unread reply', () => {
		render(AssistantStrip, { onExpand: vi.fn(), working: true, unread: true });
		expect(screen.getByRole('button', { name: 'Open the assistant (working)' })).toBeTruthy();
	});
});
