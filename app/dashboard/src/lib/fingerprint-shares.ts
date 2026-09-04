/**
 * Fingerprint shares — one shuffle, cut in three, with the word punched out.
 *
 * ── the construction, in full ───────────────────────────────────────────────────
 *
 *   1. mark the cells the word covers
 *   2. deal every OTHER cell out round-robin to A, B and C
 *
 * That is the whole thing. Each sheet holds exactly a third of the ground and not one
 * cell of the word, so laying the three over one another fills the ground completely
 * and leaves the word black. The stack is the union, cell for cell — no subcells, no
 * threshold, no second resolution. What is on screen is what the sheets are.
 *
 * The three are interchangeable. Same size, same statistics, none of them special:
 * whichever one you take away, a third of the ground goes dark with it.
 *
 * ── where the digests actually come in ──────────────────────────────────────────
 *
 * Less far than the panel's labels suggest, and it is worth being exact about it.
 *
 * The shuffle is seeded by the REQUEST digest, and it has to be seeded by something
 * that exists before any sheet is drawn — the register puts sheet A on the table
 * while the response is still being generated, so the deal is already settled by
 * then. It cannot depend on digests that have not happened yet.
 *
 * So: the request digest fixes the deal. Each digest then sets its own sheet's
 * brightness texture. And the model digest decides the one thing the verdict turns
 * on — whether sheet C is the third it was dealt, or a random handful that fits
 * nothing. What no digest does is choose its own cells independently of the others:
 * three sets chosen independently would not tile anything.
 *
 * ── what this costs, stated plainly ─────────────────────────────────────────────
 *
 * A single sheet is a third dense over the ground and empty over the word, so the
 * word is faintly there in each one, as an absence. That is forced, not sloppy. A
 * stack that is COMPLETELY black over the letters means no sheet lit a letter cell,
 * and a sheet that never lights a letter cell has a hole shaped like the word.
 *
 * Hiding that hole is what subcell expansion buys, and it costs the thing that was
 * wanted more: with expansion the letters can only ever be a third lit rather than
 * black, and getting them back to black needs a threshold pass. This file takes the
 * other side of that trade — a true black word in a true solid ground, read straight
 * off the cells, at the price of a legible ghost in each sheet.
 */

export type Grid = { rows: number; cols: number };
export const GRID_LIMITS = { rows: [7, 55], cols: [15, 165] } as const;
/**
 * Seventeen rows around an eleven-cell face: the word sits centred with three rows
 * of ground above and below it, so the stack reads as a word IN a field rather
 * than a word that is the field.
 */
export const DEFAULT_GRID: Grid = { rows: 17, cols: 74 };

export type Face = {
	/** glyph height in cells */
	size: number;
	/** stroke thickness in cells */
	weight: number;
};
export const FACE_LIMITS = { size: [5, 31], weight: [1, 5] } as const;
/**
 * Size 11 puts the glyph box at 7 cells across, which is where a hairline stroke has
 * room to be a hairline rather than a third of the letter. Heavier weights need at
 * least this much: at size 7 the box is 5 across and a two-cell stroke closes the
 * counters, so E, F and B stop being different letters.
 */
export const DEFAULT_FACE: Face = { size: 11, weight: 1 };

export const DEFAULT_WORD = 'VERIFIED';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.round(v)));

/**
 * The face is drawn rather than tabulated, because size, weight and the word itself
 * are all things you want a control on.
 *
 * Each glyph is a handful of polylines in a unit box, rasterised at one cell thick
 * and then dilated to the requested weight — so a heavier letter is the same letter
 * with a fatter pen, not a different drawing. Curves are polylines too; at nine cells
 * tall an arc and a five-segment approximation of one are the same picture.
 */
const PEN: Record<string, number[][][]> = {
	A: [
		[
			[0, 1],
			[0.5, 0],
			[1, 1]
		],
		[
			[0.16, 0.66],
			[0.84, 0.66]
		]
	],
	B: [
		[
			[0, 0],
			[0, 1]
		],
		[
			[0, 0],
			[0.62, 0],
			[0.9, 0.16],
			[0.62, 0.46],
			[0, 0.46]
		],
		[
			[0, 0.46],
			[0.68, 0.46],
			[0.96, 0.72],
			[0.68, 1],
			[0, 1]
		]
	],
	C: [
		[
			[0.96, 0.2],
			[0.68, 0],
			[0.32, 0],
			[0.04, 0.28],
			[0.04, 0.72],
			[0.32, 1],
			[0.68, 1],
			[0.96, 0.8]
		]
	],
	D: [
		[
			[0, 0],
			[0.56, 0],
			[0.92, 0.26],
			[0.92, 0.74],
			[0.56, 1],
			[0, 1],
			[0, 0]
		]
	],
	E: [
		[
			[1, 0],
			[0, 0],
			[0, 1],
			[1, 1]
		],
		[
			[0, 0.48],
			[0.78, 0.48]
		]
	],
	F: [
		[
			[1, 0],
			[0, 0],
			[0, 1]
		],
		[
			[0, 0.48],
			[0.78, 0.48]
		]
	],
	G: [
		[
			[0.96, 0.2],
			[0.68, 0],
			[0.32, 0],
			[0.04, 0.28],
			[0.04, 0.72],
			[0.32, 1],
			[0.7, 1],
			[0.96, 0.78],
			[0.96, 0.54],
			[0.56, 0.54]
		]
	],
	H: [
		[
			[0, 0],
			[0, 1]
		],
		[
			[1, 0],
			[1, 1]
		],
		[
			[0, 0.5],
			[1, 0.5]
		]
	],
	I: [
		[
			[0.08, 0],
			[0.92, 0]
		],
		[
			[0.5, 0],
			[0.5, 1]
		],
		[
			[0.08, 1],
			[0.92, 1]
		]
	],
	J: [
		[
			[1, 0],
			[1, 0.74],
			[0.7, 1],
			[0.3, 1],
			[0.02, 0.76]
		]
	],
	K: [
		[
			[0, 0],
			[0, 1]
		],
		[
			[1, 0],
			[0, 0.56]
		],
		[
			[0.34, 0.38],
			[1, 1]
		]
	],
	L: [
		[
			[0, 0],
			[0, 1],
			[1, 1]
		]
	],
	M: [
		[
			[0, 1],
			[0, 0],
			[0.5, 0.56],
			[1, 0],
			[1, 1]
		]
	],
	N: [
		[
			[0, 1],
			[0, 0],
			[1, 1],
			[1, 0]
		]
	],
	O: [
		[
			[0.5, 0],
			[0.86, 0.2],
			[1, 0.5],
			[0.86, 0.8],
			[0.5, 1],
			[0.14, 0.8],
			[0, 0.5],
			[0.14, 0.2],
			[0.5, 0]
		]
	],
	P: [
		[
			[0, 1],
			[0, 0],
			[0.62, 0],
			[0.94, 0.26],
			[0.62, 0.52],
			[0, 0.52]
		]
	],
	Q: [
		[
			[0.5, 0],
			[0.86, 0.2],
			[1, 0.5],
			[0.86, 0.8],
			[0.5, 1],
			[0.14, 0.8],
			[0, 0.5],
			[0.14, 0.2],
			[0.5, 0]
		],
		[
			[0.6, 0.7],
			[1, 1]
		]
	],
	R: [
		[
			[0, 1],
			[0, 0],
			[0.8, 0],
			[1, 0.14],
			[1, 0.4],
			[0.8, 0.52],
			[0, 0.52]
		],
		[
			[0.667, 0.6],
			[0.667, 0.7],
			[0.833, 0.7],
			[0.833, 0.9],
			[1, 0.9],
			[1, 1]
		]
	],
	S: [
		[
			[0.96, 0.18],
			[0.66, 0],
			[0.3, 0],
			[0.04, 0.2],
			[0.3, 0.44],
			[0.7, 0.56],
			[0.96, 0.78],
			[0.7, 1],
			[0.3, 1],
			[0.04, 0.82]
		]
	],
	T: [
		[
			[0, 0],
			[1, 0]
		],
		[
			[0.5, 0],
			[0.5, 1]
		]
	],
	U: [
		[
			[0, 0],
			[0, 0.72],
			[0.3, 1],
			[0.7, 1],
			[1, 0.72],
			[1, 0]
		]
	],
	V: [
		[
			[0, 0],
			[0.5, 1],
			[1, 0]
		]
	],
	W: [
		[
			[0, 0],
			[0.22, 1],
			[0.5, 0.42],
			[0.78, 1],
			[1, 0]
		]
	],
	X: [
		[
			[0, 0],
			[1, 1]
		],
		[
			[1, 0],
			[0, 1]
		]
	],
	Y: [
		[
			[0, 0],
			[0.5, 0.52],
			[1, 0]
		],
		[
			[0.5, 0.52],
			[0.5, 1]
		]
	],
	Z: [
		[
			[0, 0],
			[1, 0],
			[0, 1],
			[1, 1]
		]
	],
	'0': [
		[
			[0.5, 0],
			[0.86, 0.2],
			[1, 0.5],
			[0.86, 0.8],
			[0.5, 1],
			[0.14, 0.8],
			[0, 0.5],
			[0.14, 0.2],
			[0.5, 0]
		],
		[
			[0.76, 0.26],
			[0.24, 0.74]
		]
	],
	'1': [
		[
			[0.22, 0.2],
			[0.5, 0],
			[0.5, 1]
		],
		[
			[0.18, 1],
			[0.82, 1]
		]
	],
	'2': [
		[
			[0.04, 0.22],
			[0.32, 0],
			[0.7, 0],
			[0.96, 0.24],
			[0.04, 1],
			[1, 1]
		]
	],
	'3': [
		[
			[0.04, 0.2],
			[0.32, 0],
			[0.7, 0],
			[0.94, 0.24],
			[0.6, 0.48],
			[0.94, 0.74],
			[0.7, 1],
			[0.3, 1],
			[0.04, 0.8]
		]
	],
	'4': [
		[
			[0.74, 1],
			[0.74, 0],
			[0, 0.7],
			[1, 0.7]
		]
	],
	'5': [
		[
			[1, 0],
			[0.14, 0],
			[0.06, 0.44],
			[0.6, 0.42],
			[0.94, 0.68],
			[0.7, 1],
			[0.3, 1],
			[0.04, 0.84]
		]
	],
	'6': [
		[
			[0.84, 0.1],
			[0.5, 0],
			[0.16, 0.3],
			[0.06, 0.7],
			[0.3, 1],
			[0.7, 1],
			[0.94, 0.74],
			[0.68, 0.5],
			[0.3, 0.5],
			[0.06, 0.7]
		]
	],
	'7': [
		[
			[0, 0],
			[1, 0],
			[0.34, 1]
		]
	],
	'8': [
		[
			[0.5, 0],
			[0.84, 0.16],
			[0.6, 0.44],
			[0.94, 0.7],
			[0.7, 1],
			[0.3, 1],
			[0.06, 0.7],
			[0.4, 0.44],
			[0.16, 0.16],
			[0.5, 0]
		]
	],
	'9': [
		[
			[0.16, 0.9],
			[0.5, 1],
			[0.84, 0.7],
			[0.94, 0.3],
			[0.7, 0],
			[0.3, 0],
			[0.06, 0.26],
			[0.32, 0.5],
			[0.7, 0.5],
			[0.94, 0.3]
		]
	],
	'-': [
		[
			[0.1, 0.5],
			[0.9, 0.5]
		]
	],
	'.': [
		[
			[0.42, 0.98],
			[0.58, 0.98]
		]
	],
	'!': [
		[
			[0.5, 0],
			[0.5, 0.66]
		],
		[
			[0.5, 0.96],
			[0.5, 1]
		]
	],
	'?': [
		[
			[0.06, 0.22],
			[0.3, 0],
			[0.68, 0],
			[0.94, 0.24],
			[0.5, 0.56],
			[0.5, 0.68]
		],
		[
			[0.5, 0.96],
			[0.5, 1]
		]
	],
	' ': []
};

/** Everything the face can draw. Anything else is rendered as a space. */
export const ALPHABET = Object.keys(PEN)
	.filter((k) => k !== ' ')
	.join('');

/** Glyph box and spacing for a face. Width is odd so stems and apexes can centre. */
export function metrics(face: Face) {
	const h = clamp(face.size, FACE_LIMITS.size[0], FACE_LIMITS.size[1]);
	const w = 2 * Math.round((h * 0.62 - 1) / 2) + 1;
	// a stroke cannot be thicker than the counter it has to leave behind
	const t = clamp(
		face.weight,
		FACE_LIMITS.weight[0],
		Math.max(1, Math.min(FACE_LIMITS.weight[1], w - 2))
	);
	const gap = Math.max(2, Math.round(w * 0.32));
	return { w, h, t, gap };
}

/** Bresenham, one cell thick. */
function line(g: Uint8Array, w: number, h: number, x0: number, y0: number, x1: number, y1: number) {
	const dx = Math.abs(x1 - x0);
	const dy = -Math.abs(y1 - y0);
	const sx = x0 < x1 ? 1 : -1;
	const sy = y0 < y1 ? 1 : -1;
	let err = dx + dy;
	for (;;) {
		if (x0 >= 0 && x0 < w && y0 >= 0 && y0 < h) g[y0 * w + x0] = 1;
		if (x0 === x1 && y0 === y1) break;
		const e2 = 2 * err;
		if (e2 >= dy) {
			err += dy;
			x0 += sx;
		}
		if (e2 <= dx) {
			err += dx;
			y0 += sy;
		}
	}
}

/** One glyph at one cell thick, in a w x h box. */
function skeleton(ch: string, w: number, h: number) {
	const g = new Uint8Array(w * h);
	const pen = PEN[ch] ?? PEN[' '];
	const X = (u: number) => Math.round(u * (w - 1));
	const Y = (v: number) => Math.round(v * (h - 1));
	for (const poly of pen) {
		if (poly.length === 1) {
			line(g, w, h, X(poly[0][0]), Y(poly[0][1]), X(poly[0][0]), Y(poly[0][1]));
			continue;
		}
		for (let i = 1; i < poly.length; i++) {
			line(g, w, h, X(poly[i - 1][0]), Y(poly[i - 1][1]), X(poly[i][0]), Y(poly[i][1]));
		}
	}
	return g;
}

/** Fatten by t: every set cell becomes a t x t block, growing the box by t - 1. */
function dilate(g: Uint8Array, w: number, h: number, t: number) {
	if (t <= 1) return g;
	const W = w + t - 1;
	const out = new Uint8Array(W * (h + t - 1));
	for (let r = 0; r < h; r++) {
		for (let c = 0; c < w; c++) {
			if (!g[r * w + c]) continue;
			for (let dr = 0; dr < t; dr++) for (let dc = 0; dc < t; dc++) out[(r + dr) * W + c + dc] = 1;
		}
	}
	return out;
}

/** How the word sits on the grid, and whether it fits at all. */
export function layout(word: string, face: Face = DEFAULT_FACE, grid: Grid = DEFAULT_GRID) {
	const m = metrics(face);
	const n = Math.max(1, word.length);
	const span = n * m.w + (n - 1) * m.gap;
	return { ...m, span, fits: span <= grid.cols && m.h <= grid.rows };
}

/**
 * The word, centred on the grid: one byte per cell, 1 where a letter is. A word too
 * big for the grid is drawn from the left rather than silently cropped in the middle,
 * and `layout().fits` says so before it happens.
 */
export function stencil(word: string, face: Face = DEFAULT_FACE, grid: Grid = DEFAULT_GRID) {
	const { w, h, t, gap, span } = layout(word, face, grid);
	const mask = new Uint8Array(grid.rows * grid.cols);
	const x0 = Math.max(0, Math.floor((grid.cols - span) / 2));
	const y0 = Math.max(0, Math.floor((grid.rows - h) / 2));
	for (let li = 0; li < word.length; li++) {
		const g = dilate(skeleton(word[li], w - t + 1, h - t + 1), w - t + 1, h - t + 1, t);
		for (let r = 0; r < h; r++) {
			for (let c = 0; c < w; c++) {
				if (!g[r * w + c]) continue;
				const rr = y0 + r;
				const cc = x0 + li * (w + gap) + c;
				if (rr < grid.rows && cc < grid.cols) mask[rr * grid.cols + cc] = 1;
			}
		}
	}
	return mask;
}

/** FNV-1a over the hex, then xorshift32. Tagged so one digest can drive two streams. */
function stream(hex: string, tag: number) {
	let h = (0x811c9dc5 ^ tag) >>> 0;
	for (let i = 0; i < hex.length; i++) {
		h ^= hex.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	let s = h || 0x9e3779b9;
	return () => {
		s ^= s << 13;
		s >>>= 0;
		s ^= s >>> 17;
		s ^= s << 5;
		s >>>= 0;
		return s;
	};
}

/** Partial Fisher–Yates: k of pool, without replacement. k === pool.length shuffles. */
function pick(pool: number[], k: number, next: () => number) {
	const a = pool.slice();
	const n = Math.min(k, a.length);
	for (let i = 0; i < n; i++) {
		const j = i + (next() % (a.length - i));
		const t = a[i];
		a[i] = a[j];
		a[j] = t;
	}
	return a.slice(0, n);
}

/** the sheet the verdict decides: the output fingerprint */
export const ROGUE = 1;

export type Shares = {
	/** per cell: 0 = unlit, 1..4 = brightness (texture only, never read as data) */
	a: Uint8Array;
	b: Uint8Array;
	c: Uint8Array;
};

/**
 * Punch the word out, deal the rest three ways.
 *
 * `pass` decides sheet B only. B is the output fingerprint, and the tamper this demo
 * exists to show is an output the certifier never fingerprinted — so B is the sheet
 * that either fits the deal or does not. A and C are byte-identical either way.
 *
 * B being on the table before the verdict costs nothing: until the verdict lands
 * `pass` is true and B is drawn as dealt, which is exactly what an honest run looks
 * like. It rearranges at the moment of the verdict, not before it.
 *
 * C is the model commitment, and it is the backing the other two register onto — the
 * thing committed in advance is the thing that does not move, which is why the
 * verdict does not touch it.
 */
export function build(
	req: string,
	rsp: string,
	model: string,
	pass: boolean,
	mask: Uint8Array
): Shares {
	const N = mask.length;
	const nDeal = stream(req, 1);
	const nMiss = stream(rsp, 9);
	const lvl = [stream(req, 17), stream(rsp, 18), stream(model, 19)];

	// 1. the word is punched out first: these cells belong to nobody
	const ground: number[] = [];
	for (let i = 0; i < N; i++) if (!mask[i]) ground.push(i);

	// 2. and everything left is dealt round-robin, a third each
	const dealt = pick(ground, ground.length, nDeal);
	const sets: number[][] = [[], [], []];
	for (let k = 0; k < dealt.length; k++) sets[k % 3].push(dealt[k]);

	if (!pass) {
		// A sheet that was not the one dealt: the same number of cells, drawn from the
		// whole grid with no regard for the deal or for the word. Same density, so B
		// alone is indistinguishable — it just no longer fits.
		const all = Array.from({ length: N }, (_, i) => i);
		sets[ROGUE] = pick(all, sets[ROGUE].length, nMiss);
	}

	const out = [new Uint8Array(N), new Uint8Array(N), new Uint8Array(N)];
	for (let s = 0; s < 3; s++) for (const i of sets[s]) out[s][i] = 1 + (lvl[s]() % 4);
	return { a: out[0], b: out[1], c: out[2] };
}

/**
 * ── registration ────────────────────────────────────────────────────────────────
 *
 * Three strips, slid along Y until their patterns land on the same rows. There is NO
 * APERTURE — you cannot crop three pieces of film, you can only stack them and move
 * them — so the composite is the whole pile and nothing here is trimmed.
 *
 * The pile is `rows + 2 * slack` tall and divides into three horizontal regions:
 *
 *   top margin     slack rows, inked by A and C between them
 *   the band       rows rows, inked by all three: this is the picture
 *   bottom margin  slack rows, inked by B and C between them
 *
 * Each margin is a partition, exactly like the ground is: every cell in the top
 * margin goes to A or to C and never both, so the two together cover it completely
 * and neither has to be solid. `share` is how much of that goes to the sliding strip.
 * The backing carries the rest, which at a half-and-half split is half the ink a
 * solid margin needed and reads as a field rather than a slab.
 *
 * The strips fall out of that with no freedom left in them:
 *
 *   A   [ top margin | pattern ]     rows + slack tall, sits at pile row 0
 *   B   [ pattern | bottom margin ]  rows + slack tall, sits at pile row slack
 *   C   [ margin | pattern | margin ] full height, does not move
 *
 * ── what that costs ─────────────────────────────────────────────────────────────
 *
 * The earlier cut hid where each pattern sat by surrounding it with decoy at the
 * pattern's own density, and picked the position from a digest. Neither survives:
 * the extents are now structural, so A's pattern is always directly below its margin
 * and B's directly above its own. Registration is aligning three edges, not finding
 * three positions.
 *
 * `share` is the one dial left on that. At 0.5 the margins are denser than the
 * pattern band, so each sliding strip has a visible step where one ends and the other
 * begins. At 1/3 the margin matches the band and the strip is uniform top to bottom —
 * the band is no longer advertised by its own density, though the strip's ends still
 * say where it must go. Looks against hiding, and the hiding was never worth much:
 * a handful of slides, and every strip carries the word's ghost regardless.
 */
export type Strips = {
	/** one strip per sheet; strip i is `heights[i]` rows tall */
	cells: Uint8Array[];
	heights: number[];
	/** where each pattern starts inside its own strip */
	offsets: number[];
	/** the depth of each margin, and the range a sliding strip moves over */
	slack: number;
	/** the pile's height: grid.rows + 2 * slack */
	pile: number;
	/** index of the backing strip, which is full height and does not move */
	backing: number;
};

const BACKING = 2; // the model fingerprint: committed in advance, so it is the anchor

export function strips(
	sh: Shares,
	grid: Grid,
	slack: number,
	seed: string,
	/** the fraction of each margin carried by the sliding strip rather than the backing */
	share = 0.5
): Strips {
	const { rows, cols } = grid;
	const pile = rows + 2 * slack;
	const src = [sh.a, sh.b, sh.c];
	const heights = [rows + slack, rows + slack, pile];
	// A's pattern sits below its margin, B's above its own, C's in the middle
	const offsets = [slack, 0, slack];
	const cells = [0, 1, 2].map((i) => new Uint8Array(heights[i] * cols));

	for (let i = 0; i < 3; i++) {
		for (let r = 0; r < rows; r++) {
			for (let c = 0; c < cols; c++) {
				cells[i][(offsets[i] + r) * cols + c] = src[i][r * cols + c];
			}
		}
	}

	// Deal each margin between its sliding strip and the backing, cell by cell, the
	// same way the ground is dealt. Neither carries a solid block; together they
	// leave nothing uncovered.
	const nDeal = stream(seed, 31);
	const lvl = stream(seed, 51);
	for (let side = 0; side < 2; side++) {
		const slider = side; // A owns the top margin, B the bottom
		for (let r = 0; r < slack; r++) {
			const sliderRow = side === 0 ? r : rows + r;
			const backRow = side === 0 ? r : slack + rows + r;
			for (let c = 0; c < cols; c++) {
				const v = 1 + (lvl() % 4);
				if (nDeal() % 100000 < share * 100000) cells[slider][sliderRow * cols + c] = v;
				else cells[BACKING][backRow * cols + c] = v;
			}
		}
	}

	return { cells, heights, offsets, slack, pile, backing: BACKING };
}

/** How far strip i can be slid: zero for the backing, which is already full height. */
export function travel(st: Strips, i: number) {
	return Math.max(0, st.pile - st.heights[i]);
}

/** Where each strip's pattern lands, given how far the strip has been slid down. */
export function seats(st: Strips, shift: number[]) {
	return st.offsets.map((o, i) => o + Math.max(0, Math.min(travel(st, i), Math.round(shift[i]))));
}

/** The slide that brings each pattern onto the backing's band. */
export function homes(st: Strips) {
	return st.offsets.map((o, i) => (i === st.backing ? 0 : st.offsets[st.backing] - o));
}

/**
 * The whole pile: every strip at its current slide, nothing cropped. Returns one byte
 * per cell — 0 unlit, otherwise a bitmask of which strips lit it (1 = A, 2 = B,
 * 4 = C), which is what the composite is coloured by.
 */
export function composite(st: Strips, shift: number[], grid: Grid) {
	const { cols } = grid;
	const out = new Uint8Array(st.pile * cols);
	for (let i = 0; i < 3; i++) {
		const d = Math.max(0, Math.min(travel(st, i), Math.round(shift[i])));
		const strip = st.cells[i];
		for (let r = 0; r < st.heights[i]; r++) {
			for (let c = 0; c < cols; c++) {
				if (strip[r * cols + c]) out[(d + r) * cols + c] |= 1 << i;
			}
		}
	}
	return out;
}

/**
 * The stack: a cell is lit if any sheet handed in lit it. That is the entire read —
 * no threshold and no second resolution, which is why what you see is what the
 * sheets are. No mask below this line.
 */
export function union(layers: Uint8Array[], n: number) {
	const out = new Uint8Array(n);
	for (let i = 0; i < n; i++) out[i] = layers.some((l) => l[i]) ? 1 : 0;
	return out;
}

/** Measured, not asserted — /lab prints these so the scheme can be checked by eye. */
export function stats(s: Partial<Shares>, mask: Uint8Array) {
	const N = mask.length;
	const layers = [s.a, s.b, s.c].filter(Boolean) as Uint8Array[];
	const groundN = mask.reduce((n, v) => n + (v ? 0 : 1), 0);
	const lit = layers.map((l) => l.reduce((n, v) => n + (v ? 1 : 0), 0));

	const u = union(layers, N);
	let litL = 0;
	let litF = 0;
	let nL = 0;
	let nF = 0;
	for (let i = 0; i < N; i++) {
		if (mask[i]) {
			nL++;
			if (u[i]) litL++;
		} else {
			nF++;
			if (u[i]) litF++;
		}
	}

	// how much of each single sheet falls inside the word — the ghost, measured
	const ghost = layers.map((l) => {
		let n = 0;
		let t = 0;
		for (let i = 0; i < N; i++)
			if (mask[i]) {
				t++;
				if (l[i]) n++;
			}
		return t ? n / t : 0;
	});

	return {
		lit,
		density: lit.map((n) => n / N),
		ofGround: lit.map((n) => (groundN ? n / groundN : 0)),
		ghost,
		ground: groundN,
		word: N - groundN,
		stacked: { letters: nL ? litL / nL : 0, field: nF ? litF / nF : 0 },
		layers: layers.length
	};
}
