// axis2tse — 32-bit AXI-Stream in, CoreTSE MAC-RX-shaped word stream out.
//
// The MAC-RX bundle (see fabric_bridge.sv) needs EOF on the last word and
// BYTEVALID = number of INVALID lanes on that word. AXI-Stream gives tlast on
// the last beat, so one beat of look-ahead is enough, and it also lets a
// null last beat (tlast with tkeep == 0) be folded into the word before it.
//
//   held word is presented when its EOF status is known:
//     h_last                    -> EOF, no look-ahead needed
//     next beat present         -> EOF iff that beat is a null last beat
//
// Words are always full (tkeep == 4'b1111) except the last one, whose tkeep
// is contiguous from bit 0 — the MAC/downsizer guarantee both.

module axis2tse (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave
  input  wire        tvalid,
  output wire        tready,
  input  wire [31:0] tdata,
  input  wire [3:0]  tkeep,
  input  wire        tlast,

  // MAC-RX-shaped master (we drive data, sink drives accept)
  output wire        out_rdy,
  input  wire        out_acpt,
  output wire        out_sof,
  output wire        out_eof,
  output wire [31:0] out_dat,
  output wire [1:0]  out_bytevalid    // count of INVALID bytes, last word only
);

  function automatic logic [1:0] keep2bv(input logic [3:0] k);
    case (k)
      4'b0111: keep2bv = 2'd1;
      4'b0011: keep2bv = 2'd2;
      4'b0001: keep2bv = 2'd3;
      default: keep2bv = 2'd0;
    endcase
  endfunction

  logic        h_valid, h_last;
  logic [31:0] h_data;
  logic [3:0]  h_keep;
  logic        sof_pending;

  wire in_null_last = tvalid && tlast && (tkeep == 4'b0000);
  wire eof_now      = h_last || in_null_last;

  assign out_rdy       = h_valid && (h_last || tvalid);
  assign out_sof       = sof_pending;
  assign out_eof       = eof_now;
  assign out_dat       = h_data;
  assign out_bytevalid = eof_now ? keep2bv(h_keep) : 2'd0;

  wire out_hs = out_rdy && out_acpt;
  // take the next beat when nothing is held, or when the held word leaves now
  assign tready = !h_valid || out_hs;
  wire in_hs = tvalid && tready;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      h_valid     <= 1'b0;
      h_last      <= 1'b0;
      h_data      <= '0;
      h_keep      <= '0;
      sof_pending <= 1'b1;
    end else begin
      if (out_hs) begin
        h_valid     <= 1'b0;
        sof_pending <= eof_now;      // next word opens a frame iff this closed one
      end
      // a null last beat is swallowed: it closed the held word above (or was
      // an empty packet); anything else becomes the new held word
      if (in_hs && !in_null_last) begin
        h_valid <= 1'b1;
        h_data  <= tdata;
        h_keep  <= tkeep;
        h_last  <= tlast;
      end
    end
  end

endmodule
