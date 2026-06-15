// telemetry_uart — CPU-free dumper FSM. Walks dbg_telemetry's registers and
// streams them as ASCII hex out a byte interface (to uart_tx). Each sweep emits:
//   <ctr0> <ctr1> ... <ctr(NCTR-1)> <flags> <probe0> ... <probe(NPROBE-1)> CRLF
// where each field is 8 hex digits, space-separated. Probe lanes are dumped by
// the FSM driving dbg_telemetry's probe_sel itself — so every lane is reported
// each sweep with no host/CPU involvement. Verilog-2001.
module telemetry_uart #(parameter NCTR = 8, parameter NPROBE = 8) (
    input  wire        clk,
    input  wire        reset_n,
    // dbg_telemetry register port
    output reg  [5:0]  rd_addr,
    input  wire [31:0] rd_data,
    output reg         wr_en,
    output wire [5:0]  wr_addr,
    output wire [31:0] wr_data,
    // byte stream to uart_tx
    output wire [7:0]  tx_data,
    output wire        tx_valid,
    input  wire        tx_ready
);
    localparam NITEMS = NCTR + 1 + NPROBE;
    localparam S_SEL=3'd0, S_RD=3'd1, S_LAT=3'd2, S_HEX=3'd3, S_SEP=3'd4, S_CR=3'd5, S_LF=3'd6;

    reg [2:0]  state;
    reg [5:0]  item;
    reg [31:0] val;
    reg [3:0]  nibs;

    wire        is_flags   = (item == NCTR);
    wire        is_probe   = (item > NCTR);
    wire [4:0]  probe_lane = item[4:0] - NCTR[4:0] - 5'd1;

    assign wr_addr = 6'h30;                       // dbg_telemetry probe_sel
    assign wr_data = {27'b0, probe_lane};

    function [7:0] hexc;
        input [3:0] n;
        hexc = (n < 4'd10) ? (8'h30 + {4'b0, n}) : (8'h37 + {4'b0, n});
    endfunction

    assign tx_valid = (state==S_HEX) || (state==S_SEP) || (state==S_CR) || (state==S_LF);
    assign tx_data  = (state==S_HEX) ? hexc(val[31:28]) :
                      (state==S_SEP) ? 8'h20 :
                      (state==S_CR)  ? 8'h0D : 8'h0A;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state<=S_SEL; item<=0; wr_en<=0; rd_addr<=0; nibs<=0; val<=0;
        end else begin
            wr_en <= 1'b0;
            case (state)
                S_SEL: begin if (is_probe) wr_en <= 1'b1; state <= S_RD; end
                S_RD:  begin rd_addr <= is_flags ? 6'h20 : is_probe ? 6'h21 : item; state <= S_LAT; end
                S_LAT: begin val <= rd_data; nibs <= 4'd8; state <= S_HEX; end
                S_HEX: if (tx_ready) begin
                           val <= {val[27:0], 4'b0}; nibs <= nibs - 1;
                           if (nibs == 4'd1) state <= S_SEP;
                       end
                S_SEP: if (tx_ready) begin
                           if (item == NITEMS-1) begin item <= 0; state <= S_CR; end
                           else begin item <= item + 1; state <= S_SEL; end
                       end
                S_CR:    if (tx_ready) state <= S_LF;
                S_LF:    if (tx_ready) state <= S_SEL;
                default: state <= S_SEL;
            endcase
        end
    end
endmodule
