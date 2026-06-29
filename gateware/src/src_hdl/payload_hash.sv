// payload_hash — pass-through + ciphertext (payload) hash + early header hand-off.
//
// The data-path stage of the commitment hierarchy (see docs/traffic_commit.md).
// It forks the packet stream with an axis_splitter: one copy is the verbatim
// pass-through toward the wire (drives traffic_commit's data_out), the other is
// masked down to the payload and hashed. The hash itself only ever sees the
// payload bytes, never the full packet, so it does not manage the output.
//
//   H(payload) = SHA256( ciphertext bytes )
//
// Every packet carries the full HDR_BYTES header, so the split is fixed: the
// first HDR_BYTES bytes are the opaque header (masked off the hash branch), the
// rest is the payload. The header is handed off to record_layer *early* — the
// cycle its final word streams in, long before the payload digest — over a small
// handshake (hdr_valid/hdr_ready), so record_layer can hash the header block in
// parallel with the payload. That handshake is also the pipeline's single
// throttle: if record_layer's header core is busy, the final header beat stalls
// here (ordinary input back-pressure), keeping every downstream core lossless.
//
// On packet end the block presents H(payload) with the true length (bytes
// processed, accumulated as a counter — not the claimed tuser value) and a flush
// tag (set only for the empty bucket-boundary delimiter). A packet of exactly
// HDR_BYTES has an empty payload run -> SHA256("").

module payload_hash #(
  parameter int unsigned HDR_BYTES = 64
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

  // early header hand-off (handshaked): the full HDR_BYTES header, byte 0 in [7:0],
  // presented as soon as it has streamed in -- well before the payload digest -- so
  // record_layer can hash the header block (block 1) in parallel with the payload.
  output wire                   hdr_valid,
  input  wire                   hdr_ready,
  output wire [HDR_BYTES*8-1:0] hdr_data,

  // per-packet payload result (pulse when H(payload) finalises; the receiver,
  // record_layer, is rate-matched -- no handshake)
  output wire                   pv_valid,
  output wire [255:0]           pv_digest,    // H(payload)
  output wire [15:0]            pv_len,       // bytes processed (true length)
  output wire                   pv_flush      // 1 = empty bucket-boundary delimiter (no record)
);

  localparam int unsigned HDR_WORDS = HDR_BYTES / 4;
  localparam int unsigned HDR_BITS  = HDR_BYTES * 8;
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

  // Metadata FIFO holds {length, flush} per packet, read back in order as each
  // digest pulses (h_done). The header is no longer stored here -- it is handed
  // off early over hdr_valid/hdr_ready. At most two packets are in flight (one
  // compressing, one absorbed), so depth 2 suffices.
  localparam int unsigned FD = 2;

  logic [PW_W-1:0] cnt;          // word index within the packet
  logic            wptr, rptr;   // 1-bit slot indices (depth 2)
  logic [1:0]      occ;          // occupancy 0..2
  logic [15:0]     f_len  [0:FD-1];
  logic            f_flush[0:FD-1];
  wire fifo_full = (occ == 2'd2);

  wire [2:0] keep_cnt = 3'($countones(b_keep));
  wire is_hdr = (cnt < PW_W'(HDR_WORDS));

  // bucket-boundary delimiter: a standalone empty beat (tkeep==0, tlast) at a
  // packet boundary (cnt==0). Not a packet -- builds no record and has no header;
  // tagged in the FIFO and surfaced as pv_flush so record_layer closes the bucket.
  wire is_delim = b_valid && b_last && (b_keep == 4'b0) && (cnt == '0);

  // ---- early header hand-off ----
  // hdr_acc collects the header words as they stream; on the final header word
  // the completed header is latched into hdr_reg and presented (hdr_pend) until
  // record_layer's header core takes it. If a new header would complete while the
  // previous one is still unconsumed, the final-word beat is stalled (back-pressure).
  wire is_last_hdr_word = is_hdr && (cnt == PW_W'(HDR_WORDS-1));
  logic [HDR_BITS-1:0] hdr_acc;
  logic [HDR_BITS-1:0] hdr_reg;
  logic                hdr_pend;
  wire  hdr_stall = b_valid && is_last_hdr_word && hdr_pend && !hdr_ready;

  assign hdr_valid = hdr_pend;
  assign hdr_data  = hdr_reg;

  assign b_ready = h_ready && !fifo_full && !hdr_stall;   // stall metadata / header back-pressure
  wire fire_b = b_valid && b_ready;
  wire push   = fire_b && b_last;

  // ---- payload hash feed: payload bytes, plus a 0-byte in_last on a header-only
  // packet's last beat. The header is never hashed (masked by h_valid). ----
  assign h_valid = b_valid && !fifo_full && !hdr_stall && (!is_hdr || b_last);
  assign h_data  = b_data;
  assign h_bytes = is_hdr ? 3'd0 : keep_cnt;
  assign h_last  = b_last;

  // ---- result: the core's done pulse with the head-of-FIFO metadata. ----
  assign pv_valid  = h_done;
  assign pv_digest = h_digest;
  assign pv_len    = f_len[rptr];
  assign pv_flush  = f_flush[rptr];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= '0; wptr <= 1'b0; rptr <= 1'b0; occ <= '0;
      hdr_acc <= '0; hdr_reg <= '0; hdr_pend <= 1'b0;
    end else begin
      if (fire_b) begin
        // header words land into the accumulator by index; length runs; flush tag
        // and the wptr++ commit on the last beat
        if (is_hdr)
          hdr_acc[32*cnt +: 32] <= b_data;
        f_len[wptr] <= (cnt == '0) ? 16'(keep_cnt) : (f_len[wptr] + 16'(keep_cnt));
        cnt <= b_last ? '0 : (cnt + PW_W'(1));
        if (b_last) begin
          f_flush[wptr] <= is_delim;   // set only for the empty boundary delimiter
          wptr <= ~wptr;
        end
      end

      // present the completed header (word HDR_WORDS-1 = b_data on this beat); the
      // fire_b guard means this never fires while hdr_stall holds it back
      if (fire_b && is_last_hdr_word) begin
        hdr_reg  <= {b_data, hdr_acc[HDR_BITS-32-1:0]};
        hdr_pend <= 1'b1;
      end else if (hdr_pend && hdr_ready) begin
        hdr_pend <= 1'b0;
      end

      if (h_done) rptr <= ~rptr;               // pop as each digest is presented
      occ <= occ + (push ? 2'd1 : 2'd0) - (h_done ? 2'd1 : 2'd0);
    end
  end

endmodule
