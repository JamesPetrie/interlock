package recomp_pkg;

  // A probability is an unsigned custom float packed into one 32-bit word:
  //
  //   |31        MANT_W|MANT_W-1         0|
  //   |       EXP      |       MANT       |     value = 1.MANT × 2^(-EXP)
  //
  // - implicit leading 1: the mantissa directly expresses the fraction the
  //   iterative log2 approximation consumes, and the exponent is the integer
  //   part of the surprisal.
  // - EXP = 0 with MANT != 0 encodes values in (1.0, 2.0). Deliberately not
  //   special-cased: an oversized "probability" fails normalization on its own.

  // ---- format parameters (exponent + mantissa fill one 32-bit word) ----
  localparam int unsigned PROB_EXP_W   = 6;
  localparam int unsigned PROB_MANT_W  = 32 - PROB_EXP_W;
  localparam int unsigned PROB_EXP_MAX = (1 << PROB_EXP_W) - 1;

  typedef struct packed {
    logic [PROB_EXP_W-1:0]  exp;
    logic [PROB_MANT_W-1:0] mant;
  } prob_t;

  // Minimum representable probability: 1.0 × 2^(-PROB_EXP_MAX)
  localparam prob_t PROB_MIN = '{PROB_EXP_W'(PROB_EXP_MAX), '0};


  // A probability's binary logarithm is signed fixed point in
  // one 32-bit word:
  //
  //   |31   LOG2_FRAC_W|LOG2_FRAC_W-1    0|
  //   |       INT      |       FRAC       |     value = INT.FRAC
  //
  // The integer part must keep the 7 signed bits that -PROB_EXP_MAX needs,
  // so LOG2_FRAC_W is at most 25.
  //
  // Sums of logarithms keep the same fraction bits in a doubled
  // word (log2_acc_t).

  // ---- exact accumulator geometry (1.0 = 2^PROB_FRAC_W) ----
  // two head bits: sum stays exact through the add that crosses 1.0
  localparam int unsigned PROB_FRAC_W = PROB_EXP_MAX + PROB_MANT_W;
  localparam int unsigned PROB_ACC_W  = PROB_FRAC_W + 2;

  // ---- log2 fixed-point geometry (result + accumulator words) ----
  localparam int unsigned LOG2_FRAC_W = 25;
  localparam int unsigned LOG2_W      = 32;
  localparam int unsigned LOG2_ACC_W  = 64;

  typedef logic signed [LOG2_W-1:0]     log2_t;
  typedef logic signed [LOG2_ACC_W-1:0] log2_acc_t;

endpackage : recomp_pkg
