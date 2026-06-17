// tick_beacon — one-way clock broadcast (design A, "Clock distribution").
//
// Counts the interlock's bucket_tick (same pulse the cores use) and, every STRIDE
// ticks, emits a 32-byte beacon body as a byte stream for cert_framer to wrap in a
// canonical MAC-TX frame (DST 02:..:CB, no HMAC). The prover slaves its clock to
// this so it can declare matching buckets (design A drops mismatches). It is a
// one-way broadcast from the trusted side, carries only time (not secret), and so
// stays out of the unexplained-info budget.
//
//   body (32B, MSB-first):
//     magic(8) "ilbcn-v1" | interlock_id(8) | bucket(8) | tick_period_ns(4) | stride(4)
//
// `bucket` is the absolute bucket index starting at this beacon edge. tick_beacon
// counts ticks on the edge, so during a core's ~280-cycle boundary FSM it can be
// ahead of the cores' counter by <=1 — covered by the prover's guard interval
// (ticks are ~1 ms apart). A beacon that comes due while the previous is still
// draining is dropped (b_dropped pulses) — droppable by design; the prover uses
// the next one. Plain Verilog-2001.
`default_nettype none
module tick_beacon #(
    parameter [63:0] IID            = 64'd7,
    parameter [31:0] TICK_PERIOD_NS = 32'd1000000,   // nominal bucket width (1 ms)
    parameter [31:0] STRIDE         = 32'd16,         // ticks between beacons
    parameter [63:0] BUCKET0        = 64'd0           // initial bucket (battery-backed in prod)
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        bucket_tick,    // same tick the interlock cores count
    // beacon byte stream out (-> cert_framer with DST 02:..:CB, LEN 32)
    output wire        b_valid,
    input  wire        b_ready,
    output wire [7:0]  b_data,
    output wire        b_last,
    output wire        b_dropped       // prototype observability: a due beacon was skipped
);
    localparam [63:0] MAGIC = 64'h696c62636e2d7631;   // "ilbcn-v1"

    reg [63:0]  bucket;        // current bucket (counts the same tick as the cores)
    reg [31:0]  since;         // ticks since the last beacon was due
    reg [255:0] body;          // 32-byte beacon body, MSB-first
    reg [5:0]   bcnt;          // bytes remaining to emit (0..32)
    reg         emitting;
    reg         dropped;

    // this tick completes a stride (STRIDE>=1)
    wire beacon_due = (since + 32'd1 >= STRIDE);

    assign b_valid   = emitting;
    assign b_data    = body[255:248];
    assign b_last    = emitting && (bcnt == 6'd1);
    assign b_dropped = dropped;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bucket <= BUCKET0; since <= 32'd0; emitting <= 1'b0; bcnt <= 6'd0; dropped <= 1'b0;
        end else begin
            dropped <= 1'b0;
            if (bucket_tick) begin
                bucket <= bucket + 64'd1;
                if (beacon_due) begin
                    since <= 32'd0;
                    if (!emitting) begin
                        // report the bucket index that starts at this edge (bucket+1)
                        body     <= {MAGIC, IID, bucket + 64'd1, TICK_PERIOD_NS, STRIDE};
                        bcnt     <= 6'd32;
                        emitting <= 1'b1;
                    end else begin
                        dropped <= 1'b1;     // previous beacon still draining -> skip
                    end
                end else begin
                    since <= since + 32'd1;
                end
            end
            // stream the latched body out, one byte per accepted beat
            if (emitting && b_ready) begin
                body <= body << 8;
                bcnt <= bcnt - 6'd1;
                if (bcnt == 6'd1) emitting <= 1'b0;
            end
        end
    end
endmodule
`default_nettype wire
