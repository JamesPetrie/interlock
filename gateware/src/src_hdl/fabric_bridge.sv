// fabric_bridge — BISECTION BUILD: combinational cross-wire passthrough.
// Same port list/routing as the sanitizing bridge, but internally just wires
// port0.MRX <-> port1.MTX (and the reverse) with NO deframe/reframe/CRC and NO
// package deps. Tests whether routing MAC-client through an HDL+ SV module
// instance works on silicon (vs the direct sd_connect cross-wire that forwards).
`default_nettype none
module fabric_bridge (
  input  wire        clk,
  input  wire        rst_n,
  // Port 0 / CORETSE_0
  input  wire        tse0_mrx_rdy,
  output wire        tse0_mrx_acpt,
  input  wire        tse0_mrx_sof,
  input  wire        tse0_mrx_eof,
  input  wire [31:0] tse0_mrx_dat,
  input  wire [1:0]  tse0_mrx_bytevalid,
  output wire        tse0_mtx_rdy,
  input  wire        tse0_mtx_acpt,
  output wire        tse0_mtx_sof,
  output wire        tse0_mtx_eof,
  output wire [31:0] tse0_mtx_dat,
  output wire [1:0]  tse0_mtx_bytevalid,
  // Port 1 / CORETSE_1
  input  wire        tse1_mrx_rdy,
  output wire        tse1_mrx_acpt,
  input  wire        tse1_mrx_sof,
  input  wire        tse1_mrx_eof,
  input  wire [31:0] tse1_mrx_dat,
  input  wire [1:0]  tse1_mrx_bytevalid,
  output wire        tse1_mtx_rdy,
  input  wire        tse1_mtx_acpt,
  output wire        tse1_mtx_sof,
  output wire        tse1_mtx_eof,
  output wire [31:0] tse1_mtx_dat,
  output wire [1:0]  tse1_mtx_bytevalid
);
  // Request: CORETSE_0 RX -> CORETSE_1 TX (transparent)
  assign tse1_mtx_rdy       = tse0_mrx_rdy;
  assign tse1_mtx_sof       = tse0_mrx_sof;
  assign tse1_mtx_eof       = tse0_mrx_eof;
  assign tse1_mtx_dat       = tse0_mrx_dat;
  assign tse1_mtx_bytevalid = tse0_mrx_bytevalid;
  assign tse0_mrx_acpt      = tse1_mtx_acpt;
  // Response: CORETSE_1 RX -> CORETSE_0 TX (transparent)
  assign tse0_mtx_rdy       = tse1_mrx_rdy;
  assign tse0_mtx_sof       = tse1_mrx_sof;
  assign tse0_mtx_eof       = tse1_mrx_eof;
  assign tse0_mtx_dat       = tse1_mrx_dat;
  assign tse0_mtx_bytevalid = tse1_mrx_bytevalid;
  assign tse1_mrx_acpt      = tse0_mtx_acpt;
endmodule
`default_nettype wire
