// canon_proc — canonical-layer packet processor: per-packet checks on a
// verbatim pass-through stream.
//
// One instance per direction; all
// knowledge of the canonical packet format lives here. Beats stream through
// a shift register exactly one canonical header deep — input enters the
// tail, the output beat falls off the head — so a packet's first beat
// reaches the head exactly when its full header is in the register, and the
// header checks gate it combinationally.
//
// A header failure — including a packet too short to carry a full header —
// is known before the packet's first beat leaves, so emission is suppressed:
// nothing reaches the buffer. Payload failures — received byte count !=
// HDR_BYTES + PLD_LEN, or the upstream truncation flag — are only known at
// tlast and are signalled as the drop flag on tuser; the downstream
// abandons the record.
//
// Output tuser: total packet length (HDR_BYTES + PLD_LEN) on beat #0, drop
// flag (bit 0) on the tlast beat. A passing packet spans at least
// HDR_WORDS beats, so the two positions never collide.
//
// The register advances in lock-step with the input (one beat in, one beat
// out; once a packet's tlast has entered, bubbles may
// enter the tail behind it instead, so a finished packet drains without
// depending on the next one. A bubble never enters behind a beat of an
// unfinished packet, so a packet's beats are always contiguous and the
// parse positions are fixed. The verdict sits combinationally in front of
// the output register — add a register stage later if timing requires.

module canon_proc
  import canon_pkg::*;
#(
  // Selects the canonical-header struct *type* parsed in this direction
  // (see the g_canon generate block).
  parameter canon_dir_t DIR = CANON_DIR_REQ
) (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave (from eth_deframe; tuser = truncation flag at tlast)
  input  wire        tvalid_s,
  output wire        tready_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire        tuser_s,

  // AXI-Stream master (to the batch buffer;
  // tuser = total byte length at beat #0, drop flag at tlast)
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m,   // len@beat0, drop@tlast

  // Nonce output
  output wire [CANON_NONCE_W-1:0] nonce,

  // Timer input
  input wire [31:0] timer
);

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned HDR_BITS  = (DIR == CANON_DIR_RSP) ? CANON_RSP_HDR_BITS
                                                             : CANON_REQ_HDR_BITS;
  localparam int unsigned HDR_BYTES = HDR_BITS / 8;
  localparam int unsigned HDR_WORDS = HDR_BYTES / 4;  // canon layer is word-aligned
  localparam int unsigned PLD_MAX   = CANON_PKT_BYTES_MAX - HDR_BYTES;

  // ------------------------------------------------------------------
  // Pass-through shift register, one header deep
  // [0] = head (oldest beat, next to leave), [$-1] = tail (input).
  // ------------------------------------------------------------------
  logic [HDR_WORDS-1:0]       shift_v;
  logic [HDR_BITS -1:0]       shift_data;
  logic [HDR_BYTES-1:0]       shift_keep;
  logic [HDR_WORDS-1:0]       shift_last;
  // Upstream drop flag (input tuser at tlast), carried with the beat.
  // Since this block does not store the whole payload, it can't implement
  // the drop itself so need to propagate.
  logic [HDR_WORDS-1:0]       shift_drop;

  // AXI-Stream output register
  logic        tvalid_m_r, tlast_m_r;
  logic [31:0] tdata_m_r;
  logic [3:0]  tkeep_m_r;
  logic [15:0] tuser_m_r;
  assign tvalid_m = tvalid_m_r;
  assign tdata_m  = tdata_m_r;
  assign tkeep_m  = tkeep_m_r;
  assign tlast_m  = tlast_m_r;
  assign tuser_m  = tuser_m_r;

  wire out_free = !tvalid_m || tready_m;

  // Bucket boundary pending
  logic swap;

  // Nonce register
  logic [CANON_NONCE_W-1:0] nonce_r;

  assign nonce = nonce_r;

  // ------------------------------------------------------------------
  // Header view of the register — the head is the packet's beat #0 in
  // S_HEADER, so the register content is exactly the header once it is in
  // (the cast checks the sizes match at elaboration).
  // ------------------------------------------------------------------
  wire canon_id_t      hdr_id;
  wire canon_id_t      hdr_ref;
  wire canon_len_t     hdr_pld_len;
  wire canon_kcommit_t hdr_kcommit;
  wire                 hdr_rsvd_clr;

  generate
    if (DIR == CANON_DIR_REQ) begin : g_canon_req
      wire canon_req_hdr_t parsed = canon_req_hdr_from_wire_bits(shift_data);
      assign hdr_id       = parsed.id;
      assign hdr_ref      = parsed.reference;
      assign hdr_pld_len  = parsed.pld_len;
      assign hdr_kcommit  = parsed.key_commit;
      assign hdr_rsvd_clr = (parsed.reserved0 == '0);
    end else begin : g_canon_rsp
      wire canon_rsp_hdr_t parsed = canon_rsp_hdr_from_wire_bits(shift_data);
      assign hdr_id       = parsed.id;
      assign hdr_ref      = '0;
      assign hdr_pld_len  = parsed.pld_len;
      assign hdr_kcommit  = '0;
      assign hdr_rsvd_clr = (parsed.reserved0 == '0);
    end
  endgenerate

  // ------------------------------------------------------------------
  // FSM states
  // ------------------------------------------------------------------
  typedef enum logic [1:0] { S_HEADER, S_EMIT, S_SKIP } state_t;
  state_t state;

  // ------------------------------------------------------------------
  // Header checks (combinational, gate the head in S_HEADER)
  // ------------------------------------------------------------------

  canon_id_t      prev_id;

  // header ready for checking
  wire hdr_rdy   = (state == S_HEADER) && &shift_v;

  // header fractional check: stream ended before a full header entered
  wire hdr_fract_chk =    (shift_last[0 +: HDR_WORDS-1] == '0)  // all tlast 0s except tail
                       && (shift_keep[HDR_BYTES-1 -: 4] == '1); // tail tkeep all 1s
  // PLD_LEN check
  wire hdr_pld_len_chk = (hdr_pld_len <= canon_len_t'(PLD_MAX));
  // bucket check
  // TODO hdr_bkt_chk
  // ID value validity check
  wire hdr_id_valid_chk  = (hdr_id.id_cont != '0);
  // ID sequence check
  wire hdr_id_seq_chk  = (DIR == CANON_DIR_REQ) ? (hdr_id.id_cont == (prev_id.id_cont + 1)) :
                         (DIR == CANON_DIR_RSP) ? (hdr_id.id_cont  >  prev_id.id_cont)      :
                                                  1'b0;
  // REFERENCE check
  wire hdr_ref_chk = (DIR == CANON_DIR_REQ) ? (hdr_ref < hdr_id) : 1'b1;
  // RESERVED value check
  wire hdr_rsvd_chk = hdr_rsvd_clr;


  wire hdr_chk =    hdr_fract_chk
                 && hdr_pld_len_chk
                 && hdr_id_valid_chk
                 && hdr_id_seq_chk
                 && hdr_ref_chk
                 && hdr_rsvd_chk;

  wire canon_len_t total = canon_len_t'(HDR_BYTES) + hdr_pld_len;

  // ------------------------------------------------------------------
  // Advance control — one packet at a time at the head
  // ------------------------------------------------------------------
  canon_len_t     exp_total;         // total of the packet being emitted
  canon_len_t     out_cnt;           // bytes forwarded so far this packet
  canon_id_t      cur_id;            // header fields of the packet being

  // the word at the shift register head needs emitting
  // - S_HEADER
  wire emit_needed =    (state == S_HEADER && hdr_rdy && hdr_chk)
                     || (state == S_EMIT);

  // Insert single-beat empty (tkeep=0) transaction at the bucket boundary
  // Using S_HEADER to prevent mid-burst insert
  wire insert_swap = swap && (state == S_HEADER) && out_free;

  // shift out okay when the word at the shift register head is either:
  // - invalid
  // - valid, but should be consumed and not emitted in either
  // - valid and should be emitted and the output register will also be free
  wire shift_out_ok =    !shift_v[0]
                      || !emit_needed
                      ||  (out_free && !insert_swap);

  // Backpressure input port when can't shift the register
  assign tready_s = shift_out_ok;
  wire in_handshake = tvalid_s && tready_s;

  // a bubble may enter the tail only between packets (tail empty or closed)
  wire bubble_allowed =    !shift_v[HDR_WORDS-1]     // reg already empty / has a bouble
                        ||  shift_last[HDR_WORDS-1]; // previous packet finished


  // flag to step the shift register
  // Note: swap has higher priority
  wire advance = shift_out_ok && (in_handshake || bubble_allowed);

  // output counter new value
  wire canon_len_t out_cnt_next =
    (state == S_HEADER) ?           canon_len_t'($countones(shift_keep[3:0])) :
                          out_cnt + canon_len_t'($countones(shift_keep[3:0]));

  // payload verdict, evaluated at the tlast beat
  wire pkt_drop    = shift_drop[0] || (out_cnt_next != exp_total);

  // nonce capture register
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      nonce_r <= '0;
    end else if (hdr_rdy && hdr_fract_chk && (hdr_id == '0)) begin
      nonce_r <= CANON_NONCE_W'(hdr_kcommit);
    end
  end

  // bucket boundary pending: set free-flowing by the timer, cleared the cycle
  // the empty boundary beat is emitted (one-shot — no edge/level clear hazard).
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)            swap <= 1'b0;
    else if (timer == 1)   swap <= 1'b1;
    else if (insert_swap)  swap <= 1'b0;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      shift_v    <= '0;
      shift_last <= '0;
      state      <= S_HEADER;
      exp_total  <= '0;
      out_cnt    <= '0;
      cur_id     <= '0;
      prev_id    <= '0;
      tvalid_m_r <= 1'b0;
      tlast_m_r  <= 1'b0;
      tdata_m_r  <= '0;
      tkeep_m_r  <= '0;
      tuser_m_r  <= '0;
    end else begin
      // ---- output handshake: clear once the sink takes a beat ----
      if (tvalid_m_r && tready_m) begin
        tvalid_m_r <= 1'b0;
        tdata_m_r  <= '0;
        tkeep_m_r  <= '0;
        tlast_m_r  <= '0;
        tuser_m_r  <= '0;
      end

      if (insert_swap) begin
        // ---- empty transaction for bucket boundary ----
        tvalid_m_r <=  1'b1;
        tkeep_m_r  <=    '0;
        tlast_m_r  <=  1'b1;
      end

      if (advance) begin
        // ---- shift one beat toward the head; input or a bubble enters ----
        shift_v    <= {in_handshake,             shift_v   [HDR_WORDS-1: 1]};
        shift_data <= {tdata_s,                  shift_data[HDR_BITS -1:32]};
        shift_keep <= {tkeep_s,                  shift_keep[HDR_BYTES-1: 4]};
        shift_last <= {tlast_s,                  shift_last[HDR_WORDS-1: 1]};
        shift_drop <= {tuser_s,                  shift_drop[HDR_WORDS-1: 1]};

        if (emit_needed) begin
          // ---- emit the head of the shift reg (of a passing packet) ----
          tvalid_m_r <= 1'b1;
          tdata_m_r  <= shift_data[31:0];
          tkeep_m_r  <= shift_keep[3:0];
          tlast_m_r  <= shift_last[0];
          tuser_m_r  <= (state == S_HEADER) ? 16'(total) :
                         shift_last[0]      ? pkt_drop   :
                                              '0;
          out_cnt    <= out_cnt_next;
        end

        // ---- consume the head ----
        case (state)
          S_HEADER: begin
            // waiting for a new header
            // if the previous packet have not finished streaming out, we're still in EMIT/SKIP

            // we have a new header candidate
            if (hdr_rdy) begin
              if (hdr_chk) begin
                // capture header fields for payload and sequence checks
                cur_id     <= hdr_id;
                exp_total  <= total;
                state      <= S_EMIT; // packet passed header checks, start forwarding
              end else if (shift_last[0]) begin
                // we have a single beat fractional header.
                // consume the beat without emitting but stay in S_HEADER as
                // we could have a new candidate after the next beat.
                state <= S_HEADER;
              end else begin
                // we have a multi-beat fractional header or
                // header check fail. Don't emit and move to S_SKIP
                // to consume the whole packet.
                state <= S_SKIP;        // suppressed multi-beat packet
              end
            end
          end

          S_EMIT: begin
            // forwarded packet: keep forwarding until last
            if (shift_last[0]) begin
              state <= S_HEADER;
              // fully verified packet: advance the sequence state
              if (!pkt_drop) begin
                prev_id <= cur_id;
              end
            end
          end

          S_SKIP: begin
            // suppressed packet: consume without emitting
            // Exit only when packet ends
            if (shift_last[0])
              state <= S_HEADER;
          end

          default: state <= S_HEADER;
        endcase
      end
    end
  end

endmodule
