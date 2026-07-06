// cert_build — certificate construction and signature.
//
// The top layer of the commitment hierarchy (see docs/traffic_commit.md). Once
// per second it takes the finished "overall" hash plus the second's metadata,
// assembles the signed message m, computes tau = HMAC_k(m) on its own hash
// core, and drives the certificate frame out a 32-bit AXI-Stream port:
//
//   m   = ( version, interlock_id, bucket_start, num_buckets, overall_req,
//           overall_rsp, nonce, prev_tau )
//   tau = HMAC_k( m )
//   frame = [ reserved canonical header (zeros) ] || m || tau
//
// The reserved zero header (HDR_BYTES, its leading id = 0) flags the frame as a
// certificate to the receiver. The block latches one overall digest (with its
// metadata) on an in_valid pulse and is busy ~one HMAC until the frame drains.
//
// RSP_SYNC selects the rsp input's pairing. 1 (prod): rsp is synchronous —
// a certificate needs both digests, one rsp per cert. 0 (recomp core): rsp
// is asynchronous — a free-running sample latched whenever its valid pulses
// (like the nonce), never gating emission; certificates follow the req
// digest's cadence alone and carry the last-sampled value (zero before the
// first sample, stale after — pairing/validity semantics are the protocol
// layer's to define).
// It cannot back-pressure -- an overall arrives once per certificate period
// (~1 s), vastly longer than that, so a pulse sink with no in_ready suffices.
//
// Just structural wiring, no state machine: a serializer streams m into the
// HMAC, and a second serializer streams the assembled frame out when the HMAC
// finishes. The two serializers' own sequencing plus the HMAC's done is all the
// ordering the feed -> mac -> emit pipeline needs.

module cert_build
#(
  parameter logic [31:0] VERSION      = 32'h0000_0006,
  parameter logic [31:0] INTERLOCK_ID = 32'h42,
  parameter int unsigned HDR_BYTES    = 64,                 // reserved cert-header length
  parameter int unsigned NUM_BUCKETS  = 1000,               // buckets per certificate
  parameter bit          RSP_SYNC     = 1'b1                // 0: rsp sampled async, req drives emission
) (
  input  wire        clk,
  input  wire        rst_n,
  input  wire [255:0] key,

  // overall digests + this second's metadata (latched on the in_valid pulse)
  input  wire         in_valid_req,
  input  wire [255:0] in_overall_req,
  input  wire         in_valid_rsp,
  input  wire [255:0] in_overall_rsp,
  input  wire [127:0] in_nonce,

  // certificate frame master (len @ beat #0)
  output wire        c_valid,
  input  wire        c_ready,
  output wire [31:0] c_data,
  output wire [3:0]  c_keep,
  output wire        c_last,
  output wire [15:0] c_user
);

  // Signed message m. Packed-struct order is the big-endian wire order (first
  // field = byte 0 = MSB). See docs/traffic_commit.md (Certificate wire format).
  typedef struct packed {
    logic [31:0]  version;
    logic [31:0]  interlock_id;
    logic [63:0]  bucket_start;
    logic [31:0]  num_buckets;
    logic [255:0] overall_req;
    logic [255:0] overall_rsp;
    logic [127:0] nonce;
    logic [255:0] prev_tau;
  } cert_msg_t;

  localparam int unsigned M_BYTES     = $bits(cert_msg_t) / 8;
  localparam int unsigned FRAME_BYTES = HDR_BYTES + M_BYTES + 32;

  logic         overall_req_valid;
  logic [255:0] overall_req;

  logic         overall_rsp_valid;
  logic [255:0] overall_rsp;

  logic [127:0] nonce_q;                  // this second's nonce, latched with the overalls

  wire digests_valid = overall_req_valid && (!RSP_SYNC || overall_rsp_valid);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      overall_req_valid <= 1'b0;
      overall_req       <= '0;
      overall_rsp_valid <= 1'b0;
      overall_rsp       <= '0;
      nonce_q           <= '0;
    end else begin
      if (in_valid_req) begin
        overall_req_valid <= 1'b1;
        overall_req       <= in_overall_req;
        nonce_q           <= in_nonce; // also update the nonce if changed
      end
      if (in_valid_rsp) begin
        overall_rsp_valid <= 1'b1;
        overall_rsp       <= in_overall_rsp;
        if (RSP_SYNC) begin
          nonce_q <= in_nonce; // also update the nonce if changed
        end
      end
      if (digests_valid) begin
        overall_req_valid <= 1'b0;
        if (RSP_SYNC) begin
          overall_rsp_valid <= 1'b0;
        end
      end
    end
  end

  logic [31:0] bkt_start;

  // Certificate chain: tau of the previous certificate, folded into the next
  // m. Advances on EVERY h_done — including certs whose frame is dropped
  // because u_ser_f still holds a wire-stalled one (late link-up) — so the
  // verifier can reconstruct the dropped traffic-free certs and check them
  // against the next delivered cert's prev_tau. Emission is best-effort,
  // accounting is not (see docs/traffic_commit.md, Implementation status).
  logic [255:0] prev_tau_q;

  // m assembled combinationally from the latched per-certificate values (the
  // registered overalls + nonce, and bkt_start/prev_tau_q before their
  // post-cert bump).
  cert_msg_t m_reg;
  always_comb begin
    m_reg.version      = VERSION;
    m_reg.interlock_id = INTERLOCK_ID;
    m_reg.bucket_start = bkt_start;
    m_reg.num_buckets  = 32'(NUM_BUCKETS);
    m_reg.overall_req  = overall_req;
    m_reg.overall_rsp  = overall_rsp;
    m_reg.nonce        = nonce_q;
    m_reg.prev_tau     = prev_tau_q;
  end

  logic cuser_pend;     // the frame's length rides its first beat

  // ---- m -> HMAC: stream the message into the HMAC core ----
  localparam int unsigned SM_IB_W = $clog2(M_BYTES+1);

  wire [SM_IB_W-1:0] sm_ib = M_BYTES;

  wire         sm_ov, sm_or, sm_last;
  wire [31:0]  sm_od;
  wire [2:0]   sm_ob;
  wire         h_done;
  wire [255:0] tau;

  serializer #(.MAX_BYTES(M_BYTES)) u_ser_m (
    .clk (clk), .rst_n (rst_n),
    .in_valid (digests_valid), .in_ready (/* idle: one m per ~s period */),
    .in_data (m_reg), .in_bytes (sm_ib), .in_last (1'b1),
    .out_valid (sm_ov), .out_data (sm_od), .out_ready (sm_or),
    .out_bytes (sm_ob), .out_last (sm_last)
  );

  // auto-framed: the HMAC launches on the serializer's first beat (sm_ov) and
  // pulses h_done with tau when the mac completes.
  hmac_sha256 u_hmac (
    .clk (clk), .rst_n (rst_n), .key (key),
    .in_valid (sm_ov), .in_ready (sm_or), .in_data (sm_od),
    .in_bytes (sm_ob), .in_last (sm_last), .done (h_done), .digest (tau),
    .len (/* unused */)
  );

  // ---- frame -> output: stream [ reserved hdr (zeros) || m || tau ] out ----
  localparam int unsigned SF_IB_W = $clog2(FRAME_BYTES+1);

  wire [SF_IB_W-1:0] sf_ib = FRAME_BYTES;

  wire         sf_ov, sf_last;
  wire [31:0]  sf_od;
  wire [2:0]   sf_ob;

  serializer #(.MAX_BYTES(FRAME_BYTES)) u_ser_f (
    .clk (clk), .rst_n (rst_n),
    .in_valid (h_done), .in_ready (/* idle: one frame per ~cert period */),
    .in_data ({{(HDR_BYTES*8){1'b0}}, m_reg, tau}), .in_bytes (sf_ib), .in_last (1'b1),
    .out_valid (sf_ov), .out_data (sf_od), .out_ready (c_ready),
    .out_bytes (sf_ob), .out_last (sf_last)
  );

  assign c_valid = sf_ov;
  assign c_data  = sf_od;
  assign c_keep  = 4'b1111 >> (4-sf_ob);
  assign c_last  = sf_last;
  assign c_user  = (sf_ov && cuser_pend) ? 16'(FRAME_BYTES) : 16'h0;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bkt_start <= '0;
      cuser_pend <= 1'b0;
      prev_tau_q <= '0; // TODO anchor certificate binding across power cycles (wait for first nonce?)
    end else begin
      if (h_done) begin
        cuser_pend <= 1'b1;
        bkt_start <= bkt_start + NUM_BUCKETS;
        prev_tau_q <= tau;
      end else if (c_valid && c_ready) begin
        cuser_pend <= 1'b0;
      end
    end
  end

endmodule
