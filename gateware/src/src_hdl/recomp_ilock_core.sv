// recomp_ilock_core — recomputation interlock core on the Ethernet byte path
// (see docs/recomp_ilock_core.md; structural sibling of fabric_bridge, which
// implements the production core — the CoreTSE MAC client bundles and the
// bucket timer are identical so either can sit in the same SmartDesign).
//
// One challenge direction instead of prod's two:
//
//   port 0 (prover frontend)  ─►  deframe ─► canon_proc ─► gate ─► commit
//     ─► batch_buffer ─► recomp_feed ─► reframe  ─►  port 1 (enclosure)
//
//   port 1 (enclosure)  ─►  deframe ─► recomp_feed estimate port
//
//   cert_build + canon_proc sync  ─►  mux ─► reframe  ─►  port 0
//
// The frontend stages the challenge slice (context packets, the CTRL marker,
// the challenged response) through port 0; the slice is committed and
// buffered exactly like prod's request path, then recomp_feed forwards the
// context to the enclosure, runs the estimate/reveal loop against the
// enclosure's estimates (port 1 return path), and accumulates Û. The Û
// result and the ingress traffic digest feed cert_build; certificates and
// canon_proc's sync packets are the only egress toward the frontend.
//
// TODO the certificate content is a placeholder: cert_build emits the prod
// traffic-cert layout with Û standing in for the response digest. The
// recomp certificate format {nonce, H1_in, H2, H1_out, n_units, U} is
// pinned in the verification protocol doc and needs its own builder.

module recomp_ilock_core
  import canon_pkg::*;
#(
  // bucket period in clk cycles - 1 (tick at timer == TIMER_END); 1 ms at
  // the 80 MHz fabric clock. TBs shrink both of these.
  parameter int unsigned TIMER_END     = 79_999,
  // bucket periods per certificate (one cert per ~s)
  parameter int unsigned BKTS_PER_CERT = 1000
) (
  input  wire        clk,      // fabric clock (CORETSE M*CLK domain; both MACs share it)
  input  wire        rst_n,    // active-low synchronous reset
  // ---- Port 0 / CORETSE_0: prover frontend ----
  // MAC RX from CORETSE_0 (challenge slice in)
  input  wire        tse0_mrx_rdy,
  output wire        tse0_mrx_acpt,
  input  wire        tse0_mrx_sof,
  input  wire        tse0_mrx_eof,
  input  wire [31:0] tse0_mrx_dat,
  input  wire [1:0]  tse0_mrx_bytevalid,
  // MAC TX to CORETSE_0 (certificates + sync out)
  output wire        tse0_mtx_rdy,
  input  wire        tse0_mtx_acpt,
  output wire        tse0_mtx_sof,
  output wire        tse0_mtx_eof,
  output wire [31:0] tse0_mtx_dat,
  output wire [1:0]  tse0_mtx_bytevalid,
  // ---- Port 1 / CORETSE_1: recomputation enclosure ----
  // MAC RX from CORETSE_1 (estimates in)
  input  wire        tse1_mrx_rdy,
  output wire        tse1_mrx_acpt,
  input  wire        tse1_mrx_sof,
  input  wire        tse1_mrx_eof,
  input  wire [31:0] tse1_mrx_dat,
  input  wire [1:0]  tse1_mrx_bytevalid,
  // MAC TX to CORETSE_1 (forwarded context + token reveals out)
  output wire        tse1_mtx_rdy,
  input  wire        tse1_mtx_acpt,
  output wire        tse1_mtx_sof,
  output wire        tse1_mtx_eof,
  output wire [31:0] tse1_mtx_dat,
  output wire [1:0]  tse1_mtx_bytevalid
);

  // Timer
  logic [31:0] timer;
  wire         tick = (timer == TIMER_END) ? 1'b1 : 1'b0;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      timer <= '0;
    end else if (tick) begin
      timer <= '0;
    end else begin
      timer <= timer + 1;
    end
  end

  // Forced station addresses: frontend side .01, enclosure side .02.
  localparam logic [47:0] MAC_FRONTEND = 48'h02_00_00_00_00_01;
  localparam logic [47:0] MAC_COMPUTE  = 48'h02_00_00_00_00_02;

  // ====================================================================
  // Challenge ingress: CORETSE_0 MAC-RX -> deframe -> canon_proc
  // ====================================================================
  wire        chl_tvalid_i, chl_tready_i, chl_tlast_i;
  wire [31:0] chl_tdata_i;
  wire [3:0]  chl_tkeep_i;
  wire        chl_tuser_i;

  eth_deframe deframe_chl (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse0_mrx_rdy),
    .in_acpt       (tse0_mrx_acpt),
    .in_sof        (tse0_mrx_sof),
    .in_eof        (tse0_mrx_eof),
    .in_dat        (tse0_mrx_dat),
    .in_bytevalid  (tse0_mrx_bytevalid),
    .tvalid        (chl_tvalid_i),
    .tready        (chl_tready_i),
    .tdata         (chl_tdata_i),
    .tkeep         (chl_tkeep_i),
    .tlast         (chl_tlast_i),
    .tuser         (chl_tuser_i),
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   ()
  );

  // canon_proc sync packets, spliced into the certificate egress mux —
  // feedback toward the frontend
  wire        sync_tvalid, sync_tready, sync_tlast;
  wire [31:0] sync_tdata;
  wire [3:0]  sync_tkeep;
  wire [15:0] sync_tuser;

  wire         chl_tvalid_cp2pg, chl_tready_cp2pg, chl_tlast_cp2pg;
  wire [31:0]  chl_tdata_cp2pg;
  wire [3:0]   chl_tkeep_cp2pg;
  wire [15:0]  chl_tuser_cp2pg;
  wire [127:0] cert_nonce;

  canon_proc #(
    .DIR         (CANON_DIR_REQ), // use REQ for nonce support
    .CHK_CONTENT (1'b0)   // replayed traffic: integrity checks only, and the
                          // ID=0 CTRL marker must reach recomp_feed
  ) canon_proc_chl (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (chl_tvalid_i),
    .tready_s (chl_tready_i),
    .tdata_s  (chl_tdata_i),
    .tkeep_s  (chl_tkeep_i),
    .tlast_s  (chl_tlast_i),
    .tuser_s  (chl_tuser_i),
    .tvalid_m (chl_tvalid_cp2pg),
    .tready_m (chl_tready_cp2pg),
    .tdata_m  (chl_tdata_cp2pg),
    .tkeep_m  (chl_tkeep_cp2pg),
    .tlast_m  (chl_tlast_cp2pg),
    .tuser_m  (chl_tuser_cp2pg),
    .nonce    (cert_nonce),
    .tvalid_sync (sync_tvalid),
    .tready_sync (sync_tready),
    .tdata_sync  (sync_tdata),
    .tkeep_sync  (sync_tkeep),
    .tlast_sync  (sync_tlast),
    .tuser_sync  (sync_tuser),
    .tick     (tick),
    .timer    (timer)
  );

  // ====================================================================
  // canon_proc -> drop gate -> traffic_commit -> batch_buffer
  // ====================================================================
  wire         chl_tvalid_pg2tc, chl_tready_pg2tc, chl_tlast_pg2tc;
  wire [31:0]  chl_tdata_pg2tc;
  wire [3:0]   chl_tkeep_pg2tc;
  wire [15:0]  chl_tuser_pg2tc;

  axis_pkt_gate drop_gate_chl (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (chl_tvalid_cp2pg),
    .tready_s (chl_tready_cp2pg),
    .tdata_s  (chl_tdata_cp2pg),
    .tkeep_s  (chl_tkeep_cp2pg),
    .tlast_s  (chl_tlast_cp2pg),
    .tuser_s  (chl_tuser_cp2pg),
    .tvalid_m (chl_tvalid_pg2tc),
    .tready_m (chl_tready_pg2tc),
    .tdata_m  (chl_tdata_pg2tc),
    .tkeep_m  (chl_tkeep_pg2tc),
    .tlast_m  (chl_tlast_pg2tc),
    .tuser_m  (chl_tuser_pg2tc)
  );

  wire         chl_tvalid_tc2bb, chl_tready_tc2bb, chl_tlast_tc2bb;
  wire [31:0]  chl_tdata_tc2bb;
  wire [3:0]   chl_tkeep_tc2bb;
  wire [15:0]  chl_tuser_tc2bb;
  wire         chl_ovr_valid;
  wire [255:0] chl_ovr_digest;

  traffic_commit #(
    .HDR_BYTES   (CANON_REQ_HDR_BYTES),
    .NUM_BUCKETS (BKTS_PER_CERT),
    .OUTPUT_SWAP (1)
  ) commit_chl (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (chl_tvalid_pg2tc),
    .tready_s (chl_tready_pg2tc),
    .tdata_s  (chl_tdata_pg2tc),
    .tkeep_s  (chl_tkeep_pg2tc),
    .tlast_s  (chl_tlast_pg2tc),
    .tuser_s  (chl_tuser_pg2tc),
    .tvalid_m (chl_tvalid_tc2bb),
    .tready_m (chl_tready_tc2bb),
    .tdata_m  (chl_tdata_tc2bb),
    .tkeep_m  (chl_tkeep_tc2bb),
    .tlast_m  (chl_tlast_tc2bb),
    .tuser_m  (chl_tuser_tc2bb),
    .overall_valid (chl_ovr_valid),
    .overall       (chl_ovr_digest)
  );

  // no OUTPUT_SWAP delimiter toward recomp_feed: the feed identifies the
  // challenged response by the CTRL marker, not by batch boundaries
  wire         chl_tvalid_bb2rf, chl_tready_bb2rf, chl_tlast_bb2rf;
  wire [31:0]  chl_tdata_bb2rf;
  wire [3:0]   chl_tkeep_bb2rf;
  wire [15:0]  chl_tuser_bb2rf;

  batch_buffer #(
    .GRACE_PERIOD (2000),  // matches prod's drop-gated ingress
    .OUTPUT_SWAP  (0)
  ) buffer_chl (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (chl_tvalid_tc2bb),
    .tready_s (chl_tready_tc2bb),
    .tdata_s  (chl_tdata_tc2bb),
    .tkeep_s  (chl_tkeep_tc2bb),
    .tlast_s  (chl_tlast_tc2bb),
    .tuser_s  (chl_tuser_tc2bb),
    .tvalid_m (chl_tvalid_bb2rf),
    .tready_m (chl_tready_bb2rf),
    .tdata_m  (chl_tdata_bb2rf),
    .tkeep_m  (chl_tkeep_bb2rf),
    .tlast_m  (chl_tlast_bb2rf),
    .tuser_m  (chl_tuser_bb2rf),
    .tick     (tick),
    .timer    (timer)
  );

  // ====================================================================
  // Estimate return path: CORETSE_1 MAC-RX -> deframe -> recomp_feed
  // ====================================================================
  wire        est_tvalid, est_tready, est_tlast;
  wire [31:0] est_tdata;
  wire [3:0]  est_tkeep;

  eth_deframe deframe_est (
    .clk           (clk),
    .rst_n         (rst_n),
    .in_rdy        (tse1_mrx_rdy),
    .in_acpt       (tse1_mrx_acpt),
    .in_sof        (tse1_mrx_sof),
    .in_eof        (tse1_mrx_eof),
    .in_dat        (tse1_mrx_dat),
    .in_bytevalid  (tse1_mrx_bytevalid),
    .tvalid        (est_tvalid),
    .tready        (est_tready),
    .tdata         (est_tdata),
    .tkeep         (est_tkeep),
    .tlast         (est_tlast),
    .tuser         (),            // truncation flag unused (feed charges
                                  //   malformed frames PROB_MIN regardless)
    .dbg_hdr_valid (),
    .dbg_eth_dst   (),
    .dbg_eth_src   (),
    .dbg_eth_len   ()
  );

  // ====================================================================
  // recomp_feed: forward context, run the estimate/reveal loop, emit Û
  // ====================================================================
  wire        fwd_tvalid, fwd_tready, fwd_tlast;
  wire [31:0] fwd_tdata;
  wire [3:0]  fwd_tkeep;
  wire [15:0] fwd_tuser;
  wire        recomp_valid;
  wire [63:0] id_val;
  wire [63:0] u_val;

  recomp_feed u_feed (
    .clk      (clk),
    .rst_n    (rst_n),
    .tvalid_s (chl_tvalid_bb2rf),
    .tready_s (chl_tready_bb2rf),
    .tdata_s  (chl_tdata_bb2rf),
    .tkeep_s  (chl_tkeep_bb2rf),
    .tlast_s  (chl_tlast_bb2rf),
    .tuser_s  (chl_tuser_bb2rf),
    .tvalid_m (fwd_tvalid),
    .tready_m (fwd_tready),
    .tdata_m  (fwd_tdata),
    .tkeep_m  (fwd_tkeep),
    .tlast_m  (fwd_tlast),
    .tuser_m  (fwd_tuser),
    .tvalid_e (est_tvalid),
    .tready_e (est_tready),
    .tdata_e  (est_tdata),
    .tkeep_e  (est_tkeep),
    .tlast_e  (est_tlast),
    .out_valid (recomp_valid),
    .id_out    (id_val),
    .u_out     (u_val)
  );

  eth_reframe #(
    .FORCE_DST (MAC_COMPUTE),
    .FORCE_SRC (MAC_FRONTEND)
  ) reframe_fwd (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (fwd_tvalid),
    .tready        (fwd_tready),
    .tdata         (fwd_tdata),
    .tkeep         (fwd_tkeep),
    .tlast         (fwd_tlast),
    .tuser         (fwd_tuser),
    .out_rdy       (tse1_mtx_rdy),
    .out_acpt      (tse1_mtx_acpt),
    .out_sof       (tse1_mtx_sof),
    .out_eof       (tse1_mtx_eof),
    .out_dat       (tse1_mtx_dat),
    .out_bytevalid (tse1_mtx_bytevalid)
  );

  // ====================================================================
  // Certificate egress: cert_build + sync -> mux -> reframe -> CORETSE_0
  // ====================================================================
  wire        c_tvalid, c_tready, c_tlast;
  wire [31:0] c_tdata;
  wire [3:0]  c_tkeep;
  wire [15:0] c_tuser;

  // TODO placeholder: prod traffic-cert layout with Û standing in for the
  // response-direction digest (see module header). RSP_SYNC = 0: certs follow
  // the ingress digest cadence and carry the last-dispatched Û (zero before
  // the first challenge, stale between challenges).
  cert_build #(
    .NUM_BUCKETS (BKTS_PER_CERT),
    .RSP_SYNC    (1'b0)
  ) u_cert (
    .clk (clk), .rst_n (rst_n), .key (256'd99),
    .in_valid_req (chl_ovr_valid), .in_overall_req (chl_ovr_digest),
    .in_valid_rsp (recomp_valid),  .in_overall_rsp (256'({id_val, u_val})),
    .in_nonce (cert_nonce),
    .c_valid (c_tvalid), .c_ready (c_tready), .c_data (c_tdata),
    .c_keep (c_tkeep), .c_last (c_tlast), .c_user (c_tuser)
  );

  wire        mrg_tvalid, mrg_tready, mrg_tlast;
  wire [31:0] mrg_tdata;
  wire [3:0]  mrg_tkeep;
  wire [15:0] mrg_tuser;

  // 2×1 in the doc diagram: certificates (default grant) + sync packets
  // (priority); the middle input is tied off
  axis_mux3 u_cert_out_merge (
    .clk       (clk),
    .rst_n     (rst_n),
    .tvalid_s0 (c_tvalid),
    .tready_s0 (c_tready),
    .tdata_s0  (c_tdata),
    .tkeep_s0  (c_tkeep),
    .tlast_s0  (c_tlast),
    .tuser_s0  (c_tuser),
    .tvalid_s1 (1'b0),
    .tready_s1 (),
    .tdata_s1  (),
    .tkeep_s1  (),
    .tlast_s1  (),
    .tuser_s1  (),
    .tvalid_s2 (sync_tvalid),
    .tready_s2 (sync_tready),
    .tdata_s2  (sync_tdata),
    .tkeep_s2  (sync_tkeep),
    .tlast_s2  (sync_tlast),
    .tuser_s2  (sync_tuser),
    .tvalid_m  (mrg_tvalid),
    .tready_m  (mrg_tready),
    .tdata_m   (mrg_tdata),
    .tkeep_m   (mrg_tkeep),
    .tlast_m   (mrg_tlast),
    .tuser_m   (mrg_tuser)
  );

  eth_reframe #(
    .FORCE_DST (MAC_FRONTEND),
    .FORCE_SRC (MAC_COMPUTE)
  ) reframe_cert (
    .clk           (clk),
    .rst_n         (rst_n),
    .tvalid        (mrg_tvalid),
    .tready        (mrg_tready),
    .tdata         (mrg_tdata),
    .tkeep         (mrg_tkeep),
    .tlast         (mrg_tlast),
    .tuser         (mrg_tuser),
    .out_rdy       (tse0_mtx_rdy),
    .out_acpt      (tse0_mtx_acpt),
    .out_sof       (tse0_mtx_sof),
    .out_eof       (tse0_mtx_eof),
    .out_dat       (tse0_mtx_dat),
    .out_bytevalid (tse0_mtx_bytevalid)
  );

endmodule
