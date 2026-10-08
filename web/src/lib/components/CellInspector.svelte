<script lang="ts">
	import type { BlendForecastPoint } from '$lib/api';
	import {
		rampColor,
		argmax,
		binDateLabel,
		fmtProb,
		fmtDate,
		isoToDay,
		dayToIso,
		consensusOnsetDay,
		laterStartDay,
		monthLabel,
		probScaleMax,
		type OnsetBins
	} from '$lib/onset';
	import {
		ONSET_ESTIMATE_EXPLANATION,
		consensusBand,
		forecastOutlook,
		splitAtOnset
	} from '$lib/onset-outlook';
	import { formatCoord } from '$lib/geo';

	type Props = {
		point: BlendForecastPoint;
		bins: OnsetBins;
		issueDates: string[];
		onsetName: string;
		selectedDate: string;
		soonestColor: 'yellow' | 'purple';
		onClose: () => void;
	};
	let { point, bins, issueDates, onsetName, selectedDate, soonestColor, onClose }: Props = $props();

	// Matches the map toggle: the vivid end marks the highest probability.
	const reversed = $derived(soonestColor === 'purple');

	// How far the open-ended bin's arrow reaches past the horizon.
	const TAIL_DAYS = 6;

	const boundedBins = $derived(Array.from({ length: bins.laterIndex }, (_, i) => i));
	// Daily probabilities are small, so the dated bins scale to this cell's season
	// peak; the open-ended bin keeps the full 0–100% (see probScaleMax).
	const scaleMax = $derived(probScaleMax(bins, point.probs));
	const laterScaleMax = $derived(probScaleMax(bins, point.probs, bins.laterIndex));

	function binScale(idx: number): number {
		return idx === bins.laterIndex ? laterScaleMax : scaleMax;
	}

	function binColor(row: number[], idx: number): string {
		return rampColor((row[idx] ?? 0) / binScale(idx), reversed);
	}

	// Most-likely bin for a forecast row, or -1 if it carries no mass.
	function mostLikely(row: number[]): number {
		return row.some((p) => p > 0) ? argmax(row) : -1;
	}

	const consensusDay = $derived(consensusOnsetDay(bins, issueDates, point.probs));
	const band = $derived(consensusBand(consensusDay));
	const split = $derived(splitAtOnset(issueDates, consensusDay));

	let showAfterOnset = $state(false);
	const visibleRows = $derived(showAfterOnset ? [...split.before, ...split.after] : split.before);

	const selectedIndex = $derived(issueDates.indexOf(selectedDate));
	const selectedRow = $derived(point.probs[selectedIndex] ?? []);
	const outlook = $derived(
		selectedIndex < 0 ? null : forecastOutlook(bins, selectedDate, selectedRow, consensusDay)
	);

	const bandLabel = $derived(
		band ? `${fmtDate(dayToIso(band.start))} – ${fmtDate(dayToIso(band.end))}` : ''
	);

	// Season date axis over the shown forecasts: the earliest one's first bin →
	// the latest one's open-ended arrow.
	const axis = $derived.by(() => {
		const rows = visibleRows.length ? visibleRows : issueDates.map((_, i) => i);
		const days = rows.map((i) => isoToDay(issueDates[i]));
		const start = bins.dayRange(dayToIso(Math.min(...days)), 0).start;
		const end = laterStartDay(dayToIso(Math.max(...days))) + TAIL_DAYS;
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
</script>

<aside class="inspector">
	<header class="ins-header">
		<div>
			<span class="ins-title">Onset outlook</span>
			<span class="ins-coords"
				>{formatCoord(point.lat, 'N', 'S')} {formatCoord(point.lon, 'E', 'W')}</span
			>
		</div>
		<button class="ins-close" aria-label="Close" onclick={onClose}>×</button>
	</header>

	{#if outlook}
		<section class="summary" aria-label="Selected forecast">
			<p class="summary-issued">Forecast issued {fmtDate(selectedDate)}</p>
			{#if outlook.kind === 'after-onset'}
				<p class="summary-headline">
					{onsetName} likely began around <strong>{bandLabel}</strong>.
				</p>
				<p class="summary-note">
					This forecast was issued after that, so it doesn't describe a new onset.
				</p>
			{:else if outlook.kind === 'forecast'}
				<p class="summary-headline">
					{onsetName} most likely
					<strong
						>{outlook.mostLikely === bins.laterIndex
							? `more than 4 weeks away (${outlook.when})`
							: outlook.when}</strong
					>
					· {fmtProb(outlook.probability)}
				</p>
				<div class="summary-bars" class:daily={bins.resolution === 'daily'}>
					{#each bins.labels as label, i (label)}
						{@const p = selectedRow[i] ?? 0}
						<div
							class="summary-bar"
							class:later={i === bins.laterIndex}
							title="{label} · {binDateLabel(bins, selectedDate, i)}: {fmtProb(p)}"
						>
							{#if bins.resolution === 'weekly'}<span class="summary-pct">{fmtProb(p)}</span>{/if}
							<span
								class="summary-fill"
								class:best={i === outlook.mostLikely}
								style="height: {Math.max(4, (p / binScale(i)) * 100)}%; background: {binColor(
									selectedRow,
									i
								)}"
							></span>
						</div>
					{/each}
				</div>
				<div class="summary-axis" class:daily={bins.resolution === 'daily'}>
					{#if bins.resolution === 'weekly'}
						{#each bins.labels as label, i (label)}
							<span>{binDateLabel(bins, selectedDate, i)}</span>
						{/each}
					{:else}
						<span>{binDateLabel(bins, selectedDate, 0)}</span>
						<span>{binDateLabel(bins, selectedDate, bins.laterIndex - 1)}</span>
						<span class="summary-axis-later">{bins.labels[bins.laterIndex]}</span>
					{/if}
				</div>
			{:else}
				<p class="summary-headline">This forecast placed no chance of onset here.</p>
			{/if}
		</section>
	{/if}

	<section class="history" aria-label="How the forecast evolved">
		<p class="history-title">How the forecast evolved</p>
		<p class="ins-hint">
			Each row is one forecast, earliest at top. Its blocks sit on the dates it covers — toward
			{soonestColor} = more likely, ringed = most likely. The arrow is the chance onset comes more than
			4 weeks after the forecast was issued.
			{#if band}The shaded band is where the forecasts agree: {bandLabel}.{/if}
		</p>

		<div class="cal">
			<div class="cal-row cal-titles">
				<span class="cal-date cal-axistitle">Issued ↓</span>
				<span class="cal-axistitle">Predicted onset date →</span>
			</div>
			<div class="cal-row cal-axis">
				<span class="cal-date"></span>
				<div class="cal-track">
					{#each axis.ticks as t (t.label + t.day)}
						<span class="cal-month" style="left: {leftPct(t.day)}%">{t.label}</span>
					{/each}
				</div>
			</div>

			{#each visibleRows as di (issueDates[di])}
				{@const d = issueDates[di]}
				{@const row = point.probs[di] ?? []}
				{@const afterOnset = split.after.includes(di)}
				{@const best = afterOnset ? -1 : mostLikely(row)}
				<div class="cal-row" class:after-onset={afterOnset}>
					<span class="cal-date" class:current={d === selectedDate}>{fmtDate(d)}</span>
					<div class="cal-track">
						{#if band}
							<span
								class="cal-band"
								style="left: {leftPct(band.start)}%; width: {widthPct(band.end - band.start)}%"
							></span>
						{/if}
						{#each boundedBins as w (w)}
							<span
								class="cal-seg"
								class:best={w === best}
								style="left: {leftPct(bins.dayRange(d, w).start)}%; width: {widthPct(
									bins.binDays
								)}%; background: {binColor(row, w)}"
								title="{bins.labels[w]} · {binDateLabel(bins, d, w)}: {fmtProb(row[w] ?? 0)}"
							></span>
						{/each}
						<span
							class="cal-tail"
							class:best={best === bins.laterIndex}
							style="left: {leftPct(laterStartDay(d))}%; width: {widthPct(
								TAIL_DAYS
							)}%; background: {binColor(row, bins.laterIndex)}"
							title="{bins.labels[bins.laterIndex]} · onset {binDateLabel(
								bins,
								d,
								bins.laterIndex
							)}: {fmtProb(row[bins.laterIndex] ?? 0)}"
						></span>
					</div>
				</div>
			{/each}
		</div>

		{#if split.after.length > 0}
			<div class="fold">
				<span>
					{split.after.length} forecast{split.after.length === 1 ? '' : 's'} issued after onset likely
					began ({fmtDate(issueDates[split.after[0]])} – {fmtDate(
						issueDates[split.after[split.after.length - 1]]
					)})
				</span>
				<button type="button" onclick={() => (showAfterOnset = !showAfterOnset)}>
					{showAfterOnset ? 'Hide' : 'Show'}
				</button>
			</div>
		{/if}

		{#if band}
			<details class="estimate-note">
				<summary>How the onset date is estimated</summary>
				<p>{ONSET_ESTIMATE_EXPLANATION}</p>
			</details>
		{/if}
	</section>
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
		gap: 0.8rem;
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

	.ins-title,
	.history-title {
		display: block;
		margin: 0;
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

	.summary {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding-bottom: 0.8rem;
		border-bottom: 1px solid rgba(31, 43, 52, 0.12);
	}

	.summary-issued {
		margin: 0;
		font-size: 0.7rem;
		font-weight: 700;
		color: #56656b;
	}

	.summary-headline {
		margin: 0;
		font-size: 0.82rem;
		line-height: 1.4;
		color: #46555c;
	}

	.summary-headline strong {
		color: #18252b;
		font-weight: 800;
	}

	.summary-note {
		margin: 0;
		font-size: 0.68rem;
		line-height: 1.4;
		color: #627174;
	}

	.summary-bars {
		display: flex;
		align-items: flex-end;
		gap: 0.3rem;
		height: 4.5rem;
		margin-top: 0.2rem;
	}

	.summary-bars.daily {
		gap: 1px;
	}

	.summary-bar {
		flex: 1;
		display: flex;
		flex-direction: column;
		justify-content: flex-end;
		align-items: center;
		gap: 0.15rem;
		height: 100%;
		min-width: 0;
	}

	.summary-bars.daily .summary-bar.later {
		flex: 4;
		margin-left: 0.3rem;
	}

	.summary-pct {
		font-size: 0.6rem;
		font-weight: 700;
		color: #56656b;
		font-variant-numeric: tabular-nums;
	}

	.summary-fill {
		display: block;
		width: 100%;
		border-radius: 0.15rem 0.15rem 0 0;
		box-shadow: inset 0 0 0 1px rgba(31, 43, 52, 0.14);
	}

	.summary-fill.best {
		box-shadow: inset 0 0 0 1.5px rgba(24, 37, 43, 0.85);
	}

	.summary-axis {
		display: flex;
		gap: 0.3rem;
		font-size: 0.56rem;
		color: #77868a;
		text-align: center;
	}

	.summary-axis span {
		flex: 1;
		min-width: 0;
	}

	.summary-axis.daily span {
		flex: none;
	}

	.summary-axis.daily .summary-axis-later {
		margin-left: auto;
	}

	.history {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
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
		grid-template-columns: 3rem 1fr;
		column-gap: 0.35rem;
		align-items: center;
	}

	.cal-row.after-onset {
		filter: grayscale(1);
		opacity: 0.5;
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

	.cal-seg,
	.cal-tail {
		position: absolute;
		top: 0;
		height: 100%;
		opacity: 0.62;
	}

	.cal-seg {
		border-radius: 0.15rem;
		box-shadow: inset 0 0 0 1px rgba(31, 43, 52, 0.14);
	}

	/* Open-ended: an arrow pointing past the horizon rather than one more block. */
	.cal-tail {
		clip-path: polygon(0 0, 65% 0, 100% 50%, 65% 100%, 0 100%);
	}

	.cal-seg.best {
		opacity: 1;
		box-shadow: inset 0 0 0 1.5px rgba(24, 37, 43, 0.85);
	}

	.cal-tail.best {
		opacity: 1;
	}

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

	.fold {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.45rem 0.6rem;
		border-radius: 0.35rem;
		background: rgba(31, 43, 52, 0.06);
		font-size: 0.65rem;
		color: #46555c;
	}

	.estimate-note {
		font-size: 0.63rem;
		line-height: 1.4;
		color: #627174;
	}

	.estimate-note summary {
		cursor: pointer;
		font-weight: 700;
		color: #46555c;
	}

	.estimate-note p {
		margin: 0.3rem 0 0;
	}

	.fold button {
		flex: none;
		padding: 0.15rem 0.6rem;
		border: 1px solid rgba(31, 43, 52, 0.18);
		border-radius: 0.3rem;
		background: rgba(255, 255, 255, 0.72);
		color: #223138;
		font-size: 0.65rem;
		cursor: pointer;
	}
</style>
