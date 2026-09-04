/**
 * Properties of the deal, measured rather than asserted.
 *
 *   pnpm check:shares
 *
 * Nothing here is a claim about the construction -- every line reads the sheets
 * `build()` actually produced and counts. Check 2 is the one the whole thing exists
 * for: the stack is the stencil exactly, with no threshold in between. Check 3 is
 * the symmetry: three thirds, none of them special.
 *
 * Check 6 is different in kind. It measures the gap the construction is KNOWN to
 * have -- the word is faintly visible in a single sheet -- so the cost shows up as a
 * number in the output rather than a footnote nobody reads.
 *
 * Every knob the bench exposes is swept, because a bigger word on a smaller grid
 * leaves less ground for the three sheets to divide.
 */
import { build, union, stats, stencil, layout } from '../src/lib/fingerprint-shares.ts';

const hex = (s) =>
	[...Array(40)].map((_, i) => '0123456789abcdef'[(Math.imul(i + 1, s) >>> 3) & 15]).join('');

let bad = 0;
const fail = (m) => {
	console.log('  FAIL ' + m);
	bad++;
};

const CASES = [
	// the defaults the bench and the demo page both open on
	{ word: 'VERIFIED', face: { size: 7, weight: 1 }, grid: { rows: 12, cols: 58 } },
	{ word: 'VALID', face: { size: 21, weight: 1 }, grid: { rows: 33, cols: 105 } },
	{ word: 'VALID', face: { size: 7, weight: 1 }, grid: { rows: 33, cols: 105 } },
	{ word: 'VALID', face: { size: 31, weight: 5 }, grid: { rows: 55, cols: 165 } },
	{ word: 'OK', face: { size: 21, weight: 3 }, grid: { rows: 33, cols: 105 } },
	{ word: 'INTERLOCK', face: { size: 15, weight: 1 }, grid: { rows: 25, cols: 145 } },
	{ word: 'A', face: { size: 13, weight: 2 }, grid: { rows: 15, cols: 45 } },
	{ word: 'PROVEN 2026', face: { size: 17, weight: 2 }, grid: { rows: 45, cols: 165 } },
	// the far end of every slider, where the word no longer fits the grid at all
	{ word: 'VALID', face: { size: 5, weight: 1 }, grid: { rows: 7, cols: 15 } },
	{ word: 'X', face: { size: 5, weight: 5 }, grid: { rows: 7, cols: 15 } }
];

let ghostAcc = 0;
let dropAcc = 0;
let dropN = 0;
let cases = 0;

for (const { word, face, grid } of CASES) {
	const mask = stencil(word, face, grid);
	const N = mask.length;
	const L = layout(word, face, grid);
	const wordCells = mask.reduce((n, v) => n + v, 0);
	const ground = N - wordCells;
	let worstGhost = 0;
	let spread = 0;
	let dirty = 0;
	let dirtyN = 0;

	for (let t = 0; t < 40; t++) {
		cases++;
		const [r, s, mo] = [hex(t * 7 + 1), hex(t * 13 + 5), hex(t * 29 + 11)];
		const tag = `"${word}" ${face.size}/${face.weight} ${grid.rows}x${grid.cols} t${t}`;

		const p = build(r, s, mo, true, mask);
		const layers = [p.a, p.b, p.c];
		const st = stats(p, mask);

		// 1. the three sheets are the same size to within the rounding of a third,
		//    and between them they hold every ground cell exactly once
		const total = st.lit.reduce((a, b) => a + b, 0);
		if (total !== ground) fail(`${tag} sheets hold ${total} cells, ground is ${ground}`);
		const hi = Math.max(...st.lit);
		const lo = Math.min(...st.lit);
		if (hi - lo > 1) fail(`${tag} thirds differ by ${hi - lo}: ${st.lit}`);
		spread = Math.max(spread, hi - lo);
		// no cell is held twice -- the deal is a partition, not a covering
		for (let i = 0; i < N; i++) {
			const n = layers.reduce((k, l) => k + (l[i] ? 1 : 0), 0);
			if (n > 1) {
				fail(`${tag} cell ${i} is held by ${n} sheets`);
				break;
			}
		}

		// 2. the stack IS the stencil, cell for cell. No threshold, no second pass.
		const u = union(layers, N);
		for (let i = 0; i < N; i++) {
			if (u[i] !== (mask[i] ? 0 : 1)) {
				fail(`${tag} stacked cell ${i}: ${u[i]} want ${mask[i] ? 0 : 1}`);
				break;
			}
		}
		if (st.stacked.letters !== 0 || st.stacked.field !== 1) {
			fail(`${tag} stacked letters=${st.stacked.letters} field=${st.stacked.field}`);
		}

		// 3. symmetry: drop ANY one of the three and the ground loses about a third.
		//    No sheet is more load bearing than the others.
		for (const drop of [0, 1, 2]) {
			const two = stats(
				{
					a: drop === 0 ? undefined : layers[0],
					b: drop === 1 ? undefined : layers[1],
					c: drop === 2 ? undefined : layers[2]
				},
				mask
			);
			if (two.stacked.field >= 1) fail(`${tag} the ground fills without sheet ${drop}`);
			dropAcc += two.stacked.field;
			dropN++;
		}

		// 4. nothing on the table lights nothing
		if (union([], N).some((v) => v !== 0)) fail(`${tag} the empty stack is not black`);

		// 5. A and C are byte-identical pass or fail; B keeps its size either way
		const f = build(r, s, mo, false, mask);
		if (!p.a.every((v, i) => v === f.a[i])) fail(`${tag} sheet A differs pass/fail`);
		if (!p.c.every((v, i) => v === f.c[i])) fail(`${tag} sheet C differs pass/fail`);
		if (p.b.every((v, i) => v === f.b[i])) fail(`${tag} sheet B is unchanged by the verdict`);
		const sf = stats(f, mask);
		if (sf.lit[1] !== st.lit[1]) fail(`${tag} fail sheet B holds ${sf.lit[1]} not ${st.lit[1]}`);
		if (sf.stacked.field >= 0.999) fail(`${tag} a non-fitting B still filled the ground`);
		// Whether it also drops light INTO the word is a coin toss per cell, so on a
		// tiny grid with a tiny word it can miss entirely. That is a real property of
		// the construction, not a defect, so it is scored over the run rather than
		// asserted per trial.
		if (wordCells && sf.stacked.letters > 0.001) dirty++;
		dirtyN++;

		worstGhost = Math.max(worstGhost, ...st.ghost);
		ghostAcc += st.ghost.reduce((a, b) => a + b, 0) / 3;
	}

	console.log(
		`${('"' + word + '"').padEnd(14)} ${String(face.size).padStart(2)}/${face.weight}  ` +
			`${String(grid.rows).padStart(2)}x${String(grid.cols).padStart(3)}=${String(N).padStart(5)}  ` +
			`glyph ${L.w}x${L.h}${L.fits ? '' : ' OVERFLOWS'}  ` +
			`word ${String(wordCells).padStart(4)}  ground ${String(ground).padStart(5)}  ` +
			`thirds differ by ${spread}  ${worstGhost === 0 ? 'word untouched' : 'GHOST ' + worstGhost}` +
			`  ·  rogue B dirties the word ${((dirty / dirtyN) * 100).toFixed(0)}% of runs`
	);
	if (wordCells >= 40 && dirty / dirtyN < 0.9) {
		fail(
			`"${word}" a rogue B left the word clean ${(100 - (dirty / dirtyN) * 100).toFixed(0)}% of the time`
		);
	}
	if (worstGhost !== 0) fail(`"${word}" ${face.size}/${face.weight}: sheets lit word cells`);
}

// 6. THE KNOWN GAP, measured.
console.log(
	`\nsingle sheet: ${((ghostAcc / cases) * 100).toFixed(1)}% of its light falls inside the word ` +
		`-- the word is there as an absence, in every sheet`
);
console.log(
	`two sheets:   ground ${((dropAcc / dropN) * 100).toFixed(1)}% filled -- holed by the missing third`
);

console.log(bad ? `\n${bad} FAILURES` : '\nall checks pass');
