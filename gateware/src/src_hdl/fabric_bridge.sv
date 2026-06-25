// fabric_bridge — user-controlled fabric block on the Ethernet byte path,
// sitting between the two CoreTSE MAC client interfaces. (CoreTSE is itself
// soft IP synthesized into the fabric; what this module adds is RTL we can
// inspect or modify on the MAC-to-MAC frames — the insertion point for the
// interlock Core. Before it existed, the bytes traversed only encrypted
// CoreTSE logic and routing.)
//
// CoreTSE MAC client (packet-FIFO) interface — one bundle per direction,
// taken from the CoreTSE port list and the reference testbench (CoreTSE_tb.v):
//
//   M*DAT[31:0]       four packet bytes, little-endian (byte N in bits [7:0])
//   M*RDY             source has a valid word this cycle
//   M*ACPT            sink accepts the word this cycle
//   M*SOF             asserted on the first word of a frame
//   M*EOF             asserted on the last word of a frame
//   M*BYTEVALID[1:0]  count of *invalid* bytes in the current word (yes, the
//                     name lies — the eval RTL drives it as a data-not-valid
//                     count; see CoreTSE_tb.v `frfrm`). Always 0 except on a
//                     final word whose frame length isn't a multiple of 4:
//                     then 1/2/3 means 3/2/1 valid bytes in the low lanes.
//
// A word transfers on a rising clk edge where RDY & ACPT are both high.
//
// Port naming mirrors the CoreTSE pins so the SmartDesign wiring is 1:1:
// Directions below are from THIS module's point of view (the inverse of the
// CoreTSE pin direction on the same net): an MRX bundle is an input here
// because MRX* are CoreTSE outputs; an MTX bundle is an output here because
// MTX* are CoreTSE inputs.

module fabric_bridge (
  input  wire        clk,      // fabric clock (CORETSE M*CLK domain; both MACs share it)
  input  wire        rst_n,    // active-low synchronous reset
  // ---- Port 0 / CORETSE_0 ----
  // MAC RX from CORETSE_0
  input  wire        tse0_mrx_rdy,
  output wire        tse0_mrx_acpt,
  input  wire        tse0_mrx_sof,
  input  wire        tse0_mrx_eof,
  input  wire [31:0] tse0_mrx_dat,
  input  wire [1:0]  tse0_mrx_bytevalid,
  // MAC TX to CORETSE_0
  output wire        tse0_mtx_rdy,
  input  wire        tse0_mtx_acpt,
  output wire        tse0_mtx_sof,
  output wire        tse0_mtx_eof,
  output wire [31:0] tse0_mtx_dat,
  output wire [1:0]  tse0_mtx_bytevalid,
  // ---- Port 1 / CORETSE_1 ----
  // MAC RX from CORETSE_1
  input  wire        tse1_mrx_rdy,
  output wire        tse1_mrx_acpt,
  input  wire        tse1_mrx_sof,
  input  wire        tse1_mrx_eof,
  input  wire [31:0] tse1_mrx_dat,
  input  wire [1:0]  tse1_mrx_bytevalid,
  // MAC TX to CORETSE_1
  output wire        tse1_mtx_rdy,
  input  wire        tse1_mtx_acpt,
  output wire        tse1_mtx_sof,
  output wire        tse1_mtx_eof,
  output wire [31:0] tse1_mtx_dat,
  output wire [1:0]  tse1_mtx_bytevalid
);

  // Each direction is sanitized at the Ethernet layer:
  //
  //   MAC-RX ─► eth_deframe ─ AXI-Stream ─► eth_reframe ─► MAC-TX
  //
  // eth_deframe strips the header/PAD/FCS and forwards exactly LENGTH octets
  // of DATA; eth_reframe rebuilds the frame with forced addresses, regenerated
  // LENGTH/PAD, and a fresh FCS. canon_core sits between the two in
  // interlock_path — bypassed here for now, so reframe's byte length (tuser,
  // sampled on the first beat) comes from deframe's dbg_eth_len, the live view
  // of the held header register: complete before the first data beat and
  // stable until the next frame's header shifts through. deframe's 1-bit
  // tuser truncation flag has no consumer yet and is left open.
  //
  // Direction naming: requests flow port 0 -> port 1 (client on port 0,
  // server on port 1), responses flow port 1 -> port 0.

  // Forced station addresses: client side .01, server side .02.
  localparam logic [47:0] MAC_CLIENT = 48'h02_00_00_00_00_01;
  localparam logic [47:0] MAC_SERVER = 48'h02_00_00_00_00_02;

  // ====================================================================
  // Requests: CORETSE_0 MAC-RX -> deframe/reframe -> CORETSE_1 MAC-TX
  // ====================================================================
  wire        req_tvalid_i, req_tready_i, req_tlast_i;
  wire [31:0] req_tdata_i;
  wire [3:0]  req_tkeep_i;
  wire [15:0] req_len_i;

  eth_deframe deframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse0_mrx_rdy),
    .in_acpt       (tse0_mrx_acpt),
    .in_sof        (tse0_mrx_sof),
    .in_eof        (tse0_mrx_eof),
    .in_dat        (tse0_mrx_dat),
    .in_bytevalid  (tse0_mrx_bytevalid),
    .tvalid        (req_tvalid_i),
    .tready        (req_tready_i),
    .tdata         (req_tdata_i),
    .tkeep         (req_tkeep_i),
    .tlast         (req_tlast_i),
    .tuser         (),
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (req_len_i)
  );

  wire         req_tvalid_o, req_tready_o, req_tlast_o;
  wire [31:0]  req_tdata_o;
  wire [3:0]   req_tkeep_o;
  wire [15:0]  req_tuser_o;
  wire         req_ovr_valid;
  wire [255:0] req_ovr_digest;

  traffic_hash #(
    .HDR_BYTES (16)
  ) hash_req (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (req_tvalid_i),
    .tready_s (req_tready_i),
    .tdata_s  (req_tdata_i),
    .tkeep_s  (req_tkeep_i),
    .tlast_s  (req_tlast_i),
    .tuser_s  (req_len_i),
    .tvalid_m (req_tvalid_o),
    .tready_m (req_tready_o),
    .tdata_m  (req_tdata_o),
    .tkeep_m  (req_tkeep_o),
    .tlast_m  (req_tlast_o),
    .tuser_m  (req_tuser_o),
    .overall_valid (req_ovr_valid),
    .overall       (req_ovr_digest)
  );

  eth_reframe #(
    .FORCE_DST (MAC_SERVER),
    .FORCE_SRC (MAC_CLIENT)
  ) reframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (req_tvalid_o),
    .tready        (req_tready_o),
    .tdata         (req_tdata_o),
    .tkeep         (req_tkeep_o),
    .tlast         (req_tlast_o),
    .tuser         (req_tuser_o),
    .out_rdy       (tse1_mtx_rdy),
    .out_acpt      (tse1_mtx_acpt),
    .out_sof       (tse1_mtx_sof),
    .out_eof       (tse1_mtx_eof),
    .out_dat       (tse1_mtx_dat),
    .out_bytevalid (tse1_mtx_bytevalid)
  );

  // ====================================================================
  // Responses: CORETSE_1 MAC-RX -> deframe/reframe -> CORETSE_0 MAC-TX
  // ====================================================================
  wire        rsp_tvalid_i, rsp_tready_i, rsp_tlast_i;
  wire [31:0] rsp_tdata_i;
  wire [3:0]  rsp_tkeep_i;
  wire [15:0] rsp_len_i;

  eth_deframe deframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse1_mrx_rdy),
    .in_acpt       (tse1_mrx_acpt),
    .in_sof        (tse1_mrx_sof),
    .in_eof        (tse1_mrx_eof),
    .in_dat        (tse1_mrx_dat),
    .in_bytevalid  (tse1_mrx_bytevalid),
    .tvalid        (rsp_tvalid_i),
    .tready        (rsp_tready_i),
    .tdata         (rsp_tdata_i),
    .tkeep         (rsp_tkeep_i),
    .tlast         (rsp_tlast_i),
    .tuser         (),
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (rsp_len_i)
  );

  wire         rsp_tvalid_o, rsp_tready_o, rsp_tlast_o;
  wire [31:0]  rsp_tdata_o;
  wire [3:0]   rsp_tkeep_o;
  wire [15:0]  rsp_tuser_o;
  wire         rsp_ovr_valid;
  wire [255:0] rsp_ovr_digest;

  traffic_hash #(
    .HDR_BYTES (16)
  ) hash_rsp (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (rsp_tvalid_i),
    .tready_s (rsp_tready_i),
    .tdata_s  (rsp_tdata_i),
    .tkeep_s  (rsp_tkeep_i),
    .tlast_s  (rsp_tlast_i),
    .tuser_s  (rsp_len_i),
    .tvalid_m (rsp_tvalid_o),
    .tready_m (rsp_tready_o),
    .tdata_m  (rsp_tdata_o),
    .tkeep_m  (rsp_tkeep_o),
    .tlast_m  (rsp_tlast_o),
    .tuser_m  (rsp_tuser_o),
    .overall_valid (rsp_ovr_valid),
    .overall       (rsp_ovr_digest)
  );

  // request pipeline certificate -> cert_merge
  wire        reqc_tvalid, reqc_tready, reqc_tlast;
  wire [31:0] reqc_tdata;
  wire [3:0]  reqc_tkeep;
  wire [15:0] reqc_tuser;

  cert_build #() u_cert_req (
    .clk (clk), .rst_n (rst_n), .key (2),
    .in_valid_req (req_ovr_valid), .in_overall_req (req_ovr_digest), // pulse sink: cert period (~s) >> HMAC
    .in_valid_rsp (1'b0), .in_overall_rsp ('0),
    .in_nonce ('1),
    .c_valid (reqc_tvalid), .c_ready (reqc_tready), .c_data (reqc_tdata),
    .c_keep (reqc_tkeep), .c_last (reqc_tlast), .c_user (reqc_tuser)
  );

  // response pipeline certificate -> cert_merge
  wire        rspc_tvalid, rspc_tready, rspc_tlast;
  wire [31:0] rspc_tdata;
  wire [3:0]  rspc_tkeep;
  wire [15:0] rspc_tuser;

  cert_build #() u_cert_rsp (
    .clk (clk), .rst_n (rst_n), .key (2),
    .in_valid_req (1'b0), .in_overall_req ('0), // pulse sink: cert period (~s) >> HMAC
    .in_valid_rsp (rsp_ovr_valid), .in_overall_rsp (rsp_ovr_digest),
    .in_nonce ('1),
    .c_valid (rspc_tvalid), .c_ready (rspc_tready), .c_data (rspc_tdata),
    .c_keep (rspc_tkeep), .c_last (rspc_tlast), .c_user (rspc_tuser)
  );

  // cert_merge -> response reframe
  wire        mrg_tvalid, mrg_tready, mrg_tlast;
  wire [31:0] mrg_tdata;
  wire [3:0]  mrg_tkeep;
  wire [15:0] mrg_tuser;

  cert_merge u_cert_merge (
    .clk       (clk),
    .rst_n     (rst_n),
    .tvalid_s0 (rsp_tvalid_o),
    .tready_s0 (rsp_tready_o),
    .tdata_s0  (rsp_tdata_o),
    .tkeep_s0  (rsp_tkeep_o),
    .tlast_s0  (rsp_tlast_o),
    .tuser_s0  (rsp_tuser_o),
    .tvalid_s1 (rspc_tvalid),
    .tready_s1 (rspc_tready),
    .tdata_s1  (rspc_tdata),
    .tkeep_s1  (rspc_tkeep),
    .tlast_s1  (rspc_tlast),
    .tuser_s1  (rspc_tuser),
    .tvalid_s2 (reqc_tvalid),
    .tready_s2 (reqc_tready),
    .tdata_s2  (reqc_tdata),
    .tkeep_s2  (reqc_tkeep),
    .tlast_s2  (reqc_tlast),
    .tuser_s2  (reqc_tuser),
    .tvalid_m  (mrg_tvalid),
    .tready_m  (mrg_tready),
    .tdata_m   (mrg_tdata),
    .tkeep_m   (mrg_tkeep),
    .tlast_m   (mrg_tlast),
    .tuser_m   (mrg_tuser)
  );

  eth_reframe #(
    .FORCE_DST (MAC_CLIENT),
    .FORCE_SRC (MAC_SERVER)
  ) reframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (mrg_tvalid),
    .tready        (mrg_tready),
    .tdata         (mrg_tdata),
    .tkeep         (mrg_tkeep),
    .tlast         (mrg_tlast),
    .tuser         (mrg_tuser),
    .out_rdy       (tse0_mtx_rdy),
    .out_acpt      (tse0_mtx_acpt),
    .out_sof       (tse0_mtx_sof),
    .out_eof       (tse0_mtx_eof),
    .out_dat       (tse0_mtx_dat),
    .out_bytevalid (tse0_mtx_bytevalid)
  );

endmodule
