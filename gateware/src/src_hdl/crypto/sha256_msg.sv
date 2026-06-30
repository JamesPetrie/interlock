// sha256_msg — streaming SHA-256 over an arbitrary byte message.
//
// Absorbs the message as a stream of words (byte 0 in in_data[7:0], like an
// AXI-Stream beat), packs them big-endian into 512-bit blocks, and runs each
// block through sha256_core, chaining the digest forward. On the in_last beat
// it appends FIPS 180-4 padding (0x80, zero fill, 64-bit big-endian bit length),
// emitting the one or two closing blocks, then pulses done with the final digest.
//
// in_bytes (0..4) gives the valid byte count of the current word; a partial word
// may appear anywhere in the stream (e.g. concatenating variable-length packets),
// so a word can straddle the 512-bit block boundary — M_STRADDLE carries the
// overflow bytes into the next block. A 0-byte word contributes nothing and may
// still carry in_last (an empty stream-closing beat).
//
// Decoupled absorb / compress. The absorb FSM assembles a block and hands it to
// the compressor (M_EMIT, gated on the core being free); the core latches the
// block at start, so the absorb immediately reuses block_q for the next block.
// The back-end is independent: it tracks the in-flight block's first/last flags,
// selects H_INIT vs the running chain at submit, and emits `done` when a *last*
// block finishes — it never blocks the absorb FSM. So once a message's last
// block is submitted the absorb returns to M_ABSORB and can take the *next*
// message while the previous one is still compressing: back-to-back messages run
// with no bubble between them, the compressor staying busy across the boundary.
//
// Auto-framed: there is no `start`. A message is delimited by in_last; the first
// beat after a close (or after reset) reloads H_INIT. So the caller just streams
// in_last-delimited messages and reads back one digest per message, in order.
//
// `len` is emitted alongside `done`: the byte count of the just-finished message
// (the same total folded into the padding length word), so a consumer that needs
// the message length doesn't have to count beats itself.

module sha256_msg
  import sha256_pkg::*;
(
  input  wire         clk,
  input  wire         rst_n,

  input  wire         in_valid,
  output wire         in_ready,
  // TODO how is endianness handled?
  input  wire [31:0]  in_data,      // byte 0 in [7:0]
  input  wire [2:0]   in_bytes,     // valid bytes in this word, 1..4
  input  wire         in_last,      // final word of the message

  output wire         done,         // single-cycle pulse with digest valid
  output wire [255:0] digest,
  output wire [31:0]  len           // message byte count, valid with done
);

  typedef enum logic [2:0] {
    M_ABSORB, M_EMIT, M_STRADDLE, M_PAD1, M_PAD2
  } state_t;
  state_t state, ret;               // ret: state to resume after a block is submitted

  // Block buffer, byte 0 in the MSBs ([511:504]) so it feeds the core directly.
  logic [511:0] block_q;
  logic [6:0]   boff;               // byte fill offset within the current block, 0..64
  logic [31:0]  msg_bytes;          // total message length (for the padding word)
  logic [255:0] iv;                 // running chain value across blocks

  // Straddle carry: bytes of the boundary-crossing word that spill past byte 64.
  logic [31:0]  pend;               // the crossing word
  logic [2:0]   pfit;               // how many of its bytes fit in the closing block
  logic [2:0]   pnb;                // its total byte count
  logic         plast;              // it was the message's final word

  // Block hand-off bookkeeping.
  logic         comp_busy;          // a block is in flight (core_start..core_done)
  logic         msg_first;          // the next block to submit is its message's first
  logic         emit_last;          // the block heading into M_EMIT closes the message
  logic         infl_last;          // the in-flight block closes the message
  logic [31:0]  infl_bytes;         // byte count of the in-flight (closing) message

  wire [63:0] bitlen = {32'b0, msg_bytes} << 3;
  wire        pad_inline = (boff <= 7'd55);   // length fits in the first pad block

  // sha256_core handshake. core_iv is H_INIT for a message's first block, the
  // running chain otherwise. It is latched at submit (core_iv_q) so it lines up
  // with the registered core_start the cycle the core actually reads it — the
  // combinational select would otherwise race msg_first flipping in M_EMIT.
  logic         core_start;
  logic [255:0] core_iv_q;
  wire          core_done;
  wire [255:0]  core_digest;

  sha256_core u_core (
    .clk    (clk),
    .rst_n  (rst_n),
    .start  (core_start),
    .iv     (core_iv_q),
    .block  (block_q),
    .done   (core_done),
    .digest (core_digest)
  );

  logic         done_r;
  logic [255:0] digest_r;
  logic [31:0]  len_r;
  assign done     = done_r;
  assign digest   = digest_r;
  assign len      = len_r;
  assign in_ready = (state == M_ABSORB);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state      <= M_ABSORB;
      ret        <= M_ABSORB;
      block_q    <= '0;
      boff       <= '0;
      msg_bytes  <= '0;
      iv         <= '0;
      pend       <= '0;
      pfit       <= '0;
      pnb        <= '0;
      plast      <= 1'b0;
      comp_busy  <= 1'b0;
      msg_first  <= 1'b1;
      emit_last  <= 1'b0;
      infl_last  <= 1'b0;
      infl_bytes <= '0;
      core_start <= 1'b0;
      core_iv_q  <= '0;
      done_r     <= 1'b0;
      digest_r   <= '0;
      len_r      <= '0;
    end else begin
      core_start <= 1'b0;
      done_r     <= 1'b0;

      // back-end: a block finished compressing. Chain the digest forward, free
      // the core, and emit the result if this block closed a message.
      if (core_done) begin
        iv        <= core_digest;
        comp_busy <= 1'b0;
        if (infl_last) begin
          done_r   <= 1'b1;
          digest_r <= core_digest;
          len_r    <= infl_bytes;
        end
      end

      case (state)
        M_ABSORB: begin
          if (in_valid) begin
            // write the bytes that fit below byte 64; any that overflow are
            // carried to the next block by M_STRADDLE
            for (int k = 0; k < 4; k++)
              if (k < int'(in_bytes) && (int'(boff) + k) < 64)
                block_q[8*(63 - (boff + k)) +: 8] <= in_data[8*k +: 8];
            msg_bytes <= msg_bytes + {29'b0, in_bytes};

            if (boff + {4'b0, in_bytes} < 7'd64) begin
              // room to spare — keep absorbing (or pad if this was the last word)
              boff <= boff + {4'b0, in_bytes};
              if (in_last) begin emit_last <= 1'b0; state <= M_PAD1; end
            end else if (boff + {4'b0, in_bytes} == 7'd64) begin
              // exact fill — submit the block, then resume absorbing or pad
              boff      <= '0;
              emit_last <= 1'b0;
              ret       <= state_t'(in_last ? M_PAD1 : M_ABSORB);
              state     <= M_EMIT;
            end else begin
              // straddle — submit the filled block, finish the word after
              pend      <= in_data;
              pfit      <= (7'd64 - boff);
              pnb       <= in_bytes;
              plast     <= in_last;
              boff      <= '0;
              emit_last <= 1'b0;
              ret       <= M_STRADDLE;
              state     <= M_EMIT;
            end
          end
        end

        // submit the assembled block to the core when it is free; the core latches
        // block_q at start, so the absorb may refill it immediately afterward. A
        // last block resets the message context and returns to M_ABSORB (ready to
        // take the next message's first beat right away) while the back-end
        // finalises this one in the background.
        M_EMIT: if (!comp_busy) begin
          core_start <= 1'b1;
          core_iv_q  <= msg_first ? SHA256_H_INIT : iv;   // latched with core_start
          comp_busy  <= 1'b1;
          infl_last  <= emit_last;
          infl_bytes <= msg_bytes;                 // full count on the closing block (read at done)
          msg_first  <= emit_last ? 1'b1 : 1'b0;   // next first block reloads H_INIT
          if (emit_last) begin
            boff      <= '0;
            msg_bytes <= '0;
            state     <= M_ABSORB;
          end else begin
            state <= ret;
          end
        end

        M_STRADDLE: begin
          // write the overflow bytes (pend[pfit .. pnb-1]) at the new block start
          for (int k = 0; k < 4; k++)
            if (k >= int'(pfit) && k < int'(pnb))
              block_q[8*(63 - (k - pfit)) +: 8] <= pend[8*k +: 8];
          boff  <= {4'b0, pnb - pfit};
          state <= state_t'(plast ? M_PAD1 : M_ABSORB);
        end

        M_PAD1: begin
          // 0x80, zero fill, and (when it fits) the length, into the tail of
          // the current block; bytes below boff keep the message data.
          for (int i = 0; i < 64; i++) begin
            if (i == int'(boff))
              block_q[8*(63 - i) +: 8] <= 8'h80;
            else if (i > int'(boff)) begin
              if (pad_inline && i >= 56)
                block_q[8*(63 - i) +: 8] <= bitlen[8*(63 - i) +: 8];
              else
                block_q[8*(63 - i) +: 8] <= 8'h00;
            end
          end
          emit_last <= pad_inline;                       // inline pad closes the message
          ret       <= state_t'(pad_inline ? M_ABSORB : M_PAD2);
          state     <= M_EMIT;
        end

        M_PAD2: begin
          // second pad block: all zero but the trailing 64-bit length
          block_q   <= {448'b0, bitlen};
          emit_last <= 1'b1;
          ret       <= M_ABSORB;
          state     <= M_EMIT;
        end

        default: state <= M_ABSORB;
      endcase
    end
  end

endmodule
