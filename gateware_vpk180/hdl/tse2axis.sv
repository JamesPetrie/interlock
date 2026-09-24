// tse2axis — CoreTSE MAC-TX-shaped word stream in, 32-bit AXI-Stream out.
//
// Straight mapping: RDY/ACPT is VALID/READY, EOF is tlast, and BYTEVALID
// (count of INVALID lanes on the last word) becomes a contiguous tkeep. SOF
// has no AXI-Stream equivalent and is dropped. Combinational; no state.

module tse2axis (
  // MAC-TX-shaped slave (source drives data, we drive accept)
  input  wire        in_rdy,
  output wire        in_acpt,
  input  wire        in_sof,
  input  wire        in_eof,
  input  wire [31:0] in_dat,
  input  wire [1:0]  in_bytevalid,

  // AXI-Stream master
  output wire        tvalid,
  input  wire        tready,
  output wire [31:0] tdata,
  output wire [3:0]  tkeep,
  output wire        tlast,
  output wire        tuser
);

  wire unused_sof = in_sof;

  assign tvalid  = in_rdy;
  assign in_acpt = tready;
  assign tdata   = in_dat;
  assign tkeep   = in_eof ? (4'b1111 >> in_bytevalid) : 4'b1111;
  assign tlast   = in_eof;
  assign tuser   = 1'b0;

endmodule
