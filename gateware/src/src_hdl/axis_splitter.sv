// axis_splitter — 1->2 AXI-Stream fork.
//
// Delivers every beat of the slave to both masters; the slave advances only
// when both masters accept, so the two copies stay in lockstep. Purely
// combinational (no buffering): a master that back-pressures stalls the slave
// and therefore the other master too.

module axis_splitter (
  input  wire        s_valid,
  output wire        s_ready,
  input  wire [31:0] s_data,
  input  wire [3:0]  s_keep,
  input  wire        s_last,
  input  wire [15:0] s_user,

  output wire        a_valid,
  input  wire        a_ready,
  output wire [31:0] a_data,
  output wire [3:0]  a_keep,
  output wire        a_last,
  output wire [15:0] a_user,

  output wire        b_valid,
  input  wire        b_ready,
  output wire [31:0] b_data,
  output wire [3:0]  b_keep,
  output wire        b_last,
  output wire [15:0] b_user
);

  // Each master is valid when the slave is valid and the *other* master is
  // ready, so a beat is presented to both and retired together.
  assign a_valid = s_valid && b_ready;
  assign b_valid = s_valid && a_ready;
  assign s_ready = a_ready && b_ready;

  assign a_data = s_data;  assign a_keep = s_keep;  assign a_last = s_last;  assign a_user = s_user;
  assign b_data = s_data;  assign b_keep = s_keep;  assign b_last = s_last;  assign b_user = s_user;

endmodule
