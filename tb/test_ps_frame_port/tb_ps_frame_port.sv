// Bench wrapper: ps_frame_port with the MRMAC pin bundles folded into plain 384-bit
// AXI-Streams (tuser = word-0 error bit), AXI-Lite exposed as-is.
module tb_ps_frame_port (
  input  wire         aclk,
  input  wire         aresetn,
  input  wire         mac_clk,
  input  wire         mac_rst_n,
  input  wire [31:0]  awaddr,
  input  wire         awvalid,
  output wire         awready,
  input  wire [31:0]  wdata,
  input  wire         wvalid,
  output wire         wready,
  output wire         bvalid,
  input  wire         bready,
  input  wire [31:0]  araddr,
  input  wire         arvalid,
  output wire         arready,
  output wire [31:0]  rdata,
  output wire         rvalid,
  input  wire         rready,
  input  wire         in_tvalid,
  input  wire [383:0] in_tdata,
  input  wire [47:0]  in_tkeep,
  input  wire         in_tlast,
  input  wire         in_tuser,
  output wire         out_tvalid,
  input  wire         out_tready,
  output wire [383:0] out_tdata,
  output wire [47:0]  out_tkeep,
  output wire         out_tlast,
  output wire         out_tuser
);
  function automatic logic [10:0] ku(input logic [7:0] k, input logic e); return {2'b00, e, k}; endfunction
  wire [65:0] in_ku = {ku(in_tkeep[47:40], 1'b0), ku(in_tkeep[39:32], 1'b0), ku(in_tkeep[31:24], 1'b0),
                       ku(in_tkeep[23:16], 1'b0), ku(in_tkeep[15:8], 1'b0), ku(in_tkeep[7:0], in_tuser)};
  wire [65:0] out_ku;
  wire [1:0]  unused_bresp, unused_rresp;
  wire        unused_in_tready;
  assign out_tkeep = {out_ku[62:55], out_ku[51:44], out_ku[40:33], out_ku[29:22], out_ku[18:11], out_ku[7:0]};
  assign out_tuser = out_ku[8];
  ps_frame_port #(.W(384)) dut (
    .aclk(aclk), .aresetn(aresetn),
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(4'hF), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(unused_bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rresp(unused_rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
    .mac_clk(mac_clk), .mac_rst_n(mac_rst_n),
    .in_tvalid(in_tvalid), .in_tlast(in_tlast), .in_tdata(in_tdata), .in_tkeep_user(in_ku), .in_tready(unused_in_tready),
    .out_tvalid(out_tvalid), .out_tready(out_tready), .out_tlast(out_tlast), .out_tdata(out_tdata), .out_tkeep_user(out_ku)
  );
endmodule
