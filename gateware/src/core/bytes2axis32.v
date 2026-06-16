// bytes2axis32 — pack a byte stream into a 32-bit AXI-Stream (inverse of
// axis32_to_bytes). Lets the cert byte stream (cert_data[8]/cert_last) be framed
// by a reused eth_reframe instance. Byte order matches axis32_to_bytes: the first
// byte goes in tdata[7:0]; a partial final beat sets the low tkeep bits.
// Plain Verilog-2001.
`default_nettype none
module bytes2axis32 (
    input  wire        clk,
    input  wire        rst_n,
    // byte stream in
    input  wire        s_valid,
    output wire        s_ready,
    input  wire [7:0]  s_data,
    input  wire        s_last,
    // AXI-Stream 32-bit out
    output wire        m_valid,
    input  wire        m_ready,
    output wire [31:0] m_data,
    output wire [3:0]  m_keep,
    output wire        m_last
);
    reg [31:0] acc;
    reg [1:0]  cnt;        // bytes already accumulated this beat (0..3)
    reg        pend;       // a full/last beat is held, waiting for m_ready
    reg [3:0]  keep_q;
    reg        last_q;

    assign m_valid = pend;
    assign m_data  = acc;
    assign m_keep  = keep_q;
    assign m_last  = last_q;
    assign s_ready = !pend;                 // take a byte only when not holding a beat

    wire in_hs  = s_valid & s_ready;        // mutually exclusive with out_hs (s_ready=!pend)
    wire out_hs = m_valid & m_ready;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc <= 32'd0; cnt <= 2'd0; pend <= 1'b0; keep_q <= 4'd0; last_q <= 1'b0;
        end else begin
            if (out_hs) begin pend <= 1'b0; cnt <= 2'd0; acc <= 32'd0; end
            if (in_hs) begin
                acc[8*cnt +: 8] <= s_data;  // first byte -> [7:0]
                if (cnt == 2'd3 || s_last) begin
                    pend   <= 1'b1;
                    last_q <= s_last;
                    keep_q <= (!s_last || cnt == 2'd3) ? 4'b1111 :
                              (cnt == 2'd0) ? 4'b0001 :
                              (cnt == 2'd1) ? 4'b0011 : 4'b0111;
                    cnt    <= 2'd0;
                end else begin
                    cnt <= cnt + 2'd1;
                end
            end
        end
    end
endmodule
`default_nettype wire
