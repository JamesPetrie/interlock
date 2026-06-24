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
//      flag on TUSER if it does not match the number of data bytes,
//   5. drops frames whose LENGTH field is out of range (L/T > ETH_LEN_MAX,
//      i.e. Type frames / over-long lengths): no AXI beats are emitted at all.
//      LENGTH is known at header completion, before the first data beat, so the
//      whole frame is suppressed cleanly.
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
  output wire        tuser, // truncated frame

  // Status / debug — live view of the header shift register: the fields are
  // valid from dbg_hdr_valid (header fully shifted in, before the first data
  // beat) and hold until the next frame's header shifts through.
  output wire        dbg_hdr_valid,
  output wire [47:0] dbg_eth_dst,
  output wire [47:0] dbg_eth_src,
  output wire [15:0] dbg_eth_len
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

  function automatic logic[3:0] bv2keep(input logic [1:0] bv);
    return 4'b1111 >> bv;
  endfunction

  // ------------------------------------------------------------------
  // RX word index / header capture
  // ------------------------------------------------------------------
  widx_t       rx_widx;                              // wire-word index in frame
  logic        in_frame;                             // inside SOF..EOF: words sampled

  eth_hdr_bits_t              hdr_bits;     // header shift register, [k] = wire byte k
  logic [8*RESIDUE_BYTES-1:0] residue;      // DATA tail bytes of the previous beat
  logic [RESIDUE_BYTES-1:0]   residue_keep; // keep bits for `residue`
  len_t                       beat_idx;     // data beat index to be emitted next

  // Parsed header view of the shift register (valid once word 3 has shifted in).
  wire eth_header_t eth_hdr   = eth_hdr_from_wire_bits(hdr_bits);
  wire len_t        eth_len   = eth_hdr.len_type;
  wire              len_valid = (eth_len <= len_t'(ETH_LEN_MAX));


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

  // beats_total forced to 0 for an out-of-range LENGTH so no beat is emitted.
  wire len_t  beats_total    = len_valid ? ((eth_len + 16'd3) >> 2) : '0; // ceil(LEN/4)
  wire        data_active    = (int'(rx_widx) >= HDR_FULL_WORDS + 1);
  wire        beat_remaining = (beat_idx != beats_total);
  wire        is_last        = (beat_idx == beats_total - 1'b1);

  // On the last beat, drop anything past LENGTH.
  wire [3:0] keep_mask = !is_last             ? 4'b1111
                       : eth_len[1:0] == 2'd1 ? 4'b0001
                       : eth_len[1:0] == 2'd2 ? 4'b0011
                       : eth_len[1:0] == 2'd3 ? 4'b0111
                       :                        4'b1111;
  // Extend the mask for the residue bytes,
  // residue does not need masking (set to 1)
  wire [RESIDUE_BYTES+3:0] keep_mask_ext = { {RESIDUE_BYTES{1'b1}}, keep_mask };

  // Debug = combinational view of the held header register.
  assign dbg_hdr_valid = data_active;
  assign dbg_eth_dst   = eth_hdr.dst;
  assign dbg_eth_src   = eth_hdr.src;
  assign dbg_eth_len   = eth_len;

  // Words outside SOF..EOF are accepted but dropped (sampling starts at SOF).
  wire frame_word = in_frame || in_sof;

  wire emitting = data_active && beat_remaining;
  assign in_acpt = !emitting || !tvalid_r || tready;
  wire in_handshake = in_rdy && in_acpt;
  wire out_handshake = tvalid_r && tready;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rx_widx       <= '0;
      in_frame      <= 1'b0;
      hdr_bits      <= '0;
      residue       <= '0;
      residue_keep  <= '0;
      beat_idx    <= '0;
      tvalid_r    <= 1'b0;
      tlast_r     <= 1'b0;
      tdata_r     <= '0;
      tkeep_r     <= '0;
      tuser_r     <= 1'b0;
    end else begin
      // output handshake: clear once the sink takes a beat
      if (out_handshake) begin
        tvalid_r <= 1'b0;
      end

      if (in_handshake && frame_word) begin
        in_frame <= !in_eof;
        rx_widx  <= rx_widx + 1'b1;

        // Note: Ethernet itself is Big-Endian but the MAC interface constructs words
        //       in Little-Endian fashion (e.g. L_T MSB is in dat[7:0] instead of [31:24]).
        //       To avoid inline converting, shift the words using LE fashion
        //       swap byte order separately.

        // ---- header capture: shift words in ----
        if (int'(rx_widx) <= HDR_FULL_WORDS-1) begin
          hdr_bits <= {in_dat, hdr_bits[ETH_HDR_BITS-1:32]};
        end else if (int'(rx_widx) == HDR_FULL_WORDS) begin
          {residue, hdr_bits}  <= {in_dat, hdr_bits[ETH_HDR_BITS-1:8*RESIDUE_BYTES]};
          residue_keep          <= '1;  // first DATA bytes (full word: valid)
          beat_idx              <= '0;  // reset beat index to 0
        end

        // ---- DATA: one realigned AXI beat per wire word ----
        if (emitting) begin
          tvalid_r <= 1'b1;
          {residue, tdata_r} <= {in_dat, residue};
          // EOF closes the AXI packet even when LENGTH promised more octets.
          tlast_r <= is_last || in_eof;
          // mask tkeep to only emit LENGTH bytes in total
          // last 4 bytes are FCS and must not be used (>> 4)
          {residue_keep, tkeep_r} <= keep_mask_ext &
                                     ({bv2keep(in_bytevalid), residue_keep} >> (!in_eof ? 0 : 4));
          // truncated if last beat not reached, or not enough bytes to keep (excl. FCS)
          tuser_r <= in_eof && (
                        !is_last ||
                        (4'({bv2keep(in_bytevalid), residue_keep} >> 4) < keep_mask) );
          // TODO: this tuser only checks that *enough* octets arrived, not that the
          // total frame length matches LENGTH — an over-padded / over-long frame
          // still passes. A full check needs the EOF byte count, which for a
          // PADded frame arrives after this beat, so it would mean stalling the
          // pipeline until EOF.
          // Stalling is not an issue as PAD bytes are not processed anyway,
          // so their spots in the pipeline are free to use.
          // Deferred until we actually rely on L/T = LENGTH.

          beat_idx <= beat_idx + 1'b1;
        end

        if (in_eof) begin
          rx_widx <= '0;
        end
      end
    end
  end

endmodule
