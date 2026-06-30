// batch_buffer — format-agnostic ping-pong packet store
// (see docs/batch_buffer.md).
//
// Stages each inbound AXI-Stream packet as a length-prefixed record in the
// filling bank: the total byte length from tuser at beat #0 is written as a
// 32-bit prefix word, then the packet's words verbatim. At tlast the record
// is committed — the write pointer advances over it — or, on the drop flag,
// abandoned — the write pointer rewinds to the last committed value — so a
// bank only ever holds verified records.
//
// TODO rewrite
// The banks swap on a free-running timer, exactly at the tick. The swap is
// made safe by the admission guard: a packet's first beat is accepted only
// when at least GUARD_CYCLES remain before the tick — and the bank has room
// for a prefix plus one max packet — otherwise it stalls at beat #0 until
// after the swap. An admitted packet therefore always finishes before the
// tick: no record straddles banks, and the bank handed to the drain is
// immutable with a frozen write pointer. (Should the guard ever be violated
// — GUARD_CYCLES set below the upstream's worst-case packet time — the
// in-flight record is abandoned rather than half-committed.)
//
// The drain walks the frozen bank's records via the length prefixes at its
// own pace, re-emitting each as an AXI packet: tuser = total length on beat
// #0 (straight from the prefix), swap flag (bit 0) on the tlast of the
// batch's final record — the attestation trigger for downstream. If the
// drain has not finished when the next tick lands (wire stall), it is
// preempted: a termination beat (tkeep = 0, tlast, swap flag) closes the
// cut stream, the un-emitted remainder is dropped, and the drain rebases
// onto the new bank.
//
// Fill and drain run on the same clock; the fill writes one bank while the
// drain reads the other (including on the swap cycle), so the storage maps
// to a simple-dual-port BRAM with no address collisions.

module batch_buffer #(
  // Bank size in 32-bit words: gigabit line rate x batch period + one
  // max-packet carryover, with margin (see the doc's sizing section).
  // Default 320 KB per bank (worst case ~300 KB at full 1 GbE).
  parameter int unsigned BANK_WORDS    = 81920,
  // Grace period (time for last writes to finish after the tick)
  // Max ~1.5KB packet with at least ~2B/cycle (hashing throughput)
  parameter int unsigned GRACE_PERIOD  = 1_000, // with safety margin
  // Largest entry, in words (admission headroom).
  parameter int unsigned MAX_ENTRY_WORDS = 1 + (eth_pkg::ETH_LEN_MAX + 3) / 4,
  // re-insert the empty swap beat on the output
  parameter bit OUTPUT_SWAP = 1
) (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave
  // (tuser = total byte length at beat #0, drop flag (bit 0) at tlast).
  // tkeep is unused: record geometry comes from the length prefix.
  input  wire        tvalid_s,
  output wire        tready_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire [15:0] tuser_s,

  // AXI-Stream master
  // (tuser = total byte length at beat #0, swap flag (bit 0) at tlast)
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m,

  // bank-swap tick, single-cycle pulse (TB sync / bring-up visibility)
  input wire         tick,
  input wire [31:0]  timer    // used for grace period
);

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned ADDR_W      = $clog2(BANK_WORDS);
  // Reserved tail of one max entry: a record ending here is not committed.
  localparam int unsigned SAFE_END    = BANK_WORDS - MAX_ENTRY_WORDS;

  typedef logic [ADDR_W-1:0] ptr_t;

  // ------------------------------------------------------------------
  // Bank storage — two independent banks (bank index outer). Fill writes one
  // bank, drain reads the other, so this infers a simple dual-port RAM.
  // ------------------------------------------------------------------
  logic [31:0] mem [0:1][0:BANK_WORDS-1];

  logic       fill_sel;  // always valid
  logic [1:0] drain_sel; // bit[1] is valid flag

  // ------------------------------------------------------------------
  // Fill — admit, prefix, stream, commit/abandon
  // ------------------------------------------------------------------
  typedef enum logic [1:0] { F_ADMIT, F_DATA } fill_state_t;
  fill_state_t fstate;

  ptr_t wr_ptr;   // speculative write position in the fill bank
  ptr_t wr_cmt;   // end of committed records in the fill bank

  wire in_delimiter = tvalid_s && tlast_s && (tkeep_s == '0);

  // F_ADMIT holds beat #0 for the one cycle the prefix write takes;
  // F_DATA consume a beat per cycle.
  assign tready_s = (fstate != F_ADMIT) || in_delimiter;
  wire in_handshake = tvalid_s && tready_s;
  wire in_drop      = tuser_s[0];          // drop flag, meaningful at tlast


  // ------------------------------------------------------------------
  // Drain — walk the frozen bank's records via the length prefixes
  // ------------------------------------------------------------------
  typedef enum logic [2:0] { D_IDLE, D_PFX_RD, D_PFX_LD, D_EMIT, D_SWAP } drain_state_t;
  drain_state_t dstate;

  ptr_t        rd_ptr;     // next word to read; leads the emitted beat by the read latency
  ptr_t        rd_limit;   // frozen committed pointer of the drain bank
  logic [31:0] rd_data;    // registered RAM read
  logic [15:0] rd_pkt_len;  // current record's length prefix
  ptr_t        rd_rec_end;  // next record's prefix = end of the current record
  logic        emit_first;  // set at a record's first beat, cleared once it leaves


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

  wire out_free = !tvalid_m_r || tready_m;

  // By the last beat, rd_ptr has walked to the next record's prefix word —
  // which is exactly rd_rec_end, and (if it reaches the batch limit) batch_last.
  wire emit_last  = (rd_ptr == rd_rec_end);
  wire batch_last = (rd_ptr >= rd_limit);

  wire [3:0] last_keep = (rd_pkt_len[1:0] == 2'd1) ? 4'b0001
                       : (rd_pkt_len[1:0] == 2'd2) ? 4'b0011
                       : (rd_pkt_len[1:0] == 2'd3) ? 4'b0111
                       :                             4'b1111;

  // ------------------------------------------------------------------
  // Sequential
  // ------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      fill_sel   <= 1'b0; // bank 0 (always valid)
      drain_sel  <= 2'b01; // invalid, bank 1
      fstate     <= F_ADMIT;
      wr_ptr     <= '0;
      wr_cmt     <= '0;
      dstate     <= D_IDLE;
      rd_ptr     <= '0;
      rd_limit   <= '0;
      rd_data    <= '0;
      rd_pkt_len <= '0;
      rd_rec_end <= '0;
      emit_first <= 1'b0;
      tvalid_m_r <= 1'b0;
      tlast_m_r  <= 1'b0;
      tdata_m_r  <= '0;
      tkeep_m_r  <= '0;
      tuser_m_r  <= '0;
    end else begin
      // ---- output handshake: clear once the sink takes a beat ----
      if (tvalid_m_r && tready_m) begin
        tvalid_m_r <= 1'b0;
        tlast_m_r  <= 1'b0;
        tuser_m_r  <= '0;
      end

      // ============================== fill ==============================
      case (fstate)
        F_ADMIT: begin
          // inject the length prefix ahead of the packet's first word;
          // beat #0 waits out this cycle (tready_s low here)
          if (tvalid_s && !in_delimiter) begin
            mem[fill_sel][wr_ptr] <= {16'h0, tuser_s};
            wr_ptr <= wr_ptr + ptr_t'(1);
            fstate <= F_DATA;
          end
        end

        F_DATA: begin
          if (in_handshake) begin
            mem[fill_sel][wr_ptr] <= tdata_s;
            wr_ptr <= wr_ptr + ptr_t'(1);
            if (tlast_s) begin
              // commit the record — or abandon it on the drop flag
              // or when it overran into the reserved tail
              if (!in_drop && (wr_ptr < ptr_t'(SAFE_END))) begin
                wr_cmt <= wr_ptr + ptr_t'(1);
              end else begin
                wr_ptr <= wr_cmt;
              end
              fstate <= F_ADMIT;
            end
          end
        end

        default: fstate <= F_ADMIT;
      endcase

      // ============================== drain =============================
      // rd_ptr walks one word per advancing cycle, reading mem[rd_ptr] as it
      // goes (like wr_ptr on the fill side). The read doubles as a read-enable:
      // D_EMIT only steps when the sink takes the beat, so rd_data holds on a
      // stall and a single rd_ptr serves as both prefetch and re-read address.
      case (dstate)
        D_IDLE: begin

        end

        D_PFX_RD: begin
          // fetch the prefix word
          rd_data <= drain_sel[1] ? mem[drain_sel[0]][rd_ptr] : '0; // TODO separate these from the FSM always
          rd_ptr  <= rd_ptr + ptr_t'(1);
          dstate  <= D_PFX_LD;
        end

        D_PFX_LD: begin
          // rd_data holds the prefix
          rd_pkt_len <= rd_data[15:0];
          // latch the record end = this first-data address + the beat count
          rd_rec_end <= rd_ptr + ptr_t'((rd_data[15:0] + 16'd3) >> 2);
          // fetch the first data word
          emit_first <= 1'b1;
          rd_data    <= drain_sel[1] ? mem[drain_sel[0]][rd_ptr] : '0;
          rd_ptr     <= rd_ptr + ptr_t'(1);
          dstate     <= D_EMIT;
        end

        D_EMIT: begin
          if (out_free) begin
            // rd_data holds next data word
            // also handle first and last packet as needed
            emit_first <= 1'b0;
            tvalid_m_r <= 1'b1;
            tdata_m_r  <= rd_data;
            tkeep_m_r  <= emit_last ? last_keep : 4'b1111;
            tlast_m_r  <= emit_last;
            tuser_m_r  <= emit_first ? rd_pkt_len
                        : emit_last  ? {15'b0, batch_last}
                        :              16'h0;
            // fetch the next data (could also be new prefix)
            rd_data    <= drain_sel[1] ? mem[drain_sel[0]][rd_ptr] : '0;
            rd_ptr     <= rd_ptr + ptr_t'(1);

            if (emit_last) begin
              // rd_ptr already reached the next record's prefix and the read
              // above fetched it, so skip straight to D_PFX_LD.
              if (batch_last) dstate <= OUTPUT_SWAP ? D_SWAP : D_IDLE;
              else            dstate <= D_PFX_LD;
            end
          end
        end

        D_SWAP: begin
          if (out_free) begin
            tvalid_m_r <= 1'b1;
            tkeep_m_r  <=   '0;
            tlast_m_r  <= 1'b1;
            dstate <= D_IDLE;
          end
        end

        default: dstate <= D_IDLE;   // D_IDLE: wait for the tick
      endcase

      // =========================== bank swap ============================
      if (tick) begin
        // Swap drain side but mark as invalid for now (for isolation)
        drain_sel[0] <= !drain_sel[0];
        drain_sel[1] <= 1'b0;
        rd_ptr       <= '0;

        // Write side can only swap at the delimiter
      end

      if (in_delimiter) begin
        // Swap fill side
        fill_sel <= ~fill_sel;
        wr_ptr   <= '0;
        wr_cmt   <= '0;

        // Store final committed write index for reads
        rd_limit <= wr_cmt;
      end

      if ( timer == (GRACE_PERIOD-1) ) begin
        // Start drain only after the grace period
        drain_sel[1] <= 1'b1; // mark the drain bank selctor valid
        if (rd_limit != '0) begin
          dstate <= D_PFX_RD;
        end
      end

    end
  end

endmodule
