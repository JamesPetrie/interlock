// sha256_core — iterative SHA-256 block compressor (one 512-bit block per
// invocation), the commitment block's swappable crypto primitive.
//
// Computes the FIPS 180-4 compression: digest = iv + round^64(iv, block),
// where iv is the chaining value (H before this block) and the 64 working-var
// rounds are evaluated four per cycle over the message schedule. A full message
// is hashed by feeding its blocks in order, each with iv = the previous block's
// digest (the first block uses SHA256_H_INIT) — that sequencing lives in
// sha256_msg.
//
// Two rounds per cycle: each cycle chains two sha256_round evaluations
// combinationally and advances the schedule window by two words. The message
// schedule is held as a 16-word sliding window (w[0]=W[t], extended by
// sha256_w_next), so no 64-word expansion is stored. ~32 cycles per block (vs
// ~64 for one-round-per-cycle), trading combinational depth for throughput.
// This is the reference inferred-fabric core; the device hardware crypto block
// can replace it behind the same start / iv / block / done handshake (see
// traffic_commit open item).
//
// Handshake: pulse start with iv/block held; done pulses for one cycle
// alongside the registered digest ~16 cycles later.

module sha256_core
  import sha256_pkg::*;
(
  input  wire         clk,
  input  wire         rst_n,

  input  wire         start,        // begin compressing block on top of iv
  input  wire [255:0] iv,           // chaining value (H before this block)
  input  wire [511:0] block,        // 512-bit message block, W[0] in the MSBs

  output wire         done,         // single-cycle pulse with digest valid
  output wire [255:0] digest
);

  // Word-wise (mod 2^32) addition of two packed 8-word vectors.
  function automatic logic [255:0] add8(input logic [255:0] x, y);
    for (int i = 0; i < 8; i++)
      add8[32*i +: 32] = x[32*i +: 32] + y[32*i +: 32];
  endfunction

  typedef enum logic [1:0] { C_IDLE, C_RUN, C_FIN } state_t;
  state_t state;

  logic [255:0] s;          // working vars {a..h}
  logic [255:0] iv_r;       // chaining value, held for the final add
  logic [31:0]  w [0:15];   // message-schedule sliding window, w[0]=W[t]
  logic [5:0]   t;          // round index 0..63

  logic         done_r;
  logic [255:0] digest_r;
  assign done   = done_r;
  assign digest = digest_r;

  // Two message-schedule words ahead of the window, computed combinationally.
  // Both derive only from stored window words, so neither chains off the other.
  wire [31:0] w16 = sha256_w_next(w[0], w[1], w[9],  w[14]);
  wire [31:0] w17 = sha256_w_next(w[1], w[2], w[10], w[15]);

  // Two compression rounds chained combinationally over W[t..t+1] = w[0..1].
  wire [255:0] s_r0 = sha256_round(s,    sha256_k(int'(t))     + w[0]);
  wire [255:0] s_r1 = sha256_round(s_r0, sha256_k(int'(t) + 1) + w[1]);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= C_IDLE;
      s        <= '0;
      iv_r     <= '0;
      t        <= '0;
      done_r   <= 1'b0;
      digest_r <= '0;
      for (int i = 0; i < 16; i++) w[i] <= '0;
    end else begin
      done_r <= 1'b0;

      case (state)
        C_IDLE: begin
          if (start) begin
            s    <= iv;
            iv_r <= iv;
            t    <= '0;
            for (int i = 0; i < 16; i++) w[i] <= block[32*(15-i) +: 32];
            state <= C_RUN;
          end
        end

        C_RUN: begin
          // two compression rounds over W[t..t+1]; slide the window by two
          s <= s_r1;
          for (int i = 0; i < 14; i++) w[i] <= w[i+2];
          w[14] <= w16;
          w[15] <= w17;
          t     <= t + 6'd2;
          if (t == 6'd62) state <= C_FIN;
        end

        C_FIN: begin
          digest_r <= add8(iv_r, s);
          done_r   <= 1'b1;
          state    <= C_IDLE;
        end

        default: state <= C_IDLE;
      endcase
    end
  end

endmodule
