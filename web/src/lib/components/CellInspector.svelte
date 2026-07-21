<script lang="ts">
	import type { BlendForecastPoint } from '$lib/api';
	import {
		WEEKS,
		WEEK_LABELS,
		rampColor,
		argmax,
		fmtProb,
		fmtDate,
		isoToDay,
		dayToIso,
		windowDayRange,
		consensusOnsetDay,
		monthLabel,
		LATER_START_DAY
	} from '$lib/onset';
	import { formatCoord } from '$lib/geo';

	type Props = {
		point: BlendForecastPoint;
		issueDates: string[];
		regionName: string | null;
		selectedDate: string;
		soonestColor: 'yellow' | 'purple';
		onClose: () => void;
	};
	let { point, issueDates, regionName, selectedDate, soonestColor, onClose }: Props = $props();

	// Matches the map toggle: the vivid end marks the highest probability.
	const reversed = $derived(soonestColor === 'purple');

	// Season date axis: earliest Week-1 start → latest Week-4 end, across all
	// forecasts. "Later" is open-ended so it lives off-axis in its own column.
	const axis = $derived.by(() => {
		const days = issueDates.map(isoToDay);
		const start = Math.min(...days) + 1; // earliest Week 1 start
		const end = Math.max(...days) + 28; // latest Week 4 end
		const span = Math.max(1, end - start);

		const ticks: { label: string; day: number }[] = [];
		let y = Number(dayToIso(start).slice(0, 4));
		let m = Number(dayToIso(start).slice(5, 7));
		for (;;) {
			const iso = `${y}-${String(m).padStart(2, '0')}-01`;
			const day = isoToDay(iso);
			if (day > end) break;
			if (day >= start) ticks.push({ label: monthLabel(iso), day });
			m += 1;
			if (m > 12) {
				m = 1;
				y += 1;
			}
		}
		return { start, span, ticks };
	});

	function leftPct(day: number): number {
		return Math.max(0, Math.min(100, ((day - axis.start) / axis.span) * 100));
	}
	function widthPct(days: number): number {
		return Math.min(100, (days / axis.span) * 100);
	}

	// Most-likely window index for a forecast row, or -1 if it carries no mass.
	function bestIndex(row: number[]): number {
		return row.some((p) => p > 0) ? argmax(row) : -1;
	}

	function laterStartIso(issueIso: string): string {
		return dayToIso(isoToDay(issueIso) + LATER_START_DAY);
	}

	// Probability-weighted consensus onset date across all forecasts, using only
	// the bounded (dated) windows. Early forecasts that put their mass in "Later"
	// contribute little, so the estimate is driven by forecasts that actually
	// place onset on the calendar — i.e. where the models agree.
	const consensus = $derived.by(() => {
		const mid = consensusOnsetDay(issueDates, point.probs);
		if (mid == null) return null;
		return { start: Math.round(mid - 3), end: Math.round(mid + 3) };
	});
</script>

<aside class="inspector">
	<header class="ins-header">
		<div>
			<span class="ins-title">Onset outlook</span>
			<span class="ins-coords">{formatCoord(point.lat, 'N', 'S')} {formatCoord(point.lon, 'E', 'W')}</span>
		</div>
		<button class="ins-close" aria-label="Close" onclick={onClose}>×</button>
	</header>

	{#if consensus}
		<p class="ins-headline">
			Forecasts point to onset around
			<strong>{fmtDate(dayToIso(consensus.start))} – {fmtDate(dayToIso(consensus.end))}</strong>.
		</p>
	{/if}

	<p class="ins-hint">
		Each row is one forecast{regionName ? ` · ${regionName}` : ''}, reading top (earliest) to bottom
		(latest). Its colored blocks show where that forecast placed onset on the calendar — toward
		{soonestColor} = more likely, ringed = most likely. When the ringed blocks line up in a column,
		the forecasts agree on that onset date.
	</p>

	<div class="cal">
		<!-- axis titles -->
		<div class="cal-row cal-titles">
			<span class="cal-date cal-axistitle">Issued ↓</span>
			<span class="cal-axistitle">Predicted onset date →</span>
			<span></span>
		</div>
		<!-- month axis -->
		<div class="cal-row cal-axis">
			<span class="cal-date"></span>
			<div class="cal-track">
				{#each axis.ticks as t (t.label + t.day)}
					<span class="cal-month" style="left: {leftPct(t.day)}%">{t.label}</span>
				{/each}
			</div>
			<span class="cal-later-head">Later</span>
		</div>

		{#each issueDates as d, di (d)}
			{@const row = point.probs[di] ?? [0, 0, 0, 0, 0]}
			{@const best = bestIndex(row)}
			<div class="cal-row">
				<span class="cal-date" class:current={d === selectedDate}>{fmtDate(d)}</span>
				<div class="cal-track">
					{#if consensus}
						<span
							class="cal-band"
							style="left: {leftPct(consensus.start)}%; width: {widthPct(
								consensus.end - consensus.start
							)}%"
						></span>
					{/if}
					{#each [0, 1, 2, 3] as w (w)}
						{@const r = windowDayRange(d, w)}
						<span
							class="cal-seg"
							class:best={w === best}
							style="left: {leftPct(r.start)}%; width: {widthPct(7)}%; background: {rampColor(
								row[w] ?? 0,
								reversed
							)}"
							title="{WEEK_LABELS[WEEKS[w]]} · {fmtDate(dayToIso(r.start))}–{fmtDate(
								dayToIso(r.end)
							)}: {fmtProb(row[w] ?? 0)}"
						></span>
					{/each}
				</div>
				<span
					class="cal-later"
					class:best={best === 4}
					style="background: {rampColor(row[4] ?? 0, reversed)}"
					title="Later · onset after {fmtDate(laterStartIso(d))}: {fmtProb(row[4] ?? 0)}"
				></span>
			</div>
		{/each}
	</div>
</aside>

<style>
	.inspector {
		position: absolute;
		top: 0;
		right: 0;
		bottom: 3.4rem;
		z-index: 4;
		width: 27rem;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 0.9rem;
		background:
			linear-gradient(145deg, rgba(255, 255, 255, 0.97), rgba(239, 247, 243, 0.96)),
			var(--color-surface);
		border-left: 1px solid rgba(31, 43, 52, 0.12);
		box-shadow: -1.25rem 0 2.5rem rgba(3, 14, 25, 0.18);
		color: #1f2b34;
		overflow-y: auto;
	}

	.ins-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.ins-title {
		display: block;
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #54706f;
	}

	.ins-coords {
		display: block;
		margin-top: 0.15rem;
		font-size: 0.82rem;
		font-weight: 800;
		color: #18252b;
		font-variant-numeric: tabular-nums;
	}

	.ins-close {
		flex: none;
		width: 1.6rem;
		height: 1.6rem;
		border: 1px solid rgba(31, 43, 52, 0.18);
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.72);
		color: #223138;
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
	}

	.ins-close:hover {
		background: #fff;
		border-color: rgba(31, 43, 52, 0.32);
	}

	.ins-headline {
		margin: 0;
		font-size: 0.78rem;
		line-height: 1.4;
		color: #46555c;
	}

	.ins-headline strong {
		color: #18252b;
		font-weight: 800;
		white-space: nowrap;
	}

	.ins-hint {
		margin: 0;
		font-size: 0.63rem;
		line-height: 1.4;
		color: #627174;
	}

	.cal-titles {
		font-size: 0.54rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #77868a;
	}

	.cal-axistitle {
		white-space: nowrap;
	}

	.cal-band {
		position: absolute;
		top: -1px;
		bottom: -1px;
		background: rgba(31, 43, 52, 0.06);
		border-left: 1px solid rgba(31, 43, 52, 0.16);
		border-right: 1px solid rgba(31, 43, 52, 0.16);
		pointer-events: none;
	}

	.cal {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.cal-row {
		display: grid;
		grid-template-columns: 3rem 1fr 2.4rem;
		column-gap: 0.35rem;
		align-items: center;
	}

	.cal-date {
		font-size: 0.62rem;
		font-weight: 600;
		color: #56656b;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		text-align: right;
	}

	.cal-date.current {
		color: #18252b;
		font-weight: 800;
	}

	.cal-track {
		position: relative;
		height: 1.05rem;
	}

	.cal-seg {
		position: absolute;
		top: 0;
		height: 100%;
		border-radius: 0.15rem;
		opacity: 0.62;
		box-shadow: inset 0 0 0 1px rgba(31, 43, 52, 0.14);
	}

	.cal-seg.best,
	.cal-later.best {
		opacity: 1;
		box-shadow: inset 0 0 0 1.5px rgba(24, 37, 43, 0.85);
	}

	.cal-later {
		height: 1.05rem;
		border-radius: 0.15rem;
		opacity: 0.62;
		box-shadow: inset 0 0 0 1px rgba(31, 43, 52, 0.14);
	}

	/* axis header */
	.cal-axis {
		height: 1rem;
		margin-bottom: 0.15rem;
	}

	.cal-axis .cal-track {
		height: 1rem;
	}

	.cal-month {
		position: absolute;
		top: 0;
		transform: translateX(-50%);
		font-size: 0.56rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: #77868a;
		white-space: nowrap;
	}

	.cal-later-head {
		font-size: 0.56rem;
		font-weight: 700;
		text-transform: uppercase;
		color: #77868a;
		text-align: center;
		white-space: nowrap;
	}
</style>
