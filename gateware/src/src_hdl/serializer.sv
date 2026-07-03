// serializer — parallel byte vector -> 32-bit AXI-Stream byte stream.

module serializer
  import sha256_pkg::swap32;
#(
  parameter int unsigned MAX_BYTES = 34
) (
  input  wire        clk,
  input  wire        rst_n,

  input  wire                  in_valid,
  output wire                  in_ready,
  input  wire [MAX_BYTES*8-1:0] in_data,    // byte 0 in the MSBs
  input  wire [$clog2(MAX_BYTES+1)-1:0] in_bytes,
  input  wire                  in_last,     // this element closes the hash message

  output wire        out_valid,
  output wire [31:0] out_data,              // byte 0 in [7:0]
  input  wire        out_ready,
  output wire [2:0]  out_bytes,
  output wire        out_last
);

  localparam int unsigned MAX_BITS = MAX_BYTES * 8;
  localparam int unsigned BW       = $clog2(MAX_BYTES + 1);

  logic [MAX_BITS-1:0] buf_q;
  logic [BW-1:0]       rem;        // bytes left to shift out
  logic                elast;      // current element closes the message
  logic                busy;

  assign in_ready  = !busy;
  assign out_valid = busy;
  assign out_data  = swap32(buf_q[MAX_BITS-1 -: 32]);
  assign out_bytes = (rem >= BW'(4)) ? 3'd4 : 3'(rem);
  assign out_last  = elast && (rem <= BW'(4));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      buf_q <= '0;
      rem   <= '0;
      elast <= 1'b0;
      busy  <= 1'b0;
    end else if (!busy) begin
      if (in_valid) begin
        buf_q <= in_data;
        rem   <= in_bytes;
        elast <= in_last;
        busy  <= 1'b1;
      end
    end else if (out_ready) begin
      if (rem <= BW'(4)) begin
        busy <= 1'b0;                 // element drained; ready for the next
      end else begin
        buf_q <= buf_q << 32;
        rem   <= rem - BW'(4);
      end
    end
  end

endmodule
