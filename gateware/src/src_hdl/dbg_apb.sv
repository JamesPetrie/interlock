// dbg_apb — minimal READ-ONLY APB3 slave that exposes fabric debug signals to
// the Mi-V CPU for readback over UART. Sits on CoreAPB3 slot 4 (base
// 0x60004000). The fabric continuously drives the *_in signals onto this
// slave's register reads; the firmware polls the registers and prints them.
// This is the reusable on-silicon debug channel: to watch a new signal later,
// wire it to a free bit/register here and add a printf in the firmware.
//
// Read-only: writes are ignored; PREADY is always high (zero wait states);
// PSLVERR is tied low. PRDATA is a pure combinational mux of PADDR.
//
// Register map (word offset from 0x60004000):
//   0x00  {fed[15:0],      data_end[15:0]}
//   0x04  {sent[15:0],     pad_end[15:0]}
//   0x08  bit[5]=df_tvalid bit[4]=o_eof bit[3]=o_rdy bits[2:0]=state
//   0x0C  frame_count[31:0]   completed reframe frames (rf_o_eof rising edges)
//   0x10  {16'h0,           df_eth_len[15:0]}   raw deframe LENGTH (req_len)
//   0x14  {df_trunc_count,  df_type_count}      zero-filled | rejected(TYPE) counts
//   0x18  {df_sof_count,    df_first_lt}        frames-in + first L/T (sticky)
//   0x1C  {16'h0,           rf_tuser_at_sof}    length reframe sampled at 1st SOF
//   0x20  {df_emit_frames,  rf_last_fwd_len}    deframe-emitted count | last fwd length
module dbg_apb (
  // APB3 slave bus (PADDR/PSEL/PENABLE/PWRITE/PWDATA/PRDATA/PREADY/PSLVERR)
  input  wire        PCLK,
  input  wire        PRESETN,
  input  wire        PSEL,
  input  wire        PENABLE,
  input  wire        PWRITE,
  input  wire [31:0] PADDR,
  input  wire [31:0] PWDATA,
  output reg  [31:0] PRDATA,
  output wire        PREADY,
  output wire        PSLVERR,

  // ---- fabric debug taps (request-path reframe + deframe) ----
  input  wire [2:0]  rf_state,
  input  wire [15:0] rf_data_end,
  input  wire [15:0] rf_pad_end,
  input  wire [15:0] rf_sent,
  input  wire [15:0] rf_fed,
  input  wire        rf_o_rdy,
  input  wire        rf_o_eof,
  input  wire        df_tvalid,
  input  wire [15:0] df_eth_len,  // raw LENGTH deframe extracted (= req_len)

  // ---- failure-theory instrumentation taps (see eth_deframe/eth_reframe) ----
  input  wire [15:0] df_sof_count,    // frames that entered deframe
  input  wire [15:0] df_first_lt,     // L/T of the first frame (sticky)
  input  wire [15:0] df_type_count,   // TYPE frames (L/T > 1500) -> rejected
  input  wire [15:0] df_trunc_count,  // conforming short frames (zero-filled)
  input  wire [15:0] rf_tuser_at_sof, // length reframe sampled at the 1st SOF
  input  wire [15:0] df_emit_frames,  // frames deframe emitted (passed reject)
  input  wire [15:0] rf_last_fwd_len  // length of last frame reframe completed
);

  assign PREADY  = 1'b1;   // zero wait states
  assign PSLVERR = 1'b0;   // never errors

  // completed-frame counter: count rising edges of reframe's o_eof so a slow
  // firmware poll still sees whether ANY frame ever completed.
  logic        eof_d;
  logic [31:0] frame_count;
  always_ff @(posedge PCLK or negedge PRESETN) begin
    if (!PRESETN) begin
      eof_d       <= 1'b0;
      frame_count <= 32'd0;
    end else begin
      eof_d <= rf_o_eof;
      if (rf_o_eof && !eof_d) frame_count <= frame_count + 32'd1;
    end
  end

  // read mux (word select = PADDR[5:2]); via an explicit wire to avoid any
  // part-select-in-process ambiguity (the very class of bug this channel hunts).
  wire [3:0] wsel = PADDR[5:2];
  always_comb begin
    case (wsel)
      4'd0:    PRDATA = {rf_fed,  rf_data_end};
      4'd1:    PRDATA = {rf_sent, rf_pad_end};
      4'd2:    PRDATA = {26'h0, df_tvalid, rf_o_eof, rf_o_rdy, rf_state};  // [5]=dfTvld [4]=oEof [3]=oRdy [2:0]=state
      4'd3:    PRDATA = frame_count;
      4'd4:    PRDATA = {16'h0, df_eth_len};             // 0x10: raw deframe LENGTH (req_len)
      4'd5:    PRDATA = {df_trunc_count, df_type_count};  // 0x14: zerofill | reject(type) counts
      4'd6:    PRDATA = {df_sof_count,   df_first_lt};    // 0x18: frames-in | first L/T
      4'd7:    PRDATA = {16'h0, rf_tuser_at_sof};         // 0x1C: reframe length @ 1st SOF
      4'd8:    PRDATA = {df_emit_frames, rf_last_fwd_len};// 0x20: deframe-emitted | last fwd len
      default: PRDATA = 32'hDEAD_0000;
    endcase
  end

endmodule
