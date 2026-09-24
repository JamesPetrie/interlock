// clocked wrapper so cocotb can drive the combinational tse2axis
module tb_tse2axis (
  input  wire        clk,
  input  wire        in_rdy,
  output wire        in_acpt,
  input  wire        in_sof,
  input  wire        in_eof,
  input  wire [31:0] in_dat,
  input  wire [1:0]  in_bytevalid,
  output wire        tvalid,
  input  wire        tready,
  output wire [31:0] tdata,
  output wire [3:0]  tkeep,
  output wire        tlast,
  output wire        tuser
);
  wire unused_clk = clk;
  tse2axis u (.*);
endmodule
