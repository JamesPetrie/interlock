// record_layer — packet stream -> verbatim pass-through + record stream.
//
// The packet layer of the commitment hierarchy (see docs/traffic_commit.md). It
// instantiates leaf_hash (which forks the packet to the wire and computes the two
// per-packet leaf hashes in parallel: H(payload), and `mid` = the packet-hash
// state after the header block), then finishes the packet hash and emits one
// record per packet for the bucket hash:
//
//   H(packet) = SHA256( header || H(payload) ) = compress( .iv(mid), .blk(H(payload)||pad) )
//   record    = length || H(packet)
//
// leaf_hash has already done block 1 (the header) into `mid`, in parallel with the
// payload hash, so record_layer only runs the second block (the digest block) on
// one core: it chains H(payload) onto mid. One block per packet -- the packet hash
// keeps up with the payload hash even for minimal (sub-block) payloads.
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

  // ---- leaf hashes + pass-through ----
  // leaf_hash bundles both leaves of each packet: H(payload) and the header-primed
  // chaining value mid, presented together on pv_valid.
  wire                 pv_valid, pv_flush;
  wire [255:0]         pv_digest, pv_mid;
  wire [15:0]          pv_len;

  leaf_hash #(
    .HDR_BYTES (HDR_BYTES)
  ) u_leaf (
    .clk (clk), .rst_n (rst_n),
    .s_valid (s_valid), .s_ready (s_ready), .s_data (s_data), .s_keep (s_keep),
    .s_last (s_last), .s_user (s_user),
    .m_valid (m_valid), .m_ready (m_ready), .m_data (m_data), .m_keep (m_keep),
    .m_last (m_last), .m_user (m_user),
    .pv_valid (pv_valid), .pv_digest (pv_digest), .pv_mid (pv_mid),
    .pv_len (pv_len), .pv_flush (pv_flush)
  );

  // ==== block 2: digest core ====
  // The pv pulse is latched into a 1-deep skid so the digest can wait for the core,
  // decoupling the un-handshaked pulse from the launch. mid arrives bundled with
  // the digest, so no separate pairing is needed.
  logic         pend_v, pend_flush;
  logic [255:0] pend_digest, pend_mid;
  logic [15:0]  pend_len;

  localparam int unsigned ZPAD2 = 512 - 256 - 8 - 64;             // 184 zero bits
  wire [63:0]  bitlen2 = 64'((HDR_BYTES + 32) * 8);               // 96-byte message
  wire [511:0] d_block = {pend_digest, 8'h80, {ZPAD2{1'b0}}, bitlen2};
  // a real packet chains onto its mid; the delimiter has none, so it runs on a
  // throwaway iv purely to space its close (the digest is discarded, rec_bytes=0)
  wire [255:0] d_iv    = pend_flush ? SHA256_H_INIT : pend_mid;

  logic        dcore_busy;
  wire         d_done;
  wire [255:0] d_hash;
  wire         d_start = pend_v && !dcore_busy;

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
      dcore_busy <= 1'b0;
      pend_v <= 1'b0; pend_flush <= 1'b0;
      pend_digest <= '0; pend_mid <= '0; pend_len <= '0;
      infl_len <= '0; infl_flush <= 1'b0;
    end else begin
      // pv skid: capture each digest/mid/flush; cleared as block-2 launches it
      if (pv_valid) begin
        pend_v      <= 1'b1;
        pend_digest <= pv_digest;
        pend_mid    <= pv_mid;
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
