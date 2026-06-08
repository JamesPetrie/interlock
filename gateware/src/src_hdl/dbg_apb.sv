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
  input  wire [15:0] df_eth_len   // raw LENGTH deframe extracted (= req_len)
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

  // read mux (word select = PADDR[3:2]); via an explicit wire to avoid any
  // part-select-in-process ambiguity (the very class of bug this channel hunts).
  wire [2:0] wsel = PADDR[4:2];
  always_comb begin
    case (wsel)
      3'd0:    PRDATA = {rf_fed,  rf_data_end};
      3'd1:    PRDATA = {rf_sent, rf_pad_end};
      3'd2:    PRDATA = {26'h0, df_tvalid, rf_o_eof, rf_o_rdy, rf_state};  // [5]=dfTvld [4]=oEof [3]=oRdy [2:0]=state
      3'd3:    PRDATA = frame_count;
      3'd4:    PRDATA = {16'h0, df_eth_len};  // 0x10: raw deframe LENGTH (req_len)
      default: PRDATA = 32'hDEAD_0000;
    endcase
  end

endmodule
