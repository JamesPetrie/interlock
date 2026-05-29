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

  // ====================================================================
  // 1 <- 0  : CORETSE_1 MAC-TX <- fcs_recalc <- CORETSE_0 MAC-RX
  // ====================================================================
  fcs_recalc fcs_0_to_1 (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse0_mrx_rdy),
    .in_acpt       (tse0_mrx_acpt),
    .in_sof        (tse0_mrx_sof),
    .in_eof        (tse0_mrx_eof),
    .in_dat        (tse0_mrx_dat),
    .in_bytevalid  (tse0_mrx_bytevalid),
    .out_rdy       (tse1_mtx_rdy),
    .out_acpt      (tse1_mtx_acpt),
    .out_sof       (tse1_mtx_sof),
    .out_eof       (tse1_mtx_eof),
    .out_dat       (tse1_mtx_dat),
    .out_bytevalid (tse1_mtx_bytevalid)
  );

  // ====================================================================
  // 0 <- 1  : CORETSE_0 MAC-TX <- fcs_recalc <- CORETSE_1 MAC-RX
  // ====================================================================
  fcs_recalc fcs_1_to_0 (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse1_mrx_rdy),
    .in_acpt       (tse1_mrx_acpt),
    .in_sof        (tse1_mrx_sof),
    .in_eof        (tse1_mrx_eof),
    .in_dat        (tse1_mrx_dat),
    .in_bytevalid  (tse1_mrx_bytevalid),
    .out_rdy       (tse0_mtx_rdy),
    .out_acpt      (tse0_mtx_acpt),
    .out_sof       (tse0_mtx_sof),
    .out_eof       (tse0_mtx_eof),
    .out_dat       (tse0_mtx_dat),
    .out_bytevalid (tse0_mtx_bytevalid)
  );

endmodule
