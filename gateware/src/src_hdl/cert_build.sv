// cert_build — certificate construction and signature.
//
// The top layer of the commitment hierarchy (see docs/traffic_commit.md). Once
// per second it takes the finished "overall" hash plus the second's metadata,
// assembles the signed message m, computes tau = HMAC_k(m) on its own hash
// core, and drives the certificate frame out a 32-bit AXI-Stream port:
//
//   m   = ( version, interlock_id, bucket_start, num_buckets, overall_req,
//           overall_rsp, nonce )
//   tau = HMAC_k( m )
//   frame = [ reserved canonical header (zeros) ] || m || tau
//
// The reserved zero header (HDR_BYTES, its leading id = 0) flags the frame as a
// certificate to the receiver. The block latches one overall digest (with its
// metadata) on an in_valid pulse and is busy ~one HMAC until the frame drains.
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
  parameter int unsigned HDR_BYTES    = 16,                 // reserved cert-header length
  parameter int unsigned NUM_BUCKETS  = 1                   // buckets per certificate
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
  } cert_msg_t;

  localparam int unsigned M_BYTES     = $bits(cert_msg_t) / 8;
  localparam int unsigned FRAME_BYTES = HDR_BYTES + M_BYTES + 32;

  logic [63:0] bkt_start;

  // m assembled combinationally from the live inputs; latched on the in_valid
  // pulse so it survives the HMAC to be re-emitted in the frame.
  cert_msg_t m_reg, m_buf;
  always_comb begin
    m_reg.version      = VERSION;
    m_reg.interlock_id = INTERLOCK_ID;
    m_reg.bucket_start = bkt_start;
    m_reg.num_buckets  = 32'(NUM_BUCKETS);
    m_reg.overall_req  = in_overall_req;
    m_reg.overall_rsp  = in_overall_rsp;
    m_reg.nonce        = in_nonce;
  end

  logic h_start;        // one-cycle launch pulse to the HMAC
  logic cuser_pend;     // the frame's length rides its first beat

  // ---- m -> HMAC: stream the 72-byte message into the HMAC core ----
  wire         sm_ov, sm_or, sm_last;
  wire [31:0]  sm_od;
  wire [2:0]   sm_ob;
  wire         h_done;
  wire [255:0] tau;

  serializer #(.MAX_BYTES(M_BYTES)) u_ser_m (
    .clk (clk), .rst_n (rst_n),
    .in_valid (in_valid_req || in_valid_rsp), .in_ready (/* idle: one m per ~s period */),
    .in_data (m_reg), .in_bytes (M_BYTES), .in_last (1'b1),
    .out_valid (sm_ov), .out_data (sm_od), .out_ready (sm_or),
    .out_bytes (sm_ob), .out_last (sm_last)
  );

  hmac_sha256 u_hmac (
    .clk (clk), .rst_n (rst_n), .key (key),
    .start (h_start), .hmac_en (1'b1),
    .in_valid (sm_ov), .in_ready (sm_or), .in_data (sm_od),
    .in_bytes (sm_ob), .in_last (sm_last), .done (h_done), .digest (tau)
  );

  // ---- frame -> output: stream [ reserved hdr (zeros) || m || tau ] out ----
  wire         sf_ov, sf_last;
  wire [31:0]  sf_od;
  wire [2:0]   sf_ob;

  serializer #(.MAX_BYTES(FRAME_BYTES)) u_ser_f (
    .clk (clk), .rst_n (rst_n),
    .in_valid (h_done), .in_ready (/* idle: one frame per ~cert period */),
    .in_data ({{(HDR_BYTES*8){1'b0}}, m_buf, tau}), .in_bytes (FRAME_BYTES), .in_last (1'b1),
    .out_valid (sf_ov), .out_data (sf_od), .out_ready (c_ready),
    .out_bytes (sf_ob), .out_last (sf_last)
  );

  assign c_valid = sf_ov;
  assign c_data  = sf_od;
  assign c_keep  = 4'b1111;                        // frame is word-aligned (116 = 29*4)
  assign c_last  = sf_last;
  assign c_user  = (sf_ov && cuser_pend) ? 16'(FRAME_BYTES) : 16'h0;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      m_buf <= '0; h_start <= 1'b0; cuser_pend <= 1'b0;
      bkt_start <= '0;
    end else begin
      h_start <= in_valid_req || in_valid_rsp;                         // launch the HMAC after the pulse
      if (in_valid_req || in_valid_rsp) begin
        m_buf <= m_reg;                // retain m for the frame
        bkt_start <= bkt_start + NUM_BUCKETS;
      end
      if (h_done)                cuser_pend <= 1'b1;
      else if (sf_ov && c_ready) cuser_pend <= 1'b0;
    end
  end

endmodule
