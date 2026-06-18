// cert_merge — 3×1 AXI-Stream packet arbiter for the certificate egress
// (see docs/prod_ilock_pipeline_custom.md and docs/traffic_commit.md,
// "Certificate egress").
//
// Interleaves the egress-side packet stream with the certificate ports of
// both directions onto the single AXIS stream the reframe edge turns into
// wire frames. Arbitration is packet-granular: the grant is locked from a
// packet's first beat to its tlast, so packets never interleave, and is
// re-evaluated only between packets — certificates first, the packet stream
// as the default. A pending certificate is therefore taken at the next
// packet boundary even when the packet stream never goes idle; at one cert
// per direction per batch (bandwidth the fill side reserves, see the doc)
// the cert ports cannot meaningfully delay the packet stream.
//
// All tuser metadata (byte length on beat #0) passes through with the
// granted stream.

module cert_merge (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave 0: packet stream (default grant)
  input  wire        tvalid_s0,
  output wire        tready_s0,
  input  wire [31:0] tdata_s0,
  input  wire [3:0]  tkeep_s0,
  input  wire        tlast_s0,
  input  wire [15:0] tuser_s0,

  // AXI-Stream slave 1: certificate port (highest priority)
  input  wire        tvalid_s1,
  output wire        tready_s1,
  input  wire [31:0] tdata_s1,
  input  wire [3:0]  tkeep_s1,
  input  wire        tlast_s1,
  input  wire [15:0] tuser_s1,

  // AXI-Stream slave 2: certificate port
  input  wire        tvalid_s2,
  output wire        tready_s2,
  input  wire [31:0] tdata_s2,
  input  wire [3:0]  tkeep_s2,
  input  wire        tlast_s2,
  input  wire [15:0] tuser_s2,

  // AXI-Stream master (to eth_reframe)
  output wire        tvalid_m,
  input  wire        tready_m,
  output wire [31:0] tdata_m,
  output wire [3:0]  tkeep_m,
  output wire        tlast_m,
  output wire [15:0] tuser_m
);

  // Grant: held while a packet is in flight, otherwise the priority pick.
  // The pick may move between idle cycles, but locks the moment a beat is
  // presented (tvalid_m high), not just transferred — once the master has
  // shown a beat, AXIS forbids changing it before the handshake.
  logic       active;
  logic [1:0] sel_q;
  wire  [1:0] pick = tvalid_s2 ? 2'd2 : tvalid_s1 ? 2'd1 : 2'd0;
  wire  [1:0] sel  = active ? sel_q : pick;

  assign tvalid_m = (sel == 2'd2) ? tvalid_s2 : (sel == 2'd1) ? tvalid_s1 : tvalid_s0;
  assign tdata_m  = (sel == 2'd2) ? tdata_s2  : (sel == 2'd1) ? tdata_s1  : tdata_s0;
  assign tkeep_m  = (sel == 2'd2) ? tkeep_s2  : (sel == 2'd1) ? tkeep_s1  : tkeep_s0;
  assign tlast_m  = (sel == 2'd2) ? tlast_s2  : (sel == 2'd1) ? tlast_s1  : tlast_s0;
  assign tuser_m  = (sel == 2'd2) ? tuser_s2  : (sel == 2'd1) ? tuser_s1  : tuser_s0;

  assign tready_s0 = (sel == 2'd0) && tready_m;
  assign tready_s1 = (sel == 2'd1) && tready_m;
  assign tready_s2 = (sel == 2'd2) && tready_m;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active <= 1'b0;
      sel_q  <= 2'd0;
    end else if (tvalid_m && tready_m && tlast_m) begin
      active <= 1'b0;                       // packet done: free the grant
    end else if (!active && tvalid_m) begin
      active <= 1'b1;                       // first beat shown: lock it
      sel_q  <= sel;
    end
  end

endmodule
