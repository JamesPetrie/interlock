// 1-bit set-only register. Output goes high the first time `d` is high
// and stays high until reset. Used to latch transient error signals so
// they remain visible on an LED that would otherwise blink too briefly
// to see by eye.
module sticky_bit (
    input  logic clk,
    input  logic rst_n,
    input  logic d,
    output logic q
);
    logic q_r;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)      q_r <= 1'b0;
        else if (d)      q_r <= 1'b1;
    end
    assign q = q_r;
endmodule
