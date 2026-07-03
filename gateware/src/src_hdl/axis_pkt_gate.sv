// axis_pkt_gate — store-and-forward packet drop gate.

module axis_pkt_gate #(
  // FIFO depth in beats. POWER OF TWO (the pointer wrap relies on it).
  // Must fit the packet being written on top of a committed max packet the
  // drain is still replaying: two ETH_LEN_MAX packets = 2 x 375 words,
  // rounded up.
  parameter int unsigned DEPTH_WORDS = 1024
) (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave
  // (tuser = total byte length at beat #0, drop flag (bit 0) at tlast)
  input  wire        tvalid_s,
  output wire        tready_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire [15:0] tuser_s,

  // AXI-Stream master — verbatim replay of the committed packets
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m
);

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned ADDR_W = $clog2(DEPTH_WORDS);
  // Note: beats are stored verbatim with their sideband, buying replay with
  // zero packet knowledge at the cost of a 53-bit-wide RAM (3 LSRAM blocks
  // at the default depth). Storing a length prefix before each packet
  // instead (batch_buffer style) would shrink it to 32 bits (2 blocks), but
  // re-adds prefix walking and tkeep/tlast/tuser reconstruction on replay.
  localparam int unsigned BEAT_W = 16 + 1 + 4 + 32;   // {tuser, tlast, tkeep, tdata}

  // one wrap bit above the address, so occupancy is a plain subtraction
  typedef logic [ADDR_W:0] ptr_t;

  logic [BEAT_W-1:0] mem [0:DEPTH_WORDS-1];

  ptr_t wr_ptr, wr_cmt, rd_ptr;

  wire ptr_t occupancy = wr_ptr - rd_ptr;
  wire       full      = (occupancy == ptr_t'(DEPTH_WORDS));

  assign tready_s   = !full;
  wire in_handshake = tvalid_s && tready_s;
  wire in_drop      = tuser_s[0];   // meaningful at tlast

  // ------------------------------------------------------------------
  // Drain — fetch register (registered RAM read) + output register
  // ------------------------------------------------------------------
  logic [BEAT_W-1:0] rd_data;
  logic              rd_valid;

  logic        tvalid_m_r, tlast_m_r;
  logic [31:0] tdata_m_r;
  logic [3:0]  tkeep_m_r;
  logic [15:0] tuser_m_r;
  assign tvalid_m = tvalid_m_r;
  assign tdata_m  = tdata_m_r;
  assign tkeep_m  = tkeep_m_r;
  assign tlast_m  = tlast_m_r;
  assign tuser_m  = tuser_m_r;

  wire out_free = !tvalid_m_r || tready_m;

  // ------------------------------------------------------------------
  // Sequential
  // ------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr     <= '0;
      wr_cmt     <= '0;
      rd_ptr     <= '0;
      rd_data    <= '0;
      rd_valid   <= 1'b0;
      tvalid_m_r <= 1'b0;
      tlast_m_r  <= 1'b0;
      tdata_m_r  <= '0;
      tkeep_m_r  <= '0;
      tuser_m_r  <= '0;
    end else begin
      // ---- output handshake: clear once the sink takes a beat ----
      if (tvalid_m_r && tready_m) begin
        tvalid_m_r <= 1'b0;
        tlast_m_r  <= 1'b0;
        tkeep_m_r  <= '0;
        tuser_m_r  <= '0;
      end

      // ============================== fill ==============================
      if (in_handshake) begin
        mem[wr_ptr[ADDR_W-1:0]] <= {tuser_s, tlast_s, tkeep_s, tdata_s};
        wr_ptr <= wr_ptr + ptr_t'(1);
        if (tlast_s) begin
          if (in_drop) wr_ptr <= wr_cmt;              // abandon the record
          else         wr_cmt <= wr_ptr + ptr_t'(1);  // commit it
        end
      end

      // ============================== drain =============================
      if (out_free && rd_valid) begin
        // fetched beat moves to the output register
        tvalid_m_r <= 1'b1;
        {tuser_m_r, tlast_m_r, tkeep_m_r, tdata_m_r} <= rd_data;
      end
      if (!rd_valid || (out_free && rd_valid)) begin
        // fetch register is free (or freeing this cycle): chase wr_cmt
        if (rd_ptr != wr_cmt) begin
          rd_data  <= mem[rd_ptr[ADDR_W-1:0]];
          rd_ptr   <= rd_ptr + ptr_t'(1);
          rd_valid <= 1'b1;
        end else begin
          rd_valid <= 1'b0;
        end
      end
    end
  end

endmodule
