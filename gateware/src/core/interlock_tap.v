// interlock_tap — Phase A: an INLINE AXIS-32 pass-through that also feeds the
// canonical packet stream to interlock_core (via axis32_to_bytes) and captures
// the emitted certificate. Spliced into the datapath as deframe -> tap -> reframe.
//
// It forwards every beat unchanged (m_t* = s_t*) AND forks the bytes to the core.
// A beat transfers only when BOTH the downstream (reframe) and the core's adapter
// can accept it, so the core sees every byte and the forward stream is never
// corrupted; if the core stalls (e.g. ~280-cycle bucket finalization) the whole
// path stalls together. No buffer (a decoupling FIFO is a later throughput pass).
//
// Single direction for Phase A (s_dir tied by the parent). Fixed nonce/mac for
// bring-up validation against the Python golden (real verifier nonce = Phase C).
// All dbg_* outputs are PROTOTYPE_DEBUG (bring-up only; must be elided from a
// production build per the no-prover-observables rule).
//
// Plain Verilog-2001 (no packages) so synthesis has no room to reinterpret it.
module interlock_tap #(
    parameter [255:0] MAC_KEY = 256'h348a629f5ceed032c3e8706ec47d9bfafb00fb4250b018dd965435ca50cb836e, // H("mac")
    parameter [127:0] NONCE   = 128'h78377b525757b494427f89014f97d799,                                 // H("nonce")[:16]
    parameter IID = 7,
    parameter N   = 8
)(
    input  wire        clk,
    input  wire        rst_n,
    // AXIS-32 slave in (from eth_deframe)
    input  wire        s_tvalid,
    output wire        s_tready,
    input  wire [31:0] s_tdata,
    input  wire [3:0]  s_tkeep,
    input  wire        s_tlast,
    input  wire        s_dir,        // 0 = in/req
    // AXIS-32 master out (to eth_reframe) — unchanged pass-through
    output wire        m_tvalid,
    input  wire        m_tready,
    output wire [31:0] m_tdata,
    output wire [3:0]  m_tkeep,
    output wire        m_tlast,
    input  wire        bucket_tick,
    // certificate stream out (for byte-exact sim capture; cert_ready tied high)
    output wire        cert_valid,
    output wire [7:0]  cert_data,
    output wire        cert_last,
    // ---- PROTOTYPE_DEBUG probes (the [dbg4] accounting chain) ----
    output wire        dbg_idle,
    output wire        dbg_tick_err,
    output wire [15:0] dbg_pkt_done,    // packets the core finished (count)
    output wire [15:0] dbg_pkt_acc,     // packets accepted (count)
    output wire [15:0] dbg_bytes_fed,   // bytes pushed into the core (count)
    output wire [31:0] dbg_pr_length,   // declared length parsed from last packet (bytes 0-3, BE)
    output wire [15:0] dbg_cert_seq,    // certificates emitted (count)
    output wire [31:0] dbg_cert_chk,    // rolling fold-checksum of the last cert
    output wire [31:0] dbg_cert_b0_3,   // first 4 cert bytes  (== "iloc")
    output wire [31:0] dbg_cert_b4_7    // next 4 cert bytes   (== "k-v5")
);
    // ---- nonce one-shot: pulse nonce_valid once after reset ----
    reg nonce_seen, nonce_v;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin nonce_seen <= 1'b0; nonce_v <= 1'b0; end
        else begin nonce_v <= ~nonce_seen; nonce_seen <= 1'b1; end
    end

    // ---- inline pass-through + fork to the core ----
    // A beat transfers iff valid AND both consumers (reframe, core-adapter) accept.
    wire adapter_ready;
    assign m_tvalid = s_tvalid & adapter_ready;       // reframe sees valid only when the core can take it too
    assign m_tdata  = s_tdata;                         // unchanged pass-through
    assign m_tkeep  = s_tkeep;
    assign m_tlast  = s_tlast;
    assign s_tready = m_tready & adapter_ready;        // deframe advances only when both accept

    // ---- AXIS-32 -> byte stream (fork to the core) ----
    wire        b_valid, b_ready, b_last;
    wire [7:0]  b_data;
    axis32_to_bytes a2b (
        .clk(clk), .reset_n(rst_n),
        .in_valid(s_tvalid & m_tready),               // core-adapter sees valid only when reframe accepts too
        .in_ready(adapter_ready), .in_data(s_tdata),
        .in_keep(s_tkeep), .in_last(s_tlast),
        .out_valid(b_valid), .out_ready(b_ready), .out_data(b_data), .out_last(b_last)
    );

    // ---- interlock_core ----
    wire s_ready_core, pkt_done, pkt_accepted, idle, tick_err;
    wire c_valid, c_last;
    wire [7:0] c_data;
    interlock_core #(.IID(IID), .N(N)) core (
        .clk(clk), .rst_n(rst_n), .mac_key(MAC_KEY),
        .nonce_valid(nonce_v), .nonce(NONCE),
        .s_valid(b_valid), .s_ready(s_ready_core), .s_data(b_data), .s_last(b_last), .s_dir(s_dir),
        .pkt_done(pkt_done), .pkt_accepted(pkt_accepted),
        .bucket_tick(bucket_tick),
        .cert_valid(c_valid), .cert_ready(1'b1), .cert_data(c_data), .cert_last(c_last),
        .idle(idle), .tick_err(tick_err)
    );
    assign b_ready    = s_ready_core;
    assign cert_valid = c_valid;
    assign cert_data  = c_data;
    assign cert_last  = c_last;

    // ---- probes ----
    wire byte_hs = b_valid & b_ready;       // a byte entered the core
    wire cert_hs = c_valid;                 // cert_ready tied high

    reg [15:0] pkt_done_q, pkt_acc_q, bytes_fed_q, cert_seq_q;
    reg [31:0] prlen_q, prlen_acc;
    reg [2:0]  pbidx;                        // packet byte index (saturating at 4)
    reg        pkt_done_d;
    reg [31:0] chk_q, b03_q, b47_q;
    reg [3:0]  cbidx;                        // cert byte index (saturating)
    reg        in_cert;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pkt_done_q<=0; pkt_acc_q<=0; bytes_fed_q<=0; cert_seq_q<=0;
            prlen_q<=0; prlen_acc<=0; pbidx<=0; pkt_done_d<=0;
            chk_q<=0; b03_q<=0; b47_q<=0; cbidx<=0; in_cert<=0;
        end else begin
            pkt_done_d <= pkt_done;
            if (pkt_done & ~pkt_done_d) begin
                pkt_done_q <= pkt_done_q + 16'd1;
                if (pkt_accepted) pkt_acc_q <= pkt_acc_q + 16'd1;
            end

            // bytes into the core + declared-length capture (bytes 0..3, big-endian)
            if (byte_hs) begin
                bytes_fed_q <= bytes_fed_q + 16'd1;
                if (pbidx < 3'd4) prlen_acc <= {prlen_acc[23:0], b_data};
                if (b_last) begin
                    prlen_q <= (pbidx < 3'd4) ? {prlen_acc[23:0], b_data} : prlen_acc;
                    pbidx   <= 3'd0;
                end else if (pbidx < 3'd4) begin
                    pbidx <= pbidx + 3'd1;
                end
            end

            // cert fold-checksum + first 8 bytes + seq (rotate-xor; any byte change shows)
            if (cert_hs) begin
                if (!in_cert) begin            // first byte of a cert
                    chk_q  <= {24'd0, c_data};
                    b03_q  <= {c_data, 24'd0};
                    b47_q  <= 32'd0;
                    cbidx  <= 4'd1;
                    in_cert<= 1'b1;
                end else begin
                    chk_q <= {chk_q[30:0], chk_q[31]} ^ {24'd0, c_data};   // rol1 ^ byte
                    if (cbidx < 4'd4)      b03_q <= b03_q | ({24'd0, c_data} << (8*(3-cbidx)));
                    else if (cbidx < 4'd8) b47_q <= b47_q | ({24'd0, c_data} << (8*(7-cbidx)));
                    if (cbidx < 4'd15) cbidx <= cbidx + 4'd1;
                end
                if (c_last) begin
                    cert_seq_q <= cert_seq_q + 16'd1;
                    in_cert    <= 1'b0;
                end
            end
        end
    end

    assign dbg_idle      = idle;
    assign dbg_tick_err  = tick_err;
    assign dbg_pkt_done  = pkt_done_q;
    assign dbg_pkt_acc   = pkt_acc_q;
    assign dbg_bytes_fed = bytes_fed_q;
    assign dbg_pr_length = prlen_q;
    assign dbg_cert_seq  = cert_seq_q;
    assign dbg_cert_chk  = chk_q;
    assign dbg_cert_b0_3 = b03_q;
    assign dbg_cert_b4_7 = b47_q;
endmodule
