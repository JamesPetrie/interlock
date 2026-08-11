// recomp_feed — recomputation dataplane (see docs/recomp_feed.md).

module recomp_feed
  import canon_pkg::*;
  import recomp_pkg::*;
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
  input  wire        tvalid_e,
  output wire        tready_e,
  input  wire [31:0] tdata_e,
  input  wire [3:0]  tkeep_e,
  input  wire        tlast_e,

  // Entropy result dispatch
  output wire        out_valid,
  output wire [63:0] id_out,
  output wire [63:0] u_out
);

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned HDR_BEATS     =  CANON_RSP_HDR_BYTES / 4;
  localparam int unsigned HB_W          = $clog2(HDR_BEATS);
  localparam int unsigned TOK_MAX       = (CANON_PKT_BYTES_MAX - CANON_RSP_HDR_BYTES) / CANON_TOK_BYTES;
  // RAM depth: one max canonical payload's worth of tokens
  localparam int unsigned ADDR_W        = $clog2(TOK_MAX);
  localparam int unsigned TOK_MSBYTE    = CANON_TOK_BYTES - 1;

  typedef logic [ADDR_W-1:0] tok_addr_t;

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  typedef enum logic [3:0] {
    ARMED,     // at a bucket boundary: the next packet is the challenged response
    CHL,       // the challenged response: sanitized header out, payload captured
    CTX,       // forward the context verbatim; the closing swap ends the bucket
    LEN_EST,   // await the length estimate
    TIME_EST,  // await the timing estimate
    TOK_EST,   // await a token estimate, then reveal (or finish)
    EMIT,      // emit one TOKEN frame
    DISPATCH,  // wait out the last scored estimate, then dispatch Û
    ALIGN      // drop whole packets until the next swap beat re-arms
  } state_t;
  state_t state;

  logic [15:0] bcnt;          // ingress beat index (word address)

  // captured response, split: the header in a register (it must be sliceable
  // for parsing), the payload in a token-wide RAM (one write + one read port,
  // BRAM-inferable). payload bytes stream one per cycle into the token
  // assembler in CHL, so a token straddling a beat boundary costs nothing.
  canon_rsp_hdr_bits_t   hdr_bits;
  canon_tok_t            tok_mem [0:TOK_MAX-1];
  logic [1:0]            byte_i;        // byte within the held payload beat
  logic [1:0]            tok_i;         // byte within the token being assembled
  // token bytes assembled so far (byte 0 first); the completing byte goes
  // straight into the RAM write, so only CANON_TOK_BYTES-1 bytes stage here
  canon_tok_t  tok_buf;
  canon_tok_t  tok_buf_next;
  tok_addr_t   wr_tok;        // next token entry to write
  tok_addr_t   idx;           // reveal position
  logic        est_phase;     // estimate word parity: 0 = value, 1 = probability

  always_comb begin
    tok_buf_next = tok_buf;
    tok_buf_next[8*tok_i +: 8] = tdata_s[8*byte_i +: 8];
  end

  typedef enum logic [1:0] {
    SC_IDLE,        // wait for a frame to complete
    SC_WAIT_NORM,   // wait out norm_chk, pick p, clear norm, start log2
    SC_WAIT_LOG     // wait for log2_iter, accumulate Û
  } sc_state_t;
  sc_state_t sc_state;

  logic        val_hit_q;     // previous estimate word matched the actual value
  logic        p_latched;     // any probability latched (match or catch-all)
  logic [31:0] p_score;       // that probability, numeric (byte-swapped) form
  log2_acc_t   u_acc;         // Û accumulator, Q.LOG2_FRAC_W

  // parse the captured header. the payload holds tok_total = PLD_LEN /
  // CANON_TOK_BYTES tokens; the other fields (id, bucket-difference)
  // are for the scoring stage.
  wire canon_rsp_hdr_t resp_hdr  = canon_rsp_hdr_from_wire_bits(hdr_bits);
  wire tok_addr_t      tok_total = tok_addr_t'(resp_hdr.pld_len / CANON_TOK_BYTES);
  wire canon_bkt_t     bkt_diff  = canon_bkt_t'(resp_hdr.reserved0);

  // ------------------------------------------------------------------
  // Combinational port control
  // ------------------------------------------------------------------
  wire armed     = (state == ARMED);
  wire chl       = (state == CHL);
  wire ctx       = (state == CTX);
  wire emit_tok  = (state == EMIT);

  wire tok_last  = (tok_i == '0); // tok_i goes N-1...0

  // bucket delimiter: the empty swap beat batch_buffer re-inserts — the only
  // beat with no bytes (a real packet's last beat always keeps >= 1)
  wire swap_beat = (tkeep_s == '0) && tlast_s;

  // the challenged response's phases within CHL
  wire chl_hdr   = chl && (bcnt < 16'(HDR_BEATS));

  // sanitize mask, built field-wise on the header struct (no hardcoded
  // field order or offsets) and serialized to wire byte order by the
  // package: only ID passes through — PLD_LEN and RESERVED are the answers
  // to the length and timing estimates, and BUCKET is zeroed too, so the
  // header carries nothing but the challenge identity. Constant, so it
  // folds away in synthesis.
  canon_rsp_hdr_t san_flds;
  always_comb begin
    san_flds        = '0;
    san_flds.id     = '1;
  end
  wire canon_rsp_hdr_bits_t san_mask = canon_rsp_hdr_to_wire_bits(san_flds);
  // the mask word walking with the header beats
  wire [31:0] san_word = san_mask[32 * bcnt[HB_W-1:0] +: 32];

  // Ethernet transmits with BE byte order while AXI-Stream uses LE, so numeric values ride swapped
  wire [31:0] tdata_e_ord = {tdata_e[7:0], tdata_e[15:8], tdata_e[23:16], tdata_e[31:24]};
  // estimate port: consumed only in the estimate states, one beat per cycle,
  // held off while the scorer still works on the previous frame;
  // back-pressured elsewhere (the MAC FIFO holds early arrivals)
  // (explicit equality — Icarus does not support "inside" expressions)
  wire in_est = (state == LEN_EST) || (state == TIME_EST) || (state == TOK_EST);
  assign tready_e = in_est && (sc_state == SC_IDLE);
  wire est_fire = tvalid_e && tready_e;
  wire est_done = est_fire && tlast_e;   // current estimate frame complete

  // the actual value for the current estimate, in raw wire-byte form: tokens
  // compare verbatim, numeric values ride big-endian (hence the swap).
  // REVISIT LENGTH/TIMING value encodings (doc open items).
  wire canon_tok_t cur_tok = tok_mem[idx];
  wire [31:0] act_val = (state == LEN_EST)  ? 32'(tok_total)
                      : (state == TIME_EST) ? bkt_diff
                      :                       32'(cur_tok);

  // normalization check: each frame's probabilities accumulate
  wire        norm_fail, norm_busy;
  // final sampling of the probablity (clears norm_chk)
  wire        sc_read = (sc_state == SC_WAIT_NORM) && !norm_busy;

  norm_chk u_norm (
    .clk      (clk),
    .rst_n    (rst_n),
    .clr      (sc_read),
    .add      (est_fire && (est_phase || tlast_e)),
    .catchall (tlast_e),
    .p        (tdata_e_ord),
    .fail     (norm_fail),
    .busy     (norm_busy)
  );

  wire [31:0] sc_p    = norm_fail ? PROB_MIN : p_score;
  wire        log_done;
  log2_t      log_res;

  log2_iter u_log2 (
    .clk   (clk),
    .rst_n (rst_n),
    .start (sc_read),
    .p     (sc_p),
    .done  (log_done),
    .res   (log_res)
  );

  // Û dispatch: single-cycle pulse — u_out shows the final accumulator for
  // exactly the cycle DISPATCH exits (the last frame's scoring has landed,
  // the clear follows)
  assign out_valid = (state == DISPATCH) && (sc_state == SC_IDLE);
  assign id_out    = resp_hdr.id;
  assign u_out     = 64'(u_acc);

  wire _unused = &{1'b0, tkeep_e, resp_hdr.bucket};

  // ---- packet port routing ----
  // A slave beat either FORWARDS to the master — the handshake passes
  // straight through — or is consumed LOCALLY: swap beats, captured payload
  // byte-cycles, and everything in the drop states.
  wire s_fwd = chl_hdr || (ctx && !swap_beat);

  // local consumption rate: while armed only the delimiter is taken (the
  // response that follows is left for CHL to take from its beat #0); a
  // payload beat completes when its last byte is consumed (quarter rate —
  // harmless, one packet per challenge); full rate everywhere else, so the
  // batch_buffer drain never stalls (see the header note)
  assign tready_s = s_fwd ? tready_m
                  : armed ? swap_beat
                  : chl   ? (wr_tok != tok_total-1 ? byte_i == 2'd3 : tok_last)
                  :         1'b1;
  wire in_fire = tvalid_s && tready_s;

  // master port: the mux tree mirrors tvalid_m's two sources — forwarded
  // beats ride the slave handshake (challenge header sanitized and
  // reframed, context verbatim), the else branch is EMIT's one-beat token
  // reveal (never concurrent with forwarding).
  // The challenge header's framing is its own: with the payload stripped
  // (not masked — the packet's size alone would leak the length estimate's
  // answer) the packet ends at the last header beat, and the length on
  // tuser is the header's.
  wire [15:0] chl_user = (bcnt == '0) ? 16'(CANON_RSP_HDR_BYTES) : 16'h0;
  // Ethernet uses BE byte order but AXI-Stream uses LE,
  // so tdata LSBYTE is token MSBYTE
  wire [31:0] tdata_m_drv_ord = {idx, cur_tok}; // REVISIT support CANON_TOK_BYTES > 2?
  wire [31:0] tdata_m_drv     = {tdata_m_drv_ord[ 7: 0],
                                 tdata_m_drv_ord[15: 8],
                                 tdata_m_drv_ord[23:16],
                                 tdata_m_drv_ord[31:24]};
  assign tvalid_m = (s_fwd && tvalid_s) || emit_tok;
  assign tdata_m  = s_fwd ? (chl ? (tdata_s & san_word) : tdata_s)
                  :         tdata_m_drv;
  assign tkeep_m  = s_fwd ? tkeep_s : 4'b1111;
  assign tuser_m  = s_fwd ? (chl ? chl_user : tuser_s)
                  :         16'd4;
  assign tlast_m  = s_fwd ? (chl ? (bcnt == 16'(HDR_BEATS - 1)) : tlast_s)
                  :         1'b1;
  wire out_fire = tvalid_m && tready_m;

  // ------------------------------------------------------------------
  // Sequential
  // ------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      // the stream starts at a bucket boundary (batch_buffer emits nothing
      // before its first drain, and the swap beat trails each bucket), so
      // reset lands armed
      state      <= ARMED;
      bcnt       <= '0;
      hdr_bits   <= '0;
      byte_i     <= '0;
      tok_i      <= TOK_MSBYTE;
      tok_buf    <= '0;
      wr_tok     <= '0;
      idx        <= '0;
      est_phase  <= 1'b0;
      sc_state   <= SC_IDLE;
      val_hit_q  <= 1'b0;
      p_latched  <= 1'b0;
      p_score    <= '0;
      u_acc      <= '0;
    end else begin

      // ingress beat counter (shared across all states)
      if (in_fire) begin
        bcnt <= !tlast_s ? bcnt + 16'h1 : 16'h0;
      end

      case (state)
        // ---- at a bucket boundary: swap beats are consumed in place
        //      (empty buckets), the first packet is the challenged
        //      response — left on the port for CHL to take from beat #0 ----
        ARMED: if (tvalid_s && !swap_beat) begin
          state <= CHL;
        end

        // ---- the challenged response: header beats into the register
        //      (forwarding sanitized as they go), payload bytes one per
        //      cycle into tok_buf, each completed token written to the RAM
        //      (payload never forwarded) ----
        CHL: if (tvalid_s) begin
          if (chl_hdr) begin // header capture
            hdr_bits[32 * bcnt[HB_W-1:0] +: 32] <= tdata_s;
          end else begin // payload capture
            // Processing tokens 1 byte at a time (tready pulled low meanwhile)
            // Write to the memory only when a full token is ready
            tok_buf <= tok_buf_next;
            if (tok_last) begin
              tok_mem[wr_tok] <= tok_buf_next;
              wr_tok <= wr_tok + 1'b1;
            end
            // Ethernet uses BE byte order but AXI-Stream uses LE,
            // so tdata LSBYTE is token MSBYTE
            byte_i <= byte_i + 2'd1;
            tok_i  <= tok_last ? TOK_MSBYTE : tok_i - 1'b1;
          end
          // packet complete — only once the beat is consumed (a payload
          // tlast beat is held for its 4 byte-cycles)
          if (tready_s && tlast_s) begin
            // leave-state cleanup (tok_i back to the per-token start, so the
            // next challenge's capture is aligned)
            byte_i <= '0;
            tok_i  <= TOK_MSBYTE;
            wr_tok <= '0;
            state  <= CTX;
          end
        end

        // ---- context: forward verbatim; the closing swap beat is consumed
        //      (never forwarded) and arms the estimate expectation ----
        CTX: if (in_fire && swap_beat) begin
          state <= LEN_EST;
        end

        // ---- initial estimates: length, then timing ----
        LEN_EST: if (est_done) begin
          state <= TIME_EST;
        end

        TIME_EST: if (est_done) begin
          // skip the token loop if the payload is empty
          state <= state_t'( (tok_total != 0) ? TOK_EST : DISPATCH );
        end

        // ---- one estimate → reveal the next token, or finish once the buffer is exhausted ----
        TOK_EST: if (est_done) begin
          // reveal the next token, unless the last one is reached (no need to reveal the last)
          state <= state_t'( (idx < tok_total-1) ? EMIT : DISPATCH );  // tok_total must not be 0 here
        end

        EMIT: if (out_fire) begin
          idx   <= idx + 1'b1;
          state <= TOK_EST;
        end

        // ---- feeding complete: wait out the final frame's scoring, then
        //      dispatch Û (one-cycle pulse) and re-align ----
        DISPATCH: if (out_valid) begin
          idx   <= '0;
          u_acc <= '0;
          state <= ALIGN;
        end

        // ---- keep dropping until the next swap beat: arming is
        //      positional, so the block must re-sync on a bucket boundary,
        //      never mid-bucket ----
        ALIGN: if (in_fire && swap_beat) begin
          state <= ARMED;
        end

        default: state <= ALIGN;
      endcase

      // estimate first flag, word parity (value/probability alternate),
      // value hit flag and score latch
      if (est_fire) begin
        est_phase <= tlast_e ? 1'b0 : !est_phase;

        // Capture if the value of the next estimate matches the actual token value
        if (!est_phase) begin
          val_hit_q <= (act_val == tdata_e_ord);
        end else begin
          val_hit_q <= 1'b0;
        end

        // latch on a hit or catch-all, avoid latching multiple times
        if ((val_hit_q || tlast_e) && !p_latched) begin
          p_score   <= tdata_e_ord;
          p_latched <= 1'b1;
        end
      end

      // scorer: one estimate at a time — tready_e holds the next frame off
      // until the accumulate completes, so the latches above cannot be
      // overwritten mid-score
      case (sc_state)
        SC_IDLE: if (est_done) begin
          // Normalization already runnin at this point, wait for it to finish
          sc_state <= SC_WAIT_NORM;
        end
        SC_WAIT_NORM: if (!norm_busy) begin
          // Logarithm is automatically triggered by normalize completion, wait for it to finish
          sc_state <= SC_WAIT_LOG;
        end
        SC_WAIT_LOG:  if (log_done) begin
          u_acc     <= u_acc - log2_acc_t'(log_res);  // accumulate -log2(p)
          p_latched <= 1'b0;
          sc_state  <= SC_IDLE;
        end
        default: sc_state <= SC_IDLE;
      endcase
    end
  end

endmodule
