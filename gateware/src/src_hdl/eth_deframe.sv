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

  // ------------------------------------------------------------------
  // RX word index / header capture
  // ------------------------------------------------------------------
  widx_t       rx_widx;                              // wire-word index in frame
  logic        in_frame;                             // inside SOF..EOF: words sampled

  eth_hdr_bytes_t             hdr_bytes;  // header shift register, [k] = wire byte k
  logic [8*RESIDUE_BYTES-1:0] residue;    // DATA tail bytes of the previous beat
  len_t                       sent_bytes; // DATA bytes emitted so far

  // Parsed header view of the shift register (valid once word 3 has shifted in).
  wire eth_header_t eth_hdr = eth_hdr_from_bytes(hdr_bytes);
  wire len_t        eth_len = eth_hdr.len_type;

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

  // Debug = combinational view of the held header register.
  assign dbg_hdr_valid = data_active;
  assign dbg_eth_dst   = eth_hdr.dst;
  assign dbg_eth_src   = eth_hdr.src;
  assign dbg_eth_len   = eth_len;

  // Words outside SOF..EOF are accepted but dropped (sampling starts at SOF).
  wire frame_word = in_frame || in_sof;

  // Accept whenever the word doesn't produce an AXI beat or the beat reg is free.
  wire emitting = data_active && data_remaining;
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

        // ---- header capture: shift words in; after the partial last header
        // word, hdr_bytes[k] = wire byte k ----
        if (rx_widx <= HDR_FULL_WORDS-1) begin
          hdr_bytes <= {in_dat, hdr_bytes[ETH_HDR_BYTES-1:4]};
        end else if (rx_widx == HDR_FULL_WORDS) begin
          {residue, hdr_bytes}  <= {in_dat, hdr_bytes[ETH_HDR_BYTES-1:RESIDUE_BYTES]};
          sent_bytes <= '0;
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
        end

        if (in_eof)
          rx_widx <= '0;
      end
    end
  end

endmodule
