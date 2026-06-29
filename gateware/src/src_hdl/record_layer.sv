// record_layer — packet stream -> verbatim pass-through + record stream.
//
// The bottom layer of the commitment hierarchy (see docs/traffic_commit.md). It
// instantiates payload_hash (which forks the packet: verbatim pass-through to the
// wire + the payload hash and the early header hand-off), folds the header into
// the packet hash, and emits one record per packet for the bucket hash:
//
//   H(packet) = SHA256( header || H(payload) )      header = HDR_BYTES bytes
//   record    = length || H(packet)
//
// With a 64-byte header, header || H(payload) is 96 bytes = TWO SHA blocks:
//   block 1 = header                 (a full 512-bit block, no padding)
//   block 2 = H(payload) || padding  (chained from block 1's mid)
// The two blocks run on TWO cores. Block 1 (u_hdr) starts the moment payload_hash
// hands the header off -- in parallel with the payload hash -- and its result
// (mid) waits in a small FIFO. Block 2 (u_dig) chains from that mid when the
// payload digest arrives. With one block per core per packet, the packet hash
// keeps up with the payload hash even for minimal (sub-block) payloads, where a
// single core doing both blocks would fall behind 2:1.
//
// The empty bucket-boundary delimiter (pv_flush) has no header and builds no
// record; it runs the digest core on a throwaway block purely to space its
// zero-byte (rec_bytes=0, rec_last=1) close one op behind the preceding record,
// which the serializer turns into sha256_msg's empty in_last beat.

module record_layer
  import sha256_pkg::*;
#(
  parameter int unsigned HDR_BYTES = 64
) (
  input  wire        clk,
  input  wire        rst_n,

  // packet slave (from batch_buffer)
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

  // record master: a one-cycle element pulse. A packet is length || H(packet) with
  // rec_bytes=34; the empty bucket-boundary delimiter is a zero-byte element
  // (rec_bytes=0) with rec_last=1 that closes the bucket. Un-handshaked (spacing).
  output wire         rec_valid,
  output wire [15:0]  rec_len,
  output wire [5:0]   rec_bytes,
  output wire [255:0] rec_digest,
  output wire         rec_last
);

  localparam int unsigned HDR_BITS = HDR_BYTES * 8;

  // ---- payload hash + early header hand-off + pass-through ----
  wire                 pv_valid, pv_flush;
  wire [255:0]         pv_digest;
  wire [15:0]          pv_len;
  wire                 hdr_valid, hdr_ready;
  wire [HDR_BITS-1:0]  hdr_data;          // full header, byte 0 in [7:0]

  payload_hash #(
    .HDR_BYTES (HDR_BYTES)
  ) u_pay (
    .clk (clk), .rst_n (rst_n),
    .s_valid (s_valid), .s_ready (s_ready), .s_data (s_data), .s_keep (s_keep),
    .s_last (s_last), .s_user (s_user),
    .m_valid (m_valid), .m_ready (m_ready), .m_data (m_data), .m_keep (m_keep),
    .m_last (m_last), .m_user (m_user),
    .hdr_valid (hdr_valid), .hdr_ready (hdr_ready), .hdr_data (hdr_data),
    .pv_valid (pv_valid), .pv_digest (pv_digest), .pv_len (pv_len), .pv_flush (pv_flush)
  );

  // ==== block 1: header core ====
  // hdr_data is lane order (byte 0 in [7:0]); the SHA block wants byte 0 in the
  // MSBs, so reverse it here. The header is exactly one block, so no padding.
  wire [HDR_BITS-1:0] hdr_be;
  for (genvar gi = 0; gi < HDR_BYTES; gi++)
    assign hdr_be[HDR_BITS-1 - 8*gi -: 8] = hdr_data[8*gi +: 8];

  logic        hcore_busy;
  wire         h_done;
  wire [255:0] h_mid;
  wire         h_start = hdr_valid && hdr_ready;
  assign hdr_ready = !hcore_busy;          // accept a header whenever the core is free

  sha256_core u_hdr (
    .clk (clk), .rst_n (rst_n),
    .start (h_start), .iv (sha256_pkg::SHA256_H_INIT), .block (hdr_be),
    .done (h_done), .digest (h_mid)
  );

  // ---- mid FIFO (depth 2): block-1 results, in packet order ----
  logic [255:0] mid_q [0:1];
  logic         mwr, mrd;
  logic [1:0]   mocc;
  wire          mid_avail = (mocc != 2'd0);

  // ==== block 2: digest core ====
  // The pv (digest) pulse is latched into a 1-deep skid so the digest can wait for
  // its mid / for the core, decoupling the un-handshaked pulse from the launch.
  logic         pend_v, pend_flush;
  logic [255:0] pend_digest;
  logic [15:0]  pend_len;

  localparam int unsigned ZPAD2 = 512 - 256 - 8 - 64;             // 184 zero bits
  wire [63:0]  bitlen2 = 64'((HDR_BYTES + 32) * 8);               // 96-byte message
  wire [511:0] d_block = {pend_digest, 8'h80, {ZPAD2{1'b0}}, bitlen2};
  wire [255:0] d_iv    = pend_flush ? sha256_pkg::SHA256_H_INIT : mid_q[mrd];

  logic        dcore_busy;
  wire         d_done;
  wire [255:0] d_hash;
  // launch when a digest is pending, the core is free, and (real packet) its mid
  // is ready. The delimiter needs no mid, so it never waits on the FIFO.
  wire         d_start = pend_v && !dcore_busy && (pend_flush || mid_avail);
  wire         mid_pop = d_start && !pend_flush;

  sha256_core u_dig (
    .clk (clk), .rst_n (rst_n),
    .start (d_start), .iv (d_iv), .block (d_block),
    .done (d_done), .digest (d_hash)
  );

  // metadata of the in-flight block-2, latched at launch for the output pulse
  logic [15:0] infl_len;
  logic        infl_flush;

  // one-cycle element pulse as block 2 finishes
  assign rec_valid  = d_done;
  assign rec_len    = infl_len;
  assign rec_bytes  = infl_flush ? 6'd0 : 6'd34;
  assign rec_digest = d_hash;
  assign rec_last   = infl_flush;          // only the delimiter closes a bucket

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      hcore_busy <= 1'b0; dcore_busy <= 1'b0;
      mwr <= 1'b0; mrd <= 1'b0; mocc <= '0;
      pend_v <= 1'b0; pend_flush <= 1'b0; pend_digest <= '0; pend_len <= '0;
      infl_len <= '0; infl_flush <= 1'b0;
    end else begin
      // header core busy span
      if (h_start)        hcore_busy <= 1'b1;
      else if (h_done)    hcore_busy <= 1'b0;

      // mid FIFO: push block-1 results, pop as block-2 consumes them
      if (h_done) begin mid_q[mwr] <= h_mid; mwr <= ~mwr; end
      if (mid_pop) mrd <= ~mrd;
      mocc <= mocc + (h_done ? 2'd1 : 2'd0) - (mid_pop ? 2'd1 : 2'd0);

      // pv skid: capture each digest/flush; cleared as block-2 launches it
      if (pv_valid) begin
        pend_v      <= 1'b1;
        pend_digest <= pv_digest;
        pend_len    <= pv_len;
        pend_flush  <= pv_flush;
      end else if (d_start) begin
        pend_v <= 1'b0;
      end

      // digest core busy span; latch the in-flight metadata at launch
      if (d_start) begin
        dcore_busy <= 1'b1;
        infl_len   <= pend_len;
        infl_flush <= pend_flush;
      end else if (d_done) begin
        dcore_busy <= 1'b0;
      end
    end
  end

endmodule
