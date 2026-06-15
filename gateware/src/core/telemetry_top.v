// telemetry_top — complete CPU-free telemetry subsystem: dbg_telemetry (taps) ->
// telemetry_uart (dumper FSM) -> uart_tx (serial out). No soft CPU, no firmware.
// Wire datapath event pulses to `evt`, error pulses to `flag_set`, and up to
// NPROBE 32-bit observation buses to `probe_in`; status streams out `txd`.
// The tx_* taps are exposed for the testbench (read the ASCII without serial
// decode); leave unconnected in the real design. Verilog-2001.
module telemetry_top #(parameter NCTR = 8, parameter NPROBE = 8, parameter DIV = 694) (
    input  wire                 clk,
    input  wire                 reset_n,
    input  wire [NCTR-1:0]      evt,
    input  wire [15:0]          flag_set,
    input  wire [NPROBE*32-1:0] probe_in,
    output wire                 txd,
    // debug taps (testbench only)
    output wire [7:0]           tx_data,
    output wire                 tx_valid,
    output wire                 tx_ready
);
    wire [5:0]  rd_addr, wr_addr;
    wire [31:0] rd_data, wr_data;
    wire        wr_en;

    dbg_telemetry #(.NCTR(NCTR), .NPROBE(NPROBE)) dt (
        .clk(clk), .reset_n(reset_n), .evt(evt), .flag_set(flag_set), .probe_in(probe_in),
        .rd_en(1'b1), .rd_addr(rd_addr), .rd_data(rd_data),
        .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data));

    telemetry_uart #(.NCTR(NCTR), .NPROBE(NPROBE)) du (
        .clk(clk), .reset_n(reset_n),
        .rd_addr(rd_addr), .rd_data(rd_data),
        .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready));

    uart_tx #(.DIV(DIV)) tx (
        .clk(clk), .reset_n(reset_n),
        .data(tx_data), .valid(tx_valid), .ready(tx_ready), .txd(txd));
endmodule
