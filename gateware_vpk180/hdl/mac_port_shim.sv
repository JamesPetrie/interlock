// mac_port_shim — one interlock port: wide MAC AXI-Stream (MRMAC clock
// domain) <-> CoreTSE-shaped 32-bit word bundles (interlock core clock).
//
//   MAC RX ─► axis_pkt_fifo ─► CDC FIFO ─► axis_downsize ─► axis2tse ─► core
//   core   ─► tse2axis ─► axis_upsize ─► CDC FIFO (packet mode) ─► MAC TX
//
// Why each stage exists:
//   * axis_pkt_fifo — the MRMAC RX stream has no tready, and the core cannot
//     absorb line rate (32 bit @ core_clk vs 64..384 bit @ ~390 MHz); packets
//     that don't fit are dropped whole, and packets the MAC flags bad (tuser
//     at tlast) are dropped so the core only ever sees good complete frames,
//     as eth_deframe assumes of its MAC.
//   * CDC FIFO — MRMAC AXI clock <-> core clock. Production uses xpm_fifo_axis;
//     the benches compile sim_axis_cdc_fifo instead (`define SIM_NO_XPM).
//   * TX CDC FIFO in packet mode — the MRMAC TX side must not see tvalid drop
//     mid-packet, so a packet is released only once it is completely stored.
//   * axis_downsize / axis_upsize / axis2tse / tse2axis — width and bundle
//     conversion; the core RTL is untouched from the PolarFire build.
//
// Byte order is little-endian throughout: the first wire byte of a beat is
// bits [7:0], which is what both the MRMAC and CoreTSE bundles use.

module mac_port_shim #(
  parameter int unsigned W             = 64,    // MAC AXI-Stream width (64/128/256/384)
  parameter int unsigned RX_FIFO_DEPTH = 1024,  // beats; power of two; >= 2 max frames
  parameter int unsigned RX_CDC_DEPTH  = 512,   // beats; power of two
  parameter int unsigned TX_CDC_DEPTH  = 512    // beats; power of two; >= 1 max frame
) (
  // ---- clocks / resets ----
  input  wire            mac_rx_clk,
  input  wire            mac_rx_rst_n,
  input  wire            mac_tx_clk,
  input  wire            mac_tx_rst_n,
  input  wire            core_clk,
  input  wire            core_rst_n,

  // ---- MAC RX AXI-Stream (no tready: the MAC cannot be back-pressured) ----
  input  wire            mac_rx_tvalid,
  input  wire [W-1:0]    mac_rx_tdata,
  input  wire [W/8-1:0]  mac_rx_tkeep,
  input  wire            mac_rx_tlast,
  input  wire            mac_rx_tuser,     // error flag at tlast

  // ---- MAC TX AXI-Stream ----
  output wire            mac_tx_tvalid,
  input  wire            mac_tx_tready,
  output wire [W-1:0]    mac_tx_tdata,
  output wire [W/8-1:0]  mac_tx_tkeep,
  output wire            mac_tx_tlast,
  output wire            mac_tx_tuser,

  // ---- core side: MAC-RX-shaped bundle (to the core's tseN_mrx_*) ----
  output wire            mrx_rdy,
  input  wire            mrx_acpt,
  output wire            mrx_sof,
  output wire            mrx_eof,
  output wire [31:0]     mrx_dat,
  output wire [1:0]      mrx_bytevalid,

  // ---- core side: MAC-TX-shaped bundle (from the core's tseN_mtx_*) ----
  input  wire            mtx_rdy,
  output wire            mtx_acpt,
  input  wire            mtx_sof,
  input  wire            mtx_eof,
  input  wire [31:0]     mtx_dat,
  input  wire [1:0]      mtx_bytevalid,

  // ---- statistics (mac_rx_clk domain, one-cycle pulses) ----
  output wire            rx_drop_ovf,
  output wire            rx_drop_err
);

  // ====================================================================
  // RX: MAC -> core
  // ====================================================================
  wire            pf_tvalid, pf_tready, pf_tlast, pf_tuser;
  wire [W-1:0]    pf_tdata;
  wire [W/8-1:0]  pf_tkeep;

  axis_pkt_fifo #(.W(W), .DEPTH(RX_FIFO_DEPTH)) rx_pkt_fifo (
    .clk      (mac_rx_clk),
    .rst_n    (mac_rx_rst_n),
    .s_tvalid (mac_rx_tvalid),
    .s_tready (),                  // always 1
    .s_tdata  (mac_rx_tdata),
    .s_tkeep  (mac_rx_tkeep),
    .s_tlast  (mac_rx_tlast),
    .s_tuser  (mac_rx_tuser),
    .m_tvalid (pf_tvalid),
    .m_tready (pf_tready),
    .m_tdata  (pf_tdata),
    .m_tkeep  (pf_tkeep),
    .m_tlast  (pf_tlast),
    .m_tuser  (pf_tuser),
    .drop_ovf (rx_drop_ovf),
    .drop_err (rx_drop_err)
  );

  wire            rc_tvalid, rc_tready, rc_tlast, rc_tuser;
  wire [W-1:0]    rc_tdata;
  wire [W/8-1:0]  rc_tkeep;

`ifdef SIM_NO_XPM
  sim_axis_cdc_fifo #(.W(W), .DEPTH(RX_CDC_DEPTH), .PACKET_MODE(1'b0)) rx_cdc (
    .s_aclk(mac_rx_clk), .s_aresetn(mac_rx_rst_n),
    .s_tvalid(pf_tvalid), .s_tready(pf_tready), .s_tdata(pf_tdata), .s_tkeep(pf_tkeep), .s_tlast(pf_tlast), .s_tuser(pf_tuser),
    .m_aclk(core_clk), .m_aresetn(core_rst_n),
    .m_tvalid(rc_tvalid), .m_tready(rc_tready), .m_tdata(rc_tdata), .m_tkeep(rc_tkeep), .m_tlast(rc_tlast), .m_tuser(rc_tuser)
  );
`else
  xpm_fifo_axis #(
    .CLOCKING_MODE    ("independent_clock"),
    .FIFO_MEMORY_TYPE ("auto"),
    .PACKET_FIFO      ("false"),
    .FIFO_DEPTH       (RX_CDC_DEPTH),
    .TDATA_WIDTH      (W),
    .TUSER_WIDTH      (1),
    .RELATED_CLOCKS   (0),
    .CDC_SYNC_STAGES  (2),
    .USE_ADV_FEATURES ("1000")
  ) rx_cdc (
    .s_aclk(mac_rx_clk), .s_aresetn(mac_rx_rst_n), .m_aclk(core_clk),
    .s_axis_tvalid(pf_tvalid), .s_axis_tready(pf_tready), .s_axis_tdata(pf_tdata),
    .s_axis_tstrb('1), .s_axis_tkeep(pf_tkeep), .s_axis_tlast(pf_tlast),
    .s_axis_tid('0), .s_axis_tdest('0), .s_axis_tuser(pf_tuser),
    .m_axis_tvalid(rc_tvalid), .m_axis_tready(rc_tready), .m_axis_tdata(rc_tdata),
    .m_axis_tstrb(), .m_axis_tkeep(rc_tkeep), .m_axis_tlast(rc_tlast),
    .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(rc_tuser),
    .injectsbiterr_axis(1'b0), .injectdbiterr_axis(1'b0), .sbiterr_axis(), .dbiterr_axis(),
    .prog_full_axis(), .wr_data_count_axis(), .almost_full_axis(),
    .prog_empty_axis(), .rd_data_count_axis(), .almost_empty_axis()
  );
`endif

  wire        rd_tvalid, rd_tready, rd_tlast, rd_tuser;
  wire [31:0] rd_tdata;
  wire [3:0]  rd_tkeep;

  axis_downsize #(.W(W)) rx_down (
    .clk(core_clk), .rst_n(core_rst_n),
    .s_tvalid(rc_tvalid), .s_tready(rc_tready), .s_tdata(rc_tdata), .s_tkeep(rc_tkeep), .s_tlast(rc_tlast), .s_tuser(rc_tuser),
    .m_tvalid(rd_tvalid), .m_tready(rd_tready), .m_tdata(rd_tdata), .m_tkeep(rd_tkeep), .m_tlast(rd_tlast), .m_tuser(rd_tuser)
  );

  wire unused_rd_tuser = rd_tuser;   // errored frames never get this far

  axis2tse rx_tse (
    .clk(core_clk), .rst_n(core_rst_n),
    .tvalid(rd_tvalid), .tready(rd_tready), .tdata(rd_tdata), .tkeep(rd_tkeep), .tlast(rd_tlast),
    .out_rdy(mrx_rdy), .out_acpt(mrx_acpt), .out_sof(mrx_sof), .out_eof(mrx_eof), .out_dat(mrx_dat), .out_bytevalid(mrx_bytevalid)
  );

  // ====================================================================
  // TX: core -> MAC
  // ====================================================================
  wire        tn_tvalid, tn_tready, tn_tlast, tn_tuser;
  wire [31:0] tn_tdata;
  wire [3:0]  tn_tkeep;

  tse2axis tx_axis (
    .in_rdy(mtx_rdy), .in_acpt(mtx_acpt), .in_sof(mtx_sof), .in_eof(mtx_eof), .in_dat(mtx_dat), .in_bytevalid(mtx_bytevalid),
    .tvalid(tn_tvalid), .tready(tn_tready), .tdata(tn_tdata), .tkeep(tn_tkeep), .tlast(tn_tlast), .tuser(tn_tuser)
  );

  wire            tu_tvalid, tu_tready, tu_tlast, tu_tuser;
  wire [W-1:0]    tu_tdata;
  wire [W/8-1:0]  tu_tkeep;

  axis_upsize #(.W(W)) tx_up (
    .clk(core_clk), .rst_n(core_rst_n),
    .s_tvalid(tn_tvalid), .s_tready(tn_tready), .s_tdata(tn_tdata), .s_tkeep(tn_tkeep), .s_tlast(tn_tlast), .s_tuser(tn_tuser),
    .m_tvalid(tu_tvalid), .m_tready(tu_tready), .m_tdata(tu_tdata), .m_tkeep(tu_tkeep), .m_tlast(tu_tlast), .m_tuser(tu_tuser)
  );

`ifdef SIM_NO_XPM
  sim_axis_cdc_fifo #(.W(W), .DEPTH(TX_CDC_DEPTH), .PACKET_MODE(1'b1)) tx_cdc (
    .s_aclk(core_clk), .s_aresetn(core_rst_n),
    .s_tvalid(tu_tvalid), .s_tready(tu_tready), .s_tdata(tu_tdata), .s_tkeep(tu_tkeep), .s_tlast(tu_tlast), .s_tuser(tu_tuser),
    .m_aclk(mac_tx_clk), .m_aresetn(mac_tx_rst_n),
    .m_tvalid(mac_tx_tvalid), .m_tready(mac_tx_tready), .m_tdata(mac_tx_tdata), .m_tkeep(mac_tx_tkeep), .m_tlast(mac_tx_tlast), .m_tuser(mac_tx_tuser)
  );
`else
  xpm_fifo_axis #(
    .CLOCKING_MODE    ("independent_clock"),
    .FIFO_MEMORY_TYPE ("auto"),
    .PACKET_FIFO      ("true"),
    .FIFO_DEPTH       (TX_CDC_DEPTH),
    .TDATA_WIDTH      (W),
    .TUSER_WIDTH      (1),
    .RELATED_CLOCKS   (0),
    .CDC_SYNC_STAGES  (2),
    .USE_ADV_FEATURES ("1000")
  ) tx_cdc (
    .s_aclk(core_clk), .s_aresetn(core_rst_n), .m_aclk(mac_tx_clk),
    .s_axis_tvalid(tu_tvalid), .s_axis_tready(tu_tready), .s_axis_tdata(tu_tdata),
    .s_axis_tstrb('1), .s_axis_tkeep(tu_tkeep), .s_axis_tlast(tu_tlast),
    .s_axis_tid('0), .s_axis_tdest('0), .s_axis_tuser(tu_tuser),
    .m_axis_tvalid(mac_tx_tvalid), .m_axis_tready(mac_tx_tready), .m_axis_tdata(mac_tx_tdata),
    .m_axis_tstrb(), .m_axis_tkeep(mac_tx_tkeep), .m_axis_tlast(mac_tx_tlast),
    .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(mac_tx_tuser),
    .injectsbiterr_axis(1'b0), .injectdbiterr_axis(1'b0), .sbiterr_axis(), .dbiterr_axis(),
    .prog_full_axis(), .wr_data_count_axis(), .almost_full_axis(),
    .prog_empty_axis(), .rd_data_count_axis(), .almost_empty_axis()
  );
`endif

endmodule
