// AXI4-Lite control/status block for the VPK180 pass-through / interlock shells.
//   0x00 CTL    rw  [NPORT-1:0] ext_sel : 1 = that MRMAC's TX is driven by the external client
//                                          (pass-through / interlock), 0 = by the example generator
//                   [8]         pt_xover: 1 = pass-through crossover (in-line), 0 = reflect (reset: 1)
//                   [9]         mode_core: 1 = the interlock core owns the ports, 0 = pass-through (reset: 0)
//   0x04 STATUS ro  [3:0] led (ilock_pl: heartbeat, p0 frames, p1 frames, sticky drop)
//                   [7:4] stat_mst_reset_done (AND over the ports)
//   0x08 ID     ro  parameter ID: 0x494C4B31 "ILK1" (pass-through / PS-direct images), 0x494C4B50 "ILKP" (peer-facing)
// Full-word writes only (wstrb ignored). Controls are quasi-static; the consumers synchronise them.
module passthru_ctl_axil #(
  parameter int unsigned NPORT = 4,
  parameter logic [31:0] ID    = 32'h494C4B31
) (
  input  wire        aclk,
  input  wire        aresetn,
  /* verilator lint_off UNUSEDSIGNAL */   // only the word offset of the addresses is decoded
  input  wire [31:0] s_axi_awaddr,
  input  wire        s_axi_awvalid,
  output wire        s_axi_awready,
  input  wire [31:0] s_axi_wdata,
  input  wire [3:0]  s_axi_wstrb,           // ignored: full-word writes only
  input  wire        s_axi_wvalid,
  output wire        s_axi_wready,
  output wire [1:0]  s_axi_bresp,
  output logic       s_axi_bvalid,
  input  wire        s_axi_bready,
  input  wire [31:0] s_axi_araddr,
  /* verilator lint_on UNUSEDSIGNAL */
  input  wire        s_axi_arvalid,
  output wire        s_axi_arready,
  output logic [31:0] s_axi_rdata,
  output wire [1:0]  s_axi_rresp,
  output logic       s_axi_rvalid,
  input  wire        s_axi_rready,
  output logic [NPORT-1:0] ext_sel,
  output logic       pt_xover,
  output logic       mode_core,
  input  wire [3:0]  led,
  input  wire [3:0]  mst_reset_done
);
  wire wr = s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid;
  assign s_axi_awready = wr;
  assign s_axi_wready  = wr;
  assign s_axi_bresp   = 2'b00;
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      ext_sel <= '0; pt_xover <= 1'b1; mode_core <= 1'b0; s_axi_bvalid <= 1'b0;
    end else begin
      if (wr) begin
        s_axi_bvalid <= 1'b1;
        if (s_axi_awaddr[7:2] == 6'd0) begin
          ext_sel   <= s_axi_wdata[NPORT-1:0];
          pt_xover  <= s_axi_wdata[8];
          mode_core <= s_axi_wdata[9];
        end
      end else if (s_axi_bready) begin
        s_axi_bvalid <= 1'b0;
      end
    end
  end
  wire rd = s_axi_arvalid && !s_axi_rvalid;
  assign s_axi_arready = rd;
  assign s_axi_rresp   = 2'b00;
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      s_axi_rvalid <= 1'b0; s_axi_rdata <= '0;
    end else begin
      if (rd) begin
        s_axi_rvalid <= 1'b1;
        case (s_axi_araddr[7:2])
          6'd0:    s_axi_rdata <= {22'd0, mode_core, pt_xover, {(8-NPORT){1'b0}}, ext_sel};
          6'd1:    s_axi_rdata <= {24'd0, mst_reset_done, led};
          6'd2:    s_axi_rdata <= ID;
          default: s_axi_rdata <= 32'h0;
        endcase
      end else if (s_axi_rready) begin
        s_axi_rvalid <= 1'b0;
      end
    end
  end
endmodule
