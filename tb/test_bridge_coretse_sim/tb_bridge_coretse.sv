// tb_bridge_coretse — REAL-CoreTSE hardware-faithful sim of the fabric_bridge.
//
// The iverilog BFM sim (tb_bridge_hw) idealizes the MAC-client handshake and
// FORWARDS, yet hardware forwards nothing. This TB removes the idealization by
// putting two REAL (encrypted) CoreTSE MACs in the loop (ModelSim decrypts).
//
//   inject frame -> A.MTX -> A serial loopback -> A.MRX (real MAC output)
//                -> fabric_bridge (deframe->reframe) -> B.MTX (real MAC input)
//                -> B serial loopback -> B.MRX -> monitor/check
//
// Run with ModelSim (vsim) -- requires the Microsemi/Mentor decryption keys.

`timescale 1ns/100ps
module tb_bridge_coretse;

  `include "../../../an4623/mpf_an4623_v2022p3_df/TCL_Scripts/Libero_Project/component/work/CORETSE_0/CORETSE_0_0/coreparameters.v"

  // ---------------- clocks ----------------
  // Client side (MTX/MRX) and the bridge share one fabric clock, as on HW
  // (top.tcl line 320: all M*CLK + fabric_bridge:clk on OUT0_FABCLK_0).
  reg fabclk=0, txc=0, rxc=0, tbitx=0, tbirx=0, pclk=0;
  always #4 fabclk <= ~fabclk;   // 125 MHz client/bridge clock
  always #4 txc    <= ~txc;
  always #4 rxc    <= ~rxc;
  always #4 tbitx  <= ~tbitx;
  always #4 tbirx  <= ~tbirx;
  always #5 pclk   <= ~pclk;     // 100 MHz APB

  reg rst = 1'b1;
  wire presetn = ~rst;

  // ---------------- per-DUT nets ----------------
  // A: MTX driven by injector, MRX -> bridge.  B: MTX <- bridge, MRX -> monitor.
  // MAC-TX client (A side, we drive)
  wire a_mtxacpt; reg a_mtxrdy, a_mtxsof, a_mtxeof; reg [31:0] a_mtxdat; reg [1:0] a_mtxbv;
  // MAC-RX client (A side -> bridge)
  wire a_mrxrdy, a_mrxsof, a_mrxeof; wire [31:0] a_mrxdat; wire [1:0] a_mrxbv; wire a_mrxacpt;
  // B MAC-TX client (<- bridge)
  wire b_mtxacpt; wire b_mtxrdy, b_mtxsof, b_mtxeof; wire [31:0] b_mtxdat; wire [1:0] b_mtxbv;
  // B MAC-RX client (-> monitor)
  wire b_mrxrdy, b_mrxsof, b_mrxeof; wire [31:0] b_mrxdat; wire [1:0] b_mrxbv; reg b_mrxacpt;

  // TBI near-end loopback per instance (tcg -> rcg)
  wire [9:0] a_tcg, b_tcg;
  wire [9:0] a_anx, b_anx;   // ANX_STATE; bit 8 = link up

  // APB per DUT
  reg [31:0] a_paddr, a_pwdata, b_paddr, b_pwdata;
  reg a_psel,a_penable,a_pwrite, b_psel,b_penable,b_pwrite;
  wire [31:0] a_prdata, b_prdata; wire a_pready,b_pready;
  // MDIO per DUT (loop MDO->MDI so the internal SGMII slave is reachable)
  wire a_mdc,a_mdo,a_mdoen,b_mdc,b_mdo,b_mdoen;

  // ---------------- DUT A ----------------
  CORETSE_0_CORETSE_0_0_CORETSE #(.FAMILY(FAMILY),.GMII_TBI(GMII_TBI),.PACKET_SIZE(PACKET_SIZE),
      .SAL(SAL),.WoL(WoL),.STATS(STATS),.MDIO_PHYID(MDIO_PHYID),.SLIP_ENABLE(SLIP_ENABLE)) A (
    .MTXCLK(fabclk),.MTXRDY(a_mtxrdy),.MTXACPT(a_mtxacpt),.MTXSOF(a_mtxsof),.MTXEOF(a_mtxeof),
    .MTXDAT(a_mtxdat),.MTXBYTEVALID(a_mtxbv),.MTXCFRM(1'b0),.MTXHWM(),
    .MRXCLK(fabclk),.MRXRDY(a_mrxrdy),.MRXACPT(a_mrxacpt),.MRXSOF(a_mrxsof),.MRXEOF(a_mrxeof),
    .MRXDAT(a_mrxdat),.MRXBYTEVALID(a_mrxbv),
    .GTXCLK(),.TXCLK(txc),.RXCLK(rxc),.TXEN(),.TXD(),.TXER(),.RXDV(),.RXD(),.RXER(),.CRS(),.COL(),
    .TBI_TX_CLK(tbitx),.TBI_RX_CLK(tbirx),.TCG(a_tcg),.RCG(a_tcg),
    .TBI_TX_VALID(),.TBI_RX_VALID(),.TBI_RX_READY(1'b1),.SIGNAL_DETECT(1'b1),.RX_SLIP(),.SYNC(),
    .ANX_STATE(a_anx),.RCG_ERROR(),
    .MDC(a_mdc),.MDI(1'b1),.MDO(a_mdo),.MDOEN(a_mdoen),
    .PCLK(pclk),.PRESETN(presetn),.PADDR(a_paddr),.PSEL(a_psel),.PENABLE(a_penable),.PWRITE(a_pwrite),
    .PWDATA(a_pwdata),.PRDATA(a_prdata),.PSLVERR(),.PREADY(a_pready),
    .TSM_INTR(),.TSM_CONTROL(),.STBP(1'b0));

  // ---------------- DUT B ----------------
  CORETSE_0_CORETSE_0_0_CORETSE #(.FAMILY(FAMILY),.GMII_TBI(GMII_TBI),.PACKET_SIZE(PACKET_SIZE),
      .SAL(SAL),.WoL(WoL),.STATS(STATS),.MDIO_PHYID(MDIO_PHYID),.SLIP_ENABLE(SLIP_ENABLE)) B (
    .MTXCLK(fabclk),.MTXRDY(b_mtxrdy),.MTXACPT(b_mtxacpt),.MTXSOF(b_mtxsof),.MTXEOF(b_mtxeof),
    .MTXDAT(b_mtxdat),.MTXBYTEVALID(b_mtxbv),.MTXCFRM(1'b0),.MTXHWM(),
    .MRXCLK(fabclk),.MRXRDY(b_mrxrdy),.MRXACPT(b_mrxacpt),.MRXSOF(b_mrxsof),.MRXEOF(b_mrxeof),
    .MRXDAT(b_mrxdat),.MRXBYTEVALID(b_mrxbv),
    .GTXCLK(),.TXCLK(txc),.RXCLK(rxc),.TXEN(),.TXD(),.TXER(),.RXDV(),.RXD(),.RXER(),.CRS(),.COL(),
    .TBI_TX_CLK(tbitx),.TBI_RX_CLK(tbirx),.TCG(b_tcg),.RCG(b_tcg),
    .TBI_TX_VALID(),.TBI_RX_VALID(),.TBI_RX_READY(1'b1),.SIGNAL_DETECT(1'b1),.RX_SLIP(),.SYNC(),
    .ANX_STATE(b_anx),.RCG_ERROR(),
    .MDC(b_mdc),.MDI(1'b1),.MDO(b_mdo),.MDOEN(b_mdoen),
    .PCLK(pclk),.PRESETN(presetn),.PADDR(b_paddr),.PSEL(b_psel),.PENABLE(b_penable),.PWRITE(b_pwrite),
    .PWDATA(b_pwdata),.PRDATA(b_prdata),.PSLVERR(),.PREADY(b_pready),
    .TSM_INTR(),.TSM_CONTROL(),.STBP(1'b0));

  // ---------------- the DUT under test: the bridge ----------------
  // request path: A.MRX -> bridge -> B.MTX. response path idle.
  fabric_bridge bridge (
    .clk(fabclk), .rst_n(presetn),
    .tse0_mrx_rdy(a_mrxrdy), .tse0_mrx_acpt(a_mrxacpt), .tse0_mrx_sof(a_mrxsof),
    .tse0_mrx_eof(a_mrxeof), .tse0_mrx_dat(a_mrxdat), .tse0_mrx_bytevalid(a_mrxbv),
    .tse0_mtx_rdy(), .tse0_mtx_acpt(1'b1), .tse0_mtx_sof(), .tse0_mtx_eof(),
    .tse0_mtx_dat(), .tse0_mtx_bytevalid(),
    .tse1_mrx_rdy(1'b0), .tse1_mrx_acpt(), .tse1_mrx_sof(1'b0), .tse1_mrx_eof(1'b0),
    .tse1_mrx_dat(32'h0), .tse1_mrx_bytevalid(2'b0),
    .tse1_mtx_rdy(b_mtxrdy), .tse1_mtx_acpt(b_mtxacpt), .tse1_mtx_sof(b_mtxsof),
    .tse1_mtx_eof(b_mtxeof), .tse1_mtx_dat(b_mtxdat), .tse1_mtx_bytevalid(b_mtxbv));

  // ================= APB / MDIO / config helpers (per DUT) =================
  `define APBW(PFX,WA,WD) begin \
      @(negedge pclk); PFX``penable=0; PFX``psel=1; PFX``paddr={22'b0,WA,2'b0}; \
      PFX``pwdata=WD; PFX``pwrite=1; @(negedge pclk); PFX``penable=1; \
      wait(PFX``pready==1'b1); @(negedge pclk); PFX``pwrite=0; PFX``penable=0; PFX``psel=0; end

  `define MDIOW(PFX,REG,VAL) \
    `APBW(PFX,8'h0A,{19'h0,5'd18,3'b0,REG}) `APBW(PFX,8'h0B,{16'h0,VAL}) \
    repeat(2500) @(posedge pclk);   // let the MDIO transaction finish

  task cfg_a; begin
    `APBW(a_,8'h00,32'h00000005) `APBW(a_,8'h01,32'h00007203) `APBW(a_,8'h02,32'h40605060)
    `APBW(a_,8'h03,32'h00a1f037) `APBW(a_,8'h04,32'h00000600) `APBW(a_,8'h10,32'hA5A4A3A2)
    `APBW(a_,8'h11,32'hA1A00000) `APBW(a_,8'h12,32'h0000FF00) `APBW(a_,8'h14,32'h0AAA0555)
    `APBW(a_,8'h08,32'h00000007)   // 0x020 MII mgmt config (MDC prescaler)
    `MDIOW(a_,5'h11,16'h4020) `MDIOW(a_,5'h04,16'h00A0)
    `MDIOW(a_,5'h00,16'h8140) `MDIOW(a_,5'h00,16'h1340)
  end endtask

  task cfg_b; begin
    `APBW(b_,8'h00,32'h00000005) `APBW(b_,8'h01,32'h00007203) `APBW(b_,8'h02,32'h40605060)
    `APBW(b_,8'h03,32'h00a1f037) `APBW(b_,8'h04,32'h00000600) `APBW(b_,8'h10,32'hA5A4A3A2)
    `APBW(b_,8'h11,32'hA1A00000) `APBW(b_,8'h12,32'h0000FF00) `APBW(b_,8'h14,32'h0AAA0555)
    `APBW(b_,8'h08,32'h00000007)
    `MDIOW(b_,5'h11,16'h4020) `MDIOW(b_,5'h04,16'h00A0)
    `MDIOW(b_,5'h00,16'h8140) `MDIOW(b_,5'h00,16'h1340)
  end endtask

  // ================= frame inject into A.MTX (real MAC TX client) =================
  localparam int PAYLOAD = 46;
  localparam int FBYTES  = 14 + PAYLOAD;     // we inject hdr+data; MAC appends FCS
  reg [7:0] tf [0:255]; integer txlen;
  task build_frame; integer i; begin
    for (i=0;i<256;i=i+1) tf[i]=8'h00;
    for (i=0;i<6;i=i+1) tf[i]=8'hAA;
    for (i=0;i<6;i=i+1) tf[6+i]=8'hBB;
    tf[12]=(PAYLOAD>>8)&8'hFF; tf[13]=PAYLOAD&8'hFF;
    for (i=0;i<PAYLOAD;i=i+1) tf[14+i]=i[7:0];
    txlen=FBYTES;
  end endtask

  task inject_A; integer i,vl,wd; begin
    i=0; wd=0;
    while (i<txlen && wd<5000) begin
      vl=txlen-i; if(vl>4) vl=4;
      a_mtxdat <= {tf[i+3],tf[i+2],tf[i+1],tf[i]};
      a_mtxsof <= (i==0); a_mtxeof <= (i+4>=txlen);
      a_mtxbv  <= (i+4>=txlen) ? (4-vl) : 2'd0;
      a_mtxrdy <= 1'b1;
      @(posedge fabclk);
      if (a_mtxacpt) i=i+4;
      wd=wd+1;
    end
    a_mtxrdy<=0; a_mtxsof<=0; a_mtxeof<=0; a_mtxbv<=0;
    $display("  inject_A: %0d/%0d bytes accepted (wd=%0d, last a_mtxacpt=%b)", i, txlen, wd, a_mtxacpt);
  end endtask

  // ================= monitor B.MRX (real MAC RX client) =================
  reg [7:0] cap [0:511]; integer caplen; reg done; integer nv,k;
  always @(posedge fabclk) begin
    if (rst) begin caplen<=0; done<=0; b_mrxacpt<=1'b1; end
    else if (b_mrxrdy && b_mrxacpt) begin
      nv = b_mrxeof ? (4-b_mrxbv) : 4;
      for (k=0;k<nv;k=k+1) cap[caplen+k] = b_mrxdat[8*k +: 8];
      caplen = caplen + nv;
      if (b_mrxeof) done <= 1'b1;
    end
  end

  task report; integer i; begin
    $display("=== RESULT: forwarded %0d bytes out of B.MRX ===", caplen);
    $write("  DST:"); for(i=0;i<6;i=i+1) $write(" %02x",cap[i]); $display(" (expect 02 00 00 00 00 02)");
    $write("  SRC:"); for(i=0;i<6;i=i+1) $write(" %02x",cap[6+i]); $display(" (expect 02 00 00 00 00 01)");
    $display("  LEN: %02x %02x   DATA[0..3]: %02x %02x %02x %02x", cap[12],cap[13],cap[14],cap[15],cap[16],cap[17]);
  end endtask

  // ================= main =================
  initial begin
    a_mtxrdy=0;a_mtxsof=0;a_mtxeof=0;a_mtxdat=0;a_mtxbv=0;
    a_psel=0;a_penable=0;a_pwrite=0;a_paddr=0;a_pwdata=0;
    b_psel=0;b_penable=0;b_pwrite=0;b_paddr=0;b_pwdata=0;
    build_frame;
    repeat(40) @(posedge fabclk); rst=0; repeat(20) @(posedge fabclk);
    $display("=== configuring MAC A and B (regs + auto-neg) ===");
    cfg_a; cfg_b;
    begin : anwait
      integer w;
      for (w=0; w<40; w=w+1) begin
        repeat(1000) @(posedge tbirx);
        $display("  [t=%0t] link A.anx[8]=%b B.anx[8]=%b  a_tcg=%h", $time, a_anx[8], b_anx[8], a_tcg);
        if (a_anx[8] && b_anx[8]) disable anwait;
      end
    end
    $display("=== link A=%b B=%b ; injecting frame into A.MTX (%0d bytes) ===", a_anx[8], b_anx[8], txlen);
    fork
      begin inject_A; end
      begin
        wait(done); repeat(4) @(posedge fabclk);
        $display("FORWARDED"); report; $finish;
      end
      begin
        repeat(60000) @(posedge fabclk);
        $display("STALL: no frame egressed B.MRX in timeout");
        $display("  a_mrxrdy=%b a_mrxacpt=%b  b_mtxrdy=%b b_mtxacpt=%b  caplen=%0d",
                 a_mrxrdy,a_mrxacpt,b_mtxrdy,b_mtxacpt,caplen);
        $display("  bridge.reframe_req.state=%0d fed=%0d sent=%0d  deframe_req.tvalid_r=%b sent_bytes=%0d",
                 bridge.reframe_req.state, bridge.reframe_req.fed, bridge.reframe_req.sent,
                 bridge.deframe_req.tvalid_r, bridge.deframe_req.sent_bytes);
        $finish;
      end
    join
  end

endmodule
