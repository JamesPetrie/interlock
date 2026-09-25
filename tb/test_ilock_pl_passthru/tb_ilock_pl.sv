// Bench wrapper: ilock_pl with the MRMAC pin bundles folded into one plain
// AXI-Stream per direction and port (W = 384: six 64-bit words), so the
// cocotb helpers can drive/collect them. tuser = error bit of word 0.
module tb_ilock_pl #(
  parameter int unsigned TOP_KIND = 2,
  parameter bit          RUNTIME_BYPASS = 1'b0
) (
  input  wire         core_clk,
  input  wire         core_rst_n,
  input  wire         mac_clk,
  input  wire         mac_rst_n,
  // port 0 RX in / TX out
  input  wire         p0_rx_tvalid,
  input  wire [383:0] p0_rx_tdata,
  input  wire [47:0]  p0_rx_tkeep,
  input  wire         p0_rx_tlast,
  input  wire         p0_rx_tuser,
  output wire         p0_tx_tvalid,
  input  wire         p0_tx_tready,
  output wire [383:0] p0_tx_tdata,
  output wire [47:0]  p0_tx_tkeep,
  output wire         p0_tx_tlast,
  output wire         p0_tx_tuser,
  // port 1
  input  wire         p1_rx_tvalid,
  input  wire [383:0] p1_rx_tdata,
  input  wire [47:0]  p1_rx_tkeep,
  input  wire         p1_rx_tlast,
  input  wire         p1_rx_tuser,
  output wire         p1_tx_tvalid,
  input  wire         p1_tx_tready,
  output wire [383:0] p1_tx_tdata,
  output wire [47:0]  p1_tx_tkeep,
  output wire         p1_tx_tlast,
  output wire         p1_tx_tuser,
  input  wire         pt_xover,
  input  wire         mode_core,
  output wire [3:0]   led
);
  // MRMAC tkeep_user word: [7:0] keep, [8] err, [10:9] 0
  function automatic logic [10:0] ku(input logic [7:0] k, input logic e); return {2'b00, e, k}; endfunction
  wire [10:0] p0_tx_ku0, p0_tx_ku1, p0_tx_ku2, p0_tx_ku3, p0_tx_ku4, p0_tx_ku5;
  wire [10:0] p1_tx_ku0, p1_tx_ku1, p1_tx_ku2, p1_tx_ku3, p1_tx_ku4, p1_tx_ku5;
  assign p0_tx_tkeep = {p0_tx_ku5[7:0], p0_tx_ku4[7:0], p0_tx_ku3[7:0], p0_tx_ku2[7:0], p0_tx_ku1[7:0], p0_tx_ku0[7:0]};
  assign p1_tx_tkeep = {p1_tx_ku5[7:0], p1_tx_ku4[7:0], p1_tx_ku3[7:0], p1_tx_ku2[7:0], p1_tx_ku1[7:0], p1_tx_ku0[7:0]};
  assign p0_tx_tuser = p0_tx_ku0[8];
  assign p1_tx_tuser = p1_tx_ku0[8];

  ilock_pl #(.MAC_AXIS_W(384), .TOP_KIND(TOP_KIND), .RUNTIME_BYPASS(RUNTIME_BYPASS)) dut (
    .core_clk(core_clk), .core_rst_n(core_rst_n), .pt_xover(pt_xover), .mode_core(mode_core),
    .p0_rx_axi_clk(mac_clk), .p0_rx_rst_n(mac_rst_n), .p0_tx_axi_clk(mac_clk), .p0_tx_rst_n(mac_rst_n),
    .p0_rx_axis_tvalid(p0_rx_tvalid), .p0_rx_axis_tlast(p0_rx_tlast),
    .p0_rx_axis_tdata0(p0_rx_tdata[63:0]),    .p0_rx_axis_tdata1(p0_rx_tdata[127:64]),  .p0_rx_axis_tdata2(p0_rx_tdata[191:128]),
    .p0_rx_axis_tdata3(p0_rx_tdata[255:192]), .p0_rx_axis_tdata4(p0_rx_tdata[319:256]), .p0_rx_axis_tdata5(p0_rx_tdata[383:320]),
    .p0_rx_axis_tkeep_user0(ku(p0_rx_tkeep[7:0], p0_rx_tuser)), .p0_rx_axis_tkeep_user1(ku(p0_rx_tkeep[15:8], 1'b0)),
    .p0_rx_axis_tkeep_user2(ku(p0_rx_tkeep[23:16], 1'b0)),      .p0_rx_axis_tkeep_user3(ku(p0_rx_tkeep[31:24], 1'b0)),
    .p0_rx_axis_tkeep_user4(ku(p0_rx_tkeep[39:32], 1'b0)),      .p0_rx_axis_tkeep_user5(ku(p0_rx_tkeep[47:40], 1'b0)),
    .p0_tx_axis_tvalid(p0_tx_tvalid), .p0_tx_axis_tready(p0_tx_tready), .p0_tx_axis_tlast(p0_tx_tlast),
    .p0_tx_axis_tdata0(p0_tx_tdata[63:0]),    .p0_tx_axis_tdata1(p0_tx_tdata[127:64]),  .p0_tx_axis_tdata2(p0_tx_tdata[191:128]),
    .p0_tx_axis_tdata3(p0_tx_tdata[255:192]), .p0_tx_axis_tdata4(p0_tx_tdata[319:256]), .p0_tx_axis_tdata5(p0_tx_tdata[383:320]),
    .p0_tx_axis_tkeep_user0(p0_tx_ku0), .p0_tx_axis_tkeep_user1(p0_tx_ku1), .p0_tx_axis_tkeep_user2(p0_tx_ku2),
    .p0_tx_axis_tkeep_user3(p0_tx_ku3), .p0_tx_axis_tkeep_user4(p0_tx_ku4), .p0_tx_axis_tkeep_user5(p0_tx_ku5),
    .p1_rx_axi_clk(mac_clk), .p1_rx_rst_n(mac_rst_n), .p1_tx_axi_clk(mac_clk), .p1_tx_rst_n(mac_rst_n),
    .p1_rx_axis_tvalid(p1_rx_tvalid), .p1_rx_axis_tlast(p1_rx_tlast),
    .p1_rx_axis_tdata0(p1_rx_tdata[63:0]),    .p1_rx_axis_tdata1(p1_rx_tdata[127:64]),  .p1_rx_axis_tdata2(p1_rx_tdata[191:128]),
    .p1_rx_axis_tdata3(p1_rx_tdata[255:192]), .p1_rx_axis_tdata4(p1_rx_tdata[319:256]), .p1_rx_axis_tdata5(p1_rx_tdata[383:320]),
    .p1_rx_axis_tkeep_user0(ku(p1_rx_tkeep[7:0], p1_rx_tuser)), .p1_rx_axis_tkeep_user1(ku(p1_rx_tkeep[15:8], 1'b0)),
    .p1_rx_axis_tkeep_user2(ku(p1_rx_tkeep[23:16], 1'b0)),      .p1_rx_axis_tkeep_user3(ku(p1_rx_tkeep[31:24], 1'b0)),
    .p1_rx_axis_tkeep_user4(ku(p1_rx_tkeep[39:32], 1'b0)),      .p1_rx_axis_tkeep_user5(ku(p1_rx_tkeep[47:40], 1'b0)),
    .p1_tx_axis_tvalid(p1_tx_tvalid), .p1_tx_axis_tready(p1_tx_tready), .p1_tx_axis_tlast(p1_tx_tlast),
    .p1_tx_axis_tdata0(p1_tx_tdata[63:0]),    .p1_tx_axis_tdata1(p1_tx_tdata[127:64]),  .p1_tx_axis_tdata2(p1_tx_tdata[191:128]),
    .p1_tx_axis_tdata3(p1_tx_tdata[255:192]), .p1_tx_axis_tdata4(p1_tx_tdata[319:256]), .p1_tx_axis_tdata5(p1_tx_tdata[383:320]),
    .p1_tx_axis_tkeep_user0(p1_tx_ku0), .p1_tx_axis_tkeep_user1(p1_tx_ku1), .p1_tx_axis_tkeep_user2(p1_tx_ku2),
    .p1_tx_axis_tkeep_user3(p1_tx_ku3), .p1_tx_axis_tkeep_user4(p1_tx_ku4), .p1_tx_axis_tkeep_user5(p1_tx_ku5),
    .led(led)
  );
endmodule
