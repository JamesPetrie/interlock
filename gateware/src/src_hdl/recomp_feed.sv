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
    FWD,       // forward packets verbatim; detect the CTRL marker
    CTRL_CHK,  // check for a CTRL packet
    CAP,       // capture the challenged response into the RAM
    LEN_EST,   // await the length estimate
    TIME_EST,  // await the timing estimate
    TOK_EST,   // await a token estimate, then reveal (or finish)
    EMIT,      // emit one TOKEN frame
    DISPATCH,  // wait out the last scored estimate, then dispatch Û
    ALIGN      // drop until ingress silence at a packet boundary
  } state_t;
  state_t state;

  logic [15:0] bcnt;          // ingress beat index (word address)

  // captured response, split: the header in a register (it must be sliceable
  // for parsing), the payload in a token-wide RAM (one write + one read port,
  // BRAM-inferable). payload bytes stream one per cycle into the token
  // assembler in CAP, so a token straddling a beat boundary costs nothing.
  // Note: header storage is reused to detect the CTRL packet
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
  // CANON_TOK_BYTES tokens; the other fields (bucket, id/bucket-difference)
  // are for the scoring stage.
  wire canon_rsp_hdr_t resp_hdr  = canon_rsp_hdr_from_wire_bits(hdr_bits);
  wire tok_addr_t      tok_total = tok_addr_t'(resp_hdr.pld_len / CANON_TOK_BYTES);
  wire canon_bkt_t     bkt_diff  = canon_bkt_t'(resp_hdr.reserved0);

  // ------------------------------------------------------------------
  // Combinational port control
  // ------------------------------------------------------------------
  wire fwd       = (state == FWD);
  wire ctrl_chk  = (state == CTRL_CHK);
  wire cap       = (state == CAP);
  wire emit_tok  = (state == EMIT);

  wire tok_last  = (tok_i == '0); // tok_i goes N-1...0

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

  // the non-length header fields beyond the timing word wait for the scoring
  // encodings to be pinned
  wire _unused = &{1'b0, tkeep_e, resp_hdr.bucket, resp_hdr.id};

  // packet port ready: gated by the master when forwarding; while capturing,
  // header beats go full rate and a payload beat completes when its last
  // byte is consumed (quarter rate — harmless, one packet per challenge);
  // full rate during the loop, where consumed beats are dropped so the
  // batch_buffer drain never stalls (see the header note)
  assign tready_s = fwd      ? tready_m
                  : ctrl_chk ? 1'b0
                  : cap      ? ((bcnt < 16'(HDR_BEATS)) || (wr_tok != tok_total-1 ? byte_i == 2'd3 : tok_last))
                  :            1'b1;
  wire in_fire = tvalid_s && tready_s;

  // master port: forwarded packet passthrough, or a one-beat token reveal
  wire fwd_beat = fwd && tvalid_s;
  // Ethernet uses BE byte order but AXI-Stream uses LE,
  // so tdata LSBYTE is token MSBYTE
  wire [31:0] tdata_m_drv_ord = {idx, cur_tok}; // REVISIT support CANON_TOK_BYTES > 2?
  wire [31:0] tdata_m_drv     = {tdata_m_drv_ord[ 7: 0],
                                 tdata_m_drv_ord[15: 8],
                                 tdata_m_drv_ord[23:16],
                                 tdata_m_drv_ord[31:24]};
  assign tvalid_m = fwd_beat || emit_tok;
  assign tdata_m  = fwd ? tdata_s : tdata_m_drv;
  assign tkeep_m  = fwd ? tkeep_s : 4'b1111;
  assign tuser_m  = fwd ? tuser_s : 16'd4;
  assign tlast_m  = fwd ? tlast_s : 1'b1;
  wire out_fire = tvalid_m && tready_m;

  // ------------------------------------------------------------------
  // Sequential
  // ------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state      <= FWD;
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
        // ---- forward verbatim; a CTRL marker arms the capture. ----
        FWD: if (in_fire) begin
          if (bcnt < 16'(HDR_BEATS)) begin
            // header capture for CTRL packet detection
            hdr_bits[32 * bcnt[HB_W-1:0] +: 32] <= tdata_s;
            // only a packet spanning exactly the full header can be CTRL —
            // anything shorter leaves stale words in hdr_bits
            if (tlast_s && (bcnt == 16'(HDR_BEATS - 1))) begin
              state <= CTRL_CHK; // must be separate cycle to see the last header word
            end
          end
        end

        CTRL_CHK: begin
          if (resp_hdr.id == '0) begin
            // CTRL packet: arm the capture of the next packet
            state <= CAP;
          end else begin
            // not a CTRL packet: resume forwarding (the header is already staged)
            state <= FWD;
          end
        end

        // ---- capture: payload bytes stream one per cycle into tok_buf,
        //      each completed token written to the RAM ----
        CAP: if (tvalid_s) begin
          if (bcnt < 16'(HDR_BEATS)) begin // header capture
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
            state  <= LEN_EST;
          end
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

        // ---- keep dropping until the ingress is silent at a packet
        //      boundary, so FWD never resumes mid-packet ----
        ALIGN: if ((bcnt == 16'h0) && !tvalid_s) begin
          state <= FWD;
        end

        default: state <= FWD;
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
