// HMAC-SHA-256 over the streaming SHA wrapper: tag = H((K0^opad) || H((K0^ipad) || msg)).
//
//   start (with key)        -> begin
//   msg_valid/msg_data/msg_last/msg_ready  -> stream the message
//   done / tag              -> 256-bit tag
//
// Key is up to the SHA block size: pass it in `key[255:0]` right-zero-padded to
// 32 bytes; K0 = key || 32 zero bytes (64B), exactly HMAC's K0 for keys <= 32B.
// (The interlock MAC key is 32 bytes; keys > 64B that HMAC pre-hashes are out of
// scope here.) One sha256_stream instance, reused for both passes. Verilog-2001.
module hmac_sha256(
    input  wire         clk,
    input  wire         reset_n,
    input  wire         start,        // pulse; key sampled this cycle
    input  wire [255:0] key,
    input  wire         msg_valid,
    input  wire [7:0]   msg_data,
    input  wire         msg_last,
    output wire         msg_ready,
    output wire         done,
    output wire [255:0] tag
);
    localparam IDLE=0, I_INIT=1, I_PAD=2, I_MSG=3, I_FIN=4, I_WAIT=5,
               O_INIT=6, O_PAD=7, O_DIG=8, O_FIN=9, O_WAIT=10, DONE=11;

    reg [3:0]    state;
    reg [255:0]  key_r, inner;
    reg [511:0]  fb;        // feed buffer (MSB-first)
    reg [6:0]    fcnt;
    reg          sinit, sfin;

    wire [255:0] IP = {32{8'h36}};
    wire [255:0] OP = {32{8'h5c}};
    wire [511:0] ipad_block = {key_r ^ IP, IP};   // (K0^ipad): upper=key^0x36, lower=0x36
    wire [511:0] opad_block = {key_r ^ OP, OP};

    wire        feeding_fb = (state==I_PAD) || (state==O_PAD) || (state==O_DIG);
    wire        s_in_valid = feeding_fb ? 1'b1 : (state==I_MSG ? msg_valid : 1'b0);
    wire [7:0]  s_in_data  = feeding_fb ? fb[511:504] : msg_data;
    wire        s_ready, s_done;
    wire [255:0] s_digest;

    sha256_stream sha(.clk(clk), .reset_n(reset_n), .init(sinit),
        .in_valid(s_in_valid), .in_data(s_in_data), .in_ready(s_ready),
        .fin(sfin), .done(s_done), .digest(s_digest));

    assign msg_ready = (state==I_MSG) && s_ready;
    assign done      = (state==DONE);
    assign tag       = s_digest;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin state<=IDLE; sinit<=0; sfin<=0; end
        else begin
            sinit<=0; sfin<=0;
            case (state)
                IDLE:   if (start) begin key_r<=key; state<=I_INIT; end
                I_INIT: begin sinit<=1; fb<=ipad_block; fcnt<=7'd64; state<=I_PAD; end
                I_PAD:  if (s_ready) begin fb<=fb<<8; fcnt<=fcnt-1; if (fcnt==7'd1) state<=I_MSG; end
                I_MSG:  if (msg_valid && s_ready && msg_last) state<=I_FIN;
                I_FIN:  begin sfin<=1; state<=I_WAIT; end
                I_WAIT: if (s_done) begin inner<=s_digest; state<=O_INIT; end
                O_INIT: begin sinit<=1; fb<=opad_block; fcnt<=7'd64; state<=O_PAD; end
                O_PAD:  if (s_ready) begin
                            fb<=fb<<8; fcnt<=fcnt-1;
                            if (fcnt==7'd1) begin fb<={inner, 256'b0}; fcnt<=7'd32; state<=O_DIG; end
                        end
                O_DIG:  if (s_ready) begin fb<=fb<<8; fcnt<=fcnt-1; if (fcnt==7'd1) state<=O_FIN; end
                O_FIN:  begin sfin<=1; state<=O_WAIT; end
                O_WAIT: if (s_done) state<=DONE;
                DONE:   if (start) begin key_r<=key; state<=I_INIT; end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
