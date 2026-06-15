// axis32_to_bytes — width adapter from a 32-bit AXI-Stream (eth_deframe's payload)
// to the interlock_core byte stream. Emits the valid bytes of each beat one per
// cycle; out_last marks the last byte of the tlast beat.
//
// Byte order follows the AXI-Stream standard: tdata[7:0] is the first byte, and
// tkeep is contiguous from lane 0 (so a partial last beat sets the low bits).
// If eth_deframe presents network order (first byte in the high lane), flip the
// `out_data` select and `kc` — verify against deframe at integration (this is the
// classic synth-vs-sim byte-order trap, cf. the deframe LENGTH bug). Verilog-2001.
module axis32_to_bytes(
    input  wire        clk,
    input  wire        reset_n,
    // 32-bit AXI-Stream in
    input  wire        in_valid,
    output wire        in_ready,
    input  wire [31:0] in_data,
    input  wire [3:0]  in_keep,
    input  wire        in_last,
    // byte stream out (interlock_core s_* shape)
    output wire        out_valid,
    input  wire        out_ready,
    output wire [7:0]  out_data,
    output wire        out_last
);
    reg [31:0] word;
    reg [2:0]  nbytes, idx;   // bytes in this beat (1..4); next byte index
    reg        last_w, busy;

    function [2:0] kc;        // contiguous-from-lane0 byte count
        input [3:0] k;
        kc = k[3] ? 3'd4 : k[2] ? 3'd3 : k[1] ? 3'd2 : 3'd1;
    endfunction

    assign in_ready  = !busy;
    assign out_valid = busy;
    assign out_data  = word[idx*8 +: 8];
    assign out_last  = busy && last_w && (idx == nbytes - 3'd1);

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin busy<=0; idx<=0; nbytes<=0; last_w<=0; end
        else if (!busy) begin
            if (in_valid) begin
                word<=in_data; nbytes<=kc(in_keep); last_w<=in_last; idx<=0; busy<=1;
            end
        end else if (out_ready) begin
            if (idx == nbytes - 3'd1) busy<=0;   // beat drained; accept next
            else idx<=idx + 3'd1;
        end
    end
endmodule
