// eth_deframe — Ethernet-layer ingress adapter: CoreTSE MAC-FIFO frame in,
// canonical packet out as AXI-Stream.
//
// The MAC delivers Ethenet Frame
// (LENGTH interpretation only, i.e. L/T <= 1500 — type frames are out of scope).
//
// This block:
//   1. strips the Ethernet header,
//   2. forwards exactly LENGTH octets of DATA, word-aligned: the constant
//      offset is removed with a residue register,
//   3. drops PAD and FCS (everything after LENGTH octets),
//   4. cross-checks LENGTH against the octets actually received and raises a
//      flag on TUSER if it does not match the number of data bytes.
//
// Output AXI-Stream
//
// FCS is assumed present (CoreTSE default) and is not checked here — the MAC
// already drops frames with a bad CRC.

module eth_deframe (
  input  wire        clk,
  input  wire        rst_n,

  // MAC-RX-shaped input (source drives data, we drive accept)
  input  wire        in_rdy,
  output wire        in_acpt,
  input  wire        in_sof,
  input  wire        in_eof,
  input  wire [31:0] in_dat,
  input  wire [1:0]  in_bytevalid,    // count of INVALID bytes in this word

  // AXI-Stream master
  output wire        tvalid,
  input  wire        tready,
  output wire [31:0] tdata,
  output wire [3:0]  tkeep,
  output wire        tlast,
  output wire        tuser,
  output wire [15:0] tlen,          // in-band L/T sideband (the captured LENGTH field),
                                    // held stable with tvalid for the whole frame — the
                                    // consumer samples it AT the tvalid handshake, never
                                    // off a separately-timed event.

  // Status / debug — live view of the header shift register: the fields are
  // valid from dbg_hdr_valid (header fully shifted in, before the first data
  // beat) and hold until the next frame's header shifts through.
  output wire        dbg_hdr_valid,
  output wire [47:0] dbg_eth_dst,
  output wire [47:0] dbg_eth_src,
  output wire [15:0] dbg_eth_len,

  // ---- instrumentation taps (failure-theory probes; see dbg_apb) ----
  // Sticky / counting probes that survive a slow firmware poll. They answer:
  // does ANY frame reach deframe, and is the wire carrying 802.3-LENGTH frames
  // (L/T <= 1500) or Ethernet II / TYPE frames (L/T > 1500, e.g. IPv6 0x86DD)
  // that this LENGTH-only deframer mis-reads as a 34 KB length?
  output wire [15:0] dbg_sof_count,    // frames that entered deframe (in_sof beats)
  output wire [15:0] dbg_first_lt,     // L/T field of the FIRST frame (captured once)
  output wire [15:0] dbg_type_count,   // frames whose L/T > 1500 -> REJECTED (silent drop)
  output wire [15:0] dbg_trunc_count,  // conforming frames that ended short of LENGTH (zero-filled by reframe)
  output wire [15:0] dbg_emit_frames   // frames deframe emitted to AXIS (passed reject)
);

  import eth_pkg::*;

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned WIDX_W          = $clog2((ETH_FRAME_BYTES_MAX + 3) / 4);  // wire-word index
  localparam int unsigned LEN_W           = 16;                                     // Ethernet LENGTH width
  localparam int unsigned HDR_FULL_WORDS  = ETH_HDR_BYTES / 4;
  localparam int unsigned RESIDUE_BYTES   = 4 - (ETH_HDR_BYTES % 4); // TODO add handling for no residue case?

  typedef logic [WIDX_W-1:0] widx_t;
  typedef logic [LEN_W-1:0]  len_t;

  // ------------------------------------------------------------------
  // RX word index / header capture
  // ------------------------------------------------------------------
  widx_t       rx_widx;                              // wire-word index in frame
  logic        in_frame;                             // inside SOF..EOF: words sampled

  eth_hdr_bytes_t             hdr_bytes;  // header shift register, [k] = wire byte k
  logic [8*RESIDUE_BYTES-1:0] residue;    // DATA tail bytes of the previous beat
  len_t                       sent_bytes; // DATA bytes emitted so far
  len_t                       eth_len_q;  // LENGTH captured directly off the wire

  // instrumentation registers (PROTOTYPE_DEBUG taps; do not affect the datapath —
  // these are bring-up only and must be elided from the production bitstream, since
  // any prover-influenceable observable is itself an exfiltration channel)
  len_t sof_count_q, type_count_q, trunc_count_q, first_lt_q, emit_frames_q;
  logic first_lt_seen;
  // reject_f: this frame's L/T exceeds the protocol maximum (TYPE frame / oversized).
  // Functional (not debug): such frames are SILENTLY dropped — no AXIS output at all,
  // words drained to EOF. This is the deterministic, channel-free reject.
  logic reject_f;
  // L/T extracted from the partial last header word (wire bytes 12,13, big-endian)
  wire len_t lt_capture = {in_dat[7:0], in_dat[15:8]};

  // Parsed header view of the shift register (dst/src — debug outputs only).
  wire eth_header_t eth_hdr = eth_hdr_from_bytes(hdr_bytes);
  // LENGTH is captured directly (see always_ff) as an explicit bit-slice of the
  // wire, NOT via eth_hdr_from_bytes. The struct-cast / byte-reverse extraction
  // simulated correctly (all cocotb + ModelSim) yet produced a wrong LENGTH on
  // the MPF300 silicon — proven by hardware bisection: hardcoding reframe's
  // length made the bridge forward, so the extracted dbg_eth_len was the fault.
  // A plain registered bit-slice carries no synth-vs-sim ambiguity.
  wire len_t        eth_len = eth_len_q;

  // AXI-Stream output register (declared before the accept logic uses it)
  logic        tvalid_r;
  logic [31:0] tdata_r;
  logic [3:0]  tkeep_r;
  logic        tlast_r;
  logic        tuser_r;
  assign tvalid = tvalid_r;
  assign tdata  = tdata_r;
  assign tkeep  = tkeep_r;
  assign tlast  = tlast_r;
  assign tuser  = tuser_r;

  wire len_t  beats_total    = (eth_len + 16'd3) >> 2;                 // ceil(LEN/4)
  wire len_t  beat_idx       = sent_bytes >> 2;
  wire        data_active    = (rx_widx >= HDR_FULL_WORDS + 1);
  wire        data_remaining = (sent_bytes < eth_len) && (eth_len != '0);
  wire        is_last        = (beat_idx + 1'b1 == beats_total);
  wire [3:0]  last_keep      = (eth_len[1:0] == 2'd0) ? 4'b1111
                             : (eth_len[1:0] == 2'd1) ? 4'b0001
                             : (eth_len[1:0] == 2'd2) ? 4'b0011
                             :                          4'b0111;

  // in-band L/T sideband: the registered LENGTH, stable from the partial last
  // header word (set before the first emit) through the whole frame.
  assign tlen          = eth_len_q;

  // Debug = combinational view of the held header register.
  assign dbg_hdr_valid = data_active;
  assign dbg_eth_dst   = eth_hdr.dst;
  assign dbg_eth_src   = eth_hdr.src;
  assign dbg_eth_len   = eth_len;

  // instrumentation taps
  assign dbg_sof_count   = sof_count_q;
  assign dbg_first_lt    = first_lt_q;
  assign dbg_type_count  = type_count_q;
  assign dbg_trunc_count = trunc_count_q;
  assign dbg_emit_frames = emit_frames_q;

  // Words outside SOF..EOF are accepted but dropped (sampling starts at SOF).
  wire frame_word = in_frame || in_sof;

  // Accept whenever the word doesn't produce an AXI beat or the beat reg is free.
  // reject_f forces emitting low for the whole frame -> drop (drain to EOF, no output).
  wire emitting = data_active && data_remaining && !reject_f;
  assign in_acpt = !emitting || !tvalid_r || tready;
  wire in_handshake = in_rdy && in_acpt;
  wire out_handshake = tvalid_r && tready;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rx_widx       <= '0;
      in_frame      <= 1'b0;
      hdr_bytes     <= '0;
      residue       <= '0;
      sent_bytes    <= '0;
      eth_len_q     <= '0;
      sof_count_q   <= '0;
      type_count_q  <= '0;
      trunc_count_q <= '0;
      first_lt_q    <= '0;
      first_lt_seen <= 1'b0;
      emit_frames_q <= '0;
      reject_f      <= 1'b0;
      tvalid_r    <= 1'b0;
      tlast_r     <= 1'b0;
      tdata_r     <= '0;
      tkeep_r     <= '0;
      tuser_r     <= 1'b0;
    end else begin
      // output handshake: clear once the sink takes a beat
      if (out_handshake) begin
        tvalid_r <= 1'b0;
        tlast_r  <= 1'b0;
        tuser_r  <= 1'b0;
      end

      if (in_handshake && frame_word) begin
        in_frame <= !in_eof;
        rx_widx  <= rx_widx + 1'b1;

        // instrumentation: count frames entering deframe (one SOF per frame)
        if (in_sof) sof_count_q <= sof_count_q + 1'b1;

        // ---- header capture: shift words in; after the partial last header
        // word, hdr_bytes[k] = wire byte k ----
        if (rx_widx <= HDR_FULL_WORDS-1) begin
          // flat byte-shift: drop the low 4 bytes (32 b), shift the new word in at the top
          hdr_bytes <= {in_dat, hdr_bytes[ETH_HDR_BYTES*8-1:32]};
        end else if (rx_widx == HDR_FULL_WORDS) begin
          // partial last header word: keep all but the low RESIDUE_BYTES bytes
          {residue, hdr_bytes}  <= {in_dat, hdr_bytes[ETH_HDR_BYTES*8-1:8*RESIDUE_BYTES]};
          // LENGTH = wire bytes 12,13 (big-endian). This last header word carries
          // wire bytes 12..15; the MAC delivers wire byte N in in_dat[8*N +: 8],
          // so byte12 = in_dat[7:0] (MSB), byte13 = in_dat[15:8] (LSB).
          eth_len_q  <= lt_capture;
          sent_bytes <= '0;
          // instrumentation: sticky first L/T + count TYPE frames (L/T > 1500).
          // A LENGTH-only deframer mis-reads a TYPE frame's EtherType as a length;
          // type_count rising is the on-silicon signature of that mismatch.
          if (!first_lt_seen) begin
            first_lt_q    <= lt_capture;
            first_lt_seen <= 1'b1;
          end
          // PROTOCOL: reject frames whose reported length exceeds the maximum
          // (1500). This rejects every Ethernet II / TYPE frame (EtherTypes are
          // >= 1536) by construction. Set the per-frame drop flag and count it.
          reject_f <= (lt_capture > 16'd1500);
          if (lt_capture > 16'd1500) type_count_q <= type_count_q + 1'b1;
        end

        // ---- DATA: one realigned AXI beat per wire word ----
        if (emitting) begin
          tvalid_r <= 1'b1;
          {residue, tdata_r} <= {in_dat, residue};
          tkeep_r  <= is_last ? last_keep : 4'b1111;
          // EOF closes the AXI packet even when LENGTH promised more octets
          tlast_r  <= is_last || in_eof;
          tuser_r  <= in_eof && !is_last;  // truncated: EOF hit with DATA still owed
          sent_bytes <= sent_bytes + 16'd4;
          // instrumentation: count conforming frames that ended short of LENGTH
          // (reframe zero-fills them). TYPE frames don't reach here (rejected).
          if (in_eof && !is_last) trunc_count_q <= trunc_count_q + 1'b1;
          // instrumentation: count each emitted frame on its closing beat
          if (is_last || in_eof) emit_frames_q <= emit_frames_q + 1'b1;
        end

        if (in_eof) begin
          rx_widx  <= '0;
          reject_f <= 1'b0;   // clear drop flag for the next frame
        end
      end
    end
  end

endmodule
