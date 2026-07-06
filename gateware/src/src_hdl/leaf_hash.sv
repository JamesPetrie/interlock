// leaf_hash — pass-through + the two per-packet leaf hashes of the commitment.
//
// The data-path stage of the commitment hierarchy (see docs/traffic_commit.md).
// It forks the packet stream with an axis_splitter: one copy is the verbatim
// pass-through toward the wire, the other is split into header and payload and
// hashed. The two leaf hashes are INDEPENDENT sub-pipelines, run side by side and
// joined per packet:
//
//   H(payload) = SHA256( payload bytes )                 -- u_payload (streaming)
//   mid        = SHA256_compress( H_INIT, header block ) -- u_hdr (1 block, no pad)
//
// `mid` is the packet-hash chaining value after the fixed 64-byte (= one block)
// header; record_layer finishes the packet hash by chaining H(payload) onto it:
//   H(packet) = SHA256( header || H(payload) ) = compress( mid, H(payload)||pad )
//
// The two sub-pipelines are kept independent (not lock-stepped); their results
// are consumed in pairs at the join. u_payload is the throughput bottleneck and
// the ONLY block that back-pressures the AXI-Stream (its in_ready). The header
// side runs alongside: as the header words stream into hdr_acc, the moment a full
// header is present and u_hdr is free it kicks off automatically, dropping its
// `mid` into the header FIFO. Because u_hdr and u_payload share the same core
// (same block period) and u_hdr does one block/packet while u_payload does >= one,
// the header side keeps up on its own -- no header-side back-pressure is needed
// (b_ready = h_ready); u_payload alone gates the input.
//
// The header FIFO is decoupled from the payload side: it holds {mid, flush} per
// packet, allocated in packet order and read in order at the join (its only
// coupling to the payload side is the paired pop). The record length (PLD_LEN,
// the payload byte count) is NOT tracked here -- it comes from u_payload (the byte
// count it hashed = payload only), so the header side never has to parse the header.
//
// The empty bucket-boundary delimiter (pv_flush) has no header: u_hdr is not
// triggered; a bubble {flush=1} is allocated into the header FIFO directly. It
// still feeds u_payload a 0-byte message, so it produces a paired (discarded)
// digest and stays aligned at the join. A packet of exactly HDR_BYTES has an
// empty payload run -> SHA256("").

module leaf_hash
  // imports are what make the package dependencies visible to Libero's
  // compile-order scanner; qualified references alone are not tracked
  import eth_pkg::*;
  import sha256_pkg::*;
#(
  parameter int unsigned HDR_BYTES = 64       // canon_pkg::CANON_REQ_HDR_BYTES
) (
  input  wire        clk,
  input  wire        rst_n,

  // packet slave (from batch_buffer): len @ beat #0
  input  wire        s_valid,
  output wire        s_ready,
  input  wire [31:0] s_data,
  input  wire [3:0]  s_keep,
  input  wire        s_last,
  input  wire [15:0] s_user,

  // packet master (verbatim toward eth_reframe): drives traffic_commit data_out
  output wire        m_valid,
  input  wire        m_ready,
  output wire [31:0] m_data,
  output wire [3:0]  m_keep,
  output wire        m_last,
  output wire [15:0] m_user,

  // per-packet leaf results (pulse when both leaves are joined; the receiver,
  // record_layer, is rate-matched -- no handshake)
  output wire                   pv_valid,
  output wire [255:0]           pv_digest,    // H(payload)
  output wire [255:0]           pv_mid,       // packet-hash state after the header block
  output wire [15:0]            pv_len,       // PLD_LEN: payload byte count (u_payload hashes payload only)
  output wire                   pv_flush      // 1 = empty bucket-boundary delimiter
);

  localparam int unsigned HDR_WORDS = HDR_BYTES / 4;
  localparam int unsigned HDR_BITS  = HDR_BYTES * 8;
  localparam int unsigned PW_W      = $clog2(eth_pkg::ETH_LEN_MAX/4 + 2);

  // Header FIFO depth.
  localparam int unsigned HD   = 2;
  localparam int unsigned HD_W = $clog2(HD);

  // ---- fork the packet stream: a = pass-through, b = hash branch ----
  wire        b_valid, b_ready, b_last;
  wire [31:0] b_data;
  wire [3:0]  b_keep;
  wire [15:0] b_user;

  axis_splitter u_split (
    .s_valid (s_valid), .s_ready (s_ready), .s_data (s_data), .s_keep (s_keep),
    .s_last (s_last), .s_user (s_user),
    .a_valid (m_valid), .a_ready (m_ready), .a_data (m_data), .a_keep (m_keep),
    .a_last (m_last), .a_user (m_user),
    .b_valid (b_valid), .b_ready (b_ready), .b_data (b_data), .b_keep (b_keep),
    .b_last (b_last), .b_user (b_user)
  );

  // ---- payload hash core (streaming, multi-block, byte counter) ----
  logic        h_valid, h_last;
  logic [31:0] h_data;
  logic [2:0]  h_bytes;
  wire         h_ready, h_done;
  wire [255:0] h_digest;
  wire [31:0]  h_len;

  sha256_msg u_payload (
    .clk (clk), .rst_n (rst_n),
    .in_valid (h_valid), .in_ready (h_ready), .in_data (h_data),
    .in_bytes (h_bytes), .in_last (h_last),
    .done (h_done), .digest (h_digest), .len (h_len)
  );

  // ---- packet-position counter and beat classification ----
  logic [PW_W-1:0] cnt;          // word index within the packet
  wire [2:0] keep_cnt = 3'($countones(b_keep));
  wire is_hdr = (cnt < PW_W'(HDR_WORDS));
  // bucket-boundary delimiter: standalone empty beat at a packet boundary. This
  // is a pure beat-type DECODE -- it deliberately excludes b_valid so that no
  // master-side valid (m_valid via the splitter) can depend on m_ready through the
  // stall logic (an AXIS valid<-ready violation). Every action that consumes
  // is_delim is separately qualified by fire_b / h_valid (which carry b_valid),
  // so a spurious decode while b_valid==0 only ever toggles a stall on a beat that
  // isn't transferring -- harmless.
  wire is_delim = b_last && (b_keep == 4'b0) && (cnt == '0);
  wire is_last_hdr_word = is_hdr && (cnt == PW_W'(HDR_WORDS-1));

  // ===================================================================
  //  Header FIFO (decoupled, depth HD): {mid, flush, valid}, in order.
  // ===================================================================
  logic [255:0]    hf_mid  [0:HD-1];
  logic            hf_flush[0:HD-1];
  logic [HD_W-1:0] hf_wptr, hf_rptr;
  logic [HD_W:0]   hf_occ;

  // ---- header accumulator + u_hdr (header block-1 core) ----
  logic [HDR_BITS-1:0] hdr_acc;
  wire  [HDR_BITS-1:0] hdr_be;
  for (genvar gi = 0; gi < HDR_BYTES; gi++)
    assign hdr_be[HDR_BITS-1 - 8*gi -: 8] = hdr_acc[8*gi +: 8];

  logic            pending_hdr;     // hdr_hold holds a header waiting for u_hdr
  logic            pending_flush;   // flush waiting for u_hdr
  logic            hcore_busy;
  wire             hh_done;
  wire [255:0]     hh_mid;
  wire             hh_start     = pending_hdr   && !hcore_busy;
  wire             insert_flush = pending_flush && !hcore_busy;

  sha256_core u_hdr (
    .clk (clk), .rst_n (rst_n),
    .start (hh_start), .iv (sha256_pkg::SHA256_H_INIT), .block (hdr_be),
    .done (hh_done), .digest (hh_mid)
  );

  assign b_ready = h_ready; // bakcpressure when payload core is full, even if current beat is not for payload core
  wire fire_b = b_valid && b_ready;

  // ---- payload hash feed: payload bytes; header masked (in_bytes=0); a 0-byte
  // in_last closes header-only / delimiter packets. ----
  assign h_valid = b_valid && (!is_hdr || b_last);
  assign h_data  = b_data;
  assign h_bytes = is_hdr ? 3'd0 : keep_cnt;
  assign h_last  = b_last;

  // ---- result: the core's done pulse with the head-of-FIFO metadata. ----
  assign pv_valid  = h_done;
  assign pv_flush  = hf_flush[hf_rptr];
  assign pv_mid    = hf_mid[hf_rptr];
  assign pv_len    = h_len[15:0];   // PLD_LEN: payload bytes (u_payload counts payload only)
  assign pv_digest = h_digest;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= '0;
      hdr_acc <= '0;
      pending_hdr <= 1'b0; pending_flush <= 1'b0;
      hcore_busy <= 1'b0;
      hf_wptr <= '0; hf_rptr <= '0; hf_occ <= '0;
    end else begin
      // ---- absorb side: header accumulate, counter, slot allocation ----
      if (fire_b) begin
        if (is_hdr && !is_delim) begin
          hdr_acc[32*cnt +: 32] <= b_data;
          if (is_last_hdr_word) begin
            pending_hdr <= 1'b1;
          end
        end
        cnt <= b_last ? '0 : (cnt + PW_W'(1));

        if (is_delim) begin
          pending_flush <= 1'b1;
        end
      end

      // ---- header core: kick when a header is held and the core is free ----
      if (hh_start) begin
        hcore_busy  <= 1'b1;
        pending_hdr <= 1'b0;            // pending header consumed (latched by u_hdr)
      end
      if (hh_done) begin
        hcore_busy        <= 1'b0;
        hf_mid[hf_wptr]   <= hh_mid;
        hf_flush[hf_wptr] <= 1'b0;
        hf_wptr           <= hf_wptr + 1'b1;
      end
      // bypass header core for flush
      if (insert_flush) begin
        hf_mid[hf_wptr]   <= '0;
        hf_flush[hf_wptr] <= 1'b1;
        hf_wptr           <= hf_wptr + 1'b1;
        pending_flush     <= 1'b0;
      end

      // increment fifo read pointer when an entry is consumed
      if (pv_valid) begin
        hf_rptr <= hf_rptr + 1'b1;
      end

      // ---- header FIFO occupancy (alloc +1, pop -1; both/neither -> hold) ----
      case ( { (hh_done || insert_flush), pv_valid } )
        2'b10:   hf_occ <= hf_occ + 1'b1;
        2'b01:   hf_occ <= hf_occ - 1'b1;
        default: hf_occ <= hf_occ;
      endcase
    end
  end

endmodule
