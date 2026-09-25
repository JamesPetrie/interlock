// PS-driven Ethernet frame injector + capture with an MRMAC-client pin interface.
//
// Sits where an MRMAC client sits: `out_*` is the stream this "MAC" delivers (frames the
// PS injects), `in_*` is the stream this "MAC" would transmit (frames captured for the
// PS). So one instance can replace the example generator/monitor behind a real MRMAC
// (out -> MAC TX via the wrapper's ext mux, MAC RX -> in), and another can attach
// straight to an ilock_pl port as a virtual MAC (out -> p_rx pins, p_tx pins -> in).
//
// AXI-Lite map (16 KB window, 32-bit words, full-word writes only):
//   0x0000 ID        ro  0x50534650 "PSFP"
//   0x0004 TX_LEN    rw  frame length in bytes, 1..TXBUF_WORDS*4
//   0x0008 TX_COUNT  rw  frames to send per start
//   0x000C TX_CTL    wo  bit0: start (ignored while busy)
//   0x0010 TX_STAT   ro  bit0: busy
//   0x0014 TX_SENT   ro  frames sent since the last start
//   0x0020 RX_CTL    wo  bit0: arm (capture the next complete frame)  bit1: clear captured/overflow/drops
//                        bit2: zero RX_COUNT
//   0x0024 RX_STAT   ro  bit0: captured  bit1: overflow (frame longer than the buffer, truncated)
//                        bit2: drops seen at the input FIFO (sticky)  bit3: armed  [28:16]: captured length (bytes)
//   0x0028 RX_COUNT  ro  frames captured so far (frames queue in the input FIFO until armed; the FIFO
//                        drops whole frames on overflow and sets the drops flag)
//   0x1000.. TX buffer (rw), 0x2000.. RX buffer (ro): frame bytes, little-endian words
//
// Injector path: buffer -> 32-bit stream (aclk) -> axis_upsize -> packet-mode CDC -> MRMAC pin
// bundle (mac_clk); frames therefore reach the consumer gap-free, as an MRMAC TX requires.
// Capture path: pin bundle -> adapter -> drop FIFO (no backpressure) -> CDC -> axis_downsize ->
// buffer (aclk). Errored frames and frames that overflow the FIFO are dropped whole, like
// the shim's RX chain; the input always accepts. Frames are released from the FIFO only while
// a capture is armed (whole frames at a time), so back-to-back frames queue up and each arm
// takes the next one in order.
module ps_frame_port #(
  parameter int unsigned W             = 384,   // stream width: 64/128/256/384
  parameter int unsigned NWORDS        = 6,
  parameter int unsigned TXBUF_WORDS   = 1024,  // 4 KB
  parameter int unsigned RXBUF_WORDS   = 1024,  // 4 KB
  parameter int unsigned RX_FIFO_DEPTH = 256,   // beats, power of two
  parameter int unsigned CDC_DEPTH     = 256    // beats, power of two, >= 1 max frame for the TX side
) (
  // ---- AXI-Lite (aclk) ----
  input  wire         aclk,
  input  wire         aresetn,
  /* verilator lint_off UNUSEDSIGNAL */   // only the 16 KB window of the addresses is decoded; wstrb ignored
  input  wire [31:0]  s_axi_awaddr,
  input  wire         s_axi_awvalid,
  output wire         s_axi_awready,
  input  wire [31:0]  s_axi_wdata,
  input  wire [3:0]   s_axi_wstrb,
  input  wire         s_axi_wvalid,
  output wire         s_axi_wready,
  output wire [1:0]   s_axi_bresp,
  output logic        s_axi_bvalid,
  input  wire         s_axi_bready,
  input  wire [31:0]  s_axi_araddr,
  /* verilator lint_on UNUSEDSIGNAL */
  input  wire         s_axi_arvalid,
  output wire         s_axi_arready,
  output logic [31:0] s_axi_rdata,
  output wire [1:0]   s_axi_rresp,
  output logic        s_axi_rvalid,
  input  wire         s_axi_rready,
  // ---- MAC-facing pin bundle (mac_clk) ----
  input  wire                  mac_clk,
  input  wire                  mac_rst_n,
  input  wire                  in_tvalid,          // frames handed to this port (captured)
  input  wire                  in_tlast,
  input  wire [64*NWORDS-1:0]  in_tdata,
  input  wire [11*NWORDS-1:0]  in_tkeep_user,
  output wire                  in_tready,          // always 1
  output wire                  out_tvalid,         // frames this port delivers (injected)
  input  wire                  out_tready,
  output wire                  out_tlast,
  output wire [64*NWORDS-1:0]  out_tdata,
  output wire [11*NWORDS-1:0]  out_tkeep_user
);
  localparam int unsigned TXA_W = $clog2(TXBUF_WORDS);
  localparam int unsigned RXA_W = $clog2(RXBUF_WORDS);

  // ================================================================ AXI-Lite ==
  logic [12:0] tx_len;
  logic [31:0] tx_count, tx_sent, rx_count;
  logic        tx_busy, tx_start;
  logic        rx_armed, rx_captured, rx_ovf, rx_drops;
  logic [12:0] rx_len;
  logic        rx_arm_p, rx_clear_p, rx_zero_p;

  wire wr = s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid;
  assign s_axi_awready = wr;
  assign s_axi_wready  = wr;
  assign s_axi_bresp   = 2'b00;
  wire [1:0]  wr_region = s_axi_awaddr[13:12];
  wire [9:0]  wr_word   = s_axi_awaddr[11:2];
  wire        reg_wr    = wr && (wr_region == 2'd0);
  wire        txbuf_we  = wr && (wr_region == 2'd1);

  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      s_axi_bvalid <= 1'b0; tx_len <= 13'd64; tx_count <= 32'd1;
      tx_start <= 1'b0; rx_arm_p <= 1'b0; rx_clear_p <= 1'b0; rx_zero_p <= 1'b0;
    end else begin
      tx_start <= 1'b0; rx_arm_p <= 1'b0; rx_clear_p <= 1'b0; rx_zero_p <= 1'b0;
      if (wr) begin
        s_axi_bvalid <= 1'b1;
        if (reg_wr) begin
          case (wr_word[5:0])
            6'd1: tx_len   <= s_axi_wdata[12:0];
            6'd2: tx_count <= s_axi_wdata;
            6'd3: tx_start <= s_axi_wdata[0];
            6'd8: begin rx_arm_p <= s_axi_wdata[0]; rx_clear_p <= s_axi_wdata[1]; rx_zero_p <= s_axi_wdata[2]; end
            default: ;
          endcase
        end
      end else if (s_axi_bready) begin
        s_axi_bvalid <= 1'b0;
      end
    end
  end

  // reads: address accepted in one cycle, data (buffer read registered) the next
  logic        rd_pend;
  logic [1:0]  rd_region_q;
  logic [5:0]  rd_reg_q;
  logic [31:0] txbuf_rd_axi, rxbuf_rd;
  wire rd = s_axi_arvalid && !rd_pend && !s_axi_rvalid;
  assign s_axi_arready = rd;
  assign s_axi_rresp   = 2'b00;
  logic [31:0] reg_rd;
  always_comb begin
    case (rd_reg_q)
      6'd0:  reg_rd = 32'h50534650;
      6'd1:  reg_rd = {19'd0, tx_len};
      6'd2:  reg_rd = tx_count;
      6'd4:  reg_rd = {31'd0, tx_busy};
      6'd5:  reg_rd = tx_sent;
      6'd9:  reg_rd = {3'd0, rx_len, 12'd0, rx_armed, rx_drops, rx_ovf, rx_captured};
      6'd10: reg_rd = rx_count;
      default: reg_rd = 32'h0;
    endcase
  end
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      rd_pend <= 1'b0; s_axi_rvalid <= 1'b0; s_axi_rdata <= '0; rd_region_q <= '0; rd_reg_q <= '0;
    end else begin
      if (rd) begin rd_region_q <= s_axi_araddr[13:12]; rd_reg_q <= s_axi_araddr[7:2]; rd_pend <= 1'b1; end
      if (rd_pend) begin
        rd_pend <= 1'b0; s_axi_rvalid <= 1'b1;
        case (rd_region_q)
          2'd1:    s_axi_rdata <= txbuf_rd_axi;
          2'd2:    s_axi_rdata <= rxbuf_rd;
          default: s_axi_rdata <= reg_rd;
        endcase
      end else if (s_axi_rvalid && s_axi_rready) begin
        s_axi_rvalid <= 1'b0;
      end
    end
  end

  // ================================================================ buffers ===
  logic [31:0] txbuf [0:TXBUF_WORDS-1];
  logic [31:0] rxbuf [0:RXBUF_WORDS-1];
  logic [31:0] txbuf_rd_fsm;
  logic [TXA_W-1:0] tx_word;
  logic             rxbuf_we;
  logic [RXA_W-1:0] rx_widx;
  logic [RXA_W-1:0] rx_waddr;
  logic [31:0]      rx_wdata;
  always_ff @(posedge aclk) begin                       // no resets: block RAM
    if (txbuf_we) txbuf[wr_word[TXA_W-1:0]] <= s_axi_wdata;
    if (rd) begin
      txbuf_rd_axi <= txbuf[s_axi_araddr[2 +: TXA_W]];
      rxbuf_rd     <= rxbuf[s_axi_araddr[2 +: RXA_W]];
    end
    txbuf_rd_fsm <= txbuf[tx_word];
    if (rxbuf_we) rxbuf[rx_waddr] <= rx_wdata;
  end

  // ================================================================ injector ==
  logic        ij_tvalid, ij_tlast;
  logic [3:0]  ij_tkeep;
  wire         ij_tready;
  wire [12:0]  last_word = 13'((tx_len - 13'd1) >> 2);
  wire [3:0]   last_keep = (tx_len[1:0] == 2'd0) ? 4'hF : 4'((4'd1 << tx_len[1:0]) - 4'd1);
  typedef enum logic [1:0] { T_IDLE, T_FETCH, T_EMIT } tstate_t;
  tstate_t tstate;
  logic [31:0] tx_frame;
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      tstate <= T_IDLE; tx_busy <= 1'b0; tx_sent <= '0; tx_frame <= '0; tx_word <= '0;
      ij_tvalid <= 1'b0; ij_tlast <= 1'b0; ij_tkeep <= '0;
    end else begin
      case (tstate)
        T_IDLE: if (tx_start && tx_len != 0 && tx_count != 0) begin
          tx_busy <= 1'b1; tx_sent <= '0; tx_frame <= '0; tx_word <= '0; tstate <= T_FETCH;
        end
        T_FETCH: begin                                     // txbuf_rd_fsm valid next cycle
          ij_tvalid <= 1'b1;
          ij_tlast  <= (13'(tx_word) == last_word);
          ij_tkeep  <= (13'(tx_word) == last_word) ? last_keep : 4'hF;
          tstate    <= T_EMIT;
        end
        T_EMIT: if (ij_tready) begin
          ij_tvalid <= 1'b0;
          if (ij_tlast) begin
            tx_word  <= '0;
            tx_sent  <= tx_sent + 32'd1;
            tx_frame <= tx_frame + 32'd1;
            if (tx_frame + 32'd1 == tx_count) begin tx_busy <= 1'b0; tstate <= T_IDLE; end
            else tstate <= T_FETCH;
          end else begin
            tx_word <= tx_word + TXA_W'(1);
            tstate  <= T_FETCH;
          end
        end
        default: tstate <= T_IDLE;
      endcase
    end
  end

  wire            up_tvalid, up_tready, up_tlast, up_tuser;
  wire [W-1:0]    up_tdata;
  wire [W/8-1:0]  up_tkeep;
  axis_upsize #(.W(W)) tx_up (
    .clk(aclk), .rst_n(aresetn),
    .s_tvalid(ij_tvalid), .s_tready(ij_tready), .s_tdata(txbuf_rd_fsm), .s_tkeep(ij_tkeep), .s_tlast(ij_tlast), .s_tuser(1'b0),
    .m_tvalid(up_tvalid), .m_tready(up_tready), .m_tdata(up_tdata), .m_tkeep(up_tkeep), .m_tlast(up_tlast), .m_tuser(up_tuser)
  );
  wire            ot_tvalid, ot_tready, ot_tlast, ot_tuser;
  wire [W-1:0]    ot_tdata;
  wire [W/8-1:0]  ot_tkeep;
`ifdef SIM_NO_XPM
  sim_axis_cdc_fifo #(.W(W), .DEPTH(CDC_DEPTH), .PACKET_MODE(1'b1)) tx_cdc (
    .s_aclk(aclk), .s_aresetn(aresetn),
    .s_tvalid(up_tvalid), .s_tready(up_tready), .s_tdata(up_tdata), .s_tkeep(up_tkeep), .s_tlast(up_tlast), .s_tuser(up_tuser),
    .m_aclk(mac_clk), .m_aresetn(mac_rst_n),
    .m_tvalid(ot_tvalid), .m_tready(ot_tready), .m_tdata(ot_tdata), .m_tkeep(ot_tkeep), .m_tlast(ot_tlast), .m_tuser(ot_tuser)
  );
`else
  xpm_fifo_axis #(
    .CLOCKING_MODE("independent_clock"), .FIFO_MEMORY_TYPE("auto"), .PACKET_FIFO("true"), .FIFO_DEPTH(CDC_DEPTH),
    .TDATA_WIDTH(W), .TUSER_WIDTH(1), .RELATED_CLOCKS(0), .CDC_SYNC_STAGES(2), .USE_ADV_FEATURES("1000")
  ) tx_cdc (
    .s_aclk(aclk), .s_aresetn(aresetn), .m_aclk(mac_clk),
    .s_axis_tvalid(up_tvalid), .s_axis_tready(up_tready), .s_axis_tdata(up_tdata),
    .s_axis_tstrb('1), .s_axis_tkeep(up_tkeep), .s_axis_tlast(up_tlast), .s_axis_tid('0), .s_axis_tdest('0), .s_axis_tuser(up_tuser),
    .m_axis_tvalid(ot_tvalid), .m_axis_tready(ot_tready), .m_axis_tdata(ot_tdata),
    .m_axis_tstrb(), .m_axis_tkeep(ot_tkeep), .m_axis_tlast(ot_tlast), .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(ot_tuser),
    .injectsbiterr_axis(1'b0), .injectdbiterr_axis(1'b0), .sbiterr_axis(), .dbiterr_axis(),
    .prog_full_axis(), .wr_data_count_axis(), .almost_full_axis(), .prog_empty_axis(), .rd_data_count_axis(), .almost_empty_axis()
  );
`endif

  // ================================================================ capture ===
  wire            ar_tvalid, ar_tlast, ar_tuser;
  wire [W-1:0]    ar_tdata;
  wire [W/8-1:0]  ar_tkeep;
  mrmac_axis_adapt #(.W(W), .NWORDS(NWORDS), .WORD_BASE(0)) adapt (
    .mrmac_rx_tvalid(in_tvalid), .mrmac_rx_tdata(in_tdata), .mrmac_rx_tkeep_user(in_tkeep_user), .mrmac_rx_tlast(in_tlast),
    .rx_tvalid(ar_tvalid), .rx_tdata(ar_tdata), .rx_tkeep(ar_tkeep), .rx_tlast(ar_tlast), .rx_tuser(ar_tuser),
    .tx_tvalid(ot_tvalid), .tx_tready(ot_tready), .tx_tdata(ot_tdata), .tx_tkeep(ot_tkeep), .tx_tlast(ot_tlast), .tx_tuser(ot_tuser),
    .mrmac_tx_tvalid(out_tvalid), .mrmac_tx_tready(out_tready), .mrmac_tx_tdata(out_tdata),
    .mrmac_tx_tkeep_user(out_tkeep_user), .mrmac_tx_tlast(out_tlast)
  );
  assign in_tready = 1'b1;
  wire            pf_tvalid, pf_tready, pf_tlast, pf_tuser, drop_ovf, drop_err, unused_fifo_tready;
  wire [W-1:0]    pf_tdata;
  wire [W/8-1:0]  pf_tkeep;
  axis_pkt_fifo #(.W(W), .DEPTH(RX_FIFO_DEPTH)) rx_fifo (
    .clk(mac_clk), .rst_n(mac_rst_n),
    .s_tvalid(ar_tvalid), .s_tready(unused_fifo_tready), .s_tdata(ar_tdata), .s_tkeep(ar_tkeep), .s_tlast(ar_tlast), .s_tuser(ar_tuser),
    .m_tvalid(pf_tvalid), .m_tready(pf_tready), .m_tdata(pf_tdata), .m_tkeep(pf_tkeep), .m_tlast(pf_tlast), .m_tuser(pf_tuser),
    .drop_ovf(drop_ovf), .drop_err(drop_err)
  );
  wire            rc_tvalid, rc_tready, rc_tlast, rc_tuser;
  wire [W-1:0]    rc_tdata;
  wire [W/8-1:0]  rc_tkeep;
`ifdef SIM_NO_XPM
  sim_axis_cdc_fifo #(.W(W), .DEPTH(CDC_DEPTH), .PACKET_MODE(1'b0)) rx_cdc (
    .s_aclk(mac_clk), .s_aresetn(mac_rst_n),
    .s_tvalid(pf_tvalid), .s_tready(pf_tready), .s_tdata(pf_tdata), .s_tkeep(pf_tkeep), .s_tlast(pf_tlast), .s_tuser(pf_tuser),
    .m_aclk(aclk), .m_aresetn(aresetn),
    .m_tvalid(rc_tvalid), .m_tready(rc_tready), .m_tdata(rc_tdata), .m_tkeep(rc_tkeep), .m_tlast(rc_tlast), .m_tuser(rc_tuser)
  );
`else
  xpm_fifo_axis #(
    .CLOCKING_MODE("independent_clock"), .FIFO_MEMORY_TYPE("auto"), .PACKET_FIFO("false"), .FIFO_DEPTH(CDC_DEPTH),
    .TDATA_WIDTH(W), .TUSER_WIDTH(1), .RELATED_CLOCKS(0), .CDC_SYNC_STAGES(2), .USE_ADV_FEATURES("1000")
  ) rx_cdc (
    .s_aclk(mac_clk), .s_aresetn(mac_rst_n), .m_aclk(aclk),
    .s_axis_tvalid(pf_tvalid), .s_axis_tready(pf_tready), .s_axis_tdata(pf_tdata),
    .s_axis_tstrb('1), .s_axis_tkeep(pf_tkeep), .s_axis_tlast(pf_tlast), .s_axis_tid('0), .s_axis_tdest('0), .s_axis_tuser(pf_tuser),
    .m_axis_tvalid(rc_tvalid), .m_axis_tready(rc_tready), .m_axis_tdata(rc_tdata),
    .m_axis_tstrb(), .m_axis_tkeep(rc_tkeep), .m_axis_tlast(rc_tlast), .m_axis_tid(), .m_axis_tdest(), .m_axis_tuser(rc_tuser),
    .injectsbiterr_axis(1'b0), .injectdbiterr_axis(1'b0), .sbiterr_axis(), .dbiterr_axis(),
    .prog_full_axis(), .wr_data_count_axis(), .almost_full_axis(), .prog_empty_axis(), .rd_data_count_axis(), .almost_empty_axis()
  );
`endif
  wire        cd_tvalid, cd_tlast, cd_tuser, cd_tready;
  wire [31:0] cd_tdata;
  wire [3:0]  cd_tkeep;
  axis_downsize #(.W(W)) rx_down (
    .clk(aclk), .rst_n(aresetn),
    .s_tvalid(rc_tvalid), .s_tready(rc_tready), .s_tdata(rc_tdata), .s_tkeep(rc_tkeep), .s_tlast(rc_tlast), .s_tuser(rc_tuser),
    .m_tvalid(cd_tvalid), .m_tready(cd_tready), .m_tdata(cd_tdata), .m_tkeep(cd_tkeep), .m_tlast(cd_tlast), .m_tuser(cd_tuser)
  );
  wire unused_cd_tuser = cd_tuser;                     // errored frames never get this far

  // capture state machine: only whole frames, starting at a frame boundary
  logic        in_frame, cap_active;
  logic [12:0] cap_bytes;
  wire  [2:0]  beat_bytes  = 3'($countones(cd_tkeep));
  assign       cd_tready   = in_frame || (rx_armed && !rx_captured);   // release the next frame only when armed
  wire         cd_fire     = cd_tvalid && cd_tready;
  wire         frame_start = cd_fire && !in_frame;
  wire         cap_first   = frame_start;                                // (only ever fires armed)
  wire         at_last_w   = (rx_widx == RXA_W'(RXBUF_WORDS - 1));
  logic [RXA_W-1:0] rx_wptr;
  assign rx_widx  = rx_wptr;                                  // (declared above for the buffer port)
  assign rxbuf_we = cd_fire && (cap_first || (cap_active && !rx_ovf));
  assign rx_wdata = cd_tdata;
  always_comb rx_waddr = cap_first ? '0 : rx_wptr;
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      in_frame <= 1'b0; cap_active <= 1'b0; rx_wptr <= '0; cap_bytes <= '0;
      rx_armed <= 1'b0; rx_captured <= 1'b0; rx_ovf <= 1'b0; rx_len <= '0; rx_count <= '0;
    end else begin
      if (rx_arm_p)   rx_armed <= 1'b1;
      if (rx_clear_p) begin rx_captured <= 1'b0; rx_ovf <= 1'b0; end
      if (rx_zero_p)  rx_count <= '0;
      if (cd_fire) begin
        in_frame <= !cd_tlast;
        if (cap_first) begin
          cap_active <= !cd_tlast; rx_wptr <= RXA_W'(1); cap_bytes <= 13'(beat_bytes); rx_ovf <= 1'b0;
          if (cd_tlast) begin rx_captured <= 1'b1; rx_armed <= 1'b0; rx_len <= 13'(beat_bytes); end
        end else if (cap_active) begin
          if (!rx_ovf) begin
            cap_bytes <= cap_bytes + 13'(beat_bytes);
            if (at_last_w) begin if (!cd_tlast) rx_ovf <= 1'b1; end
            else rx_wptr <= rx_wptr + RXA_W'(1);
          end
          if (cd_tlast) begin
            cap_active <= 1'b0; rx_captured <= 1'b1; rx_armed <= 1'b0;
            rx_len <= rx_ovf ? cap_bytes : cap_bytes + 13'(beat_bytes);
          end
        end
        if (cd_tlast) rx_count <= rx_count + 32'd1;
      end
    end
  end

  // sticky "drops seen" flag: set in the MAC clock domain, cleared on request from the PS side
  logic drop_sticky_mac, clr_tgl;
  (* ASYNC_REG = "TRUE" *) logic [2:0] clr_sync;
  (* ASYNC_REG = "TRUE" *) logic [1:0] drop_sync;
  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin clr_tgl <= 1'b0; drop_sync <= '0; end
    else begin
      if (rx_clear_p) clr_tgl <= ~clr_tgl;
      drop_sync <= {drop_sync[0], drop_sticky_mac};
    end
  end
  assign rx_drops = drop_sync[1];
  always_ff @(posedge mac_clk or negedge mac_rst_n) begin
    if (!mac_rst_n) begin drop_sticky_mac <= 1'b0; clr_sync <= '0; end
    else begin
      clr_sync <= {clr_sync[1:0], clr_tgl};
      if (clr_sync[2] != clr_sync[1]) drop_sticky_mac <= 1'b0;
      if (drop_ovf || drop_err)        drop_sticky_mac <= 1'b1;
    end
  end
endmodule
