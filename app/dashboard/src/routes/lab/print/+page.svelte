<script lang="ts">
	/**
	 * /lab/print — the three strips as film.
	 *
	 * Ink is subtractive, which decides everything about this page. On screen a cell a
	 * sheet holds is drawn LIT; on film it is INK, and therefore opaque. Stack three
	 * films and a cell passes light only where no sheet inked it — and the three
	 * sheets between them ink every cell of the ground and none of the word. So the
	 * ground goes black and the WORD GLOWS THROUGH: the print is the tonal negative of
	 * the screen, and it cannot be any other way round. Inking each sheet's complement
	 * would union to the complement of the intersection, the three thirds are disjoint,
	 * the intersection is empty, and every film would come out solid black.
	 *
	 * Two things follow that matter for a print rather than a render:
	 *
	 *   - cells are edge to edge, no grid gap. A gap is a cell no sheet inks, so it
	 *     would transmit, and the ground has to be opaque or the word has nothing to
	 *     be brighter than.
	 *   - the rails down each side are for X only. They are continuous, so they give
	 *     away nothing about Y, which is the axis the registration is on.
	 *
	 * And there is no aperture. You cannot crop three pieces of film; you can only put
	 * them on top of one another and move them.
	 *
	 * So the model film is the BACKING: printed at the full height of the pile, solid
	 * above and below its pattern, and laid down first. Its solid margin is what closes
	 * the top and bottom of the finished stack, so what you end up holding is a black
	 * ground with the word glowing out of it and nothing frayed at the edges. The other
	 * two are shorter and slide on top of it.
	 *
	 * Neither margin is a solid block. The top is dealt cell by cell between film A and
	 * the backing and the bottom between film B and the backing, so the two that share
	 * a margin cover it completely between them — the same partition the ground is. At
	 * a half-and-half split (?split=, 10..90) all three films come out around a third
	 * inked, which is both easier on the printer and easier to register: no film is a
	 * slab you cannot see through.
	 */
	import {
		build,
		stencil,
		strips,
		layout,
		DEFAULT_FACE,
		DEFAULT_GRID,
		DEFAULT_WORD
	} from '$lib/fingerprint-shares';
	import { onMount } from 'svelte';

	const NAMES = ['A', 'B', 'C'];
	const ROLES = ['input fingerprint', 'output fingerprint', 'model fingerprint'];
	const SLACK = 2;
	const SPLIT = 0.5;

	let text = $state(DEFAULT_WORD);
	let size = $state(DEFAULT_FACE.size);
	let weight = $state(DEFAULT_FACE.weight);
	let cols = $state(DEFAULT_GRID.cols);
	let rows = $state(DEFAULT_GRID.rows);
	let slack = $state(SLACK);
	let split = $state(SPLIT * 100);
	let req = $state('4f74f5c6b3a19e02d81c7745aa30bb61c4e9f0d2');
	let rsp = $state('a1b93de77c04582fe61099ab3d5c77e0114fa2b8');
	let model = $state('6e6001da2106d4757498752a021df6c2bdc332c6');
	let ready = $state(false);

	const grid = $derived({ rows, cols });
	const word = $derived(text.toUpperCase());
	const face = $derived(layout(word, { size, weight }, grid));
	const mask = $derived(stencil(word, { size, weight }, grid));
	const shares = $derived(build(req, rsp, model, true, mask));
	const st = $derived(strips(shares, grid, slack, model, split / 100));

	onMount(() => {
		const q = new URLSearchParams(location.search);
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
		for (const [k, set] of [
			['text', (v: string) => (text = v)],
			['req', (v: string) => (req = v)],
			['rsp', (v: string) => (rsp = v)],
			['model', (v: string) => (model = v)]
		] as [string, (v: string) => void][]) {
			const v = q.get(k);
			if (v !== null) set(v);
		}
		ready = true;
	});
</script>

<svelte:head><title>Fingerprint film</title></svelte:head>

<div class="page">
	<section class="cover">
		<h1>Three films, one word</h1>
		<p>
			Print each of the next three pages onto transparency film at 100% scale — no “fit to page”,
			which would change the cell pitch between sheets and stop them registering.
		</p>
		<p>
			Lay them over one another against a light. Every cell one film inks is opaque, and between
			them the three ink every cell of the ground and none of the word — so the ground goes black
			and <strong>{word}</strong> comes through as clear film. The print is the negative of the screen;
			that is what ink does, and there is no way to print it the other way up.
		</p>
		<p>
			The rails down each edge are for lining the films up left to right. They are continuous, so
			they say nothing about the vertical, which is the axis that has to be found: the ground only
			closes when all three patterns land on the same rows.
		</p>
		<p>
			Nothing is trimmed. Three pieces of film have no aperture — you stack them and you move them —
			so what you get is the whole pile, and all of it closes. The band in the middle carries the
			word. The margin above it is dealt between film A and the backing and the margin below between
			film B and the backing, cell by cell, so each pair covers its own margin completely and no
			film has to be a solid block.
		</p>
		<p>
			Which means: lay C down, then slide A and B until the word appears. A reaches the top edge, B
			reaches the bottom, and when the word is right the frame is solid too.
		</p>
		<dl>
			<div>
				<dt>word</dt>
				<dd>{word}</dd>
			</div>
			<div>
				<dt>face</dt>
				<dd>{face.w} × {face.h} cells, {face.t} thick</dd>
			</div>
			<div>
				<dt>picture</dt>
				<dd>{rows} × {cols} cells</dd>
			</div>
			<div>
				<dt>strips</dt>
				<dd>A, B {st.heights[0]} rows · C {st.heights[2]} rows</dd>
			</div>
			<div>
				<dt>pile</dt>
				<dd>{rows + 2 * slack} × {cols} cells</dd>
			</div>
			<div>
				<dt>registration</dt>
				<dd class="spoiler">{st.offsets.join(' · ')}</dd>
			</div>
		</dl>
		<p class="fine">
			The registration is printed above because a demo that cannot be reset is not much of a demo.
			It is not a secret in any useful sense either: {(slack + 1) ** 3} slides is nothing to search, and
			each strip carries the word's own ghost where its pattern sits.
		</p>
	</section>

	{#if ready}
		{#each NAMES as n, i (n)}
			<section class="film-page">
				<header>
					<span class="badge">{n}</span>
					<span class="role">{ROLES[i]}</span>
					<span class="digest">0x{[req, rsp, model][i].slice(0, 12).toUpperCase()}</span>
				</header>
				<div class="film">
					<div class="rail"></div>
					<div class="cells" style="--cols:{cols};--rows:{st.heights[i]}">
						{#each st.cells[i] as v, k (k)}
							<i class:ink={v > 0}></i>
						{/each}
					</div>
					<div class="rail"></div>
				</div>
				<footer>film {i + 1} of 3 · {cols} × {st.heights[i]} cells · print at 100%</footer>
			</section>
		{/each}
	{/if}
</div>

<style>
	@page {
		size: A4 portrait;
		margin: 14mm;
	}
	.page {
		background: #fff;
		color: #000;
		font-family: var(--font-mono, ui-monospace, monospace);
	}
	section {
		break-after: page;
		padding: 6mm 0;
	}
	section:last-child {
		break-after: auto;
	}
	h1 {
		font-size: 20pt;
		letter-spacing: 0.06em;
		margin: 0 0 6mm;
	}
	p {
		max-width: 150mm;
		font-size: 10pt;
		line-height: 1.6;
		margin: 0 0 4mm;
	}
	.fine {
		font-size: 8.5pt;
		color: #444;
	}
	dl {
		display: grid;
		gap: 1.5mm;
		margin: 6mm 0;
		font-size: 9pt;
	}
	dl div {
		display: flex;
		gap: 4mm;
	}
	dt {
		width: 30mm;
		color: #555;
		text-transform: uppercase;
		letter-spacing: 0.12em;
	}
	dd {
		margin: 0;
		font-weight: 600;
	}
	.spoiler {
		letter-spacing: 0.2em;
	}
	header {
		display: flex;
		align-items: baseline;
		gap: 4mm;
		margin-bottom: 4mm;
		font-size: 9pt;
	}
	.badge {
		font-size: 16pt;
		font-weight: 700;
	}
	.role {
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: #555;
	}
	.digest {
		margin-left: auto;
	}
	.film {
		display: flex;
		align-items: stretch;
		gap: 2mm;
	}
	.rail {
		width: 1.2mm;
		background: #000;
	}
	/* edge to edge: a gap between cells is a cell no film inks, and it would transmit */
	.cells {
		display: grid;
		grid-template-columns: repeat(var(--cols), 1fr);
		width: 172mm;
		line-height: 0;
	}
	.cells i {
		aspect-ratio: 1;
		background: #fff;
	}
	.cells i.ink {
		background: #000;
	}
	footer {
		margin-top: 4mm;
		font-size: 8pt;
		color: #555;
		letter-spacing: 0.1em;
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
	}
</style>
