// traffic_commit — format-agnostic commitment block (see docs/traffic_commit.md).
//
// Sits on the drain side, one instance per direction, between the batch buffer
// and the reframe edge. It forwards every drained packet verbatim toward the
// wire and, once per second (every BUCKETS_PER_SEC batches), emits one signed
// attestation on a *separate* certificate AXIS port.
//
// Layered hash hierarchy, per the Interlock Verification Protocol (v6):
//
//   H(payload) = SHA256(ciphertext)                              per packet
//   record     = length || SHA256(header || H(payload))          per packet
//   overall     = SHA256( record_1 || ... || record_k )          per attestation
//
// Structural wiring of small reusable parts: record_layer -> serializer
// -> sha256_msg (bucket) -> serializer -> sha256_msg (overall) -> cert_build.
//
// The stages are rate-matched, so nothing back-pressures or is buffered: each
// digest pulse is captured straight by the next consumer the cycle it fires. A
// record (~9 words) is consumed far faster than a packet hash produces one; the
// overall serializer latches a bucket digest off its pulse (idle between
// batches); and cert_build latches an overall digest off its pulse. The hashes
// are never gated and there is no skid register anywhere -- the working set is
// just the cores' partial state. The one timing premise is that a certificate
// period (BUCKETS_PER_SEC batches) exceeds cert_build's HMAC (~350 cyc), so
// cert_build is idle when each overall arrives. At the production rate (one cert
// per second) that holds by a millionfold; a timeline-compressed test must still
// space its certs past the HMAC or it will drop the trailing one.
// Completeness across seconds rides on the bucket numbering:
// consecutive certs tile the timeline (bucket_start_N = bucket_start_{N-1} +
// num_buckets). nonce is driven in from canon_proc (inbound id=0 control packet).

module traffic_commit #(
  parameter int unsigned HDR_BYTES       = 64,
  // keep the empty swap beat in the output
  parameter bit          OUTPUT_SWAP     = 1
) (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave (from batch_buffer): len @ beat #0. A bucket boundary is a
  // standalone empty beat (tkeep=0, tlast) between packets — it closes the overall
  // hash and builds no record (it still passes through verbatim downstream).
  input  wire        tvalid_s,
  output wire        tready_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire [15:0] tuser_s,

  // AXI-Stream master (packets verbatim toward eth_reframe): len @ beat #0
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m,

  output wire         overall_valid,
  output wire [255:0] overall
);

  // Output swap beat masing
  wire tvalid_unmasked;

  // ---- record layer ----
  wire         rec_valid, rec_last;        // element output is a pulse (rate-matched)
  wire [5:0]   rec_bytes;                  // 34 for a record, 0 for the bucket-close element
  wire [15:0]  rec_len;
  wire [255:0] rec_digest;

  record_layer #(
    .HDR_BYTES (HDR_BYTES)
  ) u_rec (
    .clk (clk), .rst_n (rst_n),
    .s_valid (tvalid_s), .s_ready (tready_s), .s_data (tdata_s), .s_keep (tkeep_s),
    .s_last (tlast_s), .s_user (tuser_s),
    .m_valid (tvalid_unmasked), .m_ready (tready_m), .m_data (tdata_m), .m_keep (tkeep_m),
    .m_last (tlast_m), .m_user (tuser_m),
    .rec_valid (rec_valid), .rec_len (rec_len), .rec_bytes (rec_bytes),
    .rec_digest (rec_digest), .rec_last (rec_last)
  );

  //
  assign tvalid_m = tvalid_unmasked && (OUTPUT_SWAP || tkeep_m != '0);

  // ---- serialize records -> overall hash (always ready: drains an element long
  //      before the next arrives, so record_layer needs no rec_ready) ----
  wire        so_ov, so_or, so_olast;
  wire [31:0] so_od;
  wire [2:0]  so_ob;

  serializer #(.MAX_BYTES(34)) u_ser_o (
    .clk (clk), .rst_n (rst_n),
    .in_valid (rec_valid), .in_ready (/* rate-matched, always ready */),
    .in_data ({rec_len, rec_digest}), .in_bytes (rec_bytes), .in_last (rec_last),
    .out_valid (so_ov), .out_data (so_od), .out_ready (so_or),
    .out_bytes (so_ob), .out_last (so_olast)
  );

  // overall hash: absorbs continuously; cert_build captures its digest straight
  // off the ovr_ov pulse — cert_build is idle for a whole certificate period
  // (once per second in production) between overalls, so no hold is needed
  wire          ovr_ov;
  wire [255:0]  ovr_od;

  sha256_msg u_overall (
    .clk (clk), .rst_n (rst_n),
    .in_valid (so_ov), .in_ready (so_or), .in_data (so_od),
    .in_bytes (so_ob), .in_last (so_olast),
    .done (ovr_ov), .digest (ovr_od)
  );

  assign overall_valid = ovr_ov;
  assign overall       = ovr_od;

endmodule
