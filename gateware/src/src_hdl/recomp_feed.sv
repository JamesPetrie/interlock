// recomp_feed — recomputation dataplane (see docs/recomp_feed.md).
//
// FIRST VERSION — forwarding + token-feeding loop only. Estimate *contents*
// are not processed yet: normalization, surprisal, and the Û accumulator are
// deferred (docs/recomp_feed.md "Scoring" is a TODO). The estimate port is
// consumed and its packet boundaries used purely to pace the loop.
//
// Packet port (from batch_buffer): canonical packets, tuser = total byte
// length @ beat #0. Every packet is forwarded verbatim toward the enclosure
// (eth_reframe) EXCEPT the challenged response, which is preceded in the
// stream by a CTRL packet — an all-zero header, no payload. The CTRL packet
// is itself forwarded (it is the recomputation START trigger) and arms the
// capture of the packet that follows.
//
// The captured response is not forwarded: its header lands in a register
// (parsed for PLD_LEN → tok_total; bucket and id/bucket-difference for the
// scoring stage) and its payload streams byte-serially into a token-wide
// RAM — one entry per token, so CANON_TOK_BYTES of 2, 3 (beat-straddling)
// or 4 all work and the reveal is a plain indexed read.
// Tokens are fed back one at a time as one-beat TOKEN frames. After START
// the enclosure emits a length estimate, a timing estimate, then a token
// estimate per position; the loop reveals one token per token estimate until
// the buffer is exhausted, then waits for the terminal (EOS) estimate and
// dispatches Û.
//
// Estimate pacing: at most one estimate is held un-consumed. The estimate port
// is back-pressured (tready_e = !est_seen) while a completed estimate awaits a
// consumer, so the upstream MAC FIFO holds the rest — no counter, no loss even
// when estimates arrive during CAP.

module recomp_feed
  import canon_pkg::*;
(
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave (from batch_buffer): tuser = total byte length @ beat #0
  input  wire        tvalid_s,
  output wire        tready_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire [15:0] tuser_s,

  // AXI-Stream master (to the enclosure-facing eth_reframe): len @ beat #0
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m,

  // AXI-Stream slave (estimates from the enclosure-facing eth_deframe).
  // v1 ignores contents; only packet boundaries (tlast) pace the loop.
  input  wire        tvalid_e,
  output wire        tready_e,
  input  wire [31:0] tdata_e,
  input  wire [3:0]  tkeep_e,
  input  wire        tlast_e
);

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned HDR_BEATS     =  CANON_RSP_HDR_BYTES / 4;
  localparam int unsigned HB_W          = $clog2(HDR_BEATS);
  localparam int unsigned TOK_MAX       = (CANON_PKT_BYTES_MAX - CANON_RSP_HDR_BYTES) / CANON_TOK_BYTES;
  // RAM depth: one max canonical payload's worth of tokens
  localparam int unsigned ADDR_W        = $clog2(TOK_MAX);
  // tkeep for a one-beat TOKEN frame — the low CANON_TOK_BYTES lanes valid
  // (assumes CANON_TOK_BYTES <= 4, i.e. one token per AXIS beat)
  localparam logic [3:0]  TOK_KEEP      = 4'((32'h1 << CANON_TOK_BYTES) - 32'h1);

  typedef logic [ADDR_W-1:0] tok_addr_t;

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  typedef enum logic [2:0] {
    FWD,       // forward packets verbatim; detect the CTRL marker
    CAP,       // capture the challenged response into the RAM
    LEN_EST,   // await the length estimate
    TIME_EST,  // await the timing estimate
    TOK_EST,   // await a token estimate, then reveal (or finish)
    EMIT,      // emit one TOKEN frame
    DISPATCH   // await the terminal (EOS) estimate, then dispatch Û
  } state_t;
  state_t state;

  logic [15:0] bcnt;          // beat index = word address within the packet
  logic        all_zero;      // header seen so far is all-zero (CTRL marker)

  // captured response, split: the header in a register (it must be sliceable
  // for parsing — and for CANON_TOK_BYTES = 3 it would not even align to RAM
  // entries), the payload in a token-wide RAM (one write + one read port,
  // BRAM-inferable). payload bytes stream one per cycle into the token
  // assembler in CAP, so a token straddling a beat boundary costs nothing.
  canon_rsp_hdr_bits_t          hdr_bits;              // wire-order header copy
  logic [8*CANON_TOK_BYTES-1:0] tok_mem [0:TOK_MAX-1];
  logic [1:0]  byte_i;        // byte within the held payload beat
  logic [1:0]  tok_i;         // byte within the token being assembled
  logic [23:0] tok_buf;       // token bytes assembled so far (byte 0 first)
  logic [15:0] wr_tok;        // next token entry to write
  tok_addr_t   idx;           // reveal position
  logic        est_seen;      // one completed estimate awaits a consumer

  // parse the captured header. the payload holds tok_total = PLD_LEN /
  // CANON_TOK_BYTES tokens; the other fields (bucket, id/bucket-difference)
  // are for the scoring stage.
  wire canon_rsp_hdr_t resp_hdr  = canon_rsp_hdr_from_wire_bits(hdr_bits);
  wire tok_addr_t      tok_total = tok_addr_t'(resp_hdr.pld_len / CANON_TOK_BYTES);

  // ------------------------------------------------------------------
  // Combinational port control
  // ------------------------------------------------------------------
  wire fwd       = (state == FWD);
  wire cap       = (state == CAP);
  wire emit_tok  = (state == EMIT);
  wire beat_zero = (tdata_s == 32'h0);

  wire       tok_last = (tok_i == 2'(CANON_TOK_BYTES-1));

  // estimate port: accept one estimate, then back-pressure until it is consumed
  // (the MAC FIFO holds the rest). set/consume are mutually exclusive.
  assign tready_e = !est_seen;
  // v1 does not process estimate contents or the non-length header fields
  // until scoring lands ("Scoring" TODO)
  wire _unused = &{1'b0, tdata_e, tkeep_e,
                   resp_hdr.bucket, resp_hdr.id, resp_hdr.reserved0};

  // packet port ready: gated by the master when forwarding; while capturing,
  // header beats go full rate and a payload beat completes when its last
  // byte is consumed (quarter rate — harmless, one packet per challenge);
  // stalled during the loop
  assign tready_s = fwd ? tready_m
                  : cap ? ((bcnt < 16'(HDR_BEATS)) || (byte_i == 2'd3))
                  :       1'b0;
  wire in_fire = tvalid_s && tready_s;

  // master port: forwarded packet passthrough, or a one-beat token reveal
  wire fwd_beat = fwd && tvalid_s;
  assign tvalid_m = fwd_beat || emit_tok;
  assign tdata_m  = fwd ? tdata_s : 32'(tok_mem[idx]); // TODO add idx to the token frame
  assign tkeep_m  = fwd ? tkeep_s : TOK_KEEP;
  assign tlast_m  = fwd ? tlast_s : 1'b1;
  assign tuser_m  = (fwd && bcnt == 16'h0) ? tuser_s
                  : emit_tok               ? 16'(CANON_TOK_BYTES)
                  :                          16'h0;
  wire out_fire = tvalid_m && tready_m;

  // ------------------------------------------------------------------
  // Sequential
  // ------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state      <= FWD;
      bcnt       <= '0;
      all_zero   <= 1'b1;
      hdr_bits   <= '0;
      byte_i     <= '0;
      tok_i      <= '0;
      tok_buf    <= '0;
      wr_tok     <= '0;
      idx        <= '0;
      est_seen   <= 1'b0;
    end else begin
      case (state)
        // ---- forward verbatim; an all-zero header packet is the CTRL
        //      marker (forwarded as START), arming the capture ----
        FWD: if (in_fire) begin
          if (!tlast_s) begin
            all_zero <= all_zero && beat_zero;
            bcnt     <= bcnt + 16'h1;
          end else begin
            bcnt     <= '0;
            all_zero <= 1'b1;                    // reset for the next packet
            if (all_zero && beat_zero) state <= CAP;
          end
        end

        // ---- capture: header beats into the register at full rate; payload
        //      bytes stream one per cycle into tok_buf, each completed token
        //      written to the RAM ----
        CAP: if (tvalid_s) begin
          if (bcnt < 16'(HDR_BEATS)) begin
            hdr_bits[32 * bcnt[HB_W-1:0] +: 32] <= tdata_s;
          end else begin
            // Processing tokens 1 byte at a time (tready pulled low meanwhile)
            // Write to the memory only when a full token is ready
            if (!tok_last) begin
              tok_buf[8*tok_i +: 8] <= tdata_s[8*byte_i +: 8];
            end else begin
              tok_mem[wr_tok[ADDR_W-1:0]] <= {tdata_s[8*byte_i +: 8], tok_buf[8*(CANON_TOK_BYTES-1)-1:0]};
              wr_tok <= wr_tok + 16'h1;
            end
            byte_i <= byte_i + 2'd1;
            tok_i  <= tok_last ? 2'd0 : tok_i + 2'd1;
          end
          // beat consumed: a header beat, or a payload beat's last byte
          if (in_fire) begin
            if (tlast_s) begin
              // leave-state cleanup
              idx    <= '0;
              bcnt   <= '0;
              byte_i <= '0;
              tok_i  <= '0;
              wr_tok <= '0;
              state  <= LEN_EST;
            end else begin
              bcnt <= bcnt + 16'h1;
            end
          end
        end

        // ---- initial estimates: length, then timing ----
        LEN_EST: if (est_seen) begin
          est_seen <= 1'b0;
          state    <= TIME_EST;
        end

        TIME_EST: if (est_seen) begin
          est_seen <= 1'b0;
          state    <= TOK_EST;
        end

        // ---- one estimate → reveal the next token, or finish once the buffer
        //      is exhausted (the terminal EOS estimate is consumed here too) ----
        TOK_EST: if (est_seen) begin
          est_seen <= 1'b0;
          state    <= state_t'(
                      (idx             == tok_total)    ? DISPATCH :  // (non-full packet) received estimate even for token N+1 (EOS)
                      (idx             <  tok_total-1)  ? EMIT :      // not received estimate for token N yet (Note: total-1 underflows on empty payload packets)
                      (int'(tok_total) != TOK_MAX)      ? EMIT :      // non-full packet and not yet received estimate for token N+1 (EOS)
                                                          DISPATCH);  // full packet, estimate for token N+1 is not needed
                                                                      // we don't know if a next packet would continue the message or not
        end

        EMIT: if (out_fire) begin
          idx   <= idx + 1'b1;
          state <= TOK_EST;
        end

        // ---- feeding complete → dispatch Û, then idle ----
        DISPATCH: begin
          // TODO dispatch the accumulated Û once scoring lands
          state <= FWD;
        end

        default: state <= FWD;
      endcase

      // one estimate outstanding: set here on completion; each waiting state
      // above clears it (mutually exclusive — tready_e is low while est_seen)
      if (tvalid_e && tready_e && tlast_e) est_seen <= 1'b1;
    end
  end

endmodule
