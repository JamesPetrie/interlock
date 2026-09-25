// ilock_pl — programmable-logic top of the VPK180 interlock: two MAC ports
// (one MRMAC each, port 0 of the block) around the unchanged interlock core.
//
//   QSFP-DD "frontend" cage ─ MRMAC ─ p0 pins ─┐                 ┌─ p1 pins ─ MRMAC ─ QSFP-DD "compute" cage
//                                   mrmac_axis_adapt ─ mac_port_shim ─ core ─ mac_port_shim ─ mrmac_axis_adapt
//
// The core is either recomp_ilock_core (TOP_KIND 0, what main builds on the
// PolarFire), fabric_bridge (TOP_KIND 1, the production bidirectional top),
// or no core at all (TOP_KIND 2: pass-through, port 0 RX -> port 1 TX and
// port 1 RX -> port 0 TX, the bring-up step that proves the two MACs, the
// shims and the fibers before any interlock logic is involved). All expose
// the same CoreTSE-shaped port bundles.
//
// MAC-side ports carry the MRMAC's non-segmented pin bundle for one port:
// six 64-bit data words + six 11-bit tkeep_user words per direction cover
// every width up to the 384-bit 100GE interface; only MAC_AXIS_W/64 of them
// are used (see mrmac_axis_adapt for the layout). Intended to be dropped
// into the MRMAC example design in place of its packet generator/monitor, or
// added to a block design as a module reference and wired pin by pin.
//
// LEDs (VPK180 gpio_led_0..3): 0 heartbeat, 1 port-0 RX frames, 2 port-1 RX
// frames, 3 sticky "a frame was dropped by a shim" (overflow or MAC error).

module ilock_pl #(
  parameter int unsigned MAC_AXIS_W    = 384,      // MRMAC AXI-Stream width per port (64/128/256/384)
  parameter int unsigned P0_WORD_BASE  = 0,        // first MRMAC data word used by port 0
  parameter int unsigned P1_WORD_BASE  = 0,        // ... and port 1 (separate MRMAC instance)
  parameter int unsigned TOP_KIND      = 0,        // 0 recomp_ilock_core, 1 fabric_bridge, 2 pass-through (no core)
  parameter bit          RUNTIME_BYPASS = 1'b0,    // with a core: also build the pass-through switch, mode_core picks (core held in reset while bypassed)
  parameter int unsigned TIMER_END     = 7_999_999,// bucket period - 1 in core_clk cycles (80 MHz)
  parameter int unsigned BKTS_PER_CERT = 10
) (
  // ---- interlock core clock domain ----
  input  wire        core_clk,
  input  wire        core_rst_n,

  // ---- port 0 (frontend side) : MRMAC AXI clocks + pin bundle ----
  input  wire        p0_rx_axi_clk,
  input  wire        p0_rx_rst_n,
  input  wire        p0_tx_axi_clk,
  input  wire        p0_tx_rst_n,
  input  wire        p0_rx_axis_tvalid,
  input  wire        p0_rx_axis_tlast,
  input  wire [63:0] p0_rx_axis_tdata0, p0_rx_axis_tdata1, p0_rx_axis_tdata2,
                     p0_rx_axis_tdata3, p0_rx_axis_tdata4, p0_rx_axis_tdata5,
  input  wire [10:0] p0_rx_axis_tkeep_user0, p0_rx_axis_tkeep_user1, p0_rx_axis_tkeep_user2,
                     p0_rx_axis_tkeep_user3, p0_rx_axis_tkeep_user4, p0_rx_axis_tkeep_user5,
  output wire        p0_tx_axis_tvalid,
  input  wire        p0_tx_axis_tready,
  output wire        p0_tx_axis_tlast,
  output wire [63:0] p0_tx_axis_tdata0, p0_tx_axis_tdata1, p0_tx_axis_tdata2,
                     p0_tx_axis_tdata3, p0_tx_axis_tdata4, p0_tx_axis_tdata5,
  output wire [10:0] p0_tx_axis_tkeep_user0, p0_tx_axis_tkeep_user1, p0_tx_axis_tkeep_user2,
                     p0_tx_axis_tkeep_user3, p0_tx_axis_tkeep_user4, p0_tx_axis_tkeep_user5,

  // ---- port 1 (compute / enclosure side) ----
  input  wire        p1_rx_axi_clk,
  input  wire        p1_rx_rst_n,
  input  wire        p1_tx_axi_clk,
  input  wire        p1_tx_rst_n,
  input  wire        p1_rx_axis_tvalid,
  input  wire        p1_rx_axis_tlast,
  input  wire [63:0] p1_rx_axis_tdata0, p1_rx_axis_tdata1, p1_rx_axis_tdata2,
                     p1_rx_axis_tdata3, p1_rx_axis_tdata4, p1_rx_axis_tdata5,
  input  wire [10:0] p1_rx_axis_tkeep_user0, p1_rx_axis_tkeep_user1, p1_rx_axis_tkeep_user2,
                     p1_rx_axis_tkeep_user3, p1_rx_axis_tkeep_user4, p1_rx_axis_tkeep_user5,
  output wire        p1_tx_axis_tvalid,
  input  wire        p1_tx_axis_tready,
  output wire        p1_tx_axis_tlast,
  output wire [63:0] p1_tx_axis_tdata0, p1_tx_axis_tdata1, p1_tx_axis_tdata2,
                     p1_tx_axis_tdata3, p1_tx_axis_tdata4, p1_tx_axis_tdata5,
  output wire [10:0] p1_tx_axis_tkeep_user0, p1_tx_axis_tkeep_user1, p1_tx_axis_tkeep_user2,
                     p1_tx_axis_tkeep_user3, p1_tx_axis_tkeep_user4, p1_tx_axis_tkeep_user5,

  // ---- pass-through mode (TOP_KIND == 2 only; quasi-static, change only while idle) ----
  /* verilator lint_off UNUSEDSIGNAL */   // unused with a real core
  input  wire        pt_xover,           // 1 = crossover p0 RX -> p1 TX, p1 RX -> p0 TX; 0 = reflect each port
  input  wire        mode_core,          // RUNTIME_BYPASS only: 1 = the core owns the ports, 0 = pass-through
  /* verilator lint_on UNUSEDSIGNAL */
  // ---- status ----
  output wire [3:0]  led
);

  localparam int unsigned NWORDS = 6;

  // ====================================================================
  // Port 0
  // ====================================================================
  wire                    p0_rx_tvalid, p0_rx_tlast, p0_rx_tuser;
  wire [MAC_AXIS_W-1:0]   p0_rx_tdata;
  wire [MAC_AXIS_W/8-1:0] p0_rx_tkeep;
  wire                    p0_tx_tvalid, p0_tx_tready, p0_tx_tlast, p0_tx_tuser;
  wire [MAC_AXIS_W-1:0]   p0_tx_tdata;
  wire [MAC_AXIS_W/8-1:0] p0_tx_tkeep;

  mrmac_axis_adapt #(.W(MAC_AXIS_W), .NWORDS(NWORDS), .WORD_BASE(P0_WORD_BASE)) p0_adapt (
    .mrmac_rx_tvalid     (p0_rx_axis_tvalid),
    .mrmac_rx_tdata      ({p0_rx_axis_tdata5, p0_rx_axis_tdata4, p0_rx_axis_tdata3,
                           p0_rx_axis_tdata2, p0_rx_axis_tdata1, p0_rx_axis_tdata0}),
    .mrmac_rx_tkeep_user ({p0_rx_axis_tkeep_user5, p0_rx_axis_tkeep_user4, p0_rx_axis_tkeep_user3,
                           p0_rx_axis_tkeep_user2, p0_rx_axis_tkeep_user1, p0_rx_axis_tkeep_user0}),
    .mrmac_rx_tlast      (p0_rx_axis_tlast),
    .rx_tvalid (p0_rx_tvalid), .rx_tdata (p0_rx_tdata), .rx_tkeep (p0_rx_tkeep), .rx_tlast (p0_rx_tlast), .rx_tuser (p0_rx_tuser),
    .tx_tvalid (p0_tx_tvalid), .tx_tready (p0_tx_tready), .tx_tdata (p0_tx_tdata), .tx_tkeep (p0_tx_tkeep), .tx_tlast (p0_tx_tlast), .tx_tuser (p0_tx_tuser),
    .mrmac_tx_tvalid     (p0_tx_axis_tvalid),
    .mrmac_tx_tready     (p0_tx_axis_tready),
    .mrmac_tx_tdata      ({p0_tx_axis_tdata5, p0_tx_axis_tdata4, p0_tx_axis_tdata3,
                           p0_tx_axis_tdata2, p0_tx_axis_tdata1, p0_tx_axis_tdata0}),
    .mrmac_tx_tkeep_user ({p0_tx_axis_tkeep_user5, p0_tx_axis_tkeep_user4, p0_tx_axis_tkeep_user3,
                           p0_tx_axis_tkeep_user2, p0_tx_axis_tkeep_user1, p0_tx_axis_tkeep_user0}),
    .mrmac_tx_tlast      (p0_tx_axis_tlast)
  );

  wire        tse0_mrx_rdy, tse0_mrx_acpt, tse0_mrx_sof, tse0_mrx_eof;
  wire [31:0] tse0_mrx_dat;
  wire [1:0]  tse0_mrx_bytevalid;
  wire        tse0_mtx_rdy, tse0_mtx_acpt, tse0_mtx_sof, tse0_mtx_eof;
  wire [31:0] tse0_mtx_dat;
  wire [1:0]  tse0_mtx_bytevalid;
  wire        p0_drop_ovf, p0_drop_err;

  mac_port_shim #(.W(MAC_AXIS_W)) p0_shim (
    .mac_rx_clk (p0_rx_axi_clk), .mac_rx_rst_n (p0_rx_rst_n),
    .mac_tx_clk (p0_tx_axi_clk), .mac_tx_rst_n (p0_tx_rst_n),
    .core_clk (core_clk), .core_rst_n (core_rst_n),
    .mac_rx_tvalid (p0_rx_tvalid), .mac_rx_tdata (p0_rx_tdata), .mac_rx_tkeep (p0_rx_tkeep), .mac_rx_tlast (p0_rx_tlast), .mac_rx_tuser (p0_rx_tuser),
    .mac_tx_tvalid (p0_tx_tvalid), .mac_tx_tready (p0_tx_tready), .mac_tx_tdata (p0_tx_tdata), .mac_tx_tkeep (p0_tx_tkeep), .mac_tx_tlast (p0_tx_tlast), .mac_tx_tuser (p0_tx_tuser),
    .mrx_rdy (tse0_mrx_rdy), .mrx_acpt (tse0_mrx_acpt), .mrx_sof (tse0_mrx_sof), .mrx_eof (tse0_mrx_eof), .mrx_dat (tse0_mrx_dat), .mrx_bytevalid (tse0_mrx_bytevalid),
    .mtx_rdy (tse0_mtx_rdy), .mtx_acpt (tse0_mtx_acpt), .mtx_sof (tse0_mtx_sof), .mtx_eof (tse0_mtx_eof), .mtx_dat (tse0_mtx_dat), .mtx_bytevalid (tse0_mtx_bytevalid),
    .rx_drop_ovf (p0_drop_ovf), .rx_drop_err (p0_drop_err)
  );

  // ====================================================================
  // Port 1
  // ====================================================================
  wire                    p1_rx_tvalid, p1_rx_tlast, p1_rx_tuser;
  wire [MAC_AXIS_W-1:0]   p1_rx_tdata;
  wire [MAC_AXIS_W/8-1:0] p1_rx_tkeep;
  wire                    p1_tx_tvalid, p1_tx_tready, p1_tx_tlast, p1_tx_tuser;
  wire [MAC_AXIS_W-1:0]   p1_tx_tdata;
  wire [MAC_AXIS_W/8-1:0] p1_tx_tkeep;

  mrmac_axis_adapt #(.W(MAC_AXIS_W), .NWORDS(NWORDS), .WORD_BASE(P1_WORD_BASE)) p1_adapt (
    .mrmac_rx_tvalid     (p1_rx_axis_tvalid),
    .mrmac_rx_tdata      ({p1_rx_axis_tdata5, p1_rx_axis_tdata4, p1_rx_axis_tdata3,
                           p1_rx_axis_tdata2, p1_rx_axis_tdata1, p1_rx_axis_tdata0}),
    .mrmac_rx_tkeep_user ({p1_rx_axis_tkeep_user5, p1_rx_axis_tkeep_user4, p1_rx_axis_tkeep_user3,
                           p1_rx_axis_tkeep_user2, p1_rx_axis_tkeep_user1, p1_rx_axis_tkeep_user0}),
    .mrmac_rx_tlast      (p1_rx_axis_tlast),
    .rx_tvalid (p1_rx_tvalid), .rx_tdata (p1_rx_tdata), .rx_tkeep (p1_rx_tkeep), .rx_tlast (p1_rx_tlast), .rx_tuser (p1_rx_tuser),
    .tx_tvalid (p1_tx_tvalid), .tx_tready (p1_tx_tready), .tx_tdata (p1_tx_tdata), .tx_tkeep (p1_tx_tkeep), .tx_tlast (p1_tx_tlast), .tx_tuser (p1_tx_tuser),
    .mrmac_tx_tvalid     (p1_tx_axis_tvalid),
    .mrmac_tx_tready     (p1_tx_axis_tready),
    .mrmac_tx_tdata      ({p1_tx_axis_tdata5, p1_tx_axis_tdata4, p1_tx_axis_tdata3,
                           p1_tx_axis_tdata2, p1_tx_axis_tdata1, p1_tx_axis_tdata0}),
    .mrmac_tx_tkeep_user ({p1_tx_axis_tkeep_user5, p1_tx_axis_tkeep_user4, p1_tx_axis_tkeep_user3,
                           p1_tx_axis_tkeep_user2, p1_tx_axis_tkeep_user1, p1_tx_axis_tkeep_user0}),
    .mrmac_tx_tlast      (p1_tx_axis_tlast)
  );

  wire        tse1_mrx_rdy, tse1_mrx_acpt, tse1_mrx_sof, tse1_mrx_eof;
  wire [31:0] tse1_mrx_dat;
  wire [1:0]  tse1_mrx_bytevalid;
  wire        tse1_mtx_rdy, tse1_mtx_acpt, tse1_mtx_sof, tse1_mtx_eof;
  wire [31:0] tse1_mtx_dat;
  wire [1:0]  tse1_mtx_bytevalid;
  wire        p1_drop_ovf, p1_drop_err;

  mac_port_shim #(.W(MAC_AXIS_W)) p1_shim (
    .mac_rx_clk (p1_rx_axi_clk), .mac_rx_rst_n (p1_rx_rst_n),
    .mac_tx_clk (p1_tx_axi_clk), .mac_tx_rst_n (p1_tx_rst_n),
    .core_clk (core_clk), .core_rst_n (core_rst_n),
    .mac_rx_tvalid (p1_rx_tvalid), .mac_rx_tdata (p1_rx_tdata), .mac_rx_tkeep (p1_rx_tkeep), .mac_rx_tlast (p1_rx_tlast), .mac_rx_tuser (p1_rx_tuser),
    .mac_tx_tvalid (p1_tx_tvalid), .mac_tx_tready (p1_tx_tready), .mac_tx_tdata (p1_tx_tdata), .mac_tx_tkeep (p1_tx_tkeep), .mac_tx_tlast (p1_tx_tlast), .mac_tx_tuser (p1_tx_tuser),
    .mrx_rdy (tse1_mrx_rdy), .mrx_acpt (tse1_mrx_acpt), .mrx_sof (tse1_mrx_sof), .mrx_eof (tse1_mrx_eof), .mrx_dat (tse1_mrx_dat), .mrx_bytevalid (tse1_mrx_bytevalid),
    .mtx_rdy (tse1_mtx_rdy), .mtx_acpt (tse1_mtx_acpt), .mtx_sof (tse1_mtx_sof), .mtx_eof (tse1_mtx_eof), .mtx_dat (tse1_mtx_dat), .mtx_bytevalid (tse1_mtx_bytevalid),
    .rx_drop_ovf (p1_drop_ovf), .rx_drop_err (p1_drop_err)
  );

  // ====================================================================
  // Interlock core — unchanged RTL from the PolarFire build
  // ====================================================================
  // Which of the two owns the TSE bundles: the core (TOP_KIND 0/1) or the pass-through switch
  // (TOP_KIND 2, or RUNTIME_BYPASS with mode_core = 0). Core outputs c*_..., switch outputs pt*_...
  localparam bit HAS_CORE = (TOP_KIND != 2);
  localparam bit HAS_PT   = (TOP_KIND == 2) || RUNTIME_BYPASS;
  (* ASYNC_REG = "TRUE" *) logic [1:0] mode_sync;
  always_ff @(posedge core_clk or negedge core_rst_n) begin
    if (!core_rst_n) mode_sync <= 2'b00;
    else             mode_sync <= {mode_sync[0], mode_core};
  end
  wire sel_core       = HAS_CORE && (!RUNTIME_BYPASS || mode_sync[1]);
  wire core_rst_n_eff = core_rst_n && (!RUNTIME_BYPASS || mode_sync[1]);
  wire        c0_mrx_acpt, c0_mtx_rdy, c0_mtx_sof, c0_mtx_eof, c1_mrx_acpt, c1_mtx_rdy, c1_mtx_sof, c1_mtx_eof;
  wire [31:0] c0_mtx_dat, c1_mtx_dat;
  wire [1:0]  c0_mtx_bytevalid, c1_mtx_bytevalid;
  wire        pt0_mrx_acpt, pt0_mtx_rdy, pt0_mtx_sof, pt0_mtx_eof, pt1_mrx_acpt, pt1_mtx_rdy, pt1_mtx_sof, pt1_mtx_eof;
  wire [31:0] pt0_mtx_dat, pt1_mtx_dat;
  wire [1:0]  pt0_mtx_bytevalid, pt1_mtx_bytevalid;
  assign tse0_mrx_acpt      = sel_core ? c0_mrx_acpt      : pt0_mrx_acpt;
  assign tse0_mtx_rdy       = sel_core ? c0_mtx_rdy       : pt0_mtx_rdy;
  assign tse0_mtx_sof       = sel_core ? c0_mtx_sof       : pt0_mtx_sof;
  assign tse0_mtx_eof       = sel_core ? c0_mtx_eof       : pt0_mtx_eof;
  assign tse0_mtx_dat       = sel_core ? c0_mtx_dat       : pt0_mtx_dat;
  assign tse0_mtx_bytevalid = sel_core ? c0_mtx_bytevalid : pt0_mtx_bytevalid;
  assign tse1_mrx_acpt      = sel_core ? c1_mrx_acpt      : pt1_mrx_acpt;
  assign tse1_mtx_rdy       = sel_core ? c1_mtx_rdy       : pt1_mtx_rdy;
  assign tse1_mtx_sof       = sel_core ? c1_mtx_sof       : pt1_mtx_sof;
  assign tse1_mtx_eof       = sel_core ? c1_mtx_eof       : pt1_mtx_eof;
  assign tse1_mtx_dat       = sel_core ? c1_mtx_dat       : pt1_mtx_dat;
  assign tse1_mtx_bytevalid = sel_core ? c1_mtx_bytevalid : pt1_mtx_bytevalid;
  // the inactive consumer sees no traffic and no acceptance
  wire pt_on = !sel_core;
  generate
    if (TOP_KIND == 0) begin : g_recomp
      recomp_ilock_core #(.TIMER_END(TIMER_END), .BKTS_PER_CERT(BKTS_PER_CERT)) core_0 (
        .clk (core_clk), .rst_n (core_rst_n_eff),
        .tse0_mrx_rdy (tse0_mrx_rdy && sel_core), .tse0_mrx_acpt (c0_mrx_acpt), .tse0_mrx_sof (tse0_mrx_sof), .tse0_mrx_eof (tse0_mrx_eof), .tse0_mrx_dat (tse0_mrx_dat), .tse0_mrx_bytevalid (tse0_mrx_bytevalid),
        .tse0_mtx_rdy (c0_mtx_rdy), .tse0_mtx_acpt (tse0_mtx_acpt && sel_core), .tse0_mtx_sof (c0_mtx_sof), .tse0_mtx_eof (c0_mtx_eof), .tse0_mtx_dat (c0_mtx_dat), .tse0_mtx_bytevalid (c0_mtx_bytevalid),
        .tse1_mrx_rdy (tse1_mrx_rdy && sel_core), .tse1_mrx_acpt (c1_mrx_acpt), .tse1_mrx_sof (tse1_mrx_sof), .tse1_mrx_eof (tse1_mrx_eof), .tse1_mrx_dat (tse1_mrx_dat), .tse1_mrx_bytevalid (tse1_mrx_bytevalid),
        .tse1_mtx_rdy (c1_mtx_rdy), .tse1_mtx_acpt (tse1_mtx_acpt && sel_core), .tse1_mtx_sof (c1_mtx_sof), .tse1_mtx_eof (c1_mtx_eof), .tse1_mtx_dat (c1_mtx_dat), .tse1_mtx_bytevalid (c1_mtx_bytevalid)
      );
    end else if (TOP_KIND != 2) begin : g_prod
      fabric_bridge #(.TIMER_END(TIMER_END), .BKTS_PER_CERT(BKTS_PER_CERT)) core_0 (
        .clk (core_clk), .rst_n (core_rst_n_eff),
        .tse0_mrx_rdy (tse0_mrx_rdy && sel_core), .tse0_mrx_acpt (c0_mrx_acpt), .tse0_mrx_sof (tse0_mrx_sof), .tse0_mrx_eof (tse0_mrx_eof), .tse0_mrx_dat (tse0_mrx_dat), .tse0_mrx_bytevalid (tse0_mrx_bytevalid),
        .tse0_mtx_rdy (c0_mtx_rdy), .tse0_mtx_acpt (tse0_mtx_acpt && sel_core), .tse0_mtx_sof (c0_mtx_sof), .tse0_mtx_eof (c0_mtx_eof), .tse0_mtx_dat (c0_mtx_dat), .tse0_mtx_bytevalid (c0_mtx_bytevalid),
        .tse1_mrx_rdy (tse1_mrx_rdy && sel_core), .tse1_mrx_acpt (c1_mrx_acpt), .tse1_mrx_sof (tse1_mrx_sof), .tse1_mrx_eof (tse1_mrx_eof), .tse1_mrx_dat (tse1_mrx_dat), .tse1_mrx_bytevalid (tse1_mrx_bytevalid),
        .tse1_mtx_rdy (c1_mtx_rdy), .tse1_mtx_acpt (tse1_mtx_acpt && sel_core), .tse1_mtx_sof (c1_mtx_sof), .tse1_mtx_eof (c1_mtx_eof), .tse1_mtx_dat (c1_mtx_dat), .tse1_mtx_bytevalid (c1_mtx_bytevalid)
      );
    end else begin : g_nocore
      assign c0_mrx_acpt = 1'b0; assign c0_mtx_rdy = 1'b0; assign c0_mtx_sof = 1'b0; assign c0_mtx_eof = 1'b0; assign c0_mtx_dat = '0; assign c0_mtx_bytevalid = '0;
      assign c1_mrx_acpt = 1'b0; assign c1_mtx_rdy = 1'b0; assign c1_mtx_sof = 1'b0; assign c1_mtx_eof = 1'b0; assign c1_mtx_dat = '0; assign c1_mtx_bytevalid = '0;
    end
    if (HAS_PT) begin : g_passthru
      (* ASYNC_REG = "TRUE" *) logic [1:0] xo_sync;
      always_ff @(posedge core_clk or negedge core_rst_n) begin
        if (!core_rst_n) xo_sync <= 2'b11;
        else             xo_sync <= {xo_sync[0], pt_xover};
      end
      wire xo = xo_sync[1];
      // crossover (xo=1): p0 RX -> p1 TX and p1 RX -> p0 TX; reflect (xo=0): each port echoes its RX
      assign pt1_mtx_rdy       = pt_on && (xo ? tse0_mrx_rdy : tse1_mrx_rdy);
      assign pt1_mtx_sof       = xo ? tse0_mrx_sof       : tse1_mrx_sof;
      assign pt1_mtx_eof       = xo ? tse0_mrx_eof       : tse1_mrx_eof;
      assign pt1_mtx_dat       = xo ? tse0_mrx_dat       : tse1_mrx_dat;
      assign pt1_mtx_bytevalid = xo ? tse0_mrx_bytevalid : tse1_mrx_bytevalid;
      assign pt0_mtx_rdy       = pt_on && (xo ? tse1_mrx_rdy : tse0_mrx_rdy);
      assign pt0_mtx_sof       = xo ? tse1_mrx_sof       : tse0_mrx_sof;
      assign pt0_mtx_eof       = xo ? tse1_mrx_eof       : tse0_mrx_eof;
      assign pt0_mtx_dat       = xo ? tse1_mrx_dat       : tse0_mrx_dat;
      assign pt0_mtx_bytevalid = xo ? tse1_mrx_bytevalid : tse0_mrx_bytevalid;
      assign pt0_mrx_acpt      = pt_on && (xo ? tse1_mtx_acpt : tse0_mtx_acpt);
      assign pt1_mrx_acpt      = pt_on && (xo ? tse0_mtx_acpt : tse1_mtx_acpt);
    end else begin : g_nopt
      wire unused_pt = pt_on;
      assign pt0_mrx_acpt = 1'b0; assign pt0_mtx_rdy = 1'b0; assign pt0_mtx_sof = 1'b0; assign pt0_mtx_eof = 1'b0; assign pt0_mtx_dat = '0; assign pt0_mtx_bytevalid = '0;
      assign pt1_mrx_acpt = 1'b0; assign pt1_mtx_rdy = 1'b0; assign pt1_mtx_sof = 1'b0; assign pt1_mtx_eof = 1'b0; assign pt1_mtx_dat = '0; assign pt1_mtx_bytevalid = '0;
    end
  endgenerate

  // ====================================================================
  // LEDs
  // ====================================================================
  logic [25:0] hb;
  always_ff @(posedge core_clk or negedge core_rst_n) begin
    if (!core_rst_n) hb <= '0;
    else             hb <= hb + 1'b1;
  end
  assign led[0] = hb[25];

  pkt_counter p0_frames (.clk(core_clk), .rst_n(core_rst_n),
                         .frame_sof(tse0_mrx_sof && tse0_mrx_rdy && tse0_mrx_acpt), .led(led[1]));
  pkt_counter p1_frames (.clk(core_clk), .rst_n(core_rst_n),
                         .frame_sof(tse1_mrx_sof && tse1_mrx_rdy && tse1_mrx_acpt), .led(led[2]));

  // drop flags: sticky in each MAC RX domain, then a two-flop sync of the
  // (static once set) level into the core domain
  wire p0_drop_sticky, p1_drop_sticky;
  sticky_bit p0_drop (.clk(p0_rx_axi_clk), .rst_n(p0_rx_rst_n), .d(p0_drop_ovf | p0_drop_err), .q(p0_drop_sticky));
  sticky_bit p1_drop (.clk(p1_rx_axi_clk), .rst_n(p1_rx_rst_n), .d(p1_drop_ovf | p1_drop_err), .q(p1_drop_sticky));
  (* ASYNC_REG = "TRUE" *) logic [1:0] drop_sync;
  always_ff @(posedge core_clk or negedge core_rst_n) begin
    if (!core_rst_n) drop_sync <= '0;
    else             drop_sync <= {drop_sync[0], p0_drop_sticky | p1_drop_sticky};
  end
  assign led[3] = drop_sync[1];

endmodule
