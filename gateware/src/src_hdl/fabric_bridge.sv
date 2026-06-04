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
  wire        req_tvalid, req_tready, req_tlast;
  wire [31:0] req_tdata;
  wire [3:0]  req_tkeep;
  wire [15:0] req_len;

  eth_deframe deframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse0_mrx_rdy),
    .in_acpt       (tse0_mrx_acpt),
    .in_sof        (tse0_mrx_sof),
    .in_eof        (tse0_mrx_eof),
    .in_dat        (tse0_mrx_dat),
    .in_bytevalid  (tse0_mrx_bytevalid),
    .tvalid        (req_tvalid),
    .tready        (req_tready),
    .tdata         (req_tdata),
    .tkeep         (req_tkeep),
    .tlast         (req_tlast),
    .tuser         (),
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (req_len)
  );

  eth_reframe #(
    .FORCE_DST (MAC_SERVER),
    .FORCE_SRC (MAC_CLIENT)
  ) reframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (req_tvalid),
    .tready        (req_tready),
    .tdata         (req_tdata),
    .tkeep         (req_tkeep),
    .tlast         (req_tlast),
    .tuser         (req_len),
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
  wire        rsp_tvalid, rsp_tready, rsp_tlast;
  wire [31:0] rsp_tdata;
  wire [3:0]  rsp_tkeep;
  wire [15:0] rsp_len;

  eth_deframe deframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse1_mrx_rdy),
    .in_acpt       (tse1_mrx_acpt),
    .in_sof        (tse1_mrx_sof),
    .in_eof        (tse1_mrx_eof),
    .in_dat        (tse1_mrx_dat),
    .in_bytevalid  (tse1_mrx_bytevalid),
    .tvalid        (rsp_tvalid),
    .tready        (rsp_tready),
    .tdata         (rsp_tdata),
    .tkeep         (rsp_tkeep),
    .tlast         (rsp_tlast),
    .tuser         (),
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (rsp_len)
  );

  eth_reframe #(
    .FORCE_DST (MAC_CLIENT),
    .FORCE_SRC (MAC_SERVER)
  ) reframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (rsp_tvalid),
    .tready        (rsp_tready),
    .tdata         (rsp_tdata),
    .tkeep         (rsp_tkeep),
    .tlast         (rsp_tlast),
    .tuser         (rsp_len),
    .out_rdy       (tse0_mtx_rdy),
    .out_acpt      (tse0_mtx_acpt),
    .out_sof       (tse0_mtx_sof),
    .out_eof       (tse0_mtx_eof),
    .out_dat       (tse0_mtx_dat),
    .out_bytevalid (tse0_mtx_bytevalid)
  );

endmodule
