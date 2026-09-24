// axis_upsize — AXI-Stream width up-converter, 32 -> W bits, in wire order
// (input word i of a beat lands in lanes [32*i +: 32]).
//
// Words accumulate until the beat is full or tlast arrives; the beat is then
// handed to a one-deep output register. Unfilled lanes of a partial last beat
// carry tkeep == 0 and zero data. tuser (1-bit error flag at tlast) is passed
// through on the beat that carries tlast.

module axis_upsize #(
  parameter int unsigned W = 64             // output width, multiple of 32
) (
  input  wire            clk,
  input  wire            rst_n,
  // slave (32-bit)
  input  wire            s_tvalid,
  output wire            s_tready,
  input  wire [31:0]     s_tdata,
  input  wire [3:0]      s_tkeep,
  input  wire            s_tlast,
  input  wire            s_tuser,
  // master (wide)
  output wire            m_tvalid,
  input  wire            m_tready,
  output wire [W-1:0]    m_tdata,
  output wire [W/8-1:0]  m_tkeep,
  output wire            m_tlast,
  output wire            m_tuser
);

  localparam int unsigned R      = W / 32;
  localparam int unsigned LANE_W = (R > 1) ? $clog2(R) : 1;
  typedef logic [LANE_W-1:0] lane_t;

  // accumulator
  logic [W-1:0]   acc_data;
  logic [W/8-1:0] acc_keep;
  lane_t          lane;

  // output register
  logic           o_valid, o_last, o_user;
  logic [W-1:0]   o_data;
  logic [W/8-1:0] o_keep;

  assign m_tvalid = o_valid;
  assign m_tdata  = o_data;
  assign m_tkeep  = o_keep;
  assign m_tlast  = o_last;
  assign m_tuser  = o_user;

  wire out_free = !o_valid || m_tready;
  assign s_tready = out_free;
  wire s_hs      = s_tvalid && s_tready;
  wire lane_full = (lane == lane_t'(R - 1));

  // accumulator with the incoming word merged in
  logic [W-1:0]   nx_data;
  logic [W/8-1:0] nx_keep;
  always_comb begin
    nx_data = acc_data;
    nx_keep = acc_keep;
    nx_data[32*lane +: 32] = s_tdata;
    nx_keep[4*lane +: 4]   = s_tkeep;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc_data <= '0;
      acc_keep <= '0;
      lane     <= '0;
      o_valid  <= 1'b0;
      o_last   <= 1'b0;
      o_user   <= 1'b0;
      o_data   <= '0;
      o_keep   <= '0;
    end else begin
      if (o_valid && m_tready) o_valid <= 1'b0;
      if (s_hs) begin
        if (s_tlast || lane_full) begin
          o_valid  <= 1'b1;
          o_data   <= nx_data;
          o_keep   <= nx_keep;
          o_last   <= s_tlast;
          o_user   <= s_tuser;
          acc_data <= '0;
          acc_keep <= '0;
          lane     <= '0;
        end else begin
          acc_data <= nx_data;
          acc_keep <= nx_keep;
          lane     <= lane + 1'b1;
        end
      end
    end
  end

endmodule
