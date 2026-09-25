// axis_pkt_fifo — store-and-forward packet FIFO for a MAC receive stream
// that cannot be back-pressured (MRMAC RX has no tready).
//
// Whole packets only: a packet becomes visible downstream once its tlast is
// stored. Two drop rules keep the output a clean sequence of complete, good
// frames, which is what eth_deframe assumes of its MAC:
//   * overflow — if the FIFO fills mid-packet, everything written for that
//     packet is abandoned and the rest of it is discarded (drop_ovf pulses);
//   * error    — a packet whose tlast carries tuser (MRMAC tuser_err: bad
//     FCS, length, etc.) is abandoned at tlast (drop_err pulses).
// A packet longer than DEPTH beats trips the overflow rule by construction.
//
// Structure follows axis_pkt_gate (fill pointer / commit pointer / drain with
// a fetch register in front of the output register).

module axis_pkt_fifo #(
  parameter int unsigned W     = 64,      // data width, multiple of 8
  parameter int unsigned DEPTH = 1024     // beats, POWER OF TWO
) (
  input  wire            clk,
  input  wire            rst_n,

  // slave — always accepting; s_tready is provided for interface symmetry
  input  wire            s_tvalid,
  output wire            s_tready,
  input  wire [W-1:0]    s_tdata,
  input  wire [W/8-1:0]  s_tkeep,
  input  wire            s_tlast,
  input  wire            s_tuser,      // error flag, meaningful at tlast

  // master — complete, error-free packets
  output wire            m_tvalid,
  input  wire            m_tready,
  output wire [W-1:0]    m_tdata,
  output wire [W/8-1:0]  m_tkeep,
  output wire            m_tlast,
  output wire            m_tuser,      // always 0 (errored packets are dropped)

  // statistics — one-cycle pulses
  output logic           drop_ovf,
  output logic           drop_err
);

  localparam int unsigned ADDR_W = $clog2(DEPTH);
  localparam int unsigned BEAT_W = 1 + W/8 + W;        // {tlast, tkeep, tdata}
  typedef logic [ADDR_W:0] ptr_t;                       // one wrap bit above the address

  logic [BEAT_W-1:0] mem [0:DEPTH-1];

  ptr_t wr_ptr, wr_cmt, rd_ptr;
  logic dropping;

  wire ptr_t occupancy = wr_ptr - rd_ptr;
  wire       full      = (occupancy == ptr_t'(DEPTH));

  assign s_tready = 1'b1;
  assign m_tuser  = 1'b0;

  // ------------------------------------------------------------------
  // Drain — fetch register (registered RAM read) + output register
  // ------------------------------------------------------------------
  logic [BEAT_W-1:0] rd_data;
  logic              rd_valid;

  logic           tvalid_r, tlast_r;
  logic [W-1:0]   tdata_r;
  logic [W/8-1:0] tkeep_r;
  assign m_tvalid = tvalid_r;
  assign m_tdata  = tdata_r;
  assign m_tkeep  = tkeep_r;
  assign m_tlast  = tlast_r;

  wire out_free = !tvalid_r || m_tready;

  // ------------------------------------------------------------------
  // Storage — no reset on the array or the read register so that synthesis
  // maps it to block RAM (an async-reset process is not BRAM-inferable)
  // ------------------------------------------------------------------
  wire wr_en = s_tvalid && !dropping && !full;
  wire rd_en = (!rd_valid || out_free) && (rd_ptr != wr_cmt);
  always_ff @(posedge clk) begin
    if (wr_en) mem[wr_ptr[ADDR_W-1:0]] <= {s_tlast, s_tkeep, s_tdata};
    if (rd_en) rd_data <= mem[rd_ptr[ADDR_W-1:0]];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr   <= '0;
      wr_cmt   <= '0;
      rd_ptr   <= '0;
      dropping <= 1'b0;
      drop_ovf <= 1'b0;
      drop_err <= 1'b0;
      rd_valid <= 1'b0;
      tvalid_r <= 1'b0;
      tlast_r  <= 1'b0;
      tdata_r  <= '0;
      tkeep_r  <= '0;
    end else begin
      drop_ovf <= 1'b0;
      drop_err <= 1'b0;

      // ============================== fill ==============================
      if (s_tvalid) begin
        if (dropping) begin
          if (s_tlast) dropping <= 1'b0;
        end else if (full) begin
          wr_ptr   <= wr_cmt;                    // abandon what was written
          drop_ovf <= 1'b1;
          if (!s_tlast) dropping <= 1'b1;        // and discard the remainder
        end else begin                           // stored by the storage process (wr_en)
          wr_ptr <= wr_ptr + ptr_t'(1);
          if (s_tlast) begin
            if (s_tuser) begin
              wr_ptr   <= wr_cmt;                // errored: abandon
              drop_err <= 1'b1;
            end else begin
              wr_cmt <= wr_ptr + ptr_t'(1);      // good: commit
            end
          end
        end
      end

      // ============================== drain =============================
      if (out_free) begin
        tvalid_r <= rd_valid;
        if (rd_valid) {tlast_r, tkeep_r, tdata_r} <= rd_data;
      end
      if (!rd_valid || out_free) begin
        if (rd_ptr != wr_cmt) begin              // fetched by the storage process (rd_en)
          rd_ptr   <= rd_ptr + ptr_t'(1);
          rd_valid <= 1'b1;
        end else begin
          rd_valid <= 1'b0;
        end
      end
    end
  end

endmodule
