// payload_hash — pass-through + ciphertext (payload) hash, on the data path.
//
// The data-path stage of the commitment hierarchy (see docs/traffic_commit.md).
// It forks the packet stream with an axis_splitter: one copy is the verbatim
// pass-through toward the wire (drives traffic_commit's data_out), the other is
// masked down to the payload and hashed. The hash itself only ever sees the
// payload bytes, never the full packet, so it does not manage the output.
//
//   H(payload) = SHA256( ciphertext bytes )
//
// Every packet carries the full HDR_BYTES response header, so the split is fixed:
// the first HDR_BYTES bytes are the opaque header (captured for record_layer,
// masked off the hash branch), the rest is the payload. On packet end the block
// presents H(payload) with the captured header, the length (bytes actually
// processed -- the true emitted length, accumulated as a counter, not parsed and
// not the claimed tuser value), and the swap flag. A packet of exactly HDR_BYTES
// has an empty payload run -> SHA256("").

module payload_hash #(
  parameter int unsigned HDR_BYTES = 16
) (
  input  wire        clk,
  input  wire        rst_n,

  // packet slave (from batch_buffer): len @ beat #0, swap flag (bit 0) @ tlast
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

  // per-packet payload result (pulse when H(payload) finalises; the receiver,
  // record_layer, is always ready — see its header — so there is no handshake)
  output wire                   pv_valid,
  output wire [255:0]           pv_digest,    // H(payload)
  output wire [HDR_BYTES*8-1:0] pv_hdr,       // full HDR_BYTES header, byte 0 in [7:0]
  output wire [15:0]            pv_len,       // bytes processed (true length)
  output wire                   pv_swap       // closes a batch
);

  localparam int unsigned HDR_WORDS = HDR_BYTES / 4;
  localparam int unsigned HW_IDX_W  = (HDR_WORDS <= 1) ? 1 : $clog2(HDR_WORDS);
  localparam int unsigned PW_W      = $clog2(eth_pkg::ETH_LEN_MAX/4 + 2);

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

  // ---- payload hash core ----
  logic        h_valid, h_last;
  logic [31:0] h_data;
  logic [2:0]  h_bytes;
  wire         h_ready, h_done;
  wire [255:0] h_digest;

  sha256_msg u_payload (
    .clk (clk), .rst_n (rst_n),
    .in_valid (h_valid), .in_ready (h_ready), .in_data (h_data),
    .in_bytes (h_bytes), .in_last (h_last),
    .done (h_done), .digest (h_digest)
  );

  // No state machine on the data path: a counter `cnt` splits header from payload
  // and the fork is gated on the hash's h_ready, which now stays high across
  // packet boundaries (the core pipelines absorb against compress). So while
  // packet N is still finalising, packet N+1's header is already being absorbed —
  // a single metadata register would be clobbered. Instead a small metadata FIFO
  // holds {header, length, swap} per packet, read back in order as each digest
  // pulses (h_done). The FIFO slot doubles as the accumulator: header words land
  // straight into slot wptr as they stream, and the slot is committed (wptr++) at
  // the last beat. At most two packets are in flight (one compressing, one
  // absorbed), so depth 2 suffices.
  localparam int unsigned FD = 2;

  logic [PW_W-1:0]            cnt;          // word index within the packet
  logic                      wptr, rptr;    // 1-bit slot indices (depth 2)
  logic [1:0]                occ;           // occupancy 0..2
  logic [HDR_WORDS-1:0][31:0] f_hdr  [0:FD-1]; // REVISIT is 2D array OK in FPGA?
  logic [15:0]                f_len  [0:FD-1];
  logic                       f_swap [0:FD-1];
  wire fifo_full = (occ == 2'd2);

  wire [2:0] keep_cnt = 3'($countones(b_keep));
  wire is_hdr = (cnt < PW_W'(HDR_WORDS));

  assign b_ready = h_ready && !fifo_full;     // also stall if metadata can't be queued
  wire fire_b = b_valid && b_ready;
  wire push   = fire_b && b_last;

  // ---- payload hash feed: payload bytes, plus a 0-byte in_last on a header-only
  // packet's last beat (the empty input). The header is never hashed (masked off
  // by h_valid), so header beats consume nothing in the core. ----
  assign h_valid = b_valid && !fifo_full && (!is_hdr || b_last);
  assign h_data  = b_data;
  assign h_bytes = is_hdr ? 3'd0 : keep_cnt;
  assign h_last  = b_last;

  // ---- result: the core's done pulse with the head-of-FIFO metadata, which is
  // this packet's (FIFO drains in lock-step with the in-order digests). ----
  assign pv_valid  = h_done;
  assign pv_digest = h_digest;
  assign pv_hdr    = f_hdr[rptr];          // packed words -> flat header vector
  assign pv_len    = f_len[rptr];
  assign pv_swap   = f_swap[rptr];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= '0; wptr <= 1'b0; rptr <= 1'b0; occ <= '0;
    end else begin
      if (fire_b) begin
        // accumulate straight into the open slot; header words by index, length
        // running, swap and the wptr++ commit on the last beat
        if (is_hdr)
          f_hdr[wptr][cnt[HW_IDX_W-1:0]] <= b_data;
        f_len[wptr] <= (cnt == '0) ? 16'(keep_cnt) : (f_len[wptr] + 16'(keep_cnt));
        cnt <= b_last ? '0 : (cnt + PW_W'(1));
        if (b_last) begin
          //TODO
          //f_swap[wptr] <= b_user[0];
          f_swap[wptr] <= 1'b1;
          wptr <= ~wptr;
        end
      end
      if (h_done) rptr <= ~rptr;               // pop as each digest is presented
      occ <= occ + (push ? 2'd1 : 2'd0) - (h_done ? 2'd1 : 2'd0);
    end
  end

endmodule
