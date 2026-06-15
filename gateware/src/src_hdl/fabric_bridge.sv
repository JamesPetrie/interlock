// fabric_bridge — user-controlled fabric block on the Ethernet byte path,
// sitting between the two CoreTSE MAC client interfaces. (CoreTSE is itself
// soft IP synthesized into the fabric; what this module adds is RTL we can
// inspect or modify on the MAC-to-MAC frames — the insertion point for the
// interlock Core. Before it existed, the bytes traversed only encrypted
// CoreTSE logic and routing.)
//
// CoreTSE MAC client (packet-FIFO) interface — one bundle per direction,
// taken from the CoreTSE port list and the reference testbench (CoreTSE_tb.v):
//
//   M*DAT[31:0]       four packet bytes, little-endian (byte N in bits [7:0])
//   M*RDY             source has a valid word this cycle
//   M*ACPT            sink accepts the word this cycle
//   M*SOF             asserted on the first word of a frame
//   M*EOF             asserted on the last word of a frame
//   M*BYTEVALID[1:0]  count of *invalid* bytes in the current word (yes, the
//                     name lies — the eval RTL drives it as a data-not-valid
//                     count; see CoreTSE_tb.v `frfrm`). Always 0 except on a
//                     final word whose frame length isn't a multiple of 4:
//                     then 1/2/3 means 3/2/1 valid bytes in the low lanes.
//
// A word transfers on a rising clk edge where RDY & ACPT are both high.
//
// Port naming mirrors the CoreTSE pins so the SmartDesign wiring is 1:1:
// Directions below are from THIS module's point of view (the inverse of the
// CoreTSE pin direction on the same net): an MRX bundle is an input here
// because MRX* are CoreTSE outputs; an MTX bundle is an output here because
// MTX* are CoreTSE inputs.

module fabric_bridge (
  input  wire        clk,      // fabric clock (CORETSE M*CLK domain; both MACs share it)
  input  wire        rst_n,    // active-low synchronous reset
  // ---- Port 0 / CORETSE_0 ----
  // MAC RX from CORETSE_0
  input  wire        tse0_mrx_rdy,
  output wire        tse0_mrx_acpt,
  input  wire        tse0_mrx_sof,
  input  wire        tse0_mrx_eof,
  input  wire [31:0] tse0_mrx_dat,
  input  wire [1:0]  tse0_mrx_bytevalid,
  // MAC TX to CORETSE_0
  output wire        tse0_mtx_rdy,
  input  wire        tse0_mtx_acpt,
  output wire        tse0_mtx_sof,
  output wire        tse0_mtx_eof,
  output wire [31:0] tse0_mtx_dat,
  output wire [1:0]  tse0_mtx_bytevalid,
  // ---- Port 1 / CORETSE_1 ----
  // MAC RX from CORETSE_1
  input  wire        tse1_mrx_rdy,
  output wire        tse1_mrx_acpt,
  input  wire        tse1_mrx_sof,
  input  wire        tse1_mrx_eof,
  input  wire [31:0] tse1_mrx_dat,
  input  wire [1:0]  tse1_mrx_bytevalid,
  // MAC TX to CORETSE_1
  output wire        tse1_mtx_rdy,
  input  wire        tse1_mtx_acpt,
  output wire        tse1_mtx_sof,
  output wire        tse1_mtx_eof,
  output wire [31:0] tse1_mtx_dat,
  output wire [1:0]  tse1_mtx_bytevalid,

  // ---- debug taps for dbg_apb (request path: CORETSE_0 -> CORETSE_1) ----
  output wire [2:0]  dbg_rf_state,
  output wire [15:0] dbg_rf_data_end,
  output wire [15:0] dbg_rf_pad_end,
  output wire [15:0] dbg_rf_sent,
  output wire [15:0] dbg_rf_fed,
  output wire        dbg_rf_o_rdy,
  output wire        dbg_rf_o_eof,
  output wire        dbg_df_tvalid,
  output wire [15:0] dbg_df_eth_len,  // raw LENGTH deframe extracted (req_len) — vs reframe's data_end

  // ---- failure-theory instrumentation taps (request path) ----
  output wire [15:0] dbg_df_sof_count,   // frames that entered deframe
  output wire [15:0] dbg_df_first_lt,    // L/T of the first frame (sticky)
  output wire [15:0] dbg_df_type_count,  // TYPE frames (L/T > 1500)
  output wire [15:0] dbg_df_trunc_count, // conforming frames ended short (zero-filled)
  output wire [15:0] dbg_rf_tuser_at_sof, // length reframe sampled at the 1st SOF (sticky)
  output wire [15:0] dbg_df_emit_frames,  // frames deframe emitted (passed reject)
  output wire [15:0] dbg_rf_last_fwd_len, // length of last frame reframe completed

  // ---- interlock_tap probes (request path; the [dbg4] accounting chain) ----
  output wire        dbg_il_idle,
  output wire        dbg_il_tick_err,
  output wire [15:0] dbg_il_pkt_done,
  output wire [15:0] dbg_il_pkt_acc,
  output wire [15:0] dbg_il_bytes_fed,
  output wire [31:0] dbg_il_pr_length,
  output wire [15:0] dbg_il_cert_seq,
  output wire [31:0] dbg_il_cert_chk,
  output wire [31:0] dbg_il_cert_b0_3,
  output wire [31:0] dbg_il_cert_b4_7
);

  // Each direction is sanitized at the Ethernet layer:
  //
  //   MAC-RX ─► eth_deframe ─ AXI-Stream ─► eth_reframe ─► MAC-TX
  //
  // eth_deframe strips the header/PAD/FCS and forwards exactly LENGTH octets
  // of DATA; eth_reframe rebuilds the frame with forced addresses, regenerated
  // LENGTH/PAD, and a fresh FCS. canon_core sits between the two in
  // interlock_path — bypassed here for now, so reframe's byte length (tuser,
  // sampled on the first beat) comes from deframe's dbg_eth_len, the live view
  // of the held header register: complete before the first data beat and
  // stable until the next frame's header shifts through. deframe's 1-bit
  // tuser truncation flag has no consumer yet and is left open.
  //
  // Direction naming: requests flow port 0 -> port 1 (client on port 0,
  // server on port 1), responses flow port 1 -> port 0.

  // Forced station addresses: client side .01, server side .02.
  localparam logic [47:0] MAC_CLIENT = 48'h02_00_00_00_00_01;
  localparam logic [47:0] MAC_SERVER = 48'h02_00_00_00_00_02;

  // ====================================================================
  // Requests: CORETSE_0 MAC-RX -> deframe/reframe -> CORETSE_1 MAC-TX
  // ====================================================================
  // deframe -> interlock_tap -> reframe (tap spliced inline on the request path)
  wire        dq_tvalid, dq_tready, dq_tlast;   // deframe -> tap
  wire        rq_tvalid, rq_tready, rq_tlast;   // tap -> reframe
  wire [31:0] dq_tdata, rq_tdata;
  wire [3:0]  dq_tkeep, rq_tkeep;
  wire [15:0] req_len;

  // bucket-tick timer (PROTOTYPE): ~1 ms at 80 MHz. The cocotb cert test drives
  // the tap's bucket_tick directly, so this cadence only matters on silicon.
  localparam int TICK_DIV = 80000;
  reg [16:0] tick_cnt;
  reg        tick_pulse;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin tick_cnt <= '0; tick_pulse <= 1'b0; end
    else if (tick_cnt >= TICK_DIV-1) begin tick_cnt <= '0; tick_pulse <= 1'b1; end
    else begin tick_cnt <= tick_cnt + 1'b1; tick_pulse <= 1'b0; end
  end

  eth_deframe deframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse0_mrx_rdy),
    .in_acpt       (tse0_mrx_acpt),
    .in_sof        (tse0_mrx_sof),
    .in_eof        (tse0_mrx_eof),
    .in_dat        (tse0_mrx_dat),
    .in_bytevalid  (tse0_mrx_bytevalid),
    .tvalid        (dq_tvalid),
    .tready        (dq_tready),
    .tdata         (dq_tdata),
    .tkeep         (dq_tkeep),
    .tlast         (dq_tlast),
    .tuser         (),
    .tlen          (req_len),            // in-band length sideband -> reframe.tuser
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (),
    .dbg_sof_count (dbg_df_sof_count),
    .dbg_first_lt  (dbg_df_first_lt),
    .dbg_type_count(dbg_df_type_count),
    .dbg_trunc_count(dbg_df_trunc_count),
    .dbg_emit_frames(dbg_df_emit_frames)
  );

  // interlock_tap: inline pass-through that also feeds the canonical packet to
  // interlock_core and captures the cert. dbg_* read hierarchically in sim; wired
  // to dbg_apb for silicon. s_dir=0 (request = "in"). Phase A: request path only.
  interlock_tap tap_req (
    .clk        (clk),
    .rst_n      (rst_n),
    .s_tvalid   (dq_tvalid),
    .s_tready   (dq_tready),
    .s_tdata    (dq_tdata),
    .s_tkeep    (dq_tkeep),
    .s_tlast    (dq_tlast),
    .s_dir      (1'b0),
    .m_tvalid   (rq_tvalid),
    .m_tready   (rq_tready),
    .m_tdata    (rq_tdata),
    .m_tkeep    (rq_tkeep),
    .m_tlast    (rq_tlast),
    .bucket_tick(tick_pulse),
    .cert_valid (), .cert_data (), .cert_last (),
    .dbg_idle(dbg_il_idle), .dbg_tick_err(dbg_il_tick_err),
    .dbg_pkt_done(dbg_il_pkt_done), .dbg_pkt_acc(dbg_il_pkt_acc),
    .dbg_bytes_fed(dbg_il_bytes_fed), .dbg_pr_length(dbg_il_pr_length),
    .dbg_cert_seq(dbg_il_cert_seq), .dbg_cert_chk(dbg_il_cert_chk),
    .dbg_cert_b0_3(dbg_il_cert_b0_3), .dbg_cert_b4_7(dbg_il_cert_b4_7)
  );

  eth_reframe #(
    .FORCE_DST (MAC_SERVER),
    .FORCE_SRC (MAC_CLIENT)
  ) reframe_req (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (rq_tvalid),
    .tready        (rq_tready),
    .tdata         (rq_tdata),
    .tkeep         (rq_tkeep),
    .tlast         (rq_tlast),
    .tuser         (req_len),
    .out_rdy       (tse1_mtx_rdy),
    .out_acpt      (tse1_mtx_acpt),
    .out_sof       (tse1_mtx_sof),
    .out_eof       (tse1_mtx_eof),
    .out_dat       (tse1_mtx_dat),
    .out_bytevalid (tse1_mtx_bytevalid),
    .dbg_state     (dbg_rf_state),
    .dbg_data_end  (dbg_rf_data_end),
    .dbg_pad_end   (dbg_rf_pad_end),
    .dbg_sent      (dbg_rf_sent),
    .dbg_fed       (dbg_rf_fed),
    .dbg_o_rdy     (dbg_rf_o_rdy),
    .dbg_o_eof     (dbg_rf_o_eof),
    .dbg_tuser_at_sof (dbg_rf_tuser_at_sof),
    .dbg_last_fwd_len (dbg_rf_last_fwd_len)
  );

  // deframe_req's AXI-valid: does the ingress deframer ever produce output?
  assign dbg_df_tvalid  = dq_tvalid;
  // deframe_req's extracted LENGTH (eth_len_q). Compare to reframe's data_end-14:
  // equal -> deframe capture is the fault; different -> reframe sampled wrong.
  assign dbg_df_eth_len = req_len;

  // ====================================================================
  // Responses: CORETSE_1 MAC-RX -> deframe/reframe -> CORETSE_0 MAC-TX
  // ====================================================================
  wire        rsp_tvalid, rsp_tready, rsp_tlast;
  wire [31:0] rsp_tdata;
  wire [3:0]  rsp_tkeep;
  wire [15:0] rsp_len;

  eth_deframe deframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse1_mrx_rdy),
    .in_acpt       (tse1_mrx_acpt),
    .in_sof        (tse1_mrx_sof),
    .in_eof        (tse1_mrx_eof),
    .in_dat        (tse1_mrx_dat),
    .in_bytevalid  (tse1_mrx_bytevalid),
    .tvalid        (rsp_tvalid),
    .tready        (rsp_tready),
    .tdata         (rsp_tdata),
    .tkeep         (rsp_tkeep),
    .tlast         (rsp_tlast),
    .tuser         (),
    .tlen          (rsp_len),            // in-band length sideband -> reframe.tuser
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   (),
    .dbg_sof_count (),
    .dbg_first_lt  (),
    .dbg_type_count(),
    .dbg_trunc_count(),
    .dbg_emit_frames()
  );

  eth_reframe #(
    .FORCE_DST (MAC_CLIENT),
    .FORCE_SRC (MAC_SERVER)
  ) reframe_rsp (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (rsp_tvalid),
    .tready        (rsp_tready),
    .tdata         (rsp_tdata),
    .tkeep         (rsp_tkeep),
    .tlast         (rsp_tlast),
    .tuser         (rsp_len),
    .out_rdy       (tse0_mtx_rdy),
    .out_acpt      (tse0_mtx_acpt),
    .out_sof       (tse0_mtx_sof),
    .out_eof       (tse0_mtx_eof),
    .out_dat       (tse0_mtx_dat),
    .out_bytevalid (tse0_mtx_bytevalid),
    .dbg_tuser_at_sof (),
    .dbg_last_fwd_len ()
  );

endmodule
