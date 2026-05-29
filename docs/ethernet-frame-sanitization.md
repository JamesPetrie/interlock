# Ethernet Frame Sanitization

This document collects how an Ethernet frame must be **sanitized** as it crosses the interlock — that is, reduced to a known-good canonical form so that no field can be used as a covert side-channel. It is derived purely from the IEEE 802.3 frame format.

## Scope: normal frames only

We consider only **normal frames**, i.e. frames where the two-octet Length/Type field is interpreted as a **LENGTH**:

- `L/T ≤ 1500` (`0x05DC`) → the field is a LENGTH, this document applies.

Everything below assumes the Length interpretation.

## Frame layout (802.3 basic frame, as seen by the MAC client)

Preamble and SFD are consumed by the PHY/MAC, so the bytes presented to the sanitizer are:

```
  ┌────────────┬────────────┬────────┬───────────────┬────────┬────────┐
  │ DST address│ SRC address│ LENGTH │     DATA      │  PAD   │  FCS   │
  │  6 octets  │  6 octets  │ 2 oct. │  0..1500 oct. │ 0..46  │ 4 oct. │
  └────────────┴────────────┴────────┴───────────────┴────────┴────────┘
```

The minimum payload (`DATA + PAD`) is 46 octets, giving the 64-octet minimum frame; the maximum DATA is 1500 octets.

## Summary

| Field  | Action                                                        |
|--------|---------------------------------------------------------------|
| DST    | Force / whitelist a single address                            |
| SRC    | Force / whitelist a single address                            |
| LENGTH | Enforce to not exceed actual DATA; re-frame if needed         |
| DATA   | Pass through unchanged — the payload to transfer              |
| PAD    | Override to a fixed value; drop unnecessary PAD               |
| FCS    | Discard and recalculate (CRC-32 over sanitized fields)        |

## Per-field sanitization rules

### DST address

Hardcoded or whitelisted to **exactly one** permitted value.

- Replace with the single known-good destination MAC, **or**
- Compare against the one whitelisted address and drop the frame on mismatch.

No multicast/broadcast range, no table — a single address keeps the attack surface and the logic minimal.

### SRC address

Same treatment as DST: hardcoded or whitelisted to **exactly one** permitted value. Forcing the source removes it as a covert channel and guarantees the downstream peer sees a consistent origin.

### LENGTH

To prevent using segmentation as a covert channel, the canonical packets must either be constrained to fit in a single Ethernet frame or must be extracted from the frames and reframed using a fixed segmentation pattern (e.g. use max bytes except the last frame).

If reframing is not needed, then the LENGTH field **must be enforced to match the actual data** carried in the frame. The value on the wire is not trusted.

Let **L** = the LENGTH field value, and **N** = the number of payload octets actually received on the wire (everything between the LENGTH field and the FCS, i.e. the DATA + PAD region). The potential deviations are:

- **L = 0**: no data claimed → invalid, drop.
- **L < N**: claimed length shorter than received octets → the surplus `N − L` octets are PAD → see PAD field rules.
- **L > N**: claimed length exceeds received octets (EOF reached before L octets arrived) →  invalid frame, drop.
- **L = explained + unexplained octets**: the adversary transmits unexplained octets (accounted) and additionally uses L itself (segmentation) as a covert channel (unaccounted) on top of that.
  - E.g. each frame could carry 1 bit by appending an extra byte or not. That costs ~4 unexplained bits on average for 1 bit of covert bandwidth (25%).
  - Cannot be detected at the frame level — must be handled in unexplained-information counting.

### DATA

This is the **payload we actually want to transfer** — the information the sanitizer is built to carry across. DATA passes through unchanged; it is the one field whose content is preserved verbatim.

The length of DATA is determined by LENGTH (see above).

### PAD

PAD exists only to satisfy the 64-octet minimum frame size. Because PAD content is unconstrained by 802.3, it is a classic covert channel and must be neutralized:

- **Override**: any PAD octets that must remain (to meet the minimum frame size) are overwritten with a fixed, known value (e.g. all-zero) so they carry no information.
- **Drop unnecessary PAD**: PAD beyond what the minimum frame size requires is removed entirely.

After sanitization, PAD is fully deterministic given the DATA length: it is present only when DATA is short enough to need it, and its content is fixed.

### FCS

The incoming FCS is **discarded and recalculated** as a CRC-32 over the sanitized `DST | SRC | LENGTH | DATA | PAD`.

Because every preceding field may have been rewritten (addresses forced, potential re-framing, PAD overridden/dropped), the original FCS is meaningless; the fresh CRC is the only one that is valid for the frame that actually leaves the interlock. Recomputing it also means a frame with a bad incoming FCS is either corrected or rejected rather than forwarded.

## Open items

- **Type-interpreted frames** (`L/T ≥ 1536`) are out of scope and currently unhandled.
