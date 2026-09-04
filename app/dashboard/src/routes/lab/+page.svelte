<script lang="ts">
	/**
	 * /lab — the share bench.
	 *
	 * Three sheets, A B C. Each lights a third of the panel; between them they own
	 * every cell of the ground and not one cell of the word. Stack them and the ground
	 * closes while the letters stay black — and that stack is the picture, cell for
	 * cell, with no threshold pass and no second resolution anywhere in it.
	 *
	 * The bench exists so the claim can be checked rather than taken: switch a sheet
	 * off and watch its third punch straight out of the ground.
	 *
	 * Nothing here consults the stencil to decide what to draw. `union()` is handed
	 * whichever sheets are switched on and counts light; the picture is whatever comes
	 * back. MASK appears in one place only, in the measurements panel, and only to
	 * score a picture that was already drawn.
	 */
	import FingerprintRegister from '$lib/components/fingerprint-register.svelte';
	import {
		build,
		stats,
		ROGUE,
		stencil,
		strips,
		composite,
		seats,
		homes,
		travel,
		layout,
		ALPHABET,
		FACE_LIMITS,
		GRID_LIMITS,
		DEFAULT_FACE,
		DEFAULT_GRID,
		DEFAULT_WORD
	} from '$lib/fingerprint-shares';
	import { cn } from '$lib/utils';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';

	// ── palette ────────────────────────────────────────────────────────────────
	// The three sheets are colour-coded, and the codes have to survive being seen one
	// at a time: three greens would be three greens. Lime, cyan and violet sit far
	// enough apart on the wheel to be named across a room.
	const HUES = ['#b6e04c', '#3fd2ea', '#c98cf6'];
	const NAMES = ['A', 'B', 'C'];
	const ROLES = ['input fingerprint', 'output fingerprint', 'model fingerprint'];
	const SUBS = ['certified inbound', 'certified outbound', 'committed in advance'];
	/**
	 * Words that say what this actually establishes, rather than a general blessing.
	 * All eight fit the default face and grid; the longer ones want a wider grid.
	 * ATTESTED is the precise one — attestation is what the hardware does. AS RUN and
	 * NO SWAP name the claim outright: the output is the one that was run, on the
	 * model that was committed.
	 */
	const PRESETS = [
		'VERIFIED',
		'ATTESTED',
		'AS RUN',
		'NO SWAP',
		'INTACT',
		'SEALED',
		'MATCHED',
		'GENUINE'
	];
	/** the one colour the finished stack is read in */
	const LAMP = '#35e08b';
	/** and the black the letters are left in */
	const VOID = '#05080b';
	/**
	 * A sheet B that was not the one dealt is drawn in fault red where it landed IN
	 * THE BAND, and only in the pile. Two limits, both deliberate:
	 *
	 * Everywhere: in the pile, in its margin, and on its own panel. Strictly this
	 * over-reaches — B alone is statistically indistinguishable either way, and its
	 * margin cells are doing exactly their job — so the bench is showing an answer it
	 * would not have from the strip itself. That is the point of a bench rather than a
	 * demo: colouring only the cells that are individually at fault made a strip that
	 * is wrong as a whole look like a scattering of bad luck.
	 */
	const FAULT = '#f4593f';

	// ── state ──────────────────────────────────────────────────────────────────
	const rand = () =>
		Array.from({ length: 40 }, () => '0123456789abcdef'[(Math.random() * 16) | 0]).join('');

	let req = $state('4f74f5c6b3a19e02d81c7745aa30bb61c4e9f0d2');
	let rsp = $state('a1b93de77c04582fe61099ab3d5c77e0114fa2b8');
	let model = $state('6e6001da2106d4757498752a021df6c2bdc332c6');
	let on = $state([true, true, true]);
	let pass = $state(true);
	let tint = $state<'one' | 'sheet'>('one');
	let showSeq = $state(false);
	let text = $state(DEFAULT_WORD);
	let size = $state(DEFAULT_FACE.size);
	let weight = $state(DEFAULT_FACE.weight);
	let cols = $state(DEFAULT_GRID.cols);
	let rows = $state(DEFAULT_GRID.rows);

	const grid = $derived({ rows, cols });
	const KEEP = new RegExp(`[^${ALPHABET.replace(/[-^\]\\]/g, '\\$&')} ]`, 'g');
	const word = $derived(text.toUpperCase().replace(KEEP, ''));

	/**
	 * The grid is in cells and the panels are in pixels, so each pitch is whatever
	 * makes the one fit the other. A coarse grid gets fat cells and a fine one small
	 * ones; the panel stays the same width either way, which is what keeps the layout
	 * still while a slider is being dragged.
	 */
	const GAP = 1;
	/**
	 * Spare rows on a strip. Every one of them is height on the printed film that
	 * carries no picture, so it stays small: a strip half again as tall as its own
	 * content already looks like a mistake in the hand.
	 */
	let slack = $state(2);
	/**
	 * How much of each margin the SLIDING strip carries, as a percentage; the backing
	 * takes the rest. At 50 the three strips come out near enough the same weight of
	 * ink. At 33 each margin matches the pattern band's own density, so a sliding
	 * strip is uniform top to bottom and does not advertise its band by a density step.
	 */
	let split = $state(50);
	const STRIP_ROWS = $derived(rows + slack);
	/** and the pile is taller again, because nothing is cropped */
	const PILE_ROWS = $derived(rows + 2 * slack);
	const fitPitch = (max: number, cap: number) =>
		Math.max(2, Math.min(cap, Math.floor((max + GAP) / cols)));
	const ST_PITCH = $derived(fitPitch(944, 16));
	const ST_CELL = $derived(ST_PITCH - GAP);
	const ST_W = $derived(cols * ST_PITCH - GAP);
	const ST_H = $derived(PILE_ROWS * ST_PITCH - GAP);
	const SH_PITCH = $derived(fitPitch(734, 10));
	const SH_CELL = $derived(SH_PITCH - GAP);
	const SH_W = $derived(cols * SH_PITCH - GAP);
	// each strip is drawn where it currently sits in the pile, so the panel is the
	// pile's height and the strip moves inside it
	const SH_H = $derived(PILE_ROWS * SH_PITCH - GAP);

	// `metrics` is the face the stencil will ACTUALLY use, which is not always the one
	// the sliders asked for: a stroke cannot be thicker than the counter it has to
	// leave behind, so weight is clamped against the glyph width. Reading the clamped
	// value back is why the panel can say what it drew rather than what it was told.
	const face = $derived(layout(word, { size, weight }, grid));
	const mask = $derived(stencil(word, { size, weight }, grid));
	const N = $derived(rows * cols);
	const wordCells = $derived(mask.reduce((n, v) => n + v, 0));
	const shares = $derived(build(req, rsp, model, pass, mask));

	const sel = $derived([0, 1, 2].filter((i) => on[i]));
	const measured = $derived(stats(shares, mask));

	const count = $derived(sel.length);

	/**
	 * Registration. The three are printed on strips and have to be slid along Y until
	 * their patterns seat on the same row. There is no aperture to hold against film,
	 * so the panel is the whole pile — see the shares module for how the pile divides
	 * into a band and two dealt margins.
	 *
	 * This is not a hiding property and the panel says so: the extents are structural,
	 * a few dozen slides is nothing to search, and every strip carries the word's own
	 * ghost. What it buys is a processing step you can watch, and a physical demo where
	 * three printed films have to be registered before the word comes through.
	 */
	const st = $derived(strips(shares, grid, slack, model, split / 100));
	let shift = $state([0, 0, 0]);
	const view = $derived(composite(st, shift, grid));
	/** where each strip's pattern currently sits in the pile */
	const seat = $derived(seats(st, shift));
	/** registered when the three patterns land on the same row, wherever that is */
	const band = $derived(new Set(sel.map((i) => seat[i])).size === 1 ? seat[sel[0] ?? 0] : slack);
	const registered = $derived(count > 0 && new Set(sel.map((i) => seat[i])).size === 1);

	// measured on the band the patterns are sitting in, plus the fringes above and
	// below it, because the fringes are part of the pile and not a rendering artefact
	const inView = $derived.by(() => {
		let gl = 0,
			gn = 0,
			wl = 0,
			wn = 0,
			fl = 0,
			fn = 0;
		for (let r = 0; r < PILE_ROWS; r++) {
			const inBand = r >= band && r < band + rows;
			for (let c = 0; c < cols; c++) {
				const lit = sel.some((k) => view[r * cols + c] & (1 << k));
				if (!inBand) {
					fn++;
					if (lit) fl++;
					continue;
				}
				if (mask[(r - band) * cols + c]) {
					wn++;
					if (lit) wl++;
				} else {
					gn++;
					if (lit) gl++;
				}
			}
		}
		return {
			ground: gn ? gl / gn : 0,
			word: wn ? wl / wn : 0,
			fringe: fn ? fl / fn : 0
		};
	});

	let raf = 0;
	const still = () =>
		typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

	/** Slide each strip to where the verifier says it goes, staggered so you see three. */
	let working = $state(false);

	/**
	 * The processing step, as something you can watch.
	 *
	 * A straight ease to the answer read as "the answer was always known", which is
	 * the wrong impression: it should look like a search that lands. So the strips
	 * step through the positions available to them first — quantised and quick, one
	 * row at a time, which is what a piece of film actually does under a thumb — and
	 * only then settle onto the registration.
	 *
	 * At small slack there are only a few rows to move over, so the search reads
	 * through cadence rather than distance: a rapid ratchet, then a stop.
	 */
	function runVerifier() {
		cancelAnimationFrame(raf);
		const to = homes(st);
		if (still()) {
			shift = to;
			working = false;
			return;
		}
		const HUNT = 900;
		const SETTLE = 620;
		const STEP = 90; // one position per step: any faster and it blurs
		const from = [...shift];
		const t0 = performance.now();
		working = true;
		let lastStep = -1;
		// captured once, at the moment hunting stops -- easing from a value that is
		// re-read every frame converges geometrically instead of arriving, and can
		// leave the strips a row short of home
		let launch: number[] | null = null;

		const step = (now: number) => {
			const t = now - t0;
			if (t < HUNT) {
				// hunting: each strip walks its own range, offset so the three are never
				// in step with one another
				const n = Math.floor(t / STEP);
				if (n !== lastStep) {
					lastStep = n;
					shift = [0, 1, 2].map((i) => {
						const span = travel(st, i);
						return span ? (from[i] + n + i * 2) % (span + 1) : 0;
					});
				}
				raf = requestAnimationFrame(step);
				return;
			}
			if (!launch) launch = [...shift];
			const k = Math.min(1, (t - HUNT) / SETTLE);
			shift = launch.map((f, i) => {
				const lane = Math.max(0, Math.min(1, (k - i * 0.12) / 0.76));
				const e = 1 - Math.pow(1 - lane, 3);
				return Math.round(f + (to[i] - f) * e);
			});
			// every path either schedules the next frame or finishes; an earlier cut had
			// one that did neither and stalled the strips mid-hunt
			if (k < 1) {
				raf = requestAnimationFrame(step);
			} else {
				shift = to;
				working = false;
			}
		};
		raf = requestAnimationFrame(step);
	}

	function scatter() {
		cancelAnimationFrame(raf);
		working = false;
		// the backing does not move, so only the two sliding strips get thrown
		for (;;) {
			const next = [0, 1, 2].map((i) => Math.floor(Math.random() * (travel(st, i) + 1)));
			if (new Set(seats(st, next)).size > 1) {
				shift = next;
				return;
			}
		}
	}
	const verdict = $derived(
		count < 3
			? `${count} of 3 sheets — the missing third is a hole in the ground`
			: !registered
				? 'out of register — slide A and B onto the backing'
				: pass
					? 'registered — the ground closes, and the word stays black'
					: 'registered, but B was not the strip that was dealt — red is where it landed'
	);

	// ── painting ───────────────────────────────────────────────────────────────
	const hex2rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
	const rgb2hex = (c: number[]) =>
		'#' +
		c
			.map((v) =>
				Math.max(0, Math.min(255, Math.round(v)))
					.toString(16)
					.padStart(2, '0')
			)
			.join('');
	/** t = 0 is all of a, t = 1 is all of b */
	const mix = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);
	const HUE_RGB = HUES.map(hex2rgb);
	const FAULT_RGB = hex2rgb(FAULT);
	const WHITE = [255, 255, 255];
	/**
	 * How far towards white a cell goes once n sheets have lit it. The deal gives each
	 * ground cell to one sheet, so almost every lit cell is n = 1 and the ground reads
	 * as a mosaic of the three. The few brighter ones are the top-up: cells a sheet
	 * lit that another already owned, which is what evens the densities out.
	 */
	const GLOW = [0, 0, 0.45, 0.8];

	function fit(cv: HTMLCanvasElement, w: number, h: number) {
		const dpr = window.devicePixelRatio || 1;
		cv.width = Math.round(w * dpr);
		cv.height = Math.round(h * dpr);
		cv.style.width = `${w}px`;
		cv.style.height = `${h}px`;
		const ctx = cv.getContext('2d');
		ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
		return ctx;
	}

	function paintStack(cv: HTMLCanvasElement | null, which: number[], mode: 'one' | 'sheet') {
		if (!cv) return;
		const ctx = fit(cv, ST_W, ST_H);
		if (!ctx) return;
		ctx.fillStyle = VOID;
		ctx.fillRect(0, 0, ST_W, ST_H);
		for (let r = 0; r < PILE_ROWS; r++) {
			for (let c = 0; c < cols; c++) {
				const i = r * cols + c;
				// what the pile is transmitting right now, not what the strips would say
				// if they were already registered
				const by = which.filter((k) => view[i] & (1 << k));
				if (!by.length) continue;
				// Every cell B lit, wherever it lit it. Its margin cells are doing their
				// job, so this reddens some ink that is not itself at fault — but a strip
				// that was not the one dealt is wrong as a whole, and colouring only part
				// of it made the fault look smaller than it is.
				const rogue = !pass && by.includes(ROGUE);
				if (mode === 'one') {
					ctx.fillStyle = rogue ? FAULT : LAMP;
				} else {
					const src = by.map((k) => (k === ROGUE && rogue ? FAULT_RGB : HUE_RGB[k]));
					const avg = [0, 1, 2].map((ch) => src.reduce((a, c) => a + c[ch], 0) / src.length);
					ctx.fillStyle = rgb2hex(mix(avg, WHITE, GLOW[by.length]));
				}
				ctx.fillRect(c * ST_PITCH, r * ST_PITCH, ST_CELL, ST_CELL);
			}
		}
	}

	/**
	 * One strip, drawn where it currently sits in the pile. The panel is the pile's
	 * full height and the film moves inside it, which is the only thing you can do to a
	 * piece of film.
	 */
	function paintSheet(
		cv: HTMLCanvasElement | null,
		strip: Uint8Array,
		hue: string,
		live: boolean,
		drop: number,
		tall: number
	) {
		if (!cv) return;
		const ctx = fit(cv, SH_W, SH_H);
		if (!ctx) return;
		ctx.fillStyle = VOID;
		ctx.fillRect(0, 0, SH_W, SH_H);
		const dim = ['#0f141b', '#131922'];
		for (let r = 0; r < PILE_ROWS; r++) {
			const sr = r - drop;
			const on = sr >= 0 && sr < tall;
			for (let c = 0; c < cols; c++) {
				const l = on ? strip[sr * cols + c] : 0;
				ctx.globalAlpha = l ? (live ? [0, 0.62, 0.76, 0.88, 1][l] : 0.2) : 1;
				ctx.fillStyle = l ? hue : on ? dim[(r + c) & 1] : '#080b10';
				ctx.fillRect(c * SH_PITCH, r * SH_PITCH, SH_CELL, SH_CELL);
			}
		}
		ctx.globalAlpha = 1;
	}

	let stackCv = $state<HTMLCanvasElement | null>(null);
	let sheetCv = $state<(HTMLCanvasElement | null)[]>([null, null, null]);

	$effect(() => {
		void [view, mask, pass];
		paintStack(stackCv, sel, tint);
	});
	$effect(() => {
		void [mask, st, shift, pass];
		for (let i = 0; i < 3; i++) {
			const hue = i === ROGUE && !pass ? FAULT : HUES[i];
			paintSheet(sheetCv[i], st.cells[i], hue, on[i], shift[i], st.heights[i]);
		}
	});

	// ?a=0&b=1&c=1&outcome=fail&tint=sheet so a state can be linked, or shot headlessly
	onMount(() => {
		const q = new URLSearchParams(location.search);
		['a', 'b', 'c'].forEach((k, i) => {
			const v = q.get(k);
			if (v !== null) on[i] = v !== '0';
		});
		if (q.get('outcome') === 'fail') pass = false;
		const ti = q.get('tint');
		if (ti === 'one' || ti === 'sheet') tint = ti;
		const num = (k: string, set: (v: number) => void) => {
			const v = Number(q.get(k));
			if (q.get(k) !== null && Number.isFinite(v) && v) set(v);
		};
		num('size', (v) => (size = v));
		num('weight', (v) => (weight = v));
		num('cols', (v) => (cols = v));
		num('rows', (v) => (rows = v));
		num('slack', (v) => (slack = v));
		if (q.get('split') !== null) split = Number(q.get('split'));
		const tx = q.get('text');
		if (tx !== null) text = tx;
		const sf = q.get('shift');
		if (sf) {
			const v = sf.split(',').map(Number);
			if (v.length === 3 && v.every(Number.isFinite)) {
				shift = v;
				return;
			}
		}
		// Open out of register and then register: the page arrives showing what three
		// films dropped on a light box look like, and resolves itself once, so the
		// mechanism is the first thing you see rather than something you have to go
		// looking for a button to trigger.
		scatter();
		setTimeout(runVerifier, 700);
		if (q.get('seq') === '1') showSeq = true;
	});

	const fmt = (h: string) => '0x' + h.slice(0, 12).toUpperCase();
	const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
</script>

<svelte:head><title>Share bench</title></svelte:head>

<div class="min-h-svh bg-background text-foreground">
	<header class="border-b border-border">
		<div class="mx-auto flex max-w-[1040px] items-baseline justify-between gap-4 px-6 py-3">
			<span class="font-mono text-sm font-semibold tracking-[0.22em]">INTERLOCK · LAB</span>
			<span class="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
				share bench · no hardware attached
			</span>
		</div>
	</header>

	<main class="mx-auto flex max-w-[1040px] flex-col gap-4 px-6 py-6">
		<!-- ── the stack ────────────────────────────────────────────────────── -->
		<section class="border border-border bg-card">
			<div
				class="flex flex-wrap items-baseline justify-between gap-4 border-b border-border px-4 py-2"
			>
				<span class="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
					The pile · A + B + C, nothing cropped
				</span>
				<div class="flex items-center gap-2">
					<span
						class={cn(
							'font-mono text-[10px] tracking-[0.12em]',
							count === 3 && pass ? 'text-verified' : 'text-muted-foreground/70'
						)}
					>
						{verdict}
					</span>
					{#each [{ v: 'one', l: 'one colour' }, { v: 'sheet', l: 'by sheet' }] as o (o.v)}
						<button
							class={cn(
								'border px-2 py-0.5 font-mono text-[9px] tracking-[0.12em] uppercase transition-colors',
								tint === o.v
									? 'border-foreground text-foreground'
									: 'border-border text-muted-foreground hover:text-foreground'
							)}
							onclick={() => (tint = o.v as 'one' | 'sheet')}>{o.l}</button
						>
					{/each}
				</div>
			</div>
			<div class="flex justify-center overflow-x-auto p-4">
				<canvas bind:this={stackCv} class="shrink-0" aria-hidden="true"></canvas>
			</div>
			<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border px-4 py-2.5">
				<span class="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
					Registration
				</span>
				{#each NAMES as n, i (n)}
					<label class="flex items-center gap-2 font-mono text-[10px]" style="color:{HUES[i]}">
						{n}
						<input
							type="range"
							min="0"
							max={Math.max(1, travel(st, i))}
							step="1"
							disabled={travel(st, i) === 0}
							bind:value={shift[i]}
							class="w-28 disabled:opacity-30"
							aria-label="slide strip {n} along Y"
						/>
						<span class="tabular w-10 text-foreground">
							{travel(st, i) === 0 ? 'fixed' : shift[i]}
						</span>
					</label>
				{/each}
				<button
					class="border border-signal bg-signal/10 px-3 py-1 font-mono text-[10px] tracking-[0.14em] text-signal uppercase transition-colors hover:bg-signal/20"
					onclick={runVerifier}>{working ? 'Registering…' : 'Run the verifier'}</button
				>
				<button
					class="border border-border px-3 py-1 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase transition-colors hover:text-foreground"
					onclick={scatter}>Scatter</button
				>
				<span
					class={cn(
						'font-mono text-[10px] tracking-[0.12em]',
						registered ? 'text-verified' : 'text-muted-foreground/70'
					)}
				>
					{registered ? 'in register' : `${(slack + 1) ** 3} slides, few of them register`}
				</span>
			</div>
		</section>

		<!-- ── the sheets ───────────────────────────────────────────────────── -->
		<div class="flex items-baseline justify-between gap-4 px-1">
			<span class="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
				The strips · A and B are {STRIP_ROWS} rows and slide; C backs the pile at {PILE_ROWS}
			</span>
			<span class="font-mono text-[10px] tracking-[0.12em] text-muted-foreground/70">
				each drawn where it currently sits · A tops the frame, B foots it, C spans
			</span>
		</div>
		<div class="flex flex-col gap-2">
			{#each NAMES as name, i (name)}
				<div
					class={cn(
						'flex items-center gap-4 border bg-card p-3 transition-colors',
						on[i] ? 'border-border' : 'border-border/40'
					)}
				>
					<div class="flex w-[180px] shrink-0 flex-col gap-1.5">
						<div class="flex items-center gap-2">
							<span
								class="size-2.5 shrink-0 transition-opacity"
								style="background:{HUES[i]};opacity:{on[i] ? 1 : 0.3}"
							></span>
							<span class="font-mono text-[15px] font-semibold" style="color:{HUES[i]}">
								{name}
							</span>
							<span class="font-mono text-[9px] tracking-[0.12em] text-muted-foreground uppercase">
								{ROLES[i]}
							</span>
						</div>
						<span
							class="tabular font-mono text-[12px] transition-opacity"
							style="color:{HUES[i]};opacity:{on[i] ? 1 : 0.4}"
						>
							{fmt([req, rsp, model][i])}
						</span>
						<span class="font-mono text-[9px] tracking-[0.12em] text-muted-foreground/60 uppercase">
							{SUBS[i]} · {measured.lit[i]} cells, {pct(measured.density[i])} of panel
						</span>
						<button
							class={cn(
								'mt-0.5 w-fit border px-3 py-1 font-mono text-[10px] tracking-[0.16em] uppercase transition-colors',
								on[i]
									? 'border-current bg-current/10'
									: 'border-border text-muted-foreground hover:text-foreground'
							)}
							style={on[i] ? `color:${HUES[i]}` : ''}
							aria-pressed={on[i]}
							onclick={() => (on[i] = !on[i])}
						>
							{on[i] ? 'on' : 'off'}
						</button>
					</div>
					<div class="overflow-x-auto">
						<canvas bind:this={sheetCv[i]} class="shrink-0" aria-hidden="true"></canvas>
					</div>
				</div>
			{/each}
		</div>

		<div class="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
			<!-- ── controls ─────────────────────────────────────────────────── -->
			<div class="flex flex-col gap-4 border border-border bg-card p-4">
				<div class="flex flex-wrap items-center gap-2">
					<span
						class="w-20 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase"
					>
						Sheets
					</span>
					<button
						class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
						onclick={() => (on = [true, true, true])}>All on</button
					>
					{#each NAMES as n, i (n)}
						<button
							class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
							onclick={() => (on = on.map((_, k) => k === i))}>{n} only</button
						>
					{/each}
					<button
						class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
						onclick={() => (on = on.map((_, k) => k !== 2))}>A + B</button
					>
				</div>

				<div class="flex flex-wrap items-center gap-2">
					<span
						class="w-20 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase"
					>
						Sheet B
					</span>
					{#each [{ v: true, l: 'was dealt' }, { v: false, l: 'was not' }] as o (o.l)}
						<button
							class={cn(
								'border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] uppercase transition-colors',
								pass === o.v
									? 'border-foreground text-foreground'
									: 'border-border text-muted-foreground hover:text-foreground'
							)}
							onclick={() => (pass = o.v)}>{o.l}</button
						>
					{/each}
					<span class="font-mono text-[10px] text-muted-foreground/70">
						A and C are byte-identical either way — only B changes
					</span>
				</div>

				<div class="flex flex-wrap items-center gap-2">
					<span
						class="w-20 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase"
					>
						Word
					</span>
					<input bind:value={text} class="lab-hex lab-word" spellcheck="false" maxlength="16" />
					<span class="font-mono text-[10px] whitespace-nowrap text-muted-foreground/70">
						A–Z 0–9
					</span>
				</div>
				<div class="flex flex-wrap items-center gap-1.5 pl-22">
					{#each PRESETS as w (w)}
						<button
							class={cn(
								'border px-2 py-1 font-mono text-[10px] tracking-[0.1em] uppercase transition-colors',
								word === w
									? 'border-foreground text-foreground'
									: 'border-border text-muted-foreground hover:text-foreground'
							)}
							onclick={() => (text = w)}>{w}</button
						>
					{/each}
				</div>

				<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
					<span
						class="w-20 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase"
					>
						Face
					</span>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						SIZE
						<input
							type="range"
							min={FACE_LIMITS.size[0]}
							max={FACE_LIMITS.size[1]}
							step="1"
							bind:value={size}
							class="w-36"
							aria-label="letter height in cells"
						/>
						<span class="tabular w-10 text-foreground">{face.h}</span>
					</label>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						WEIGHT
						<input
							type="range"
							min={FACE_LIMITS.weight[0]}
							max={FACE_LIMITS.weight[1]}
							step="1"
							bind:value={weight}
							class="w-24"
							aria-label="stroke thickness in cells"
						/>
						<span class="tabular w-10 text-foreground">{face.t}</span>
					</label>
					<span class="font-mono text-[10px] text-muted-foreground/70">
						glyph {face.w}×{face.h}, gap {face.gap}
					</span>
				</div>

				<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
					<span
						class="w-20 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase"
					>
						Grid
					</span>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						WIDTH
						<input
							type="range"
							min={GRID_LIMITS.cols[0]}
							max={GRID_LIMITS.cols[1]}
							step="1"
							bind:value={cols}
							class="w-36"
							aria-label="grid width in cells"
						/>
						<span class="tabular w-10 text-foreground">{cols}</span>
					</label>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						HEIGHT
						<input
							type="range"
							min={GRID_LIMITS.rows[0]}
							max={GRID_LIMITS.rows[1]}
							step="1"
							bind:value={rows}
							class="w-24"
							aria-label="grid height in cells"
						/>
						<span class="tabular w-10 text-foreground">{rows}</span>
					</label>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						SLACK
						<input
							type="range"
							min="2"
							max="10"
							step="1"
							bind:value={slack}
							class="w-24"
							aria-label="spare rows on each strip"
						/>
						<span class="tabular w-10 text-foreground">{slack}</span>
					</label>
					<label class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
						SPLIT
						<input
							type="range"
							min="10"
							max="90"
							step="1"
							bind:value={split}
							class="w-24"
							aria-label="share of each margin carried by the sliding strip"
						/>
						<span class="tabular w-14 text-foreground">{split}/{100 - split}</span>
					</label>
					<span class="font-mono text-[10px] text-muted-foreground/70">
						{N} cells at {ST_CELL}px
					</span>
				</div>

				{#if !face.fits}
					<p class="font-mono text-[10px] leading-relaxed text-fault">
						The word wants {face.span} × {face.h} cells and the grid is {cols} × {rows}. It is drawn
						from the left rather than cropped down the middle — widen the grid, or take the size
						down.
					</p>
				{/if}

				<div class="flex flex-col gap-2">
					{#each [{ k: 'a', label: 'A · input' }, { k: 'b', label: 'B · output' }, { k: 'c', label: 'C · model' }] as f, i (f.k)}
						<div class="flex items-center gap-2">
							<span
								class="w-20 shrink-0 font-mono text-[10px] tracking-[0.14em] uppercase"
								style="color:{HUES[i]}"
							>
								{f.label}
							</span>
							{#if f.k === 'a'}
								<input bind:value={req} class="lab-hex" spellcheck="false" />
							{:else if f.k === 'b'}
								<input bind:value={rsp} class="lab-hex" spellcheck="false" />
							{:else}
								<input bind:value={model} class="lab-hex" spellcheck="false" />
							{/if}
						</div>
					{/each}
					<div class="flex gap-2 pl-22">
						<button
							class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
							onclick={() => {
								req = rand();
								rsp = rand();
								model = rand();
							}}>Randomise all</button
						>
						<button
							class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
							onclick={() => (showSeq = !showSeq)}
							aria-pressed={showSeq}>{showSeq ? 'Hide' : 'Show'} sequence</button
						>
						<a
							class="border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors hover:text-foreground"
							href="{resolve('/lab/print')}?{new URLSearchParams({
								text,
								size: String(size),
								weight: String(weight),
								cols: String(cols),
								rows: String(rows),
								slack: String(slack),
								split: String(split),
								req,
								rsp,
								model
							})}"
							target="_blank"
							rel="noopener">Print the film</a
						>
					</div>
				</div>
			</div>

			<!-- ── measurements, not claims ─────────────────────────────────── -->
			<div class="min-w-[280px] border border-border bg-card p-4 font-mono text-[11px]">
				<div class="mb-3 text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
					Measured on the sheets above
				</div>
				<dl class="tabular flex flex-col gap-1.5">
					{#each NAMES as n, i (n)}
						<div class="flex justify-between gap-6">
							<dt style="color:{HUES[i]}">sheet {n} of ground</dt>
							<dd class={measured.ofGround[i] > 0.32 ? 'text-verified' : 'text-caution'}>
								{pct(measured.ofGround[i])}
							</dd>
						</div>
					{/each}
					<div class="flex justify-between gap-6">
						<dt class="text-muted-foreground">…of which inside the word</dt>
						<dd class={measured.ghost.every((g) => g === 0) ? 'text-verified' : 'text-fault'}>
							{pct(Math.max(...measured.ghost))}
						</dd>
					</div>
					<div class="my-1 h-px bg-border"></div>
					<div class="text-[10px] tracking-[0.14em] text-muted-foreground/70 uppercase">
						in the band, {count} strip{count === 1 ? '' : 's'} on
					</div>
					<div class="flex justify-between gap-6">
						<dt class="text-muted-foreground">ground lit</dt>
						<dd class={inView.ground === 1 ? 'text-verified' : 'text-caution'}>
							{pct(inView.ground)}
						</dd>
					</div>
					<div class="flex justify-between gap-6">
						<dt class="text-muted-foreground">word lit</dt>
						<dd class={inView.word === 0 ? 'text-verified' : 'text-fault'}>{pct(inView.word)}</dd>
					</div>
					<div class="flex justify-between gap-6">
						<dt class="text-muted-foreground">fringe lit</dt>
						<dd class="text-muted-foreground">{pct(inView.fringe)}</dd>
					</div>
					<div class="flex justify-between gap-6">
						<dt class="text-muted-foreground">patterns seated at</dt>
						<dd class={registered ? 'text-verified' : 'text-caution'}>{seat.join(' / ')}</dd>
					</div>
					<div class="my-1 h-px bg-border"></div>
					<div class="flex justify-between gap-6 text-muted-foreground/70">
						<dt>strip / pile</dt>
						<dd>{STRIP_ROWS} / {PILE_ROWS} rows</dd>
					</div>
					<div class="flex justify-between gap-6 text-muted-foreground/70">
						<dt>grid</dt>
						<dd>{rows} × {cols} = {N} · {ST_CELL}px cells</dd>
					</div>
					<div class="flex justify-between gap-6 text-muted-foreground/70">
						<dt>held per sheet</dt>
						<dd>{measured.lit.join(' / ')}</dd>
					</div>
					<div class="flex justify-between gap-6 text-muted-foreground/70">
						<dt>word</dt>
						<dd>{wordCells} cells ({pct(wordCells / N)})</dd>
					</div>
					<div class="flex justify-between gap-6 text-muted-foreground/70">
						<dt>stroke</dt>
						<dd>{face.t} cell{face.t === 1 ? '' : 's'}</dd>
					</div>
				</dl>
				<p class="mt-3 leading-relaxed text-muted-foreground/70">
					The ground is dealt out a cell at a time, so all three sheets are load bearing: switch one
					off and its third goes black where it stood, which is the holed ground above rather than a
					blank panel.
				</p>
				<p class="mt-2 leading-relaxed text-muted-foreground/70">
					Nothing here is cropped. There is no aperture you could hold against three pieces of film
					— you stack them and you move them — so the panel is the whole pile. It divides in three:
					a top margin dealt between A and the backing, the band where all three carry the picture,
					and a bottom margin dealt between B and the backing. Each margin is a partition exactly
					like the ground is, so the two strips that share it cover it completely and neither has to
					be a solid block.
				</p>
				<p class="mt-2 leading-relaxed text-muted-foreground/70">
					The extents are structural, so nothing hides where a pattern sits: A's is always under its
					margin and B's always over its own, and registering is aligning three edges rather than
					finding three positions. SPLIT is the one dial left on that — at 33 each margin matches
					the band's own density and a sliding strip is uniform top to bottom, at 50 the three
					strips weigh about the same in ink. The hiding was never worth much anyway: every strip
					carries the word's ghost regardless.
				</p>
				<p class="mt-2 leading-relaxed text-caution/80">
					The word being <em>completely</em> black in the stack means no sheet lit a single cell of it
					— so each sheet has a hole shaped like the word, and you can find it if you look. That is forced,
					not sloppy: hiding the hole needs subcell expansion, and expansion is what puts a third of the
					light back into the letters.
				</p>
			</div>
		</div>

		{#if showSeq}
			<section class="flex flex-col gap-2">
				<span class="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
					Sequence preview — the register as the demo page drives it
				</span>
				<!-- The register sizes its pile to the height it is GIVEN, which on the demo
				     page is whatever the flex column has left. Here there is no such column,
				     so give it one explicitly rather than letting it collapse to nothing. -->
				<div class="flex h-[380px] flex-col">
					<FingerprintRegister
						req={on[0] ? req : null}
						rsp={on[1] ? rsp : null}
						model={on[2] ? model : null}
						stage={count === 3 ? (pass ? 'pass' : 'fail') : 'dealing'}
						{word}
						face={{ size, weight }}
						{grid}
						{slack}
						split={split / 100}
					/>
				</div>
			</section>
		{/if}
	</main>
</div>

<style>
	.lab-hex {
		flex: 1;
		min-width: 0;
		border: 1px solid var(--border);
		background: var(--background);
		padding: 0.35rem 0.6rem;
		font-family: var(--font-mono);
		font-size: 11px;
		font-variant-numeric: tabular-nums;
		color: var(--foreground);
	}
	.lab-word {
		text-transform: uppercase;
		letter-spacing: 0.14em;
	}
	.lab-hex:focus {
		outline: 1px solid var(--ring);
		outline-offset: -1px;
	}
</style>
