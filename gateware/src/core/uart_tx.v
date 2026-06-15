// uart_tx — minimal 8N1 UART transmitter (no CPU). Drives a serial debug line
// directly from fabric. DIV = clk cycles per bit (= CLK_HZ / BAUD; e.g. 80 MHz /
// 115200 = 694). Hand it a byte with valid while ready; it serializes
// start(0), d0..d7 (LSB first), stop(1). Verilog-2001.
module uart_tx #(parameter DIV = 694) (
    input  wire       clk,
    input  wire       reset_n,
    input  wire [7:0] data,
    input  wire       valid,
    output wire       ready,
    output reg        txd
);
    localparam IDLE = 1'b0, SEND = 1'b1;
    reg        state;
    reg [9:0]  sh;        // {stop, data[7:0], start}, shifted out LSB-first
    reg [3:0]  nbits;
    reg [15:0] cnt;

    assign ready = (state == IDLE);

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state <= IDLE; txd <= 1'b1; sh <= 10'h3FF; nbits <= 0; cnt <= 0;
        end else case (state)
            IDLE: begin
                txd <= 1'b1;
                if (valid) begin sh <= {1'b1, data, 1'b0}; cnt <= 0; nbits <= 4'd10; state <= SEND; end
            end
            SEND:
                if (cnt == 0) begin
                    txd   <= sh[0];
                    sh    <= {1'b1, sh[9:1]};
                    cnt   <= DIV - 1;
                    nbits <= nbits - 1;
                    if (nbits == 4'd1) state <= IDLE;   // stop bit emitted
                end else cnt <= cnt - 1;
        endcase
    end
endmodule
