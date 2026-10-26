# Gateware crypto backend — where the Core's SHA-256 and HMAC come from

```
TODO (James): Claude reviewed this doc (2026-09-18). It predates the current
crypto/ cores and certificate format; update it to the current state or
retire it.
```

Decision record for the interlock Core (the gateware version of
`prototype/interlock.py`). The Core needs two crypto primitives:

- **SHA-256, at line rate** — every accepted packet is folded into a running
  per-direction bucket hash, buckets fold into a per-second window hash
  (`H(ciphertext)`, `record`, bucket, window). This runs continuously on the
  wire, ~hundreds of MB/s aggregate full-duplex.
- **HMAC-SHA-256, once per second** — the certificate tag over the 108-byte
  cert body. Tiny and occasional.

These have opposite profiles (streaming vs. occasional), so the backend choice
isn't one decision — it's two. Three sources were evaluated.

## Options

### 1. secworks/sha256 — open fabric core
Plain Verilog-2001 SHA-256 core (BSD-2-Clause), NIST-vector tested, silicon-proven
(ASIC + FPGA). `init`/`next` give native multi-block streaming — exactly the
running bucket/window contexts. 66 cycles/block → ~100 MB/s per instance at
100 MHz; instantiate one per context to hit aggregate throughput.

- **Needs:** a small `sha256_stream` wrapper (byte→512-bit block assembly +
  padding + length tracking + `init`/`next` sequencing), written once and reused;
  for HMAC, a thin FSM doing the two passes (`key⊕ipad`, `key⊕opad`). NB: build
  this FSM over the SHA core — secworks's own `hmac` repo is marked
  non-functional ("does NOT yet work") and truncates to a 128-bit tag, whereas the
  cert uses the full 256-bit tag. Validate the FSM against RFC 4231 vectors. The
  HMAC here is the trivial case (fixed 108-byte body, fixed-size key, no streaming).
- **Pros:** pure fabric, no CPU, fully inspectable (small TCB), line-rate, you
  own every line. Plain Verilog avoids the complex-SV synth-vs-sim trap that bit
  the deframe path.
- **Cons:** padding wrapper is yours to get right; HMAC key lives in fabric
  registers (protected only by the battery-backed key domain, not dedicated
  crypto hardware).
- **Repo:** https://github.com/secworks/sha256

### 2. PolarFire User Crypto / TeraFire — the on-chip HMAC hardware
Athena TeraFire EXP-F5200B, the `PF_CRYPTO` hard block. Natively does HMAC-SHA,
all SHA-2, AES, ECC, RNG; DPA-resistant; 56 KB sNVM secure key storage. Max
189 MHz. Present only on **"S"-grade** devices — our eval board
(`MPF300TS-1FCG1152`) qualifies.

- **Needs:** a **soft CPU** (Mi-V RV32) running Microchip's **CAL** C library to
  command it over AHB-Lite, plus the supporting fabric (two `CoreAHBL2AHBL`
  bridges for the separate 180 MHz crypto domain + DMA, SRAM buffer,
  `CoreSysServices_PF` for sNVM). The Mi-V is ~6.7k LUTs (~2% of MPF300).
- **Why the CPU:** the only *documented* control interface is the CAL library —
  the register/command protocol is encapsulated in it and not published. The
  block is a microcoded coprocessor commanded by software, not a fixed-function
  datapath unit. (A `START` pin documents a "standalone, no host processor" mode,
  but how to feed it operations is undocumented; every reference design, sample,
  and the CAL flow assume the coprocessor+CPU model.)
- **Pros:** hardware-protected key custody (DPA resistance + sNVM) — directly
  serves the interlock's "protect the HMAC secret" assumption; correctness is
  Microchip's, not ours.
- **Cons:** adds a soft CPU + firmware + a gated, black-box library (CAL) to the
  TCB — the opposite of "small, inspectable verifier." Coprocessor request/
  response model is **not** line-rate, so it can only serve the once/sec HMAC,
  not the streaming hashes. CAL and the design files are request-gated
  (`FPGA_marketing@microchip.com`); needs a Libero Eval/Gold/Platinum license.
- **Reference:** AC464 "Implementing Data Security Using User Cryptoprocessor"
  ([V11 PDF](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ApplicationNotes/ApplicationNotes/Microchip_PolarFire_FPGA_Implementing_Data_Security_Using_User_Cryptoprocessor_Application_Note_AC464_V11.pdf))
  — the canonical Mi-V + PF_CRYPTO reference design. Sample project
  `RV32_Message_Authentication` is the MAC/HMAC example; Appendix 2 covers
  simulating the block.

### 3. PolarFire System Services SHA-256 — CPU-free, but limited
The System Controller exposes a SHA-256 *system service*; `CoreSysServices_PF` is
a fabric master that issues it with no CPU.

- **Pros:** no soft CPU; uses a hardened SHA.
- **Cons:** SHA-256 only (HMAC's two passes still yours to wrap); routes through
  the System Controller mailbox — built for occasional ops (e.g. digesting device
  content), **not** line rate. Marginal benefit over option 1; key still lives in
  fabric.
- **Reference:** [PolarFire System Services User Guide](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/PolarFire_FPGA_and_PolarFire_SoC_FPGA_System_Services_User_Guide_VD.pdf),
  [CoreSysServices_PF driver guide](https://github.com/polarfire-soc/polarfire-soc-documentation/blob/master/bare-metal-embedded-software/bare-metal-driver-user-guides/soft-ip-driver-user-guides/CoreSysServices-PF/coresysservices-pf-driver-user-guide.md).

## Summary

| | secworks fabric | User Crypto (TeraFire) | System Services SHA |
|---|---|---|---|
| Soft CPU needed | no | **yes** (Mi-V + CAL) | no |
| Line-rate streaming | **yes** | no | no |
| HMAC native | no (thin wrapper) | **yes** | no (thin wrapper) |
| Key custody | fabric regs | **DPA + sNVM** | fabric regs |
| TCB / inspectability | **small, all ours** | large (CPU + gated lib) | medium |
| Licensing | open (BSD) | gated (CAL, S-grade lic.) | in Libero |

## Side-channel resistance (a stated requirement)

Side-channel (DPA/SPA — power/EM) resistance **is** a requirement for this system,
eventually. Crucially, it only applies where a **secret** is used in computation:

- **Streaming hashes** are plain SHA-256 over data already on the wire (headers +
  ciphertext; even `recomp_commitment = H(key)` is a publicly-transmitted hash).
  No key is involved, so a power/EM attacker watching the hashing learns nothing a
  wire tap wouldn't give. **No SCA hardening needed on the line-rate path.**
- The **HMAC** is the only secret-bearing operation (the MAC key), and the key's
  *storage* must also resist extraction. **This is the SCA-sensitive part** — and
  it's the small, once-per-second one.

Why this matters in the threat model: the prover **physically hosts** the
interlock, so a power/EM attack to lift the MAC key → forge certificates →
defeats the scheme. SCA resistance + secure key storage is what makes the
existing "protect the HMAC secret" assumption hold against a physically-present
adversary — not gold-plating.

Implication: **do not DIY SCA countermeasures in fabric.** Masking/hiding of the
compression function is a specialist discipline, easy to get subtly wrong and
hard to validate. This is the one place the black-box hardware genuinely wins —
the TeraFire's countermeasures are patented, validated, and the single-chip
crypto flow was NCSC-reviewed. Note also that full SCA resistance is a *system*
property (PSU filtering, EM shielding, layout, constant-time control), not just
the core; the hardened core is necessary, not sufficient.

## Recommendation

- **Streaming hashes (per-packet / bucket / window):** secworks fabric cores —
  the only line-rate option, the simplest, and no secret to leak. Unchanged by
  the SCA requirement.
- **HMAC tag — phased, because the conformance harness is backend-agnostic:**
  - *Phase 1 (functional):* secworks SHA + a small HMAC FSM. Gets the Core and
    `honest_pair` green and exercises the whole certificate dataflow. No SCA.
  - *Phase 2 (SCA-hardened):* swap the HMAC to the **User Crypto / TeraFire**
    (DPA/SPA-resistant) with the key in **sNVM**. Brings back the Mi-V + CAL, but
    only for the HMAC path (the CPU isn't secret-bearing, so it needn't be
    hardened). The cocotb test checks cert *bytes*, so the swap is **no-rework**
    on conformance.
- This resolves the earlier security-vs-TCB tension: a pure-fabric TCB cannot
  provide hardware SCA assurance at all, so the stated SCA requirement makes the
  User Crypto the right call for the HMAC — the soft-CPU cost is the price of
  validated hardware countermeasures.

## Note on the conformance harness

The cocotb harness in [`gateware/tb/`](../gateware/tb/) checks the Core's emitted
certificate **byte-for-byte** against the Python golden model — so it is agnostic
to which backend above is chosen. Whichever SHA/HMAC source the Core uses, the
same test validates it.
