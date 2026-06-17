// interlock_core — gateware twin of prototype/interlock.py.
//
// Processes packets one at a time (the conformance subset; full-duplex is a
// later throughput pass). For each packet: pkt_record computes the 44B record;
// validity rules decide accept/drop; accepted records fold into a running
// per-direction bucket hash. On bucket_tick the bucket hash is finalized and
// folded into a per-direction cert-window hash. After N ticks the window roots
// form the 108-byte certificate body (version|iid|bucket_start|N|overall_in|
// overall_out|nonce); hmac_sha256 then tags it and the full 140-byte certificate
// (body || HMAC-SHA256 tag) streams out on cert_*.
//
// Mirrors the streaming model exactly (H(concat) == streaming update), so the
// emitted certificate is byte-identical to interlock.py on_second(). Verilog-2001.
module interlock_core #(
    parameter IID  = 7,
    parameter N    = 8,            // buckets per certificate
    parameter SMAX = 100000,       // max declared payload length
    parameter CAP  = 100000        // max bytes per bucket per direction
)(
    input  wire         clk,
    input  wire         rst_n,
    input  wire [255:0] mac_key,   // reserved (HMAC deferred)
    // verifier nonce latch
    input  wire         nonce_valid,
    input  wire [127:0] nonce,
    // canonical packet in (one at a time; s_dir held for the packet)
    input  wire         s_valid,
    output wire         s_ready,
    input  wire [7:0]   s_data,
    input  wire         s_last,
    input  wire         s_dir,     // 0 = in, 1 = out
    output reg          pkt_done,
    output reg          pkt_accepted,
    // bucket boundary tick
    input  wire         bucket_tick,
    // certificate body out (108 bytes; HMAC tag appended in a later stage)
    output wire         cert_valid,
    input  wire         cert_ready,
    output wire [7:0]   cert_data,
    output wire         cert_last,
    // high when not folding/finalizing/emitting — safe to pulse bucket_tick
    output wire         idle,
    // sticky: a bucket_tick was dropped because too many were pending (misuse —
    // boundaries take ~280 cycles, real ticks are 1 ms apart). Certs are invalid
    // once set; for observability/integration, not exercised on the normal path.
    output wire         tick_err
);
    localparam [63:0] VERSION = 64'h696c6f636b2d7635;   // "ilock-v5"
    localparam [63:0] IID_W = IID;
    localparam [31:0] N_W   = N;
    localparam [63:0] N64   = N;

    localparam BOOT=0, IDLE=1, FOLD=2, DROP=3,
               B_FIN=4, B_WAIT=5, B_FOLD=6, B_REINIT=7, B_DONE=8,
               C_FIN_IN=9, C_WAIT_IN=10, C_FIN_OUT=11, C_WAIT_OUT=12,
               C_EMIT=13, C_REINIT=14, H_START=15, H_FEED=16, H_WAIT=17;

    localparam [3:0] TICK_MAX = 4'hF;
    reg [4:0]   state;
    reg [3:0]   tick_cnt;          // pending bucket ticks (queued, not lost)
    reg         tick_ovf;          // sticky: queue saturated, a tick was dropped
    reg         bd;                // direction being processed at a boundary
    reg [31:0]  nb;                // buckets closed this window
    reg [63:0]  bucket, bstart;
    reg [31:0]  used_in, used_out;
    reg [63:0]  last_in_id, last_out_id;
    reg         have_in, have_out;
    reg [127:0] nonce_r;
    reg [255:0] ov_in, ov_out;
    reg [351:0] rbuf;  reg [6:0] rcnt;     // record fold buffer
    reg [255:0] wbuf;  reg [5:0] wcnt;     // bucket-digest fold buffer
    reg [863:0] body_r, hb;                // 108-byte cert body (held / HMAC feed)
    reg [1119:0] cbuf; reg [7:0] ccnt;     // 140-byte certificate output buffer
    reg          hm_start;
    reg         acc_dir;

    // --- packet record path ---
    wire        pr_rec_valid, pr_dir, pr_in_ready;
    wire [351:0] pr_record;
    wire [31:0] pr_length, pr_cipher_len;
    wire [63:0] pr_request_id, pr_bucket_id;
    pkt_record pr(.clk(clk), .reset_n(rst_n), .dir(s_dir),
        .in_valid(state==IDLE ? s_valid : 1'b0), .in_data(s_data), .in_last(s_last),
        .in_ready(pr_in_ready), .rec_valid(pr_rec_valid), .record(pr_record),
        .pkt_dir(pr_dir), .length(pr_length), .request_id(pr_request_id),
        .bucket_id(pr_bucket_id), .cipher_len(pr_cipher_len));

    assign s_ready = (state==IDLE) ? pr_in_ready : 1'b0;

    // --- four running hash contexts ---
    reg  bin_init, bin_fin, bout_init, bout_fin, win_i_init, win_i_fin, win_o_init, win_o_fin;
    wire bin_rdy, bin_done, bout_rdy, bout_done, win_i_rdy, win_i_done, win_o_rdy, win_o_done;
    wire [255:0] bin_dig, bout_dig, win_i_dig, win_o_dig;

    wire bin_iv  = (state==FOLD) && (acc_dir==1'b0);
    wire bout_iv = (state==FOLD) && (acc_dir==1'b1);
    wire wi_iv   = (state==B_FOLD) && (bd==1'b0);
    wire wo_iv   = (state==B_FOLD) && (bd==1'b1);

    sha256_stream bkt_in (.clk(clk), .reset_n(rst_n), .init(bin_init),
        .in_valid(bin_iv), .in_data(rbuf[351:344]), .in_ready(bin_rdy),
        .fin(bin_fin), .done(bin_done), .digest(bin_dig));
    sha256_stream bkt_out(.clk(clk), .reset_n(rst_n), .init(bout_init),
        .in_valid(bout_iv), .in_data(rbuf[351:344]), .in_ready(bout_rdy),
        .fin(bout_fin), .done(bout_done), .digest(bout_dig));
    sha256_stream win_in (.clk(clk), .reset_n(rst_n), .init(win_i_init),
        .in_valid(wi_iv), .in_data(wbuf[255:248]), .in_ready(win_i_rdy),
        .fin(win_i_fin), .done(win_i_done), .digest(win_i_dig));
    sha256_stream win_out(.clk(clk), .reset_n(rst_n), .init(win_o_init),
        .in_valid(wo_iv), .in_data(wbuf[255:248]), .in_ready(win_o_rdy),
        .fin(win_o_fin), .done(win_o_done), .digest(win_o_dig));

    wire bkt_rdy_sel  = acc_dir ? bout_rdy  : bin_rdy;
    wire win_rdy_sel  = bd      ? win_o_rdy : win_i_rdy;
    wire bkt_done_sel = bd      ? bout_done : bin_done;
    wire [255:0] bkt_dig_sel = bd ? bout_dig : bin_dig;

    // --- validity decision (combinational, valid at pr_rec_valid) ---
    wire [31:0] hdrlen_w = pr_dir ? 32'd20 : 32'd52;   // header now carries 8B bucket
    wire [31:0] pktlen_w = hdrlen_w + pr_cipher_len;
    wire [63:0] last_w   = pr_dir ? last_out_id : last_in_id;
    wire        have_w   = pr_dir ? have_out    : have_in;
    wire [31:0] used_w   = pr_dir ? used_out    : used_in;
    wire bad_len = (pr_length != pr_cipher_len) || (pr_length > SMAX);
    wire bad_id  = have_w && (pr_request_id <= last_w);
    wire bad_cap = ({1'b0, used_w} + {1'b0, pktlen_w}) > CAP;   // 33-bit: no wrap
    wire bad_bucket = (pr_bucket_id != bucket);                 // design A: exact match
    wire accept_w = !bad_len && !bad_id && !bad_cap && !bad_bucket;

    // a queued tick is consumed only when IDLE and not starting a packet fold
    wire consume_tick = (state==IDLE) && !pr_rec_valid && (tick_cnt != 4'd0);

    // --- HMAC over the 108-byte body -> tag ---
    wire        hm_ready, hm_done;
    wire [255:0] hm_tag;
    hmac_sha256 hm(.clk(clk), .reset_n(rst_n), .start(hm_start), .key(mac_key),
        .msg_valid(state==H_FEED), .msg_data(hb[863:856]),
        .msg_last((state==H_FEED) && (ccnt==8'd1)),
        .msg_ready(hm_ready), .done(hm_done), .tag(hm_tag));

    // --- certificate output (140 bytes: 108-byte body || 32-byte HMAC tag) ---
    assign cert_valid = (state==C_EMIT);
    assign cert_data  = cbuf[1119:1112];
    assign cert_last  = (state==C_EMIT) && (ccnt==8'd1);
    assign idle       = (state==IDLE);
    assign tick_err   = tick_ovf;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=BOOT; pkt_done<=0; pkt_accepted<=0; tick_cnt<=0; tick_ovf<=0;
            nb<=0; bucket<=0; used_in<=0; used_out<=0;
            last_in_id<=0; last_out_id<=0; have_in<=0; have_out<=0; nonce_r<=0;
            bin_init<=0; bin_fin<=0; bout_init<=0; bout_fin<=0;
            win_i_init<=0; win_i_fin<=0; win_o_init<=0; win_o_fin<=0; bd<=0; hm_start<=0;
        end else begin
            pkt_done<=0; hm_start<=0;
            bin_init<=0; bin_fin<=0; bout_init<=0; bout_fin<=0;
            win_i_init<=0; win_i_fin<=0; win_o_init<=0; win_o_fin<=0;
            if (nonce_valid) nonce_r<=nonce;

            case (state)
                BOOT: begin
                    bin_init<=1; bout_init<=1; win_i_init<=1; win_o_init<=1;
                    state<=IDLE;
                end

                IDLE:
                    if (pr_rec_valid) begin
                        acc_dir<=pr_dir; rbuf<=pr_record; rcnt<=7'd44; pkt_accepted<=accept_w;
                        if (accept_w) begin
                            if (pr_dir==1'b0) begin used_in <=used_in +pktlen_w; last_in_id <=pr_request_id; have_in <=1; end
                            else              begin used_out<=used_out+pktlen_w; last_out_id<=pr_request_id; have_out<=1; end
                            state<=FOLD;
                        end else state<=DROP;
                    end else if (tick_cnt != 4'd0) begin
                        bd<=0; state<=B_FIN;        // tick_cnt decremented below
                    end

                FOLD:
                    if (bkt_rdy_sel) begin
                        rbuf<=rbuf<<8; rcnt<=rcnt-1;
                        if (rcnt==7'd1) begin pkt_done<=1; state<=IDLE; end
                    end

                DROP: begin pkt_done<=1; state<=IDLE; end

                B_FIN: begin
                    if (bd==1'b0) bin_fin<=1; else bout_fin<=1;
                    state<=B_WAIT;
                end
                B_WAIT:
                    if (bkt_done_sel) begin wbuf<=bkt_dig_sel; wcnt<=6'd32; state<=B_FOLD; end
                B_FOLD:
                    if (win_rdy_sel) begin
                        wbuf<=wbuf<<8; wcnt<=wcnt-1;
                        if (wcnt==6'd1) state<=B_REINIT;
                    end
                B_REINIT: begin
                    if (bd==1'b0) bin_init<=1; else bout_init<=1;
                    if (bd==1'b0) begin bd<=1; state<=B_FIN; end
                    else state<=B_DONE;
                end
                B_DONE: begin
                    bucket<=bucket+1; used_in<=0; used_out<=0; have_out<=0; last_out_id<=0;
                    if (nb+1==N_W) begin bstart<=bucket+64'd1-N64; state<=C_FIN_IN; end
                    else begin nb<=nb+1; state<=IDLE; end
                end

                C_FIN_IN:  begin win_i_fin<=1; state<=C_WAIT_IN; end
                C_WAIT_IN: if (win_i_done) begin ov_in<=win_i_dig; state<=C_FIN_OUT; end
                C_FIN_OUT: begin win_o_fin<=1; state<=C_WAIT_OUT; end
                C_WAIT_OUT: if (win_o_done) begin
                        ov_out<=win_o_dig;
                        body_r<={VERSION, IID_W, bstart, N_W, ov_in, win_o_dig, nonce_r};
                        hb    <={VERSION, IID_W, bstart, N_W, ov_in, win_o_dig, nonce_r};
                        state<=H_START;
                    end
                H_START: begin hm_start<=1; ccnt<=8'd108; state<=H_FEED; end
                H_FEED:  if (hm_ready) begin
                        hb<=hb<<8; ccnt<=ccnt-1;
                        if (ccnt==8'd1) state<=H_WAIT;
                    end
                H_WAIT:  if (hm_done) begin
                        cbuf<={body_r, hm_tag};      // 108-byte body || 32-byte tag
                        ccnt<=8'd140; state<=C_EMIT;
                    end
                C_EMIT:
                    if (cert_ready) begin
                        if (ccnt==8'd1) state<=C_REINIT;
                        else begin cbuf<=cbuf<<8; ccnt<=ccnt-1; end
                    end
                C_REINIT: begin win_i_init<=1; win_o_init<=1; nb<=0; state<=IDLE; end
                default: state<=IDLE;            // SEU/X guard: recover to a safe state
            endcase

            // bucket_tick accounting: queue ticks; resolve simultaneous arrive+consume
            if (bucket_tick && !consume_tick) begin
                if (tick_cnt == TICK_MAX) tick_ovf <= 1'b1;
                else                      tick_cnt <= tick_cnt + 4'd1;
            end else if (!bucket_tick && consume_tick) begin
                tick_cnt <= tick_cnt - 4'd1;
            end
        end
    end

`ifdef SIMDBG
    reg [4:0] dbg_prev;
    always @(posedge clk) begin
        dbg_prev <= state;
        if (state !== dbg_prev)
            $display("[ilk] t=%0t %0d->%0d nb=%0d bucket=%0d bd=%0d rcnt=%0d wcnt=%0d ccnt=%0d",
                     $time, dbg_prev, state, nb, bucket, bd, rcnt, wcnt, ccnt);
    end
`endif
endmodule
