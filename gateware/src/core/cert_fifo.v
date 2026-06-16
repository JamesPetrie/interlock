// cert_fifo — small byte FIFO (with a per-byte 'last' tag) that DECOUPLES a
// certificate from the forward datapath. interlock_core only accepts new packet
// bytes in IDLE, so while it streams a cert (state C_EMIT) it stalls its tap's
// forward path. If the cert and the forwarded traffic then share one MAC-TX mux,
// they deadlock (the stalled forwarded frame keeps the mux busy, so the cert
// never drains, so the core never returns to IDLE). This FIFO holds a whole cert
// (>=140 B), so the core dumps it quickly, returns to IDLE, and resumes
// forwarding; the FIFO drains to the framer/mux at its own pace. Certs are ~1 per
// window (seconds apart), so the FIFO fully empties between them. Plain Verilog.
`default_nettype none
module cert_fifo #(
    parameter AW = 8                       // depth = 2^AW = 256 bytes
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        s_valid,
    output wire        s_ready,
    input  wire [7:0]  s_data,
    input  wire        s_last,
    output wire        m_valid,
    input  wire        m_ready,
    output wire [7:0]  m_data,
    output wire        m_last
);
    reg  [8:0]    mem [0:(1<<AW)-1];        // {last, data[7:0]}
    reg  [AW:0]   wptr, rptr;               // extra MSB to tell full from empty

    wire full  = (wptr[AW] != rptr[AW]) && (wptr[AW-1:0] == rptr[AW-1:0]);
    wire empty = (wptr == rptr);

    assign s_ready = !full;
    assign m_valid = !empty;
    assign m_data  = mem[rptr[AW-1:0]][7:0];
    assign m_last  = mem[rptr[AW-1:0]][8];

    wire wr = s_valid & s_ready;
    wire rd = m_valid & m_ready;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wptr <= {(AW+1){1'b0}};
            rptr <= {(AW+1){1'b0}};
        end else begin
            if (wr) begin mem[wptr[AW-1:0]] <= {s_last, s_data}; wptr <= wptr + 1'b1; end
            if (rd) rptr <= rptr + 1'b1;
        end
    end
endmodule
`default_nettype wire
