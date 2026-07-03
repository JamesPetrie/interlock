// log2_iter — iterative binary logarithm of a custom-float probability
// (see docs/recomp_feed.md "Scoring" and recomp_pkg for the number formats).
//
// log2(1.mant × 2^-exp) = log2(1.mant) - exp, so the exponent drops out as
// the (negative) integer part and only log2 of the significand, in [0, 1),
// is computed. Its fraction bits come out one per cycle, MSB first, by
// repeated squaring: x in [1,2) squares into [1,4); a square >= 2 emits a 1
// and renormalizes with a right shift, a square < 2 emits a 0.
//
// res is a log2_t: signed Q(LOG2_W-LOG2_FRAC_W).LOG2_FRAC_W. Downstream
// accumulation belongs in a log2_acc_t (same fraction bits, doubled word).
//
// x is truncated (round-toward-zero) after each squaring. In the log
// domain the step-i truncation loses tau_i <= 2^-XW/ln2 from log2(x), and
// telescoping the iteration — emitted = log2(x0) - sum(tau_i * 2^-i) -
// residual — shows each loss reaches the output attenuated by 2^-i, so the
// compounded total stays below 1.44*2^-XW regardless of iteration count.
// GUARD_W guard bits under the output precision put that at
// 1.44*2^-GUARD_W of an ulp: res sits within 1.006 ulp below true log2 and
// never above. One-sided matters: -log2 surprisal is never undercharged;
// round-to-nearest would halve the error but make it two-sided.

module log2_iter
  import recomp_pkg::*;
(
  input  wire        clk,
  input  wire        rst_n,

  input  wire        start,  // latch p and begin (ignored while busy)
  input  wire [31:0] p,      // prob_t: custom float (see recomp_pkg)
  output logic       done,   // 1-cycle pulse: res valid
  output log2_t      res     // log2(p)
);

  localparam int unsigned M       = PROB_MANT_W;
  // guard bits below the output precision: rule-of-thumb log2(n_out)+3,
  // comfortably above the constant few bits the telescoping bound (header)
  // strictly needs; the compounded loss is 1.44*2^-GUARD_W of an ulp
  localparam int unsigned GUARD_W = $clog2(LOG2_FRAC_W) + 3;
  // x fraction bits; never narrower than the mantissa so the load is exact
  localparam int unsigned XW      = (LOG2_FRAC_W + GUARD_W > M)
                                    ? LOG2_FRAC_W + GUARD_W : M;
  localparam int unsigned CNT_W   = $clog2(LOG2_FRAC_W + 1);

  wire prob_t pf = prob_t'(p);

  logic busy;

  logic [PROB_EXP_W-1:0]  exp_q;
  logic [XW:0]            x;     // Q1.XW significand, in [1,2)
  logic [LOG2_FRAC_W-1:0] frac;  // log2 fraction bits, MSB first
  logic [CNT_W-1:0]       cnt;

  wire [2*XW+1:0]        sq     = x * x;                  // Q2.2XW, in [1,4)
  wire                   fbit   = sq[2*XW+1];             // square >= 2
  wire [XW:0]            xn     = fbit ? sq[2*XW+1 -: XW+1]
                                       : sq[2*XW   -: XW+1];
  wire [LOG2_FRAC_W-1:0] frac_n = (frac << 1) | LOG2_FRAC_W'(fbit);
  wire                   last   = (cnt == 1);

  wire log2_t res_n = (-$signed(LOG2_W'(exp_q)) <<< LOG2_FRAC_W)
                      + $signed(LOG2_W'(frac_n));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      busy <= 1'b0;
      done <= 1'b0;
    end else begin
      done <= 1'b0;
      if (!busy && start) begin
        exp_q <= pf.exp;
        x     <= (XW+1)'({1'b1, pf.mant}) << (XW - M);
        frac  <= '0;
        cnt   <= CNT_W'(LOG2_FRAC_W);
        busy  <= 1'b1;
      end else if (busy) begin
        x    <= xn;
        frac <= frac_n;
        cnt  <= cnt - 1'b1;
        if (last) begin
          busy <= 1'b0;
          done <= 1'b1;
          res  <= res_n;
        end
      end
    end
  end

endmodule
