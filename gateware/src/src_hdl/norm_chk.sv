// norm_chk — running normalization check over one estimate's probabilities
// (see docs/recomp_feed.md "Scoring" and recomp_pkg for the number format).
//
// An estimate is a valid sub-distribution iff its probabilities sum to at
// most 1.0. Each add barrel-shifts the significand {1, mant} into a
// full-range fixed-point accumulator — every representable probability
// lands exactly, so no rounding can leak mass either way. fail latches the
// first time the running sum exceeds 1.0 (further adds are ignored) and
// clears on clr for the next estimate.
//
// The catch-all entry (add qualified by catchall) is a per-value probability
// covering the whole unlisted value space: it is charged at p × 2^SPACE_W,
// pure exponent arithmetic — an exponent below SPACE_W means the charge
// alone exceeds 1.0 and fails directly. REVISIT: 2^W is an upper bound for
// the exact 2^W − K unlisted values (K = listed entries); sound — never
// under-counts mass — and over-counts by ≤ K/2^W relative, but revisit if
// an exact count is ever wanted (needs a multiplier).
//
// Two-stage pipeline (the shift and the add/compare would not close timing
// in one cycle): stage 1 registers the shifted contribution, stage 2
// accumulates and compares. Consumers need not know the depth: busy is high
// while any add/clr is pending or in flight, and fail is coherent whenever
// busy is low. Operations complete in issue order, so clr may be pulsed
// back-to-back with adds — it must only not coincide with an add's stage-2
// cycle (the feed's value/probability word alternation guarantees that gap
// for free).

module norm_chk
  import recomp_pkg::*;
(
  input  wire        clk,
  input  wire        rst_n,

  input  wire        clr,      // start a new estimate: clears sum and fail
  input  wire        add,      // accumulate p this cycle
  input  wire        catchall, // qualifies add: charge p × 2^SPACE_W
  input  wire [31:0] p,        // prob_t: custom float (see recomp_pkg)
  output logic       fail,     // sticky: the running sum exceeded 1.0
  output wire        busy      // an add/clr is in flight; fail is coherent
                               // while busy is low
);

  // catch-all value-space width: the token space. REVISIT if LENGTH/TIMING
  // estimates ever get their own (smaller) spaces, this becomes an input.
  localparam int unsigned SPACE_W = 8 * canon_pkg::CANON_TOK_BYTES;

  localparam logic [PROB_ACC_W-1:0] ONE = PROB_ACC_W'(1) << PROB_FRAC_W;

  wire prob_t pf = prob_t'(p);

  // ---- stage 1: barrel shift — 1.mant placed by the (effective) exponent ----
  logic                  add_q, clr_q;
  logic [PROB_ACC_W-1:0] contrib_q;
  logic                  ca_ovf_q;

  // in-flight only — not including the raw add/clr inputs
  assign busy = add_q || clr_q;

  // catch-all: p × 2^SPACE_W reduces the exponent by SPACE_W; an exponent
  // below SPACE_W means the charge alone exceeds 1.0 (contribution muxed to
  // zero, ovf carries the verdict). in the non-ovf branch the shift stays
  // within the plain-add range [0, PROB_EXP_MAX].
  wire        ca_ovf = catchall && (32'(pf.exp) < SPACE_W);
  wire [31:0] shamt  = PROB_EXP_MAX + (catchall ? SPACE_W : 0) - 32'(pf.exp);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      add_q     <= 1'b0;
      clr_q     <= 1'b0;
      ca_ovf_q  <= 1'b0;
      contrib_q <= '0;
    end else begin
      add_q     <= add;
      clr_q     <= clr;
      ca_ovf_q  <= add && ca_ovf;
      contrib_q <= ca_ovf ? '0
                          : PROB_ACC_W'({1'b1, pf.mant}) << shamt;
    end
  end

  // ---- stage 2: accumulate and compare ----
  logic [PROB_ACC_W-1:0] acc;
  wire  [PROB_ACC_W-1:0] sum = acc + contrib_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc  <= '0;
      fail <= 1'b0;
    end else if (clr_q) begin
      acc  <= '0;
      fail <= 1'b0;
    end else if (add_q && !fail) begin
      acc  <= sum;
      fail <= ca_ovf_q || (sum > ONE);
    end
  end

endmodule
