// mac_tx_mux — frame-boundary mux of 4 MAC-TX-client sources onto one MAC-TX.
// in0 = forwarded traffic (eth_reframe), in1 = request cert frame, in2 = response
// cert frame, in3 = tick beacon. A whole frame from the selected source passes
// through (sof..eof) before re-arbitration, so frames are never interleaved.
// Priority: cert frames (1,2) > forwarded (0) > beacon (3). Certs are rare and
// must not be starved by forwarded traffic; the beacon is frequent but tiny and
// droppable, so it takes the lowest priority and never delays forwarding or certs.
// Plain Verilog.
`default_nettype none
module mac_tx_mux (
    input  wire        clk,
    input  wire        rst_n,
    // source 0: forwarded traffic
    input  wire        in0_rdy, input wire in0_sof, input wire in0_eof,
    input  wire [31:0] in0_dat, input wire [1:0] in0_bv, output wire in0_acpt,
    // source 1: request cert frame
    input  wire        in1_rdy, input wire in1_sof, input wire in1_eof,
    input  wire [31:0] in1_dat, input wire [1:0] in1_bv, output wire in1_acpt,
    // source 2: response cert frame
    input  wire        in2_rdy, input wire in2_sof, input wire in2_eof,
    input  wire [31:0] in2_dat, input wire [1:0] in2_bv, output wire in2_acpt,
    // source 3: tick beacon (lowest priority)
    input  wire        in3_rdy, input wire in3_sof, input wire in3_eof,
    input  wire [31:0] in3_dat, input wire [1:0] in3_bv, output wire in3_acpt,
    // output to the MAC
    output wire        out_rdy, output wire out_sof, output wire out_eof,
    output wire [31:0] out_dat, output wire [1:0] out_bytevalid,
    input  wire        out_acpt
);
    reg        busy;
    reg [1:0]  sel;

    // arbitrate when idle: certs (1,2) before forwarded (0) before beacon (3)
    wire       any  = in0_rdy | in1_rdy | in2_rdy | in3_rdy;
    wire [1:0] pick = in1_rdy ? 2'd1 : in2_rdy ? 2'd2 : in0_rdy ? 2'd0 : 2'd3;

    wire        s_rdy = (sel==2'd0) ? in0_rdy : (sel==2'd1) ? in1_rdy : (sel==2'd2) ? in2_rdy : in3_rdy;
    wire        s_sof = (sel==2'd0) ? in0_sof : (sel==2'd1) ? in1_sof : (sel==2'd2) ? in2_sof : in3_sof;
    wire        s_eof = (sel==2'd0) ? in0_eof : (sel==2'd1) ? in1_eof : (sel==2'd2) ? in2_eof : in3_eof;
    wire [31:0] s_dat = (sel==2'd0) ? in0_dat : (sel==2'd1) ? in1_dat : (sel==2'd2) ? in2_dat : in3_dat;
    wire [1:0]  s_bv  = (sel==2'd0) ? in0_bv  : (sel==2'd1) ? in1_bv  : (sel==2'd2) ? in2_bv  : in3_bv;

    assign out_rdy       = busy & s_rdy;
    assign out_sof       = busy & s_sof;
    assign out_eof       = busy & s_eof;
    assign out_dat       = s_dat;
    assign out_bytevalid = s_bv;

    assign in0_acpt = busy & (sel==2'd0) & out_acpt;
    assign in1_acpt = busy & (sel==2'd1) & out_acpt;
    assign in2_acpt = busy & (sel==2'd2) & out_acpt;
    assign in3_acpt = busy & (sel==2'd3) & out_acpt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0; sel <= 2'd0;
        end else if (!busy) begin
            if (any) begin busy <= 1'b1; sel <= pick; end
        end else if (out_rdy & out_acpt & out_eof) begin
            busy <= 1'b0;              // frame complete -> re-arbitrate
        end
    end
endmodule
`default_nettype wire
