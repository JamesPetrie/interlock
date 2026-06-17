// record_layer — packet stream -> verbatim pass-through + record stream.
//
// The bottom layer of the commitment hierarchy (see docs/traffic_commit.md). It
// instantiates payload_hash (which forks the packet: verbatim pass-through to
// the wire + the payload hash), folds the captured header into the packet hash,
// and emits one record per packet for the bucket hash:
//
//   H(packet) = SHA256( header || H(payload) )
//   record    = length || H(packet)
//
// presented in parallel (rec_len, rec_digest) as a one-cycle pulse, with rec_last
// marking the last record of a batch (the swap-flagged tlast).
//
// header || H(payload) is at most HDR_BYTES + 32 <= 44 bytes, so H(packet) is a
// *single* SHA-256 block. record_layer assembles that padded block in one cycle
// and hands it straight to a raw sha256_core — no streaming, no per-word feed.
// That makes the stage strictly faster than payload_hash (one block + a 1-cycle
// load, versus a payload absorb + one block), so it is always ready when a
// payload result appears: payload_hash needs no result handshake. Symmetrically,
// the bucket serializer consumes a record (~9 words) far faster than a packet
// arrives (~one block), so the record output is an un-handshaked pulse — there is
// no rec_ready.

module record_layer
  import sha256_pkg::*;
#(
  parameter int unsigned HDR_BYTES = 16
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

  // record master (parallel pulse): length || H(packet); rec_last = batch boundary
  output wire         rec_valid,
  output wire [15:0]  rec_len,
  output wire [255:0] rec_digest,
  output wire         rec_last
);

  // ---- payload hash + pass-through (no result handshake; always ready) ----
  wire                   pv_valid, pv_swap;
  wire [255:0]           pv_digest;
  wire [HDR_BYTES*8-1:0] pv_hdr;          // full header, lane order (byte 0 in [7:0])
  wire [15:0]            pv_len;

  payload_hash #(
    .HDR_BYTES (HDR_BYTES)
  ) u_pay (
    .clk (clk), .rst_n (rst_n),
    .s_valid (s_valid), .s_ready (s_ready), .s_data (s_data), .s_keep (s_keep),
    .s_last (s_last), .s_user (s_user),
    .m_valid (m_valid), .m_ready (m_ready), .m_data (m_data), .m_keep (m_keep),
    .m_last (m_last), .m_user (m_user),
    .pv_valid (pv_valid), .pv_digest (pv_digest),
    .pv_hdr (pv_hdr), .pv_len (pv_len), .pv_swap (pv_swap)
  );

  // ---- assemble the single padded block, byte 0 in the MSBs ----
  //   header (big-endian) || H(payload) || 0x80 || zero fill || 64-bit bit-length
  // The header is always HDR_BYTES, so the layout is fixed (HDR_BYTES + 32 <= 44
  // bytes, one block). pv_hdr arrives lane order (byte 0 in [7:0]); the SHA block
  // wants byte 0 in the MSBs, so reverse it here — the SHA byte order is this
  // stage's concern, not the header producer's. pv_digest is already big-endian.
  wire [HDR_BYTES*8-1:0] hdr_be;
  for (genvar gi = 0; gi < HDR_BYTES; gi++)
    assign hdr_be[HDR_BYTES*8-1 - 8*gi -: 8] = pv_hdr[8*gi +: 8];

  localparam int unsigned ZPAD = 512 - HDR_BYTES*8 - 256 - 8 - 64;   // zero fill bits
  wire [63:0]  bitlen = 64'((HDR_BYTES + 32) * 8);                   // (header + 32 bytes) in bits
  wire [511:0] blk = {hdr_be, pv_digest, 8'h80, {ZPAD{1'b0}}, bitlen};

  // ---- packet hash core: one block, fed in a single cycle ----
  //
  // The block is combinational from the pv result, so it is submitted to the raw
  // core the very cycle pv pulses (start = pv_valid). record_layer is <=
  // payload_hash in latency — one direct block versus a payload absorb plus >=
  // one block — so the core has always finished the previous packet by the time
  // the next pv arrives: no pending register, no handshake. Only the in-flight
  // length/swap are latched, to ride alongside the digest to the output pulse.
  logic [15:0]  infl_len;
  logic         infl_swap;
  wire          core_done;
  wire [255:0]  core_digest;

  sha256_core u_packet (
    .clk (clk), .rst_n (rst_n),
    .start (pv_valid), .iv (sha256_pkg::SHA256_H_INIT), .block (blk),
    .done (core_done), .digest (core_digest)
  );

  // one-cycle record pulse as the block finishes
  assign rec_valid  = core_done;
  assign rec_len    = infl_len;
  assign rec_digest = core_digest;
  assign rec_last   = infl_swap;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      infl_len <= '0; infl_swap <= 1'b0;
    end else if (pv_valid) begin
      infl_len <= pv_len; infl_swap <= pv_swap;
    end
  end

endmodule
