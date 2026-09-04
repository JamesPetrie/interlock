<script lang="ts" module>
	/**
	 * idle      nothing on the wire yet
	 * dealing   the strips exist and are lying out of register
	 * searching the proof is in flight — the verifier is hunting for the seat
	 * pass/fail the verdict has landed and the strips settle onto it
	 */
	export type Stage = 'idle' | 'dealing' | 'searching' | 'pass' | 'fail';
</script>

<script lang="ts">
	/**
	 * Fingerprint register — the /lab bench, wired to the live run.
	 *
	 * Three digests come off a run: the certifier's fingerprint of the request, its
	 * fingerprint of the response, and the commitment to the model weights. Each is
	 * printed as a strip of film. Between them the three own every cell of the ground
	 * and not one cell of the word, so a registered pile closes the ground completely
	 * and the letters stay black.
	 *
	 * The strips are different heights and none of them is cropped, so they have to be
	 * SLID along Y until their patterns seat on the same rows — see $lib/fingerprint-
	 * shares for how the pile divides into a band and two dealt margins. That slide is
	 * the animation: the proof running is the strips hunting for their seat, and the
	 * verdict landing is them settling onto it.
	 *
	 * C is the backing. It is the model commitment, it is full height, and it does not
	 * move — the thing committed in advance is the thing the other two register onto.
	 * B is the output fingerprint, and it is the one the verdict decides: a response
	 * the certifier never fingerprinted is a strip of the right density that no longer
	 * fits the deal, and it is drawn in fault red.
	 *
	 * Nothing below this comment consults the stencil. `composite()` is handed the
	 * strips at their current slide and reports which of them lit each cell; the
	 * picture is whatever comes back. Drop a strip and its third punches straight out
	 * of the ground — that is the only thing holding the word up.
	 */
	import { untrack } from 'svelte';
	import {
		build,
		composite,
		homes,
		ROGUE,
		seats,
		stencil,
		strips,
		travel,
		DEFAULT_FACE,
		DEFAULT_GRID,
		DEFAULT_WORD,
		type Face,
		type Grid
	} from '$lib/fingerprint-shares';
	import { cn } from '$lib/utils';

	let {
		req = null,
		rsp = null,
		model = null,
		stage = 'idle',
		word = DEFAULT_WORD,
		face = DEFAULT_FACE,
		grid = DEFAULT_GRID,
		slack = 2,
		split = 0.5,
		embed = false
	}: {
		/** the certifier's fingerprint of the request — strip A */
		req?: string | null;
		/** the certifier's fingerprint of the response — strip B */
		rsp?: string | null;
		/** the commitment to the weights — strip C, the backing */
		model?: string | null;
		stage?: Stage;
		/** the word the three strips add up to */
		word?: string;
		/** letter size and stroke weight, both in cells */
		face?: Face;
		/** the picture band in cells; the pixel pitch is derived to fit the column */
		grid?: Grid;
		/** spare rows on a sliding strip, and the depth of each margin */
		slack?: number;
		/** how much of each margin the sliding strip carries; the backing takes the rest */
		split?: number;
		/**
		 * Bare-pile mode for hosts that provide their own chrome: no section frame,
		 * no header, no per-strip previews -- just the measured pile filling its
		 * parent. The lid embeds this inside the GPU-cluster box; /lab keeps the
		 * full bench.
		 */
		embed?: boolean;
	} = $props();

	// ── the three films ────────────────────────────────────────────────────────
	// Lime, cyan and violet: the codes have to survive being seen one at a time
	// while they slide past each other, and three greens would be three greens.
	// Same three as the bench, so /lab and the demo are talking about one picture.
	const HUES = ['#b6e04c', '#3fd2ea', '#c98cf6'];
	const NAMES = ['A', 'B', 'C'];
	// Named for the audience, not for the protocol. Someone seeing this for the first
	// time needs to know which of the three things on the wire each sheet came from;
	// "certified inbound" tells them nothing they can act on.
	const ROLES = ['THE QUESTION', 'THE ANSWER', 'THE MODEL'];
	const SUBS = [
		'fingerprinted on the way in',
		'fingerprinted on the way out',
		'fixed before anything ran'
	];
	/** the one colour a registered pile is read in */
	const LAMP = '#35e08b';
	/** and the black the letters are left in */
	const VOID = '#05080b';
	const FAULT = '#f4593f';

	const pass = $derived(stage !== 'fail');
	const mask = $derived(stencil(word, face, grid));
	const sh = $derived(build(req ?? '', rsp ?? '', model ?? '', pass, mask));
	const st = $derived(strips(sh, grid, slack, model ?? '', split));

	/**
	 * Which strips are on the table. A and C both arrive with the request — the deal
	 * is seeded by the request digest, so C cannot be drawn before it exists even
	 * though the commitment it carries was made long beforehand. B arrives when the
	 * response is certified on the way back.
	 */
	const shown = $derived([!!req, !!rsp, !!req]);
	const sel = $derived([0, 1, 2].filter((i) => shown[i]));
	/** the same pile with the strips that have not arrived blanked, geometry intact */
	const table = $derived({
		...st,
		cells: st.cells.map((c, i) => (shown[i] ? c : new Uint8Array(c.length)))
	});

	// ── geometry ───────────────────────────────────────────────────────────────
	// The grid is in cells and the panel is in pixels, so the pitch is whatever makes
	// the one fit the other, measured off the column rather than assumed: this sits
	// under a scope that is already as wide as the page allows.
	const GAP = 1;
	/**
	 * The box the pile may use. Both dimensions are measured rather than assumed,
	 * because this has to hold on a panel in the lid of a case as well as on a
	 * projector: the pitch is whatever makes the grid fit the TIGHTER of the two.
	 * The canvas is positioned absolutely inside the measured box, so sizing it
	 * cannot feed back into the measurement.
	 */
	let boxW = $state(0);
	let boxH = $state(0);
	const PAD = 12;
	const PITCH = $derived(
		Math.max(
			3,
			Math.min(
				22,
				Math.floor((Math.max(320, boxW || 1152) - PAD + GAP) / grid.cols),
				Math.floor((Math.max(90, boxH || 260) - PAD + GAP) / st.pile)
			)
		)
	);
	const CELL = $derived(PITCH - GAP);
	const W = $derived(grid.cols * PITCH - GAP);
	const H = $derived(st.pile * PITCH - GAP);
	/**
	 * The three previews sit side by side under the pile, so each gets a third of the
	 * width. Capped hard and low, and driven by WIDTH ONLY: the pile's own pitch is
	 * set by the height left over after this row, so sizing this row off that height
	 * would be a loop -- fatter previews, shorter pile, thinner previews, and so on.
	 * They are the secondary graphic; the pile gets the room.
	 */
	const SP = $derived(
		Math.max(2, Math.min(4, Math.floor(((boxW || 1152) / 3 - 16 + GAP) / grid.cols)))
	);
	const SW = $derived(grid.cols * SP - GAP);
	const SH = $derived(st.pile * SP - GAP);

	// ── registration ───────────────────────────────────────────────────────────
	let shift = $state([0, 0, 0]);
	const view = $derived(composite(table, shift, grid));
	const seat = $derived(seats(st, shift));
	/** registered when every strip on the table seats its pattern on the same row */
	const registered = $derived(sel.length === 3 && new Set(sel.map((i) => seat[i])).size === 1);

	/** 0 is three coloured films, 1 is the single-colour read of a registered pile */
	let blend = $state(0);
	let raf = 0;
	let rafB = 0;
	const frozen = () =>
		typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

	/** Somewhere out of register. Rejection-sampled, because most slides are not. */
	function scattered() {
		for (let n = 0; n < 64; n++) {
			const next = [0, 1, 2].map((i) => Math.floor(Math.random() * (travel(st, i) + 1)));
			if (new Set(seats(st, next)).size > 1) return next;
		}
		return [travel(st, 0), 0, 0];
	}

	/**
	 * The proof, as something you can watch. Each sliding strip GLIDES through
	 * its travel on its own period — a triangle sweep, out and back — and the
	 * paint path renders fractional offsets, so the search reads as film
	 * physically sliding rather than clicking through detents. The two sweeps
	 * share no period, so they are never in step. It runs for as long as the
	 * prover does; nothing here knows how long that will be.
	 */
	function hunt() {
		const PERIOD = [2600, 3400, 0];
		const t0 = performance.now();
		const step = (now: number) => {
			const t = now - t0;
			shift = [0, 1, 2].map((i) => {
				const span = travel(st, i);
				if (!span || !PERIOD[i]) return shift[i];
				const ph = (t / PERIOD[i] + i * 0.37) % 1;
				const tri = ph < 0.5 ? ph * 2 : 2 - ph * 2;
				return tri * span;
			});
			raf = requestAnimationFrame(step);
		};
		raf = requestAnimationFrame(step);
	}

	/** and the landing: staggered, eased, smooth in flight, exact on arrival */
	function settle() {
		const to = homes(st);
		const from = [...shift];
		const t0 = performance.now();
		const D = 820;
		const step = (now: number) => {
			const k = Math.min(1, (now - t0) / D);
			shift = from.map((f, i) => {
				const lane = Math.max(0, Math.min(1, (k - i * 0.1) / 0.8));
				const e = 1 - Math.pow(1 - lane, 3);
				return f + (to[i] - f) * e;
			});
			if (k < 1) {
				raf = requestAnimationFrame(step);
			} else {
				shift = to;
				develop(1);
			}
		};
		raf = requestAnimationFrame(step);
	}

	/** the colour cross-fade, once the pile is actually seated */
	function develop(to: number) {
		cancelAnimationFrame(rafB);
		if (frozen()) {
			blend = to;
			return;
		}
		const from = blend;
		const t0 = performance.now();
		const step = (now: number) => {
			const k = Math.min(1, (now - t0) / 560);
			blend = from + (to - from) * (1 - Math.pow(1 - k, 3));
			if (k < 1) rafB = requestAnimationFrame(step);
		};
		rafB = requestAnimationFrame(step);
	}

	// Driven by the stage and nothing else. The reads inside are untracked because
	// every branch writes `shift`, and a tracked read of it would make each animation
	// frame re-enter the effect that started the animation.
	$effect(() => {
		const s = stage;
		untrack(() => {
			cancelAnimationFrame(raf);
			if (s === 'pass' || s === 'fail') {
				if (frozen()) {
					shift = homes(st);
					blend = 1;
				} else {
					settle();
				}
				return;
			}
			develop(0);
			if (s === 'idle') {
				shift = [0, 0, 0];
				return;
			}
			if (s === 'dealing' || frozen()) {
				shift = scattered();
				return;
			}
			hunt();
		});
	});

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
	const LAMP_RGB = hex2rgb(LAMP);
	const FAULT_RGB = hex2rgb(FAULT);
	const WHITE = [255, 255, 255];
	/**
	 * How far towards white a cell goes once n strips have lit it. The ground is
	 * dealt one cell to one strip, so in register almost every lit cell is n = 1 and
	 * the pile reads as a mosaic of three. The bright ones are the margins doubling
	 * up out of register, which is exactly the thing to make visible.
	 */
	const GLOW = [0, 0, 0.45, 0.8];

	/**
	 * The empty panel: a dim checker, so a register with no sheets in it still reads
	 * as a grid waiting rather than as a hole. A slow band travels across it, because
	 * a case left open on a table between demos should not look switched off.
	 */
	const DIM = ['#0f141b', '#131922'];
	let shimmer = $state(0);
	function ghost(ctx: CanvasRenderingContext2D, pitch: number) {
		const head = shimmer * (grid.cols + 36) - 18;
		for (let r = 0; r < st.pile; r++) {
			for (let c = 0; c < grid.cols; c++) {
				const lift = Math.max(0, 1 - Math.abs(c - head) / 13);
				ctx.globalAlpha = 0.5 + 0.75 * lift * lift;
				ctx.fillStyle = DIM[(r + c) & 1];
				ctx.fillRect(c * pitch, r * pitch, pitch - GAP, pitch - GAP);
			}
		}
		ctx.globalAlpha = 1;
	}

	// Reads `stage` and writes `shimmer`, never the other way round, so the frame it
	// schedules cannot re-enter the effect that scheduled it.
	$effect(() => {
		if (stage !== 'idle' || frozen()) return;
		let h = 0;
		const t0 = performance.now();
		const step = (now: number) => {
			shimmer = ((now - t0) / 3000) % 1;
			h = requestAnimationFrame(step);
		};
		h = requestAnimationFrame(step);
		return () => cancelAnimationFrame(h);
	});

	function surface(cv: HTMLCanvasElement, w: number, h: number) {
		const dpr = window.devicePixelRatio || 1;
		cv.width = Math.round(w * dpr);
		cv.height = Math.round(h * dpr);
		cv.style.width = `${w}px`;
		cv.style.height = `${h}px`;
		const ctx = cv.getContext('2d');
		ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
		return ctx;
	}

	/**
	 * The pile: every strip at its current slide, nothing cropped. What is drawn is
	 * what the films are transmitting right now, not what they would say if they were
	 * already seated.
	 */
	function paintPile(cv: HTMLCanvasElement | null) {
		if (!cv) return;
		const ctx = surface(cv, W, H);
		if (!ctx) return;
		ctx.fillStyle = VOID;
		ctx.fillRect(0, 0, W, H);
		if (!sel.length) {
			ghost(ctx, PITCH);
			return;
		}
		// Film in motion is drawn at its TRUE fractional offset, one translucent
		// layer per strip; the exact share-composite colouring (glow for doubled
		// cells, the lamp read) resumes the moment the strips rest on whole rows.
		const frac = sel.some((i) => Math.abs(shift[i] - Math.round(shift[i])) > 1e-4);
		if (frac && blend < 0.999) {
			for (const i of [2, 0, 1]) {
				if (!shown[i]) continue;
				const hue = i === ROGUE && !pass ? FAULT : HUES[i];
				const off = Math.max(0, Math.min(travel(st, i), shift[i])) * PITCH;
				const strip = st.cells[i];
				ctx.fillStyle = hue;
				for (let r = 0; r < st.heights[i]; r++) {
					for (let c = 0; c < grid.cols; c++) {
						const l = strip[r * grid.cols + c];
						if (!l) continue;
						ctx.globalAlpha = [0, 0.55, 0.68, 0.8, 0.92][l];
						ctx.fillRect(c * PITCH, off + r * PITCH, CELL, CELL);
					}
				}
			}
			ctx.globalAlpha = 1;
			return;
		}
		for (let r = 0; r < st.pile; r++) {
			for (let c = 0; c < grid.cols; c++) {
				const by = sel.filter((k) => view[r * grid.cols + c] & (1 << k));
				if (!by.length) continue;
				// Every cell B lit, wherever it lit it. Its margin cells are doing their
				// job, so this reddens some ink that is not itself at fault — but a strip
				// that was not the one dealt is wrong as a whole, and colouring only the
				// cells that individually miss made the fault look like bad luck.
				const rogue = !pass && by.includes(ROGUE);
				const spread = by.map((k) => (k === ROGUE && rogue ? FAULT_RGB : HUE_RGB[k]));
				const avg = [0, 1, 2].map((ch) => spread.reduce((a, x) => a + x[ch], 0) / spread.length);
				const film = mix(avg, WHITE, GLOW[by.length]);
				ctx.fillStyle = rgb2hex(mix(film, rogue ? FAULT_RGB : LAMP_RGB, blend));
				ctx.fillRect(c * PITCH, r * PITCH, CELL, CELL);
			}
		}
	}

	/** One strip, drawn where it currently sits in the pile — film moving in a frame. */
	function paintStrip(cv: HTMLCanvasElement | null, i: number) {
		if (!cv) return;
		const ctx = surface(cv, SW, SH);
		if (!ctx) return;
		ctx.fillStyle = VOID;
		ctx.fillRect(0, 0, SW, SH);
		if (!shown[i]) {
			ghost(ctx, SP);
			return;
		}
		const hue = i === ROGUE && !pass ? FAULT : HUES[i];
		const drop = Math.max(0, Math.min(travel(st, i), Math.round(shift[i])));
		const strip = st.cells[i];
		for (let r = 0; r < st.pile; r++) {
			const sr = r - drop;
			const on = sr >= 0 && sr < st.heights[i];
			for (let c = 0; c < grid.cols; c++) {
				const l = on ? strip[sr * grid.cols + c] : 0;
				ctx.globalAlpha = l ? [0, 0.62, 0.76, 0.88, 1][l] : 1;
				ctx.fillStyle = l ? hue : on ? DIM[(r + c) & 1] : '#080b10';
				ctx.fillRect(c * SP, r * SP, SP - GAP, SP - GAP);
			}
		}
		ctx.globalAlpha = 1;
	}

	let pileCv = $state<HTMLCanvasElement | null>(null);
	let stripCv = $state<(HTMLCanvasElement | null)[]>([null, null, null]);

	$effect(() => {
		void [view, shift, blend, sel, pass, PITCH, shimmer];
		paintPile(pileCv);
	});
	$effect(() => {
		void [st, shift, shown, pass, SP, shimmer];
		for (let i = 0; i < 3; i++) paintStrip(stripCv[i], i);
	});

	const digests = $derived([req, rsp, model]);
	const fmt = (h: string | null) => (h ? '0x' + h.slice(0, 12).toUpperCase() : '—');

	const note = $derived(
		stage === 'pass'
			? registered
				? 'lined up — the word comes through'
				: 'lining them up'
			: stage === 'fail'
				? "the answer's sheet does not fit — that is the red"
				: stage === 'searching'
					? 'lining them up'
					: sel.length
						? 'three sheets, out of line'
						: 'waiting'
	);
</script>

{#snippet pile()}
	<!-- The pile takes whatever height is left; the canvas floats inside the measured
	     box so its own size cannot feed back into the measurement. -->
	<div class="relative min-h-0 flex-1" bind:clientWidth={boxW} bind:clientHeight={boxH}>
		<div
			class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
			style="width:{W}px;height:{H}px"
			role="img"
			aria-label="the three fingerprints, stacked — {note}"
		>
			<div
				class="pointer-events-none absolute -inset-8 transition-opacity duration-700"
				style="opacity:{stage === 'pass'
					? 1
					: 0};background:radial-gradient(ellipse at center,color-mix(in oklab,var(--verified) 18%,transparent),transparent 70%)"
			></div>
			<div
				class="pointer-events-none absolute -inset-8 transition-opacity duration-700"
				style="opacity:{stage === 'fail'
					? 1
					: 0};background:radial-gradient(ellipse at center,color-mix(in oklab,var(--fault) 16%,transparent),transparent 68%)"
			></div>
			<canvas bind:this={pileCv} class="absolute top-0 left-0" aria-hidden="true"></canvas>
		</div>
	</div>
{/snippet}

{#if embed}
	{@render pile()}
{:else}
	<section class="flex min-h-0 flex-1 flex-col border border-border bg-card">
	<div
		class="flex shrink-0 flex-wrap items-baseline justify-between gap-3 border-b border-border px-4 py-1.5"
	>
		<span class="t-tag font-mono text-muted-foreground uppercase">The three fingerprints</span>
		<span
			class={cn(
				't-body font-mono',
				stage === 'fail'
					? 'text-fault'
					: stage === 'pass'
						? 'text-verified'
						: 'text-muted-foreground/70'
			)}
		>
			{note}
		</span>
	</div>

	{@render pile()}

	<div class="grid shrink-0 grid-cols-3 gap-4 px-4 pt-1 pb-2">
		{#each NAMES as name, i (name)}
			<div
				class={cn(
					'flex flex-col gap-1 transition-opacity duration-500',
					shown[i] ? 'opacity-100' : 'opacity-30'
				)}
			>
				<div class="flex items-baseline gap-x-2">
					<span
						class="size-[0.7em] shrink-0 self-center"
						style="background:{i === ROGUE && !pass ? FAULT : HUES[i]}"
					></span>
					<span class="t-body font-mono font-semibold" style="color:{HUES[i]}">{name}</span>
					<span class="t-body font-mono tracking-[0.1em]">{ROLES[i]}</span>
				</div>
				<!-- On its own line: sat on the same line as the role it read as the NEXT
				     column's digest, which is the one misreading that matters here. -->
				<div
					class="tabular t-body font-mono leading-none font-semibold"
					style="color:{i === ROGUE && !pass ? FAULT : HUES[i]}"
				>
					{fmt(digests[i])}
				</div>
				<div class="t-tag font-mono text-muted-foreground/70">{SUBS[i]}</div>
				<canvas bind:this={stripCv[i]} class="fp-strip" aria-hidden="true"></canvas>
			</div>
		{/each}
	</div>
	</section>
{/if}
