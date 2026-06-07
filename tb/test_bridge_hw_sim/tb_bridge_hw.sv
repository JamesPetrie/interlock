// tb_bridge_hw — hardware-faithful testbench for the fabric_bridge datapath.
//
// Purpose: the cocotb tests drive a *forgiving model* of the CoreTSE MAC-client
// interface and PASS, yet the bridge forwards ZERO frames on real hardware.
// This TB drives the bridge's MAC-side pins with BFMs that mirror the real
// CoreTSE client handshake (derived from CoreTSE_tb.v frfrm/ftfrm: a word
// transfers on a posedge where RDY & ACPT are both high; SOF on first word,
// EOF + BYTEVALID = count-of-INVALID-bytes on the last).
//
// Two calibration controls against KNOWN hardware ground truth:
//   -DDIRECT : port-0 MRX wired straight to port-1 MTX (the SSH-working
//              cross-wire). MUST print FORWARDED  (proves the harness works).
//   default  : frames flow through fabric_bridge (eth_deframe->eth_reframe).
//              On hardware this forwards nothing; a faithful TB MUST print
//              STALL and dump which signal is wedged.
//
// Only if it reproduces FORWARDED(direct) / STALL(bridge) do we trust it to
// localize the bug. If the bridge case FORWARDS here too, the BFM is too
// forgiving (like cocotb) -> escalate to the real CoreTSE RTL.

`timescale 1ns/1ps
module tb_bridge_hw;

  // ---------------- clock / reset ----------------
  logic clk = 0;
  always #4 clk = ~clk;                 // 125 MHz fabric clock
  logic rst_n = 0;

  // ---------------- bridge MAC-side nets ----------------
  // Port-0 MAC RX: TB (acting as the MAC) drives data; bridge drives acpt.
  logic        p0_rxrdy, p0_rxsof, p0_rxeof;  logic [31:0] p0_rxdat;  logic [1:0] p0_rxbv;
  wire         p0_rxacpt;
  // Port-1 MAC TX: bridge drives data; TB (acting as the MAC) drives acpt.
  wire         p1_txrdy, p1_txsof, p1_txeof;  wire  [31:0] p1_txdat;  wire  [1:0] p1_txbv;
  logic        p1_txacpt;
  // unused-direction stubs (port-1 RX idle, port-0 TX sink always ready)
  logic        p1_rxrdy = 0, p1_rxsof = 0, p1_rxeof = 0;  logic [31:0] p1_rxdat = 0;  logic [1:0] p1_rxbv = 0;
  wire         p1_rxacpt;
  logic        p0_txacpt = 1'b1;
  wire         p0_txrdy, p0_txsof, p0_txeof;  wire [31:0] p0_txdat;  wire [1:0] p0_txbv;

`ifdef DIRECT
  // ---- CONTROL B: transparent cross-wire (the known-good HW datapath) ----
  assign p1_txrdy = p0_rxrdy;  assign p1_txsof = p0_rxsof;  assign p1_txeof = p0_rxeof;
  assign p1_txdat = p0_rxdat;  assign p1_txbv  = p0_rxbv;   assign p0_rxacpt = p1_txacpt;
`else
  // ---- CONTROL A / DUT: the sanitizing bridge ----
  fabric_bridge dut (
    .clk(clk), .rst_n(rst_n),
    // port 0
    .tse0_mrx_rdy(p0_rxrdy), .tse0_mrx_acpt(p0_rxacpt), .tse0_mrx_sof(p0_rxsof),
    .tse0_mrx_eof(p0_rxeof), .tse0_mrx_dat(p0_rxdat), .tse0_mrx_bytevalid(p0_rxbv),
    .tse0_mtx_rdy(p0_txrdy), .tse0_mtx_acpt(p0_txacpt), .tse0_mtx_sof(p0_txsof),
    .tse0_mtx_eof(p0_txeof), .tse0_mtx_dat(p0_txdat), .tse0_mtx_bytevalid(p0_txbv),
    // port 1
    .tse1_mrx_rdy(p1_rxrdy), .tse1_mrx_acpt(p1_rxacpt), .tse1_mrx_sof(p1_rxsof),
    .tse1_mrx_eof(p1_rxeof), .tse1_mrx_dat(p1_rxdat), .tse1_mrx_bytevalid(p1_rxbv),
    .tse1_mtx_rdy(p1_txrdy), .tse1_mtx_acpt(p1_txacpt), .tse1_mtx_sof(p1_txsof),
    .tse1_mtx_eof(p1_txeof), .tse1_mtx_dat(p1_txdat), .tse1_mtx_bytevalid(p1_txbv)
  );
`endif

  // ---------------- stimulus frame ----------------
  localparam int PAYLOAD = 46;                 // = ETH_DATA_MIN -> clean 64B frame
  localparam int FRAME_BYTES = 14 + PAYLOAD + 4;
  reg [7:0] txframe [0:255];
  integer   txlen;

  task build_frame;
    integer i;
    begin
      for (i = 0; i < 256; i = i + 1) txframe[i] = 8'h00;
      // DST aa:..  SRC bb:..  (forced by reframe, so values here are arbitrary)
      for (i = 0; i < 6; i = i + 1) txframe[i]   = 8'hAA;
      for (i = 0; i < 6; i = i + 1) txframe[6+i] = 8'hBB;
      txframe[12] = (PAYLOAD >> 8) & 8'hFF;     // LEN high (big-endian on wire)
      txframe[13] =  PAYLOAD       & 8'hFF;     // LEN low
      for (i = 0; i < PAYLOAD; i = i + 1) txframe[14+i] = i[7:0];   // DATA pattern
      txframe[14+PAYLOAD+0] = 8'hDE;            // FCS (junk; deframe drops it)
      txframe[14+PAYLOAD+1] = 8'hAD;
      txframe[14+PAYLOAD+2] = 8'hBE;
      txframe[14+PAYLOAD+3] = 8'hEF;
      txlen = FRAME_BYTES;
    end
  endtask

  // ---------------- MAC-RX SOURCE BFM (complement of frfrm) ----------------
  // Drive with nonblocking (values take effect for the next posedge); sample
  // acpt AT the posedge; advance only on an accepted word. No same-edge race.
  task mac_rx_send;
    integer i, valid_last;
    begin
      i = 0;
      while (i < txlen) begin
        valid_last = txlen - i; if (valid_last > 4) valid_last = 4;
        p0_rxdat <= {txframe[i+3], txframe[i+2], txframe[i+1], txframe[i]};  // little-endian word
        p0_rxsof <= (i == 0);
        p0_rxeof <= (i + 4 >= txlen);
        p0_rxbv  <= (i + 4 >= txlen) ? (4 - valid_last) : 2'd0;   // count of INVALID bytes
        p0_rxrdy <= 1'b1;
        @(posedge clk);
        if (p0_rxacpt) i = i + 4;               // word taken this cycle; else hold & retry
      end
      p0_rxrdy <= 1'b0; p0_rxsof <= 1'b0; p0_rxeof <= 1'b0; p0_rxbv <= 2'd0;
    end
  endtask

  // ---------------- MAC-TX SINK BFM (complement of ftfrm) ----------------
  reg [7:0] cap [0:511];
  integer   caplen;
  reg       done;
  integer   nvalid, k;
  always @(posedge clk) begin
    if (!rst_n) begin caplen <= 0; done <= 0; end
    else if (p1_txrdy && p1_txacpt) begin
      nvalid = p1_txeof ? (4 - p1_txbv) : 4;
      for (k = 0; k < nvalid; k = k + 1) cap[caplen + k] = p1_txdat[8*k +: 8];
      caplen = caplen + nvalid;
      if (p1_txeof) done <= 1'b1;
    end
  end

  // ---------------- checker ----------------
  task check_frame;
    integer i; reg ok;
    begin
      ok = 1'b1;
      $display("  egress length    : %0d bytes (expected %0d)", caplen, FRAME_BYTES);
      $write  ("  egress DST       :"); for (i=0;i<6;i=i+1) $write(" %02x", cap[i]);   $display("   (expect 02 00 00 00 00 02)");
      $write  ("  egress SRC       :"); for (i=0;i<6;i=i+1) $write(" %02x", cap[6+i]); $display("   (expect 02 00 00 00 00 01)");
      $display("  egress LEN       : %02x %02x (expect %02x %02x)", cap[12], cap[13], (PAYLOAD>>8)&8'hFF, PAYLOAD&8'hFF);
      if (cap[0] !== 8'h02 || cap[1] !== 8'h00 || cap[5] !== 8'h02) ok = 0;   // forced DST
      if (cap[6] !== 8'h02 || cap[11] !== 8'h01)                    ok = 0;   // forced SRC
      for (i = 0; i < PAYLOAD; i = i + 1) if (cap[14+i] !== i[7:0]) ok = 0;   // DATA preserved
      $display("  DATA preserved   : %s", ok ? "yes" : "NO");
      $display(ok ? "RESULT: FORWARDED + sanitized correctly" : "RESULT: FORWARDED but content mismatch");
    end
  endtask

  // ---------------- state dump on stall ----------------
  task dump_state;
    begin
`ifndef DIRECT
      $display("  -- pins --  p0_rxrdy=%b p0_rxacpt=%b   p1_txrdy=%b p1_txacpt=%b",
               p0_rxrdy, p0_rxacpt, p1_txrdy, p1_txacpt);
      $display("  -- AXI  --  req_tvalid=%b req_tready=%b req_tlast=%b req_len=%0d",
               dut.req_tvalid, dut.req_tready, dut.req_tlast, dut.req_len);
      $display("  -- deframe_req --  rx_widx=%0d in_frame=%b sent_bytes=%0d tvalid_r=%b",
               dut.deframe_req.rx_widx, dut.deframe_req.in_frame,
               dut.deframe_req.sent_bytes, dut.deframe_req.tvalid_r);
      $display("  -- reframe_req --  state=%0d fed=%0d sent=%0d data_end=%0d pad_end=%0d o_rdy=%b",
               dut.reframe_req.state, dut.reframe_req.fed, dut.reframe_req.sent,
               dut.reframe_req.data_end, dut.reframe_req.pad_end, dut.reframe_req.o_rdy);
`endif
    end
  endtask

  // ---------------- main ----------------
  initial begin
    p0_rxrdy=0; p0_rxsof=0; p0_rxeof=0; p0_rxdat=0; p0_rxbv=0; p1_txacpt=1'b1;
    build_frame;
    repeat (8) @(posedge clk); rst_n = 1; repeat (4) @(posedge clk);
`ifdef DIRECT
    $display("=== CONTROL B: DIRECT cross-wire (must FORWARD) ===");
`else
    $display("=== DUT: fabric_bridge sanitizer (HW forwards nothing -> expect STALL) ===");
`endif
    fork
      begin
        mac_rx_send;
        wait (done);
        repeat (2) @(posedge clk);
        $display("FORWARDED");
        check_frame;
        $finish;
      end
      begin
        repeat (4000) @(posedge clk);
        $display("STALL: no complete frame egressed within timeout");
        dump_state;
        $finish;
      end
    join
  end

endmodule
