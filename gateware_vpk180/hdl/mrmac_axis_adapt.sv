// mrmac_axis_adapt — MRMAC non-segmented AXI-Stream pin bundle <-> plain
// AXI-Stream (tdata/tkeep/tlast/tuser).
//
// The MRMAC presents each port's stream as 64-bit words rx/tx_axis_tdata<M>
// with an 11-bit rx/tx_axis_tkeep_user<M> per word (PG314, non-segmented
// mode):
//   [7:0] per-byte keep — meaningful on the tlast beat only
//   [8]   error (RX: frame must be discarded; TX: user-requested error)
//   [9]   preemption (unused here)
//   [10]  reserved
// A W-bit port uses W/64 consecutive words starting at WORD_BASE; which
// words a port owns depends on the speed configuration, so confirm against
// the generated example design before trusting WORD_BASE.
//
// RX: keep is forced to all-ones on non-last beats (the MAC leaves it
// undefined there) and the error bits of all used words are OR-ed into
// tuser. TX: keep is driven on every beat, tuser goes to bit 8 of each word.
// Combinational.

module mrmac_axis_adapt #(
  parameter int unsigned W         = 64,   // port stream width: 64, 128, 256 or 384
  parameter int unsigned NWORDS    = 6,    // words carried on the pin bundle
  parameter int unsigned WORD_BASE = 0     // first MRMAC word used by this port
) (
  // ---- MRMAC RX pins ----
  /* verilator lint_off UNUSEDSIGNAL */   // words above W/64 are unused by design
  input  wire                  mrmac_rx_tvalid,
  input  wire [64*NWORDS-1:0]  mrmac_rx_tdata,       // {tdata<NWORDS-1>, ..., tdata0}
  input  wire [11*NWORDS-1:0]  mrmac_rx_tkeep_user,  // {tkeep_user<NWORDS-1>, ..., tkeep_user0}
  input  wire                  mrmac_rx_tlast,
  /* verilator lint_on UNUSEDSIGNAL */
  // ---- plain RX stream ----
  output wire                  rx_tvalid,
  output wire [W-1:0]          rx_tdata,
  output wire [W/8-1:0]        rx_tkeep,
  output wire                  rx_tlast,
  output wire                  rx_tuser,
  // ---- plain TX stream ----
  input  wire                  tx_tvalid,
  output wire                  tx_tready,
  input  wire [W-1:0]          tx_tdata,
  input  wire [W/8-1:0]        tx_tkeep,
  input  wire                  tx_tlast,
  input  wire                  tx_tuser,
  // ---- MRMAC TX pins ----
  output wire                  mrmac_tx_tvalid,
  input  wire                  mrmac_tx_tready,
  output wire [64*NWORDS-1:0]  mrmac_tx_tdata,
  output wire [11*NWORDS-1:0]  mrmac_tx_tkeep_user,
  output wire                  mrmac_tx_tlast
);

  localparam int unsigned NW = W / 64;

  logic [NW-1:0] err_bits;

  genvar i;
  generate
    for (i = 0; i < NW; i++) begin : g_used
      assign rx_tdata[64*i +: 64] = mrmac_rx_tdata[64*(WORD_BASE+i) +: 64];
      assign rx_tkeep[8*i +: 8]   = mrmac_rx_tlast ? mrmac_rx_tkeep_user[11*(WORD_BASE+i) +: 8]
                                                   : 8'hFF;
      assign err_bits[i]          = mrmac_rx_tkeep_user[11*(WORD_BASE+i) + 8];

      assign mrmac_tx_tdata[64*(WORD_BASE+i) +: 64]      = tx_tdata[64*i +: 64];
      assign mrmac_tx_tkeep_user[11*(WORD_BASE+i) +: 11] = {2'b00, tx_tuser, tx_tkeep[8*i +: 8]};
    end
    for (i = 0; i < NWORDS; i++) begin : g_unused
      if (i < WORD_BASE || i >= WORD_BASE + NW) begin : g_z
        assign mrmac_tx_tdata[64*i +: 64]      = 64'h0;
        assign mrmac_tx_tkeep_user[11*i +: 11] = 11'h0;
      end
    end
  endgenerate

  assign rx_tvalid = mrmac_rx_tvalid;
  assign rx_tlast  = mrmac_rx_tlast;
  assign rx_tuser  = |err_bits;

  assign mrmac_tx_tvalid = tx_tvalid;
  assign mrmac_tx_tlast  = tx_tlast;
  assign tx_tready       = mrmac_tx_tready;

endmodule
