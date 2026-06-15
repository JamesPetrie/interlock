// dbg_telemetry — read-only status/telemetry block for UART debug via the MiV.
//
// Sits OFF the datapath: it only taps signals (event pulses, sticky-flag pulses,
// and a bank of selectable 32-bit observation buses) and exposes them through a
// simple register read/write port that an APB wrapper maps for the MiV. The MiV
// firmware polls these and prints status over UART; a runtime-writable probe_sel
// repoints the probe mux at a different internal bus with no rebuild.
//
// Register map (rd_addr / wr_addr, 32-bit):
//   read  0x00..(NCTR-1) : event counters
//   read  0x20           : sticky flags  [15:0]
//   read  0x21           : probe_val (the probe_in lane selected by probe_sel)
//   read  0x22           : probe_sel readback
//   write 0x30           : probe_sel <= wr_data (select probe lane)
//   write 0x31           : sticky    <= sticky & ~wr_data   (write-1-to-clear)
//
// Single clock (the fabric/MiV sys clock); no CDC. Verilog-2001.
module dbg_telemetry #(
    parameter NCTR   = 8,     // event counters
    parameter NPROBE = 8      // 32-bit observation buses into the probe mux
)(
    input  wire                   clk,
    input  wire                   reset_n,
    input  wire [NCTR-1:0]        evt,        // pulse -> increment counter[i]
    input  wire [15:0]            flag_set,   // pulse -> set sticky flag[i]
    input  wire [NPROBE*32-1:0]   probe_in,   // selectable observation buses
    // register port (APB-mapped by a wrapper)
    input  wire                   rd_en,
    input  wire [5:0]             rd_addr,
    output reg  [31:0]            rd_data,
    input  wire                   wr_en,
    input  wire [5:0]             wr_addr,
    input  wire [31:0]            wr_data
);
    reg [31:0] ctr [0:NCTR-1];
    reg [15:0] sticky;
    reg [4:0]  probe_sel;
    integer    i;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sticky <= 16'h0; probe_sel <= 5'h0;
            for (i = 0; i < NCTR; i = i + 1) ctr[i] <= 32'h0;
        end else begin
            for (i = 0; i < NCTR; i = i + 1)
                if (evt[i]) ctr[i] <= ctr[i] + 32'd1;
            sticky <= sticky | flag_set;                 // set (pulses)
            if (wr_en) case (wr_addr)
                6'h30:   probe_sel <= wr_data[4:0];
                6'h31:   sticky    <= (sticky | flag_set) & ~wr_data[15:0];  // clear wins
                default: ;
            endcase
        end
    end

    wire [31:0] probe_val = probe_in[probe_sel*32 +: 32];

    always @(*) begin
        if (rd_addr < NCTR)
            rd_data = ctr[rd_addr[2:0]];   // NCTR <= 8
        else case (rd_addr)
            6'h20:   rd_data = {16'h0, sticky};
            6'h21:   rd_data = probe_val;
            6'h22:   rd_data = {27'h0, probe_sel};
            default: rd_data = 32'hDEAD0000 | {26'h0, rd_addr};
        endcase
    end
endmodule
