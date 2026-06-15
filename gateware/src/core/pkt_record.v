// Per-packet record path (mirrors wire.record / wire.packet_hash).
//
// A packet arrives as a byte stream: header (44B for dir=0 "in", 12B for dir=1
// "out") followed by ciphertext. This module computes:
//     h1          = H(ciphertext)
//     packet_hash = H(header || h1)
//     record      = length(4) || request_id(8) || packet_hash(32)   [44 bytes]
// and also exposes the parsed length / request_id / ciphertext length for the
// validity checks in the next stage. Two sha256_stream instances (one for the
// ciphertext, one for header||h1). Verilog-2001.
module pkt_record(
    input  wire         clk,
    input  wire         reset_n,
    input  wire         dir,         // 0 = in (44B header), 1 = out (12B header)
    input  wire         in_valid,
    input  wire [7:0]   in_data,
    input  wire         in_last,     // last byte of the packet
    output wire         in_ready,
    output reg          rec_valid,   // 1-cycle pulse when outputs below are valid
    output wire [351:0] record,      // length || request_id || packet_hash
    output wire         pkt_dir,     // latched direction of this packet
    output reg [31:0]   length,
    output reg [63:0]   request_id,
    output reg [31:0]   cipher_len
);
    localparam IDLE=0, HDR=1, CT_INIT=2, CT=3, CT_FIN=4, CT_WAIT=5,
               PK_INIT=6, FEED_HDR=7, FEED_H1=8, PK_FIN=9, PK_WAIT=10, EMIT=11;

    reg [3:0]   state;
    reg         dir_r;
    reg [6:0]   hcnt;        // header bytes captured
    reg         empty;       // ciphertext is empty
    reg [351:0] hdr;         // captured header bytes (shift-left, byte0 ends high)
    reg [255:0] h1;          // H(ciphertext)
    reg [255:0] ph;          // packet_hash
    reg [351:0] fbuf;        // feed buffer for sha_pkt (MSB-first)
    reg [6:0]   fcnt;

    wire [6:0]  hdr_len = dir_r ? 7'd12 : 7'd44;
    // left-justify the captured header so byte0 sits at [351:344] for feeding
    wire [351:0] left_hdr = dir_r ? (hdr << 256) : hdr;

    assign record = {length, request_id, ph};
    assign pkt_dir = dir_r;

    // --- ciphertext hash (h1) ---
    reg          ct_init, ct_fin;
    wire         ct_ready, ct_done;
    wire [255:0] ct_digest;
    sha256_stream sha_ct(.clk(clk), .reset_n(reset_n), .init(ct_init),
        .in_valid(state==CT ? in_valid : 1'b0), .in_data(in_data),
        .in_ready(ct_ready), .fin(ct_fin), .done(ct_done), .digest(ct_digest));

    // --- header||h1 hash (packet_hash) ---
    reg          pk_init, pk_fin;
    wire         pk_ready, pk_done;
    wire [255:0] pk_digest;
    wire         pk_feeding = (state==FEED_HDR) || (state==FEED_H1);
    sha256_stream sha_pkt(.clk(clk), .reset_n(reset_n), .init(pk_init),
        .in_valid(pk_feeding), .in_data(fbuf[351:344]),
        .in_ready(pk_ready), .fin(pk_fin), .done(pk_done), .digest(pk_digest));

    assign in_ready = (state==HDR) || (state==CT && ct_ready);

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state<=IDLE; rec_valid<=0; ct_init<=0; ct_fin<=0; pk_init<=0; pk_fin<=0;
            hcnt<=0; cipher_len<=0; empty<=0;
        end else begin
            rec_valid<=0; ct_init<=0; ct_fin<=0; pk_init<=0; pk_fin<=0;
            case (state)
                IDLE:
                    if (in_valid) begin
                        dir_r<=dir; hcnt<=0; cipher_len<=0; hdr<=0;
                        length<=0; request_id<=0; empty<=0; state<=HDR;
                    end

                HDR:
                    if (in_valid) begin
                        hdr <= {hdr[343:0], in_data};
                        if (hcnt < 4)        length     <= {length[23:0], in_data};
                        else if (hcnt < 12)  request_id <= {request_id[55:0], in_data};
                        hcnt <= hcnt + 1;
                        if (hcnt == hdr_len - 1) begin
                            empty <= in_last;     // last header byte == last packet byte
                            state <= CT_INIT;
                        end
                    end

                CT_INIT: begin ct_init<=1; state <= empty ? CT_FIN : CT; end

                CT:                              // route ciphertext bytes to sha_ct
                    if (in_valid && ct_ready) begin
                        cipher_len <= cipher_len + 1;
                        if (in_last) state <= CT_FIN;
                    end

                CT_FIN:  begin ct_fin<=1; state<=CT_WAIT; end
                CT_WAIT: if (ct_done) begin h1<=ct_digest; state<=PK_INIT; end

                PK_INIT: begin pk_init<=1; fbuf<=left_hdr; fcnt<=hdr_len; state<=FEED_HDR; end

                FEED_HDR:
                    if (pk_ready) begin
                        fbuf <= fbuf << 8; fcnt <= fcnt - 1;
                        if (fcnt == 1) begin fbuf <= {h1, 96'b0}; fcnt <= 32; state<=FEED_H1; end
                    end

                FEED_H1:
                    if (pk_ready) begin
                        fbuf <= fbuf << 8; fcnt <= fcnt - 1;
                        if (fcnt == 1) state <= PK_FIN;
                    end

                PK_FIN:  begin pk_fin<=1; state<=PK_WAIT; end
                PK_WAIT: if (pk_done) begin ph<=pk_digest; state<=EMIT; end

                EMIT: begin rec_valid<=1; state<=IDLE; end
            endcase
        end
    end
endmodule
