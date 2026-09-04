<script lang="ts">
	/**
	 * Interlock — the case.
	 *
	 * Driven by demo_server.py (interlock/app), which orchestrates a Raspberry Pi
	 * holding the certified wire and a Spark running the model behind a PolarFire
	 * FPGA that fingerprints every packet in both directions. Every value rendered
	 * here arrived over that wire. The only thing this file invents is the pacing.
	 *
	 * ...unless the board is off, in which case it invents everything — see
	 * `simulate()`. That path exists so the demo survives a dead board, and it says
	 * so on screen in a strip that is never dismissible, because a rehearsal that
	 * cannot be told apart from a run is worse than no rehearsal.
	 *
	 * WHO IS READING THIS. A panel in the lid of a case, seen standing up from a few
	 * feet by someone who has not been briefed. One screen, no scroll, and nothing
	 * static: no title, no headings, no explainer copy. The picture IS the
	 * explanation — the question itself rides the cable, seals into its real
	 * ciphertext bytes and opens again; the certifier stamps a fingerprint out of
	 * each passing packet and RACKS IT INSIDE ITS OWN BODY; only when the proof
	 * begins do the films slide into the GPU cluster and combine there, beside the
	 * model's own commitment, because that is where the proof actually runs. The
	 * numbers an engineer wants are on the console and in /lab.
	 */
	import {
		build,
		stencil,
		strips,
		DEFAULT_FACE,
		DEFAULT_GRID,
		DEFAULT_WORD
	} from '$lib/fingerprint-shares';
	import { cn } from '$lib/utils';
	import { resolve } from '$app/paths';
	import { io, type Socket } from 'socket.io-client';
	import { onDestroy, onMount } from 'svelte';

	type Verdict = { verdict: string; U: string; verify: string; keybind: string };

	let socket: Socket | undefined;
	let connected = $state(false);
	let agentUp = $state(false);
	let wireOk = $state(false);
	let wireFault = $state('');
	/** the server's own mode label, kept verbatim for the console and the tooltip */
	let modeRaw = $state('');
	let busy = $state(false);

	/** the tamper switch: sticky, and only the operator flips it */
	let armed = $state(false);
	/** and whether the run being shown was actually the tampered one */
	let tampered = $state(false);
	/** force the simulator even with a healthy board */
	let simForce = $state(false);
	/** whether the run being shown came out of `simulate()` rather than the wire */
	let simRun = $state(false);

	let caption = $state('Ready — press PROMPT to send a question.');
	let phase = $state<'idle' | 'req' | 'gen' | 'rsp' | 'prove' | 'done'>('idle');
	let hotFpga = $state(false);
	let hotSpark = $state(false);

	/**
	 * Where the payload is, along the cable: your machine, the certifier, the mouth
	 * of the GPU cluster. The payload is the TEXT — the question and later the
	 * answer ride the wire themselves, scrambling into their real ciphertext bytes
	 * at the moment they are sealed and back at the moment they are opened.
	 */
	// One y for the whole journey: the card rides the cable's height everywhere,
	// including inside the cluster -- it moves left to right and never bobs.
	const STOP = [
		{ x: 11, y: 33 }, // the corner of the L: where the cryptography happens
		{ x: 39, y: 33 },
		{ x: 81.5, y: 33 }
	];
	/** the web: requests come DOWN from it, answers climb back up and vanish */
	const WEB = { x: 11, y: 23 };
	let pktX = $state(STOP[0]);
	let pktText = $state('');
	let pktSealed = $state(false);
	let pktShown = $state(false);

	let reqFp = $state<string | null>(null);
	let rspFp = $state<string | null>(null);
	let modelFp = $state<string | null>(null);
	/**
	 * The two fingerprints the certifier mints, as FILMS carrying the strip's own
	 * cell pattern. Each is stamped as the packet passes and then HELD inside the
	 * certifier — the input film through generation, both films through delivery —
	 * because until the proof runs, the certifier is the only party holding them.
	 * When the proof begins they slide together into the GPU cluster (the proof
	 * runs on the Spark, not in the cable) and are absorbed into the register.
	 * 'held' is racked inside the certifier; 'docked' is arrived over the
	 * register; 'gone' is absorbed into it (the register then shows the strip).
	 */
	type Chip = 'hidden' | 'held' | 'drop' | 'run' | 'docked' | 'gone';
	let chipA = $state<Chip>('hidden');
	let chipB = $state<Chip>('hidden');
	/** the model's own commitment is revealed in the cluster when the proof begins */
	let modelShown = $state(false);
	/** the proof is in flight: the register hunts for the seat until this clears */
	let proving = $state(false);
	/** the two sheets' hunt animations, releasable one at a time: each is let
	 * go at the peak of its own swing, where its velocity is already zero, so
	 * the glide home starts from rest instead of yanking a moving sheet */
	let huntA = $state(false);
	let huntB = $state(false);
	/** the landing glide's start offset per sheet (the peak it was released
	 * at), or null when not landing -- feeds --land-from on the sheet */
	let landA = $state<string | null>(null);
	let landB = $state<string | null>(null);
	/** the stack has touched down: crossfade the three sheets into ONE
	 * composite svg -- a single raster pass cannot disagree with itself */
	let settled = $state(false);
	/** the hunt periods and amplitudes -- MUST match ilk-hunt-a / ilk-hunt-b
	 * in layout.css */
	const HUNT_TA = 1700;
	const HUNT_TB = 2200;
	const HUNT_AMPA = 30;
	const HUNT_AMPB = 35;
	/** when the hunt began: the verdict must let it run ~3s before settling */
	let proveT0 = 0;
	let promptText = $state('');
	let answer = $state('');
	let verdict = $state<Verdict | null>(null);
	/** a run that died: the server's own words, shown until the next run starts */
	let runFault = $state('');
	/** the closing tableau: the pile departs and the sentence takes its place */
	let finale = $state(false);
	/** the cluster is chewing on the decrypted request: the ghost wears a busy bar */
	let processing = $state(false);
	/** the word on the card while a key operation runs */
	let sealing = $state<'' | 'encrypting' | 'decrypting'>('');
	/** kill the card's transition for one frame, for pixel-identical swaps */
	let pktInstant = $state(false);
	/** what the traveling card IS right now, printed on its eyebrow */
	let pktRole = $state<'request' | 'response'>('request');
	/** the request's ghost: holds the decrypted question while the model runs */
	let gqShown = $state(false);
	let gqText = $state('');
	let gqY = $state(33);

	const short = (h: string | null, n = 8) => (h ? h.slice(0, n).toUpperCase() : '—');
	/** the run's narration, on the console rather than on the lid */
	const log = (s: string) => console.debug('[interlock]', s);

	let pending: Record<string, any> = {};
	/** the run's answer beat, held for the choreography to await */
	let answerD: any = null;
	let chain: Promise<unknown> = Promise.resolve();
	let generation = 0;
	const queue = (fn: () => Promise<void>) => (chain = chain.then(fn).catch(console.error));

	/** the transport's pause: playback holds between beats while this is up */
	let paused = $state(false);
	/** the control bar hides behind an invisible latch in the top-left corner:
	 * the stage is the screen, and the operator knows where the door is */
	let menuOpen = $state(false);
	/** Sleep that resolves to false if a newer run started while we waited.
	 * While paused, it holds between beats instead of returning. */
	async function step(ms: number, gen: number) {
		await new Promise((r) => setTimeout(r, ms));
		while (paused && gen === generation) {
			await new Promise((r) => setTimeout(r, 120));
		}
		return gen === generation;
	}

	const frozen = () =>
		typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

	/** Wait for a wire fact that may not have arrived yet. The choreography
	 * starts at beat:start, while the real round trip is still in flight, and
	 * HOLDS at the natural beat until the fact lands -- a sim run passes a
	 * complete `d` and never waits. Returns null when the run is over or
	 * faulted, and reads the live `pending` because the socket handlers
	 * replace that object as facts arrive. */
	async function waitFact(d: any, key: string, gen: number) {
		while (gen === generation && !runFault && d[key] == null && pending[key] == null) {
			if (!(await step(120, gen))) return null;
		}
		if (gen !== generation || runFault) return null;
		return d[key] ?? pending[key];
	}
	/** and the response itself, which only exists once the wire delivers it */
	async function waitAnswer(gen: number) {
		while (gen === generation && !runFault && !answerD) {
			if (!(await step(150, gen))) return null;
		}
		return gen === generation && !runFault ? answerD : null;
	}

	// ── the seal/open animation ────────────────────────────────────────────────
	/**
	 * The payload text boiling into different bytes, one character at a time behind
	 * a scanning head. The cipher form is the run's REAL ciphertext (ct_in/ct_out
	 * off the wire), not decoration — what appears on the cable is a window onto
	 * the bytes that actually crossed it.
	 */
	let scrRaf = 0;
	const HEXCHARS = '0123456789ABCDEF';
	function scrambleTo(target: string, ms = 900) {
		cancelAnimationFrame(scrRaf);
		if (frozen()) {
			pktText = target;
			return;
		}
		const from = pktText;
		const t0 = performance.now();
		const tick = (now: number) => {
			const k = Math.min(1, (now - t0) / ms);
			// the string's LENGTH morphs smoothly between the two forms, so the
			// wrap point drifts as the head advances instead of the whole tail
			// hanging on and snapping away at the end
			const len = Math.round(from.length + (target.length - from.length) * k);
			const head = Math.floor(k * Math.max(len, target.length));
			let out = target.slice(0, Math.min(head, len));
			while (out.length < len) {
				const i = out.length;
				out +=
					i < head + 3
						? HEXCHARS[(Math.random() * 16) | 0]
						: (from[i] ?? HEXCHARS[(Math.random() * 16) | 0]);
			}
			pktText = k >= 1 ? target : out;
			if (k < 1) scrRaf = requestAnimationFrame(tick);
		};
		scrRaf = requestAnimationFrame(tick);
	}

	/** the response arrives the way a model produces one: character by character */
	function typeTo(target: string, ms = 3200) {
		cancelAnimationFrame(scrRaf);
		if (frozen()) {
			pktText = target;
			return;
		}
		const t0 = performance.now();
		const tick = (now: number) => {
			const k = Math.min(1, (now - t0) / ms);
			pktText = target.slice(0, Math.round(k * target.length));
			if (k < 1) scrRaf = requestAnimationFrame(tick);
		};
		scrRaf = requestAnimationFrame(tick);
	}

	/** the payload's ciphertext, cut to the plaintext's own length: the sealed
	 * card wraps to the same lines the words did, so nothing grows or shrinks */
	function cipherOf(hex: string | undefined, n: number) {
		let h = (hex || '').toUpperCase().replace(/[^0-9A-F]/g, '');
		if (!h) h = 'A7F03C9E5B21D48C6E90F17B24A8D35E';
		const k = Math.max(10, n);
		while (h.length < k) h += h;
		return '0x' + h.slice(0, k);
	}

	/** everything shown on a card must fit its hard two-line well */
	const clip = (s: string, n = 42) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

	/** Everything a run puts on the screen. The switches are not part of a run. */
	function clearRun() {
		phase = 'idle';
		caption = 'Ready — press PROMPT to send a question.';
		hotFpga = hotSpark = false;
		pktShown = pktSealed = false;
		pktX = STOP[0];
		pktText = '';
		cancelAnimationFrame(scrRaf);
		reqFp = rspFp = null;
		chipA = chipB = 'hidden';
		modelShown = false;
		proving = false;
		huntA = huntB = false;
		landA = landB = null;
		settled = false;
		finale = false;
		proveT0 = 0;
		processing = false;
		sealing = '';
		pktInstant = false;
		gqShown = false;
		gqText = '';
		gqY = 33;
		pktRole = 'request';
		runFault = '';
		promptText = '';
		answer = '';
		verdict = null;
		tampered = false;
		simRun = false;
		busy = false;
		pending = {};
		answerD = null;
	}

	/** the Reset button: the run, the switches, and anything armed on the server */
	function resetAll() {
		generation += 1; // orphans an in-flight animation, simulated or not
		chain = Promise.resolve();
		clearRun();
		armed = false;
		socket?.emit('demo:reset');
	}

	async function runRequest(d: any, gen: number) {
		phase = 'req';
		pktSealed = false;
		pktRole = 'request';
		pktInstant = true;
		pktX = WEB;
		pktText = asked || promptText;
		if (!(await step(60, gen))) return;
		pktInstant = false;
		pktShown = true;
		caption = 'A question comes down from the web…';
		if (!(await step(1100, gen))) return;

		// down the vertical of the L to the corner, where the cryptography lives
		pktX = STOP[0];
		if (!(await step(1900, gen))) return;

		sealing = 'encrypting';
		caption = 'It is encrypted before it touches the cable.';
		// the real ciphertext may still be in flight: hold at the corner,
		// encrypting, until the wire hands it over
		const ctIn = await waitFact(d, 'ct_in', gen);
		if (ctIn == null) return;
		pktSealed = true;
		scrambleTo(cipherOf(ctIn, pktText.length), 1800);
		log(`SEAL   ${d.n_tokens ?? pending.n_tokens} tok → ${ctIn.length / 2}B`);
		if (!(await step(2600, gen))) return;
		sealing = '';

		pktX = STOP[1];
		hotFpga = true;
		if (!(await step(1900, gen))) return;
		reqFp = await waitFact(d, 'request_digest', gen);
		if (reqFp == null) return;
		// the film drops into the certifier's lower slot and STAYS there: traffic
		// moves on, the evidence does not
		chipA = 'held';
		caption = 'The certifier stamps a fingerprint off the sealed bytes and keeps it.';
		log(`CERT   inbound  ${short(reqFp, 16)} audit=${(d.request_audit ?? pending.request_audit) ? 'PASS' : 'FAIL'}`);
		if (!(await step(frozen() ? 30 : 2400, gen))) return;

		pktX = STOP[2];
		hotFpga = false;
		hotSpark = true;
		if (!(await step(2000, gen))) return;
		// the sealed request STAYS in the cluster: the next beat works on it in
		// place, processing it into the answer
	}

	async function runGenerate(d: any, text: string, gen: number) {
		// The answer exists in the clear HERE first: the cluster generates it,
		// and only then does it get encrypted for the trip back. Showing that is
		// honest -- the datacenter necessarily holds the plaintext it produced --
		// and it is the beat that makes "encrypted on the wire" legible: you see
		// the words exist, then boil into bytes, then travel.
		phase = 'gen';
		pktX = STOP[2];
		pktShown = true;
		// beat one: the request is DECRYPTED first -- the model cannot read
		// ciphertext, and only the datacenter holds this session's key
		sealing = 'decrypting';
		pktSealed = false;
		scrambleTo(clip(asked || promptText), 2200);
		caption = 'Inside the cluster the request is decrypted…';
		if (!(await step(3200, gen))) return;
		sealing = '';

		// beat two: the plaintext request hands off to its ghost (pixel-identical,
		// so the swap is invisible), slides aside, and the model RUNS on it
		gqText = pktText;
		gqY = 33;
		gqShown = true;
		pktInstant = true;
		pktShown = false;
		if (!(await step(60, gen))) return;
		gqY = 24;
		processing = true;
		caption = '…the model runs on the decrypted request…';
		if (!(await step(frozen() ? 30 : 1400, gen))) return;

		// beat three: a NEW box -- the response -- GENERATED character by
		// character, the way a model actually produces one. No word for it:
		// the thing itself is the caption.
		pktText = '';
		pktRole = 'response';
		pktX = { x: 81.5, y: 47 };
		if (!(await step(60, gen))) return;
		pktInstant = false;
		pktShown = true;
		typeTo(clip(text), 1400);
		caption = '…and a response is generated…';
		if (!(await step(2200, gen))) return;
		processing = false;

		// the request has served; the response takes the center
		gqShown = false;
		pktX = STOP[2];
		if (!(await step(1300, gen))) return;

		sealing = 'encrypting';
		const ctOut = await waitFact(d, 'ct_out', gen);
		if (ctOut == null) return;
		pktSealed = true;
		scrambleTo(cipherOf(ctOut, clip(text).length), 1800);
		caption = '…and it is encrypted for the trip back.';
		if (!(await step(2800, gen))) return;
		sealing = '';
	}

	async function runReturn(d: any, text: string, gen: number) {
		phase = 'rsp';
		pktX = STOP[1];
		hotSpark = false;
		hotFpga = true;
		if (!(await step(1900, gen))) return;

		rspFp = await waitFact(d, 'response_digest', gen);
		if (rspFp == null) return;
		// second film racks above the first: the certifier now holds both
		chipB = 'held';
		caption = 'The answer is fingerprinted on the way out — the certifier holds both.';
		log(`CERT   outbound ${short(rspFp, 16)} audit=${(d.response_audit ?? pending.response_audit) ? 'PASS' : 'FAIL'}`);
		if (!(await step(frozen() ? 30 : 2400, gen))) return;

		pktX = STOP[0];
		hotFpga = false;
		if (!(await step(1900, gen))) return;
		pktSealed = false;
		sealing = 'decrypting';
		scrambleTo(clip(text), 1800);
		caption = 'Decrypted at the corner — only your machine holds the key.';
		if (!(await step(2600, gen))) return;
		sealing = '';
		if (!(await step(900, gen))) return;

		// up the vertical, home to the web -- and it STAYS: the delivered answer
		// parks at the left edge of the story for the rest of the run
		pktX = WEB;
		if (!(await step(1900, gen))) return;
		answer = text;
		log(`OPEN   ${text}`);
	}

	async function runProve(gen: number) {
		phase = 'prove';
		hotSpark = true;
		// beat one: a breath first (the response has just parked), then the
		// cluster shows its hand -- the commitment to the model it promised to
		// run, fixed before anything was sent -- and the reveal gets a full
		// second to be seen before anything else moves
		caption = 'The cluster reveals the fingerprint of the model it promised to run.';
		if (!(await step(frozen() ? 30 : 2300, gen))) return;
		modelShown = true;
		if (!(await step(frozen() ? 30 : 7200, gen))) return;

		// beat two: both films ride ONE road -- an L, right out of the
		// certifier's wall at the lower rack's height, then up into the
		// cluster from below. The input departs straight from its slot; the
		// output advances down into the lane it vacated (the rack's own
		// push-down move) and follows the identical road behind it. Leg
		// durations are matched to one cruise speed (linear along the lane,
		// ease-out up the rise), so nothing slows down at the bend.
		caption = 'The certifier sends its two fingerprints into the cluster.';
		const t = (ms: number) => step(frozen() ? 30 : ms, gen);
		// the two films move TOGETHER, a conveyor with a fixed gap: the output
		// starts advancing while the input is still clearing the lane, and is
		// running the lane while the input rises -- nobody waits for anybody,
		// and the spacing keeps them from ever touching.
		chipA = 'run';
		if (!(await t(400))) return;
		chipB = 'drop';
		if (!(await t(400))) return;
		chipB = 'run';
		if (!(await t(100))) return;
		chipA = 'docked';
		if (!(await t(550))) return;
		// the moment a film lands it is absorbed and the sliding is already
		// running: travel and hunt are one continuous motion, with no pause
		// between them. `proving` stays up until the verdict lands however long
		// that takes -- the animation is paced by the prover, not a timer.
		chipA = 'gone';
		proving = true;
		huntA = huntB = true;
		landA = landB = null;
		settled = false;
		proveT0 = performance.now();
		caption = 'Now it has to prove all three came from the promised model.';
		if (!(await t(250))) return;
		chipB = 'docked';
		if (!(await t(550))) return;
		chipB = 'gone';
	}

	async function runVerdict(d: any, gen: number) {
		// A verdict for a run that has already been superseded -- Reset pressed, or a
		// second Prompt -- must not repaint the panel it is no longer about.
		if (gen !== generation) return;
		// the sliding is the drama: even when the prover has already ruled, the
		// sheets get their five seconds of hunting before the word is allowed
		// to develop
		if (proving && proveT0 && !frozen()) {
			const left = 5000 - (performance.now() - proveT0);
			if (left > 0 && !(await step(left, gen))) return;
			// the wind-down: each sheet finishes the swing it is on and is
			// released at the PEAK of that swing, where its velocity is already
			// zero. It is handed a one-shot LANDING animation whose start value
			// is that exact peak (--land-from), so the sheet visibly SLIDES
			// from where it was into true register -- continuity by
			// construction, never a snap. The two sheets stop on their own
			// beats; the word develops as the second one lands, and only then
			// does the verdict paint.
			const release = (T: number, amp: number, firstNeg: boolean) => {
				const el = performance.now() - proveT0;
				const half = T / 2;
				const wait = (T / 4 - (el % half) + half) % half;
				const firstHalf = (el + wait) % T < half;
				return { wait, from: `${(firstHalf === firstNeg ? -1 : 1) * amp}%` };
			};
			const ra = release(HUNT_TA, HUNT_AMPA, true);
			const rb = release(HUNT_TB, HUNT_AMPB, false);
			const aFirst = ra.wait <= rb.wait;
			if (!(await step(Math.min(ra.wait, rb.wait), gen))) return;
			if (aFirst) {
				huntA = false;
				landA = ra.from;
			} else {
				huntB = false;
				landB = rb.from;
			}
			if (!(await step(Math.abs(rb.wait - ra.wait), gen))) return;
			if (aFirst) {
				huntB = false;
				landB = rb.from;
			} else {
				huntA = false;
				landA = ra.from;
			}
			// the second sheet's landing glide (1100ms) touches down
			if (!(await step(1100, gen))) return;
		}
		// touchdown: crossfade the three landed sheets into one composite
		// drawing of the same picture. One svg, one raster pass, perfect
		// registration -- the fade reads as the stack clicking into focus.
		settled = true;
		verdict = d.result as Verdict;
		phase = 'done';
		proving = false;
		huntA = huntB = false;
		hotSpark = false;
		const ok = verdict?.verdict === 'PASS';
		caption = ok
			? 'It did. The three fingerprints line up.'
			: 'It could not. That answer never went past the certifier.';
		log(`PROOF  ${verdict?.verdict}  verify=${verdict?.verify}  binding=${verdict?.keybind}`);
		busy = false;
		// the finale, PASS only: let the developed word stand for a beat, then
		// the pile bows out through the floor and the sentence takes the stage
		if (ok) {
			if (!(await step(frozen() ? 30 : 2000, gen))) return;
			finale = true;
		}
	}

	onMount(() => {
		socket = io('/demo', { transports: ['websocket'] });
		socket.on('connect', () => (connected = true));
		socket.on('disconnect', () => (connected = false));
		socket.on('hello', (d: any) => {
			agentUp = !!d.agent;
			wireOk = !!d.wire;
			if (d.mode) modeRaw = d.mode;
			if (d.prompt) SIM_PROMPT = d.prompt;
			if (d.model_fp) modelFp = d.model_fp;
		});
		socket.on('wire:agent', (d: any) => {
			agentUp = !!d.up;
			if (!d.up) wireOk = false;
		});
		socket.on('wire:ready', () => {
			wireOk = true;
			wireFault = '';
		});
		// The board goes quiet a few hours after power-up. The strip says so, and the
		// simulator picks itself up, so nobody presses PROMPT and finds out twelve
		// seconds later in front of an audience.
		socket.on('wire:fault', (d: any) => {
			wireOk = false;
			wireFault = d?.error ?? 'no sync stream';
			log(`FAULT  ${wireFault}`);
		});
		socket.on('beat:armed', () => (tampered = true));
		socket.on('beat:reset', clearRun);
		socket.on('beat:start', (d: any) => {
			generation += 1; // orphans any in-flight animation from a previous run
			chain = Promise.resolve();
			clearRun();
			// The server stamps the run it started (a hardware-panel run may be
			// tampered with this browser's switch off); the local switch is only
			// the fallback for older servers.
			tampered = d?.tampered ?? armed;
			paused = false;
			promptText = d?.prompt ?? '';
			busy = true;
			// The choreography starts NOW, so the press answers instantly. The
			// prompt text is already known; every beat that needs a wire fact
			// (ciphertext, digest, the answer itself) holds at its natural
			// place until the fact lands -- the opening beats occupy the same
			// seconds the real round trip is using, so nothing is faked and
			// nothing sits blank.
			const gen = generation;
			queue(async () => {
				await runRequest(pending, gen);
				const a = await waitAnswer(gen);
				if (!a) return;
				const text = !a.ok
					? 'could not open it'
					: tampered
						? '<TAMPERED RESPONSE>'
						: a.text;
				await runGenerate(pending, text, gen);
				await runReturn(pending, text, gen);
				if (a.ok) await runProve(gen);
			});
		});
		socket.on('beat:tokenized', (d: any) => (pending = { ...pending, ...d }));
		socket.on('beat:certified', (d: any) => (pending = { ...pending, ...d }));
		socket.on('beat:answer', (d: any) => {
			// the choreography launched at beat:start is already waiting on this
			// (a run that failed to open has no proof coming: the server's
			// beat:error follows, and the pipeline aborts on the fault)
			answerD = d;
		});
		socket.on('beat:verdict', (d: any) => {
			if (d.model_fp) modelFp = d.model_fp;
			const gen = generation;
			queue(() => runVerdict(d, gen));
		});
		socket.on('beat:proof_status', (d: any) => log(`PROVE  ${String(d.line).trim()}`));
		// A run that dies must SAY so. This used to write a caption that no longer
		// renders, which read as "I pressed PROMPT and nothing happened" -- the
		// worst possible answer. The banner strip carries it now.
		socket.on('wire:busy', () => {
			runFault =
				'the wire is still finishing the previous run — give it a moment and press PROMPT again, or press RESET';
			log('BUSY   run refused: previous run still holds the wire');
		});
		socket.on('beat:error', (d: any) => {
			runFault = d.error ?? 'something went wrong on the wire — press PROMPT to try again';
			log(`ERROR  ${d.error ?? ''}`);
			proving = false;
			huntA = huntB = false;
			landA = landB = null;
			settled = false;
			busy = false;
			// A halt that did not take leaves the machine up, so the panel comes back
			// rather than sitting behind a "powering off" curtain that will never lift.
			goingDown = false;
		});
		socket.on('beat:shutdown', () => (goingDown = true));
	});
	onDestroy(() => socket?.disconnect());

	// ── running it ─────────────────────────────────────────────────────────────
	/** the wire is only live when all three of these hold */
	const liveWire = $derived(connected && agentUp && wireOk);
	const simMode = $derived(simForce || !liveWire);

	function start() {
		if (busy || goingDown) return;
		if (simMode) {
			simulate(armed);
			return;
		}
		socket?.emit('demo:reset');
		setTimeout(() => socket?.emit(armed ? 'demo:tamper' : 'demo:run', {}), 250);
	}

	// ── the simulator ──────────────────────────────────────────────────────────
	/**
	 * A run with no hardware in it.
	 *
	 * The board goes quiet a few hours after power-up and the Pi has to be on the
	 * Spark's AP, so there are plenty of ways to open the case and find nothing on
	 * the wire. This drives the same four beat functions the socket drives, on the
	 * same pacing, so what is rehearsed is what will run -- and it fabricates its
	 * digests rather than replaying a captured pair, because a recording of a real
	 * run is exactly the thing that could be passed off as one.
	 */
	/**
	 * The question the simulator asks, and the answer it invents. Both are only a
	 * FALLBACK: a connected server sends its own PROMPT in `hello` and that wins.
	 * Two copies of one string drift the moment somebody sets PROMPT in the
	 * environment, and the copy that drifts is the one on screen when the board is
	 * dead -- which is exactly when nobody can check it against a real run.
	 */
	let SIM_PROMPT = $state('Question: What does IAEA stand for?\nAnswer:');
	const SIM_ANSWER = 'The International Atomic Energy Agency';
	const SIM_STATUS = [
		'loading proving key',
		'committing to the token layer',
		'weld: binding ciphertext to the proven tokens',
		'B1/B2 key binding at full strength',
		'folding'
	];

	/**
	 * Forty hex characters that behave like a digest: dependent on everything handed
	 * in, and different every run. FNV-1a into xorshift32 -- crypto.subtle is not
	 * available here, the demo being served over plain http on a LAN address, and
	 * this has no security job to do anyway.
	 */
	function fauxDigest(s: string) {
		let h = 0x811c9dc5 >>> 0;
		for (let i = 0; i < s.length; i++) {
			h ^= s.charCodeAt(i);
			h = Math.imul(h, 0x01000193) >>> 0;
		}
		let out = '';
		for (let i = 0; i < 40; i++) {
			h ^= h << 13;
			h >>>= 0;
			h ^= h >>> 17;
			h ^= h << 5;
			h >>>= 0;
			out += '0123456789abcdef'[h & 15];
		}
		return out;
	}

	// ── the sim transport ──────────────────────────────────────────────────────
	// The simulated run is SCRUBBABLE: five chapters, each with an entry
	// snapshot (absolute state, applied instantly) and a play function. The
	// transport seeks by snapping to a chapter and playing forward from there;
	// pause holds `step()` between beats.
	type SimCtx = { d: any; text: string; tamper: boolean };
	let simCtx: SimCtx | null = null;
	let simChapter = $state(0);

	function snapCommon(ctx: SimCtx) {
		clearRun();
		busy = true;
		simRun = true;
		tampered = ctx.tamper;
		promptText = SIM_PROMPT;
		pktInstant = true;
		if (!modelFp) modelFp = fauxDigest('simulated-weights');
	}
	const SNAPS: ((ctx: SimCtx) => void)[] = [
		() => {},
		(ctx) => {
			// the sealed request sits in the cluster; the input film is racked
			pktShown = true;
			pktSealed = true;
			pktRole = 'request';
			pktX = STOP[2];
			pktText = cipherOf(ctx.d.ct_in, clip(asked || promptText).length);
			reqFp = ctx.d.request_digest;
			chipA = 'held';
			hotSpark = true;
		},
		(ctx) => {
			SNAPS[1](ctx);
			// the response is generated and sealed, ready for the trip back
			pktRole = 'response';
			pktText = cipherOf(ctx.d.ct_out, clip(ctx.text).length);
		},
		(ctx) => {
			SNAPS[1](ctx);
			// delivered: the open answer parks at the web, both films racked
			hotSpark = false;
			pktRole = 'response';
			pktSealed = false;
			pktX = WEB;
			pktText = clip(ctx.text);
			rspFp = ctx.d.response_digest;
			chipB = 'held';
		},
		(ctx) => {
			SNAPS[3](ctx);
			// combined: commitment on file, films absorbed, the hunt running
			chipA = 'gone';
			chipB = 'gone';
			modelShown = true;
			phase = 'prove';
			proving = true;
			huntA = huntB = true;
			landA = landB = null;
			settled = false;
			proveT0 = performance.now();
		}
	];
	const PLAYS: ((ctx: SimCtx, gen: number) => Promise<void>)[] = [
		(ctx, gen) => runRequest(ctx.d, gen),
		(ctx, gen) => runGenerate(ctx.d, ctx.text, gen),
		(ctx, gen) => runReturn(ctx.d, ctx.text, gen),
		async (_ctx, gen) => {
			await runProve(gen);
			for (const line of SIM_STATUS) {
				if (!(await step(1150, gen))) return;
				log(`PROVE  ${line}`);
			}
			if (!(await step(900, gen))) return;
		},
		(ctx, gen) =>
			runVerdict(
				{
					result: ctx.tamper
						? { verdict: 'FAIL', U: '2^-40', verify: 'REJECT', keybind: 'MISMATCH' }
						: { verdict: 'PASS', U: '2^-40', verify: 'ACCEPT', keybind: 'OK' }
				},
				gen
			)
	];

	function playFrom(i: number) {
		if (!simCtx) return;
		const ctx = simCtx;
		generation += 1;
		const gen = generation;
		chain = Promise.resolve();
		paused = false;
		snapCommon(ctx);
		SNAPS[i](ctx);
		simChapter = i;
		queue(async () => {
			if (!(await step(80, gen))) return;
			pktInstant = false;
			for (let c = i; c < PLAYS.length; c++) {
				if (gen !== generation) return;
				simChapter = c;
				await PLAYS[c](ctx, gen);
			}
		});
	}

	function simSeek(delta: number) {
		if (!simCtx || !simRun) {
			simulate(armed);
			return;
		}
		playFrom(Math.max(0, Math.min(SNAPS.length - 1, simChapter + delta)));
	}

	function simPlayPause() {
		if (!simCtx || !busy) {
			simulate(armed);
			return;
		}
		paused = !paused;
	}

	function simulate(tamper: boolean) {
		const nonce = `${Date.now()}:${Math.random()}`;
		simCtx = {
			text: tamper ? '<TAMPERED RESPONSE>' : SIM_ANSWER,
			tamper,
			d: {
				n_tokens: 11,
				ct_in: fauxDigest(`ct_in:${nonce}`) + fauxDigest(`ct_in2:${nonce}`),
				ct_out: fauxDigest(`ct_out:${nonce}`) + fauxDigest(`ct_out2:${nonce}`),
				request_digest: fauxDigest(`in:${SIM_PROMPT}:${nonce}`),
				response_digest: fauxDigest(`out:${SIM_ANSWER}:${nonce}`),
				request_audit: true,
				response_audit: true
			}
		};
		log('SIM    simulated board — nothing below was certified or proven');
		playFrom(0);
	}

	// ── power ──────────────────────────────────────────────────────────────────
	/**
	 * Two presses, and the second one only counts inside four seconds. This kills the
	 * Pi and then the Spark -- including the server that is serving this page, so
	 * there is no undo and no way back except opening the case.
	 */
	let armDown = $state(false);
	let downTimer = 0;
	let goingDown = $state(false);

	function shutdown() {
		// A dead link must refuse, not pretend: pressing this with the socket
		// down used to arm, confirm, paint the "Powering off" curtain -- and
		// emit into nothing, leaving two healthy machines behind a black
		// screen that said otherwise.
		if (!connected) {
			log('SHUTDOWN ignored: no link to the orchestrator');
			return;
		}
		if (!armDown) {
			armDown = true;
			clearTimeout(downTimer);
			downTimer = window.setTimeout(() => (armDown = false), 4000);
			return;
		}
		clearTimeout(downTimer);
		armDown = false;
		// goingDown is set by beat:shutdown -- the server's own acknowledgement
		// -- never optimistically here, so the curtain only falls when the
		// order has actually been received.
		// The token is checked server-side, so a stray `demo:shutdown` from a console
		// or a replayed frame cannot power the rack off by itself.
		socket?.emit('demo:shutdown', { confirm: 'POWER OFF' });
	}

	// ── the combine ────────────────────────────────────────────────────────────
	// What the cluster actually HOLDS, as opposed to what exists somewhere on the
	// stage: the model commitment once the proof reveals it, and the certifier's
	// two films only once they have landed. The stack reads these, so nothing
	// shows up inside the cluster before the storyboard says it arrived.
	const regReq = $derived(chipA === 'gone' ? reqFp : null);
	const regRsp = $derived(chipB === 'gone' ? rspFp : null);
	const regModel = $derived(modelShown ? modelFp : null);

	// ── the strip that is never dismissible when it matters ────────────────────
	/**
	 * What an audience has a right to know before they believe the screen. The
	 * simulator outranks the tamper because "none of this happened" is the more
	 * important of the two. A live, honest run has nothing to disclaim, so the
	 * strip only exists when one of the warnings holds; the server's own mode
	 * label rides as the stage tooltip for whoever is running the demo.
	 */
	const banner = $derived(
		runFault ? { tag: 'Fault', tone: 'fault' as const, say: runFault } : null
	);

	/**
	 * The prompt without its scaffolding, so the payoff reads as a question. A
	 * few-shot prompt carries worked examples in front; the question being asked
	 * is the LAST one, so take the text after the final "Question:".
	 */
	const asked = $derived(
		(promptText.split(/Question:/i).pop() ?? '')
			.replace(/\s*Answer:\s*$/i, '')
			.trim()
	);
	/** and your own machine lights up for the two moments the key is in use —
	 * not for the whole time the delivered answer sits parked there */
	const hotHome = $derived(pktShown && pktX === STOP[0] && (phase === 'req' || phase === 'rsp'));
	const TONE = {
		caution: { bar: 'border-l-caution', text: 'text-caution' },
		fault: { bar: 'border-l-fault', text: 'text-fault' }
	};

	// ── the flying films: geometry and dressing ────────────────────────────────
	// Stage coordinates for a film's journey: racked inside the certifier's tall
	// body while it holds them (input in the lower slot, output stacked above it,
	// the way the storyboard stacks them), then a long slide right into the
	// cluster at prove time, where the register absorbs them. C never travels —
	// the model commitment lives in the cluster and is simply revealed there.
	// A film's journey is a U: racked inside the certifier (input low, output
	// above -- the way the storyboard stacks them), then out through the
	// certifier's floor, along the trench under the stage, and up into the
	// cluster from below, where the register absorbs it beside the model
	// commitment. Each leg is its own state so the corners are real corners.
	// A film with its label runs ~10% of stage height; slots and docks are
	// spaced ~11.5% so the stack never collides with itself.
	// The mint slot sits RIGHT UNDER the cable, where the passing payload is:
	// each film pops out directly beneath the packet it was stamped from. The
	// input film takes that slot on the request pass; when the output film pops
	// out on the return pass it takes the slot itself, pushing the input film
	// down a rack.
	const SLOT_FRESH = { x: 39, y: 59 };
	const SLOT_PUSHED = { x: 39, y: 76.5 };
	// Docks are the sheets' OWN rows in the stack: the stack pile is centered at
	// stage {81.5, 36.35}, and each sheet's home center sits dy rows off the
	// pile's center (input one row up, output one row down). The chip is
	// anchored on its strip, drawn at the same width and cell size as the
	// stack, so the landing is pixel-exact: nothing teleports at the handoff.
	// Both films ride ONE road -- an L: right out of the certifier's wall at
	// the lower rack's height, then up into the cluster from below. The input
	// departs straight from its slot; the output first advances down into the
	// lane the input vacated (the rack's own push-down move), then follows
	// the identical road behind it.
	const CHIP_B = {
		held: SLOT_FRESH,
		drop: SLOT_PUSHED,
		run: { x: 81.5, y: 76.5 },
		dock: { x: 81.5, y: 36.35, dy: 1 }
	};
	const CHIP_A = $derived({
		held: chipB === 'hidden' ? SLOT_FRESH : SLOT_PUSHED,
		drop: SLOT_PUSHED,
		run: { x: 81.5, y: 76.5 },
		dock: { x: 81.5, y: 36.35, dy: -1 }
	});
	/** a chip's top style: plain %, or % plus a whole number of stack rows */
	const chipTop = (at: { y: number; dy?: number }) =>
		at.dy ? `calc(${at.y}% + ${at.dy} * ${FILMW} / ${DEFAULT_GRID.cols})` : `${at.y}%`;
	// Three shades of one green, the way the printed cards ink them: the input
	// film lightest, the output deeper, the model commitment deepest — the same
	// family so the stack reads as one instrument, stepped so the three strips
	// stay tellable apart. (The print inks are darker; these are the same
	// ordering rebalanced for a dark panel.)
	const HUEA = '#9ad161';
	const HUEB = '#7cc055';
	const HUEC = '#5fae4c';
	/** one width for a film everywhere it appears, so landing IS combining --
	 * nothing resizes between the flight and the stack */
	const FILMW = '24cqw';
	// A tampered run wears its color from the moment the output is minted --
	// the red is the claim; the verdict only confirms it.
	const badB = $derived(tampered || (!!verdict && verdict.verdict !== 'PASS'));
	// The films' own cell patterns — the same strips the register is composing,
	// derived from the same digests, so what flies is what lands.
	const filmMask = stencil(DEFAULT_WORD, DEFAULT_FACE, DEFAULT_GRID);
	const filmShares = $derived(build(reqFp ?? '', rspFp ?? '', modelFp ?? '', !badB, filmMask));
	const filmStrips = $derived(strips(filmShares, DEFAULT_GRID, 2, modelFp ?? '', 0.5));
	const clusterStatus = $derived(
		verdict
			? verdict.verdict === 'PASS'
				? 'workload verified via zero-knowledge proof'
				: ''
			: proving
				? 'computing zero-knowledge proof…'
				: phase === 'gen'
					? 'running the model'
					: ''
	);
</script>

{#snippet toggle(on: boolean, label: string, tone: 'fault' | 'caution', click: () => void)}
	<button
		type="button"
		role="switch"
		aria-checked={on}
		aria-label={label}
		onclick={click}
		disabled={goingDown}
		class="flex items-center gap-2.5 disabled:pointer-events-none disabled:opacity-35"
	>
		<span
			class={cn(
				'relative h-[1.5em] w-[2.9em] border text-[clamp(0.7rem,0.85vw,1rem)] transition-colors duration-200',
				on
					? tone === 'fault'
						? 'border-fault bg-fault/20'
						: 'border-caution bg-caution/20'
					: 'border-border bg-muted'
			)}
		>
			<span
				class={cn(
					'absolute top-[0.13em] size-[1.2em] transition-all duration-200',
					on
						? `left-[1.57em] ${tone === 'fault' ? 'bg-fault' : 'bg-caution'}`
						: 'left-[0.13em] bg-muted-foreground/70'
				)}
			></span>
		</span>
		<span
			class={cn(
				't-tag font-mono uppercase transition-colors',
				on
					? tone === 'fault'
						? 'text-fault'
						: 'text-caution'
					: 'text-muted-foreground hover:text-foreground'
			)}
		>
			{label}
		</span>
	</button>
{/snippet}

{#snippet filmchip(
	idx: number,
	role: string,
	hex: string | null,
	hue: string,
	state: Chip,
	pos: Record<'held' | 'drop' | 'run' | 'dock', { x: number; y: number; dy?: number }>,
	bad: boolean
)}
	{@const at =
		state === 'hidden'
			? { x: pos.held.x, y: pos.held.y - 5 }
			: pos[state === 'docked' || state === 'gone' ? 'dock' : state]}
	{@const ink = bad ? 'var(--fault)' : hue}
	{@const rows = filmStrips.heights[idx]}
	<!-- The chip is anchored on its STRIP (the label floats above and takes no
	     layout), drawn at the stack's own width with the same crisp 1x1 cells,
	     so its dock position IS its row in the pile: absorption is a same-frame,
	     same-pixels swap -- no fade, no teleport. -->
	<div
		class={cn(
			'absolute z-20 -translate-x-1/2 -translate-y-1/2 transition-all duration-[1400ms] ease-in-out motion-reduce:transition-none',
			state === 'hidden' && 'opacity-0 duration-[600ms]',
			state === 'held' && 'ilk-spring opacity-100 duration-[1100ms]',
			// the L, leg by leg: a small settling rack-advance, then one
			// accelerate-turn-decelerate arc -- the ease-in lane ends at the
			// speed the ease-out rise enters at, so the bend turns without
			// the film slowing down
			state === 'drop' && 'opacity-100 duration-[400ms] ease-in-out',
			state === 'run' && 'opacity-100 duration-[900ms] ease-in',
			state === 'docked' && 'opacity-100 duration-[550ms] ease-out',
			state === 'gone' && 'opacity-0 duration-[0ms]'
		)}
		style="left:{at.x}%;top:{chipTop(at)};width:{FILMW}"
	>
		<span
			class={cn(
				'absolute bottom-full left-1/2 mb-[0.4cqw] -translate-x-1/2 font-mono text-[1.15cqw] font-semibold whitespace-nowrap uppercase transition-opacity duration-300',
				(state === 'docked' || state === 'gone') && 'opacity-0'
			)}
			style="color:{ink}">{role} · {hex ? '0x' + short(hex, 8) : '—'}</span
		>
		<!-- the strip's actual cells: what flies is what the stack composes -->
		<svg
			viewBox="0 0 {DEFAULT_GRID.cols} {rows}"
			class="block w-full"
			preserveAspectRatio="none"
			shape-rendering="crispEdges"
			aria-hidden="true"
		>
			{#each { length: rows } as _r, r (r)}
				{#each { length: DEFAULT_GRID.cols } as _c, c (c)}
					{#if filmStrips.cells[idx][r * DEFAULT_GRID.cols + c] > 0}
						<rect x={c} y={r} width="1" height="1" fill={ink} />
					{/if}
				{/each}
			{/each}
		</svg>
	</div>
{/snippet}

<div class="flex h-svh flex-col overflow-hidden bg-background text-foreground">
	<main class="relative flex min-h-0 flex-1">
		<!-- the invisible latch: a silent corner the operator knows about. It sits
		     above everything, including the open bar, so it also closes it. -->
		<button
			type="button"
			class="absolute bottom-0 left-0 z-50 h-14 w-14 opacity-0"
			aria-label="toggle control bar"
			onclick={() => (menuOpen = !menuOpen)}
		></button>
		{#if menuOpen}
			<!-- controls only: everything else on this screen is a live value -->
			<div
				class="absolute bottom-0 left-0 z-40 flex w-64 flex-col items-stretch gap-4 border-t border-r border-border bg-background/95 px-4 pt-5 pb-16 backdrop-blur"
			>
			<div class="flex flex-col items-stretch gap-3">
				{@render toggle(armed, 'Tamper', 'fault', () => (armed = !armed))}
				{@render toggle(simForce || !liveWire, 'Simulate', 'caution', () => {
					// off is only offerable when there is a wire to fall back to
					if (liveWire) simForce = !simForce;
				})}
				{#if simMode}
					<!-- the transport: only a SIMULATED run can be scrubbed -->
					<div class="flex items-center gap-1">
						<button
							class="t-tag border border-border px-2 py-1 font-mono text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
							disabled={goingDown}
							onclick={() => simSeek(-1)}
							aria-label="back one chapter">◀◀</button
						>
						<button
							class="t-tag border border-border px-2 py-1 font-mono text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
							disabled={goingDown}
							onclick={simPlayPause}
							aria-label="play or pause">{busy && !paused ? '❚❚' : '▶'}</button
						>
						<button
							class="t-tag border border-border px-2 py-1 font-mono text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
							disabled={goingDown}
							onclick={() => simSeek(1)}
							aria-label="forward one chapter">▶▶</button
						>
					</div>
				{/if}
			</div>
			<div class="flex flex-col items-stretch gap-3 border-t border-border pt-4">
				<a
					href={resolve('/lab')}
					class="font-mono text-[10px] tracking-[0.16em] text-muted-foreground/40 uppercase hover:text-foreground"
					>bench</a
				>
				<button
					class={cn(
						't-tag border px-3 py-1 font-mono uppercase transition-colors',
						armDown
							? 'border-fault bg-fault/20 text-fault'
							: 'border-border text-muted-foreground/60 hover:border-fault hover:text-fault'
					)}
					disabled={goingDown || !connected}
					onclick={shutdown}
				>
					{armDown ? 'press again to power off' : connected ? 'shut down' : 'no link'}
				</button>
				<button
					class="t-tag border border-border px-4 py-2 font-mono text-muted-foreground uppercase transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
					disabled={goingDown}
					onclick={() => {
						menuOpen = false;
						resetAll();
					}}>Reset</button
				>
				<button
					class={cn(
						't-lead border border-signal bg-signal/10 px-8 py-2 font-mono tracking-[0.14em] text-signal uppercase transition-colors hover:bg-signal/20 disabled:pointer-events-none disabled:opacity-35',
						!busy && !goingDown && phase !== 'done' && 'ilk-breathe'
					)}
					disabled={busy || goingDown}
					onclick={() => {
						menuOpen = false;
						start();
					}}>Prompt</button
				>
			</div>
			</div>
		{/if}

		<!-- only exists while there is something to disclaim -->
		{#if banner}
			<div
				class={cn(
					't-body absolute inset-x-0 bottom-0 z-30 flex flex-wrap items-baseline gap-x-3 border-l-4 bg-card/90 px-4 py-2 font-mono backdrop-blur',
					TONE[banner.tone].bar
				)}
			>
				<span class={cn('shrink-0 tracking-[0.16em] uppercase', TONE[banner.tone].text)}
					>{banner.tag}</span
				>
				<span class="text-muted-foreground">{banner.say}</span>
			</div>
		{/if}

		<!--
			The stage, laid out to the storyboard. Far left: the globe — your machine
			at the end of the wire, where the question leaves and the opened answer
			parks. Center: the certifier, a tall body the cable runs straight
			through, racking the films it stamps. Right: the GPU cluster, where the
			model commitment is revealed and the films slide in to combine — the
			proof runs in there.
		-->
		<div class="flex h-full min-h-0 w-full flex-1 items-center justify-center">
			<!-- the stage is its own measure: everything inside is sized in cqw,
			     fractions of THIS box's width, so a small screen gets the same
			     picture smaller rather than a big picture cropped. It owns the
			     whole screen, letterboxed only by its 16:9 ratio. -->
			<section
				class="relative aspect-video max-h-full w-full overflow-hidden bg-card"
				style="max-width:min(100%, calc(100vh * 16 / 9));container-type:inline-size"
			>
			<div
				class="pointer-events-none absolute inset-0 opacity-50"
				style="background-image:linear-gradient(to right,var(--grid) 1px,transparent 1px),linear-gradient(to bottom,var(--grid) 1px,transparent 1px);background-size:3cqw 3cqw"
			></div>

			<!-- the wire: an L from the globe -- straight down, one hard 90° corner,
			     across into the certifier's wall -- and a second run from the
			     certifier's far wall into the cluster. Plain hairlines: the payload
			     card itself is the traffic. -->
			<div class="absolute top-[21%] left-[11%] h-[12%] w-px bg-border"></div>
			<div class="absolute top-[33%] right-[70%] left-[11%] h-px bg-border"></div>
			<div class="absolute top-[33%] right-[35%] left-[48%] h-px bg-border"></div>

			<!-- your machine: the globe at the top of the L -->
			<div
				class={cn(
					'absolute top-[12%] left-[11%] -translate-x-1/2 -translate-y-1/2 transition-colors duration-300',
					hotHome ? 'text-signal' : 'text-muted-foreground'
				)}
			>
				<svg
					viewBox="0 0 24 24"
					class="w-[5cqw] transition-all duration-300"
					fill="none"
					stroke="currentColor"
					stroke-width="1.3"
					aria-hidden="true"
				>
					<circle cx="12" cy="12" r="9" />
					<ellipse cx="12" cy="12" rx="4.2" ry="9" />
					<path d="M3 12h18" />
					<path d="M4.4 7.2h15.2M4.4 16.8h15.2" />
				</svg>
			</div>

			<!-- the certifier: a tall body the cable runs straight through; the films
			     it stamps rack up inside it until the proof calls for them -->
			<div
				class={cn(
					'absolute top-[5%] h-[81%] w-[30cqw] -translate-x-1/2 border bg-background transition-all duration-300',
					hotFpga ? 'border-signal' : 'border-border'
				)}
				style="left:{STOP[1].x}%"
			>
				<div class="flex flex-col items-center border-b border-border px-[0.8cqw] py-[0.5cqw]">
					<span class="font-mono text-[1.15cqw] font-semibold tracking-[0.05em]">NETWORK CERTIFIER</span>
				</div>
			</div>

			<!-- the GPU cluster: where the model runs, and where the proof combines.
			     Top-aligned with the certifier and shorter, the way the storyboard
			     draws them — the certifier is the dominant body on the wire, and
			     the films rise into this box from below. -->
			<div
				class={cn(
					'absolute top-[5%] right-[1%] left-[64%] h-[57%] flex flex-col overflow-hidden border bg-background transition-all duration-500',
					verdict
						? verdict.verdict === 'PASS'
							? 'border-verified'
							: 'border-fault'
						: hotSpark
							? 'border-signal'
							: 'border-border'
				)}
			>
				<div class="flex shrink-0 flex-col items-center gap-0.5 border-b border-border px-[0.8cqw] py-[0.5cqw]">
					<span class="font-mono text-[1.15cqw] font-semibold tracking-[0.05em]">GPU CLUSTER</span>
					{#if clusterStatus}
						<span
							class={cn(
								'font-mono text-[0.9cqw] tracking-[0.16em] uppercase',
								verdict
									? verdict.verdict === 'PASS'
										? 'text-verified'
										: 'text-fault'
									: 'text-muted-foreground'
							)}>{clusterStatus}</span
						>
					{/if}
				</div>
				<!-- the combine: the three films stacked at true registration, in place
				     and at film size — landing IS combining, there is no handoff to a
				     larger instrument. The pile is ANCHORED (center of the box's body,
				     stage y 36.35) so the flying chips' docks are its own sheet rows.
				     Above it, a legend keeps all three names on screen through the
				     sliding, and calls the match when the verdict lands. -->
				<div
					class="absolute top-[55%] left-1/2 w-full -translate-x-1/2 -translate-y-1/2"
					style="max-width:min({FILMW},92%)"
				>
						<div
							class="relative w-full"
							style="aspect-ratio:{DEFAULT_GRID.cols}/{filmStrips.pile}"
						>
							{#each [2, 0, 1] as i (i)}
							{@const on = i === 2 ? !!regModel : i === 0 ? !!regReq : !!regRsp}
							{@const ink = i === 2 ? HUEC : i === 0 ? HUEA : badB ? 'var(--fault)' : HUEB}
							{@const home = i === 1 ? filmStrips.slack : 0}
							<!-- Every sheet is drawn in the SAME pile-sized coordinate space,
							     its cells already at their home rows: registration is exact by
							     construction, instead of asking three separately-scaled boxes
							     to agree to the pixel. Cells are exactly 1x1 with crisp edges,
							     because a 5% overdraw that hides hairlines inside one sheet
							     reads as a shadow the moment sheets stack. -->
							<svg
								viewBox="0 0 {DEFAULT_GRID.cols} {filmStrips.pile}"
								preserveAspectRatio="none"
								shape-rendering="crispEdges"
								class={cn(
									'absolute inset-0 h-full w-full',
									i === 2
										? 'transition-[opacity,transform] duration-[700ms]'
										: 'transition-transform duration-[1000ms] ease-in-out',
									on
										? 'translate-y-0 opacity-100'
										: i === 2
											? '-translate-y-[16%] opacity-0'
											: 'opacity-0',
									huntA && i === 0 && 'ilk-hunt-a',
									huntB && i === 1 && 'ilk-hunt-b',
									i === 0 && landA && 'ilk-land',
									i === 1 && landB && 'ilk-land',
									settled && (i === 2 ? 'opacity-0' : 'transition-opacity duration-[600ms] opacity-0')
								)}
								style={i === 0 && landA
									? `--land-from:${landA}`
									: i === 1 && landB
										? `--land-from:${landB}`
										: ''}
								aria-hidden="true"
							>
								{#each { length: filmStrips.heights[i] } as _r, r (r)}
									{#each { length: DEFAULT_GRID.cols } as _c, c (c)}
										{#if filmStrips.cells[i][r * DEFAULT_GRID.cols + c] > 0}
											<rect x={c} y={home + r} width="1" height="1" fill={ink} />
										{/if}
									{/each}
								{/each}
							</svg>
							{/each}
							{#if settled}
								<!-- the settled register, redrawn as ONE svg and faded in over
								     the three sheets as they fade out: a crossfade between two
								     images of the same picture, ending on the one that cannot
								     be out of register with itself -->
								<svg
									viewBox="0 0 {DEFAULT_GRID.cols} {filmStrips.pile}"
									preserveAspectRatio="none"
									shape-rendering="crispEdges"
									class="ilk-fadein absolute inset-0 h-full w-full"
									aria-hidden="true"
								>
									{#each [2, 0, 1] as i (i)}
										{@const von = i === 2 ? !!regModel : i === 0 ? !!regReq : !!regRsp}
										{@const vink = i === 2 ? HUEC : i === 0 ? HUEA : badB ? 'var(--fault)' : HUEB}
										{@const vhome = i === 1 ? filmStrips.slack : 0}
										{#if von}
											{#each { length: filmStrips.heights[i] } as _r, r (r)}
												{#each { length: DEFAULT_GRID.cols } as _c, c (c)}
													{#if filmStrips.cells[i][r * DEFAULT_GRID.cols + c] > 0}
														<rect x={c} y={vhome + r} width="1" height="1" fill={vink} />
													{/if}
												{/each}
											{/each}
										{/if}
									{/each}
								</svg>
							{/if}
						</div>
						<!-- clearance above the pile = the sheets' biggest upward throw
						     while hunting, so the rake never runs into the legend -->
						<div class="absolute inset-x-0 bottom-full mb-[2.6cqw] flex flex-col items-center gap-[0.05cqw] leading-tight">
							{#if verdict && verdict.verdict !== 'PASS'}
								<!-- the failed verdict collapses the ledger into its reading -->
								<span
									class="text-center font-mono text-[1.1cqw] font-semibold uppercase leading-snug text-fault"
									>OUTPUT FINGERPRINT INCONSISTENT WITH INPUT AND MODEL, TAMPERING DETECTED</span
								>
							{:else if finale}
								<!-- the closing line, standing over the still-stacked pile -->
								<span
									class="ilk-flyin px-[1cqw] text-center font-mono text-[1.2cqw] font-semibold uppercase leading-snug"
									style="color:{HUEA}"
									>FINGERPRINTS VERIFIED, OUTPUT MATCHES DECLARED MODEL</span
								>
							{:else}
								{#each [
									['DECLARED MODEL FINGERPRINT', regModel, HUEC],
									['INPUT FINGERPRINT', regReq, HUEA],
									['OUTPUT FINGERPRINT', regRsp, badB ? 'var(--fault)' : HUEB]
								] as [role, hex, ink] (role)}
									<span
										class={cn(
											'font-mono text-[1.05cqw] font-semibold whitespace-nowrap uppercase transition-opacity duration-500',
											hex ? 'opacity-100' : 'opacity-0'
										)}
										style="color:{ink}">{role} · {hex ? '0x' + short(hex, 6) : '—'}</span
									>
								{/each}
							{/if}
					</div>
				</div>
			</div>

			<!-- the payload: the text itself rides the cable, sealed or open. Fixed
			     width, wrapping to as many lines as it needs — sealing swaps every
			     character but never reshapes the envelope. -->
			<div
				class={cn(
					'ilk-spring absolute z-10 flex w-[17cqw] -translate-x-1/2 -translate-y-1/2 items-start gap-[0.6cqw] border px-[0.9cqw] py-[0.45cqw] font-mono motion-reduce:transition-none',
					pktInstant ? 'transition-none' : 'transition-all duration-[1600ms]',
					pktSealed
						? 'border-[#d9a13b] bg-muted text-foreground'
						: 'border-border bg-muted text-foreground',
					pktShown ? 'opacity-100' : 'opacity-0'
				)}
				style="left:{pktX.x}%;top:{pktX.y}%"
			>
				{#if sealing}
					<!-- the key operation, named while it runs -- the lock rides beside
					     the word, closed while sealing, open while opening -->
					<span
						class="absolute bottom-full left-0 mb-[0.35cqw] flex items-center gap-[0.4cqw] font-mono text-[1.05cqw] font-semibold tracking-[0.14em] uppercase text-[#d9a13b]"
					>
						<svg
							viewBox="0 0 24 24"
							class="size-[1.25em] shrink-0"
							fill="none"
							stroke="currentColor"
							stroke-width="2.4"
							aria-hidden="true"
						>
							<rect x="4" y="11" width="16" height="10" rx="1.5" />
							{#if sealing === 'encrypting'}
								<path d="M8 11V7a4 4 0 0 1 8 0v4" />
							{:else}
								<path d="M8 11V7a4 4 0 0 1 7.4-2" />
							{/if}
						</svg>
						{sealing}…</span
					>
				{/if}
				<!-- ciphertext has no spaces to break on, so it breaks anywhere;
				     plaintext keeps its words whole -->
				<div class="min-w-0 flex-1">
					<div
						class="mb-[0.15cqw] font-mono text-[0.9cqw] font-semibold tracking-[0.2em] text-muted-foreground uppercase"
					>
						{pktRole}
					</div>
					<!-- a HARD two-line well: sealing, opening, and typing change the
					     words, never the box -- a transient third line mid-scramble is
					     clipped rather than allowed to grow the card -->
					<span
						class={cn(
							'block h-[2lh] overflow-hidden text-[1.25cqw] leading-snug',
							pktSealed ? 'break-all' : 'break-words'
						)}
						>{pktText}</span
					>
				</div>
			</div>

			<!-- the request's ghost: pixel-identical to the open payload card, it
			     holds the decrypted question while the model runs on it -->
			<div
				class={cn(
					'absolute z-10 flex w-[17cqw] -translate-x-1/2 -translate-y-1/2 items-start gap-[0.6cqw] border border-border bg-muted px-[0.9cqw] py-[0.45cqw] font-mono text-foreground',
					gqShown ? 'opacity-100' : 'pointer-events-none opacity-0'
				)}
				style="left:81.5%;top:{gqY}%;transition:top 900ms var(--spring), opacity {gqShown
					? '0ms'
					: '900ms'} ease-in-out"
			>
				<div class="min-w-0 flex-1">
					<div
						class="mb-[0.15cqw] font-mono text-[0.9cqw] font-semibold tracking-[0.2em] text-muted-foreground uppercase"
					>
						request
					</div>
					<span class="block h-[2lh] overflow-hidden text-[1.25cqw] leading-snug break-words"
						>{gqText}</span
					>
				</div>
				{#if processing}
					<!-- the busy bar: the model is running on this request -->
					<div class="ilk-proc pointer-events-none absolute inset-x-0 bottom-0 h-[0.25cqw]"></div>
				{/if}
			</div>

			<!-- the certifier's two stamps in flight: the strip patterns themselves -->
			{@render filmchip(0, 'INPUT FINGERPRINT', reqFp, HUEA, chipA, CHIP_A, false)}
			{@render filmchip(1, 'OUTPUT FINGERPRINT', rspFp, HUEB, chipB, CHIP_B, badB)}
			</section>
		</div>
	</main>

	{#if goingDown}
		<!-- CRT power-off: the lit field collapses to a scanline, the scanline to
		     a point, and a faint power glyph breathes until the machine serving
		     this page actually dies and takes the screen with it. -->
		<div class="fixed inset-0 z-50 overflow-hidden bg-black">
			<div class="ilk-crt"></div>
			<svg
				class="ilk-powerglyph"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.6"
				stroke-linecap="round"
				aria-label="powering off"
			>
				<path d="M12 3v8" />
				<path d="M7.2 6.4a7.5 7.5 0 1 0 9.6 0" />
			</svg>
		</div>
	{/if}
</div>
