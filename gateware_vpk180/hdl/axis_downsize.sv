// axis_downsize — AXI-Stream width down-converter, W -> 32 bits, in wire
// order (lane 0 = the first bytes of the beat; MRMAC and CoreTSE both put
// the first wire byte in bits [7:0]).
//
// One input beat is buffered and emitted lane by lane. Lanes after the last
// non-empty one (tkeep nibble == 0) are skipped, so a partial last beat costs
// only as many output beats as it carries data words. A null last beat (tlast
// with tkeep == 0, which a MAC may emit) passes through as one null 32-bit
// beat (tkeep == 0, tlast == 1); axis2tse closes the packet on the previous
// word when it sees one.
//
// tuser is a 1-bit error flag qualified at tlast (MRMAC tuser_err convention)
// and is replicated onto the output beat that carries tlast.

module axis_downsize #(
  parameter int unsigned W = 64             // input width, multiple of 32
) (
  input  wire            clk,
  input  wire            rst_n,
  // slave (wide)
  input  wire            s_tvalid,
  output wire            s_tready,
  input  wire [W-1:0]    s_tdata,
  input  wire [W/8-1:0]  s_tkeep,
  input  wire            s_tlast,
  input  wire            s_tuser,
  // master (32-bit)
  output wire            m_tvalid,
  input  wire            m_tready,
  output wire [31:0]     m_tdata,
  output wire [3:0]      m_tkeep,
  output wire            m_tlast,
  output wire            m_tuser
);

  localparam int unsigned R      = W / 32;
  localparam int unsigned LANE_W = (R > 1) ? $clog2(R) : 1;
  typedef logic [LANE_W-1:0] lane_t;

  // index of the last lane carrying data (0 for a null beat)
  function automatic lane_t last_lane(input logic [W/8-1:0] keep);
    last_lane = '0;
    for (int k = 0; k < R; k++)
      if (keep[4*k +: 4] != 4'b0000) last_lane = lane_t'(k);
  endfunction

  logic           b_valid, b_last, b_user;
  logic [W-1:0]   b_data;
  logic [W/8-1:0] b_keep;
  lane_t          lane;

  wire lane_t b_last_lane  = last_lane(b_keep);
  wire        at_last_lane = (lane == b_last_lane);
  wire        m_hs         = m_tvalid && m_tready;

  assign m_tvalid = b_valid;
  assign m_tdata  = b_data[32*lane +: 32];
  assign m_tkeep  = b_keep[4*lane +: 4];
  assign m_tlast  = b_last && at_last_lane;
  assign m_tuser  = b_user && m_tlast;
  // accept a new beat when the buffer is empty or draining its last lane now
  assign s_tready = !b_valid || (m_hs && at_last_lane);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      b_valid <= 1'b0;
      b_last  <= 1'b0;
      b_user  <= 1'b0;
      b_data  <= '0;
      b_keep  <= '0;
      lane    <= '0;
    end else begin
      if (m_hs) begin
        if (at_last_lane) begin
          b_valid <= 1'b0;
          lane    <= '0;
        end else begin
          lane <= lane + 1'b1;
        end
      end
      // load after the drain above so a same-cycle reload wins
      if (s_tvalid && s_tready) begin
        b_valid <= 1'b1;
        b_data  <= s_tdata;
        b_keep  <= s_tkeep;
        b_last  <= s_tlast;
        b_user  <= s_tuser;
        lane    <= '0;
      end
    end
  end

endmodule
