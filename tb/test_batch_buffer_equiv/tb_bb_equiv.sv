// The committed batch_buffer (batch_buffer_ref) and the current one, same inputs, every output compared
module tb_bb_equiv #(
  parameter int unsigned BANK_WORDS = 2048
) (
  input  wire        clk,
  input  wire        rst_n,
  input  wire        tvalid_s,
  input  wire [31:0] tdata_s,
  input  wire [3:0]  tkeep_s,
  input  wire        tlast_s,
  input  wire [15:0] tuser_s,
  input  wire        tready_m,
  input  wire        tick,
  input  wire [31:0] timer,
  output wire        mismatch,
  output wire        a_tready_s, a_tvalid_m, a_tlast_m,
  output wire [31:0] a_tdata_m,
  output wire [3:0]  a_tkeep_m,
  output wire [15:0] a_tuser_m
);
  wire        b_tready_s, b_tvalid_m, b_tlast_m;
  wire [31:0] b_tdata_m;
  wire [3:0]  b_tkeep_m;
  wire [15:0] b_tuser_m;
  batch_buffer #(.BANK_WORDS(BANK_WORDS)) a (
    .clk(clk), .rst_n(rst_n), .tvalid_s(tvalid_s), .tready_s(a_tready_s), .tdata_s(tdata_s), .tkeep_s(tkeep_s), .tlast_s(tlast_s), .tuser_s(tuser_s),
    .tvalid_m(a_tvalid_m), .tready_m(tready_m), .tdata_m(a_tdata_m), .tkeep_m(a_tkeep_m), .tlast_m(a_tlast_m), .tuser_m(a_tuser_m), .tick(tick), .timer(timer));
  batch_buffer_ref #(.BANK_WORDS(BANK_WORDS)) b (
    .clk(clk), .rst_n(rst_n), .tvalid_s(tvalid_s), .tready_s(b_tready_s), .tdata_s(tdata_s), .tkeep_s(tkeep_s), .tlast_s(tlast_s), .tuser_s(tuser_s),
    .tvalid_m(b_tvalid_m), .tready_m(tready_m), .tdata_m(b_tdata_m), .tkeep_m(b_tkeep_m), .tlast_m(b_tlast_m), .tuser_m(b_tuser_m), .tick(tick), .timer(timer));
  assign mismatch = (a_tready_s !== b_tready_s) || (a_tvalid_m !== b_tvalid_m) || (a_tdata_m !== b_tdata_m) ||
                    (a_tkeep_m !== b_tkeep_m) || (a_tlast_m !== b_tlast_m) || (a_tuser_m !== b_tuser_m);
endmodule
