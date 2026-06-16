// cert_framer — turn a 140-byte certificate byte stream into a canonical MAC-TX
// frame addressed to the verifier. Reuses eth_reframe (forced DST/SRC + FCS) fed
// by bytes2axis32. The cert is fixed-length (140 B), carried as the frame's DATA
// with a distinct CERT_DST MAC so the verifier filters cert frames from forwarded
// traffic. c_ready backpressures the core, so a busy egress holds the cert in the
// core's cert buffer (no separate FIFO needed). Plain Verilog-2001 wrapper.
`default_nettype none
module cert_framer #(
    parameter [47:0] CERT_DST = 48'h02_00_00_00_00_ce,   // verifier / cert sink
    parameter [47:0] CERT_SRC = 48'h02_00_00_00_00_cf,
    parameter [15:0] CERT_LEN = 16'd140
)(
    input  wire        clk,
    input  wire        rst_n,
    // certificate byte stream in (interlock_core cert_*)
    input  wire        c_valid,
    output wire        c_ready,
    input  wire [7:0]  c_data,
    input  wire        c_last,
    // MAC-TX-client frame out
    output wire        out_rdy,
    input  wire        out_acpt,
    output wire        out_sof,
    output wire        out_eof,
    output wire [31:0] out_dat,
    output wire [1:0]  out_bytevalid
);
    wire        ax_valid, ax_ready, ax_last;
    wire [31:0] ax_data;
    wire [3:0]  ax_keep;

    bytes2axis32 pk (
        .clk(clk), .rst_n(rst_n),
        .s_valid(c_valid), .s_ready(c_ready), .s_data(c_data), .s_last(c_last),
        .m_valid(ax_valid), .m_ready(ax_ready), .m_data(ax_data),
        .m_keep(ax_keep), .m_last(ax_last)
    );

    eth_reframe #(.FORCE_DST(CERT_DST), .FORCE_SRC(CERT_SRC)) rf (
        .clk(clk), .rst_n(rst_n),
        .tvalid(ax_valid), .tready(ax_ready), .tdata(ax_data),
        .tkeep(ax_keep), .tlast(ax_last), .tuser(CERT_LEN),
        .out_rdy(out_rdy), .out_acpt(out_acpt), .out_sof(out_sof),
        .out_eof(out_eof), .out_dat(out_dat), .out_bytevalid(out_bytevalid),
        .dbg_state(), .dbg_data_end(), .dbg_pad_end(), .dbg_sent(), .dbg_fed(),
        .dbg_o_rdy(), .dbg_o_eof(), .dbg_tuser_at_sof(), .dbg_last_fwd_len()
    );
endmodule
`default_nettype wire
