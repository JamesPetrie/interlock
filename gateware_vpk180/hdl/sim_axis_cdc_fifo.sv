// sim_axis_cdc_fifo — SIMULATION-ONLY stand-in for xpm_fifo_axis
// (independent clocks, optional packet mode). The XPM model needs SVA
// support Icarus lacks, so the cocotb benches compile this instead
// (`define SIM_NO_XPM). Not for synthesis: no CDC constraints, plain
// gray-coded pointers with two-flop synchronisers, combinational read.
//
// PACKET_MODE: the write pointer is published to the reader only at tlast,
// so a packet is presented downstream only once it is complete (what the
// MRMAC TX side needs: tvalid must not drop mid-packet).

module sim_axis_cdc_fifo #(
  parameter int unsigned W           = 64,
  parameter int unsigned DEPTH       = 512,   // POWER OF TWO
  parameter bit          PACKET_MODE = 1'b0
) (
  input  wire            s_aclk,
  input  wire            s_aresetn,
  input  wire            s_tvalid,
  output wire            s_tready,
  input  wire [W-1:0]    s_tdata,
  input  wire [W/8-1:0]  s_tkeep,
  input  wire            s_tlast,
  input  wire            s_tuser,

  input  wire            m_aclk,
  input  wire            m_aresetn,
  output wire            m_tvalid,
  input  wire            m_tready,
  output wire [W-1:0]    m_tdata,
  output wire [W/8-1:0]  m_tkeep,
  output wire            m_tlast,
  output wire            m_tuser
);

  localparam int unsigned AW     = $clog2(DEPTH);
  localparam int unsigned BEAT_W = 1 + 1 + W/8 + W;
  typedef logic [AW:0] ptr_t;

  function automatic ptr_t bin2gray(input ptr_t b); return b ^ (b >> 1); endfunction
  function automatic ptr_t gray2bin(input ptr_t g);
    gray2bin = '0;
    for (int k = AW; k >= 0; k--) gray2bin[k] = (k == AW) ? g[k] : (gray2bin[k+1] ^ g[k]);
  endfunction

  logic [BEAT_W-1:0] mem [0:DEPTH-1];

  // pointers (declared up front: the write side samples the reader's gray pointer)
  ptr_t wr_bin, wr_pub_gray;          // write: in-progress pointer, published (committed) pointer
  ptr_t rd_bin, rd_gray_m;            // read: pointer and its gray copy
  ptr_t wr_gray_s1, wr_gray_s2;       // write pointer synchronised into m_aclk

  // ---- write side ----
  ptr_t rd_gray_s1, rd_gray_s2;        // reader pointer synchronised into s_aclk
  wire  ptr_t rd_bin_w = gray2bin(rd_gray_s2);
  wire  full = (wr_bin[AW-1:0] == rd_bin_w[AW-1:0]) && (wr_bin[AW] != rd_bin_w[AW]);
  assign s_tready = !full;
  wire  s_hs = s_tvalid && s_tready;

  always_ff @(posedge s_aclk or negedge s_aresetn) begin
    if (!s_aresetn) begin
      wr_bin      <= '0;
      wr_pub_gray <= '0;
      rd_gray_s1  <= '0;
      rd_gray_s2  <= '0;
    end else begin
      rd_gray_s1 <= rd_gray_m;
      rd_gray_s2 <= rd_gray_s1;
      if (s_hs) begin
        mem[wr_bin[AW-1:0]] <= {s_tuser, s_tlast, s_tkeep, s_tdata};
        wr_bin <= wr_bin + ptr_t'(1);
        if (!PACKET_MODE || s_tlast) wr_pub_gray <= bin2gray(wr_bin + ptr_t'(1));
      end
    end
  end

  // ---- read side ----
  wire  empty = (bin2gray(rd_bin) == wr_gray_s2);
  wire  m_hs  = m_tvalid && m_tready;

  assign m_tvalid = !empty;
  assign {m_tuser, m_tlast, m_tkeep, m_tdata} = mem[rd_bin[AW-1:0]];

  always_ff @(posedge m_aclk or negedge m_aresetn) begin
    if (!m_aresetn) begin
      rd_bin     <= '0;
      rd_gray_m  <= '0;
      wr_gray_s1 <= '0;
      wr_gray_s2 <= '0;
    end else begin
      wr_gray_s1 <= wr_pub_gray;
      wr_gray_s2 <= wr_gray_s1;
      if (m_hs) begin
        rd_bin    <= rd_bin + ptr_t'(1);
        rd_gray_m <= bin2gray(rd_bin + ptr_t'(1));
      end
    end
  end

endmodule
