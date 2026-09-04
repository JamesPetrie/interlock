<script lang="ts">
	/**
	 * /lab/deck — six hand cards, 95 × 64 mm landscape, for narrating the demo.
	 *
	 * Two message cards (double-sided: plaintext on the face, the run's REAL
	 * ciphertext on the flip), three fingerprint cards in three greens, and one
	 * red impostor. The fingerprint cards use the same share optics as
	 * /lab/print: each inks its own share of the ground and none of the word, so
	 * films stacked on the solid model card close the ground and the word reads
	 * out in white. Swap the green OUTPUT film for the red scrambled one — the
	 * datacenter claiming an answer the certifier never saw — and the ground
	 * never closes: no word, and red where the lie sits.
	 *
	 * STACKING IS THE DESIGN CONSTRAINT. All four fingerprint cards share one
	 * geometry: the same grid position and the same header band. Each card fills
	 * only its own slot of that band (INPUT left, OUTPUT centre, MODEL right),
	 * so an aligned stack composes a single complete header instead of three
	 * labels mashed into mush. Cards are pre-registered: corners flush =
	 * patterns seated -- the card edges themselves are the registration.
	 *
	 * The defaults below are values captured off a real PASSing run with the few-shot prompt (2026-08-25);
	 * override any of them with query parameters (q, answer, ctin, ctout, req,
	 * rsp, model, text for the word).
	 */
	import {
		build,
		homes,
		stencil,
		strips,
		DEFAULT_FACE,
		DEFAULT_GRID,
		DEFAULT_WORD
	} from '$lib/fingerprint-shares';
	import { onMount } from 'svelte';

	// ── the run on the cards (a real one; params override) ─────────────────────
	let q = $state('What does IAEA stand for?');
	let answer = $state('International Atomic Energy Agency');
	let ctin = $state(
		'ba98330dd9a87875aa050d51b9c32e37065e2e286dcb9fc8ec20b3b8dbb4dcff424574bad386ab29e619b184e2fec7c93e9da4965b9483be7f99af614d7d9e7d693611b26871089e200449953e70b2e2de8ab193b8ad669551056696516aefb1f6e1a1abe3e80cb802956c68f10fa312'
	);
	let ctout = $state(
		'7d1bf2757998a67262771478da0c47d8fcfaadc2'
	);
	let req = $state('a9368f47e724515062810b54c7b1c19c2c797aad57b61c678831193d7407a50a');
	let rsp = $state('9ddad76a4dd8226a729e9942652142380b80bbe64d506377466ad8396ddd12f9');
	let model = $state('6e6001da2106d4757498752a021df6c2bdc332c650aae4bae6b0c004dcf14933');
	let text = $state(DEFAULT_WORD);
	/**
	 * ?media=stock prints only the cardstock pages, ?media=film only the
	 * transparency sheet -- two documents, because the two stocks go through
	 * the printer separately anyway. ?media=reds is the shade-calibration
	 * sheet: three pairs of the impostor card, each pair in a different
	 * candidate red, on ONE piece of film -- the winning pair is already two
	 * cut-ready cards, so the test print costs nothing. Default is everything.
	 */
	let media = $state('all');
	let ready = $state(false);

	// ── the shares ─────────────────────────────────────────────────────────────
	const word = $derived(text.toUpperCase());
	const grid = DEFAULT_GRID;
	const mask = $derived(stencil(word, DEFAULT_FACE, grid));
	const shares = $derived(build(req, rsp, model, true, mask));
	const SLACK = 2;
	const st = $derived(strips(shares, grid, SLACK, model, 0.5));
	/** registered drop per strip; the cards are printed pre-seated at these */
	const HOME = $derived(homes(st));

	// ── card geometry, mm ──────────────────────────────────────────────────────
	const CW = 95;
	const CH = 64;
	const CR = 0;
	const PATW = 86;
	const P = $derived(PATW / grid.cols);
	const X0 = (CW - PATW) / 2;
	const Y0 = 26;
	const PILE = $derived(grid.rows + 2 * SLACK);

	// Three greens with real distance between them: films read apart in the
	// hand, overlaps darken toward the backing, and the word stays the one
	// white thing. Red is reserved for the impostor.
	const INK = { A: '#5ea44b', B: '#2e7d43', C: '#14532d', X: '#c62828' };
	const ROLES = ['INPUT', 'OUTPUT', 'MODEL'];
	/** header slots: left / centre / right, identical on every card */
	const SLOT = [
		{ x: 4, anchor: 'start' },
		{ x: CW / 2, anchor: 'middle' },
		{ x: CW - 4, anchor: 'end' }
	];
	const digests = $derived([req, rsp, model]);
	const short = (h: string) => '0x' + h.slice(0, 8).toUpperCase();

	// ── the impostor ───────────────────────────────────────────────────────────
	/** deterministic scramble: B's geometry, nobody's share */
	function scrambleCells(n: number, density = 0.34, seed = 0x7a3b91) {
		let s = seed >>> 0;
		const cells = new Uint8Array(n);
		for (let i = 0; i < n; i++) {
			s ^= s << 13;
			s >>>= 0;
			s ^= s >> 17;
			s ^= s << 5;
			s >>>= 0;
			cells[i] = s % 1000 < density * 1000 ? 1 + (s % 4) : 0;
		}
		return cells;
	}
	const xCells = $derived(scrambleCells(st.heights[1] * grid.cols));
	const xDigest = $derived(
		'0x' + rsp.slice(0, 8).split('').reverse().join('').toUpperCase()
	);

	// ── text layout helpers ────────────────────────────────────────────────────
	function wrap(s: string, max: number) {
		const out: string[] = [];
		let line = '';
		for (const w of s.split(/\s+/)) {
			if (line && (line + ' ' + w).length > max) {
				out.push(line);
				line = w;
			} else line = line ? line + ' ' + w : w;
		}
		if (line) out.push(line);
		return out;
	}
	/**
	 * The flip side's hex, illustrative rather than exhaustive: as many
	 * characters of the real ciphertext as the plaintext has, at the same
	 * measure and size, so the flip reads as THE SAME MESSAGE in its other
	 * form -- equal weight, different alphabet. An ellipsis marks a cut; a
	 * ciphertext shorter than its plaintext is shown whole.
	 */
	function hexBig(h: string, plainLen: number) {
		let s = '0x' + h.toUpperCase();
		if (s.length > plainLen) s = s.slice(0, plainLen - 1) + '…';
		const per = 20;
		const out: string[] = [];
		for (let i = 0; i < s.length; i += per) out.push(s.slice(i, i + per));
		return out;
	}

	onMount(() => {
		const p = new URLSearchParams(location.search);
		for (const [k, set] of [
			['q', (v: string) => (q = v)],
			['answer', (v: string) => (answer = v)],
			['ctin', (v: string) => (ctin = v)],
			['ctout', (v: string) => (ctout = v)],
			['req', (v: string) => (req = v)],
			['rsp', (v: string) => (rsp = v)],
			['model', (v: string) => (model = v)],
			['text', (v: string) => (text = v)],
			['media', (v: string) => (media = v)]
		] as [string, (v: string) => void][]) {
			const v = p.get(k);
			if (v !== null) set(v);
		}
		ready = true;
	});
</script>

<svelte:head><title>Interlock hand deck</title></svelte:head>

{#snippet paper(base: string, id: string, dot: string, bleed = 0)}
	<!-- SVG fills, not CSS backgrounds, so the stock prints even with
	     "background graphics" off: a soft paper tone under a fine dot grain.
	     `bleed` extends the stock past the cut line (the svg overflows on
	     purpose), so a printer whose printable area sits off-centre cannot
	     leave a white sliver on a duplexed back. -->
	<defs>
		<pattern id={id} width="3.5" height="3.5" patternUnits="userSpaceOnUse">
			<circle cx="1.75" cy="1.75" r="0.17" fill={dot} />
		</pattern>
	</defs>
	<rect x={0.3 - bleed} y={0.3 - bleed} width={CW - 0.6 + 2 * bleed}
		height={CH - 0.6 + 2 * bleed} rx={CR} fill={base} />
	<rect x={0.3 - bleed} y={0.3 - bleed} width={CW - 0.6 + 2 * bleed}
		height={CH - 0.6 + 2 * bleed} rx={CR} fill="url(#{id})" />
{/snippet}

{#snippet outline()}
	<rect
		x="0.1"
		y="0.1"
		width={CW - 0.2}
		height={CH - 0.2}
		rx={CR}
		fill="none"
		stroke="#999"
		stroke-width="0.2"
		stroke-dasharray="1.4 1"
	/>
{/snippet}

{#snippet slot(i: number, role: string, digest: string, ink: string, sub = '')}
	<text x={SLOT[i].x} y="10" text-anchor={SLOT[i].anchor} class="t-name" fill={ink}>{role}</text>
	<text x={SLOT[i].x} y="15.6" text-anchor={SLOT[i].anchor} class="t-hex" fill={ink}
		>{digest}</text
	>
	{#if sub}
		<text x={SLOT[i].x} y="20.4" text-anchor={SLOT[i].anchor} class="t-hex" fill={ink}
			>{sub}</text
		>
	{/if}
{/snippet}

{#snippet film(cells: Uint8Array, height: number, drop: number, ink: string)}
	<!-- nothing around the grid: the cards are printed pre-seated, so the card
	     edges themselves are the registration -->
	<g transform="translate({X0},{Y0 + drop * P})">
		{#each { length: height } as _r, r (r)}
			{#each { length: grid.cols } as _c, c (c)}
				{#if cells[r * grid.cols + c] > 0}
					<rect x={c * P} y={r * P} width={P + 0.02} height={P + 0.02} fill={ink} />
				{/if}
			{/each}
		{/each}
	</g>
{/snippet}

{#snippet fpCard(
	slotIdx: number,
	role: string,
	digest: string,
	ink: string,
	cells: Uint8Array,
	height: number,
	drop: number,
	opaque: boolean,
	sub = ''
)}
	<svg class="card" width="{CW}mm" height="{CH}mm" viewBox="0 0 {CW} {CH}">
		{#if opaque}
			{@render paper('#fbf4e6', 'stock-paper', 'rgba(24,24,24,0.055)')}
		{/if}
		{@render outline()}
		{@render slot(slotIdx, role, digest, ink, sub)}
		{@render film(cells, height, drop, ink)}
	</svg>
{/snippet}

{#snippet msgFront(label: string, big: string)}
	<!-- plaintext face: WHITE on black, the panel's own language on paper.
	     The white is a knockout -- the printer lays black everywhere except
	     the letters, and the stock shows through. -->
	<svg class="card" width="{CW}mm" height="{CH}mm" viewBox="0 0 {CW} {CH}">
		{@render paper('#0d0e11', 'stock-black', 'rgba(255,255,255,0.05)')}
		{@render outline()}
		<text x="6" y="12" class="t-eyebrow" fill="#f4f1e8">{label}</text>
		<line x1="6" y1="15.5" x2={CW - 6} y2="15.5" stroke="#f4f1e8" stroke-width="0.3" />
		{#each wrap(big, 20) as line, i (i)}
			<text x="6" y={30 + i * 10} class="t-big" fill="#f4f1e8">{line}</text>
		{/each}
	</svg>
{/snippet}

{#snippet msgBack(label: string, hex: string, plainLen: number)}
	<!-- no dashed outline here on purpose: the cut follows the FACE side's
	     lines, and a cut line on a duplexed back would only advertise the
	     printer's offset. The bleed absorbs it instead. -->
	<!-- sealed face: GOLD on black, matching the gold seal on screen. Print
	     gold is an ochre -- there is no metallic in CMYK -- but on the black
	     ground it reads unmistakably as gold. -->
	<svg class="card" width="{CW}mm" height="{CH}mm" viewBox="0 0 {CW} {CH}"
		style="overflow:visible">
		{@render paper('#0d0e11', 'stock-black-b', 'rgba(255,255,255,0.05)', 3)}
		<text x="6" y="12" class="t-eyebrow" fill="#c9a227">{label} · ENCRYPTED</text>
		<!-- closed padlock: modest, clear of the rule -->
		<g
			transform="translate({CW - 13},6)"
			fill="none"
			stroke="#c9a227"
			stroke-width="0.75"
			stroke-linecap="round"
		>
			<rect x="0" y="3.6" width="7" height="5" rx="0.8" />
			<path d="M 1.6 3.6 V 2.2 a 1.9 1.9 0 0 1 3.8 0 v 1.4" />
		</g>
		<line x1="6" y1="15.5" x2={CW - 17} y2="15.5" stroke="#c9a227" stroke-width="0.3" />
		{#each hexBig(hex, plainLen) as row, i (i)}
			<text x="6" y={30 + i * 10} class="t-ct" fill="#c9a227">{row}</text>
		{/each}
	</svg>
{/snippet}

<div class="page">
	{#if ready}
		{#if media !== 'film' && media !== 'reds'}
		<section class="sheet">
			<div class="pair">{@render msgFront('REQUEST', q)}{@render msgFront('REQUEST', q)}</div>
			<div class="pair">{@render msgFront('RESPONSE', answer)}{@render msgFront('RESPONSE', answer)}</div>
		</section>

		<section class="sheet">
			<div class="pair">{@render msgBack('REQUEST', ctin, q.length)}{@render msgBack('REQUEST', ctin, q.length)}</div>
			<div class="pair">{@render msgBack('RESPONSE', ctout, answer.length)}{@render msgBack('RESPONSE', ctout, answer.length)}</div>
		</section>

		<section class="sheet">
			<div class="pair">
				{@render fpCard(2, ROLES[2], short(digests[2]), INK.C, st.cells[2], st.heights[2], HOME[2], true, 'llama-1.1b')}
				{@render fpCard(2, ROLES[2], short(digests[2]), INK.C, st.cells[2], st.heights[2], HOME[2], true, 'llama-1.1b')}
			</div>
		</section>
		{/if}

		{#if media !== 'stock' && media !== 'reds'}
		<section class="sheet">
			<div class="pair">
				{@render fpCard(0, ROLES[0], short(digests[0]), INK.A, st.cells[0], st.heights[0], HOME[0], false)}
				{@render fpCard(0, ROLES[0], short(digests[0]), INK.A, st.cells[0], st.heights[0], HOME[0], false)}
			</div>
			<div class="pair">
				{@render fpCard(1, ROLES[1], short(digests[1]), INK.B, st.cells[1], st.heights[1], HOME[1], false)}
				{@render fpCard(1, ROLES[1], short(digests[1]), INK.B, st.cells[1], st.heights[1], HOME[1], false)}
			</div>
			<div class="pair">
				{@render fpCard(1, ROLES[1], xDigest, INK.X, xCells, st.heights[1], HOME[1], false)}
				{@render fpCard(1, ROLES[1], xDigest, INK.X, xCells, st.heights[1], HOME[1], false)}
			</div>
		</section>
		{/if}

		{#if media === 'reds'}
			<section class="sheet">
				{#each ['#b71c30', '#b0203e', '#a4133c'] as red (red)}
					<div>
						<!-- shade tag lives OUTSIDE the cut line: gone once the cards are cut -->
						<p class="redtag">{red}</p>
						<div class="pair">
							{@render fpCard(1, ROLES[1], xDigest, red, xCells, st.heights[1], HOME[1], false)}
							{@render fpCard(1, ROLES[1], xDigest, red, xCells, st.heights[1], HOME[1], false)}
						</div>
					</div>
				{/each}
			</section>
		{/if}
	{/if}
</div>

<style>
	@page {
		size: A4 portrait;
		margin: 10mm 8mm;
	}
	/* the app's theme paints the body graphite; this route is a paper document */
	:global(body) {
		background: #fff;
	}
	.page {
		background: #fff;
		color: #000;
		font-family: var(--font-mono, ui-monospace, monospace);
	}
	section {
		break-after: page;
		padding: 5mm 0;
	}
	section:last-child {
		break-after: auto;
	}
	.sheet {
		display: flex;
		flex-direction: column;
		gap: 6mm;
		/* centred, so the slack between pair width and printable width splits
		   evenly instead of piling up as a lopsided right margin */
		align-items: center;
	}
	/* two copies of each card, side by side: one print run yields two decks.
	   The columns are identical, so the duplex flip still lands every back on
	   a front of the same card. */
	.pair {
		display: flex;
		gap: 2mm;
	}
	.redtag {
		margin: 0 0 1mm;
		font-size: 8pt;
		color: #777;
		letter-spacing: 0.1em;
	}
	.card {
		display: block;
	}
	.card text {
		font-family: var(--font-mono, ui-monospace, monospace);
	}
	.t-eyebrow {
		font-size: 3.2px;
		letter-spacing: 0.35px;
		font-weight: 700;
	}
	.t-big {
		font-size: 6.6px;
		font-weight: 700;
		letter-spacing: 0.05px;
	}
	.t-ct {
		font-size: 6.6px;
		font-weight: 700;
		letter-spacing: 0.05px;
	}
	.t-name {
		font-size: 4.6px;
		font-weight: 700;
		letter-spacing: 0.5px;
	}
	.t-hex {
		font-size: 3.8px;
		font-weight: 600;
	}
	@media screen {
		.page {
			max-width: 210mm;
			margin: 0 auto;
			padding: 10mm;
		}
		section {
			border-bottom: 1px dashed #bbb;
		}
		.card {
			box-shadow: 0 1px 6px rgba(0, 0, 0, 0.18);
		}
	}
</style>
