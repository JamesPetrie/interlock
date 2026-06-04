// eth_reframe — Ethernet-layer egress adapter: data in as
// AXI-Stream, CoreTSE MAC-FIFO frame out.
//
// Builds   [HEADER][DATA]<[PAD]>[FCS]
//
// Byte shift register in lock-step: the header is preloaded at SOP, then each
// cycle one MAC word leaves the head while one AXI beat enters the tail; once
// the AXI stream is done, PAD zero-bytes shift in instead. Input and output
// advance together — when either side stalls, both stall. FCS bytes never
// enter the buffer: after the last body word the EMIT_PEN / EMIT_LAST states
// (the fcs_recalc tail pattern) merge the residual body bytes with the FCS,
// which finished folding at least one cycle earlier.
//
// One MAC word out per AXI beat in: streaming, no packet buffer.
// Output is MAC-TX-shaped (out_bytevalid = count of INVALID lanes, last word).

module eth_reframe #(
  parameter logic [47:0] FORCE_DST = 48'h02_00_00_00_00_02,
  parameter logic [47:0] FORCE_SRC = 48'h02_00_00_00_00_01
) (
  input  wire        clk,
  input  wire        rst_n,

  // AXI-Stream slave (canonical packet, tuser = byte length on first beat)
  input  wire        tvalid,
  output wire        tready,
  input  wire [31:0] tdata,
  input  wire [3:0]  tkeep,
  input  wire        tlast,
  input  wire [15:0] tuser,

  // MAC-TX-shaped output (we drive data, sink drives accept)
  output wire        out_rdy,
  input  wire        out_acpt,
  output wire        out_sof,
  output wire        out_eof,
  output wire [31:0] out_dat,
  output wire [1:0]  out_bytevalid
);

  import eth_pkg::*;
  import crc32_pkg::*;

  // ------------------------------------------------------------------
  // Geometry
  // ------------------------------------------------------------------
  localparam int unsigned LEN_W     = 16;                  // Ethernet LENGTH width
  // lock-step: occupancy never exceeds the header preload (pop every advance)
  localparam int unsigned BUF_BYTES = ETH_HDR_BYTES;
  typedef logic [LEN_W-1:0] len_t;

  // PAD octets needed to bring DATA up to the minimum frame size
  function automatic len_t pad_len(input len_t data_bytes);
    return (data_bytes < ETH_DATA_MIN) ? len_t'(ETH_DATA_MIN - data_bytes) : '0;
  endfunction

  // ------------------------------------------------------------------
  // Frame geometry, latched
  // ------------------------------------------------------------------
  len_t data_end;      // HDR + L
  len_t pad_end;       // HDR + L + PAD

  // ------------------------------------------------------------------
  // Byte shift register, input and output in lock-step
  // ------------------------------------------------------------------
  typedef enum logic [2:0] { F_IDLE, F_RUN, F_FOLD_PEN, F_EMIT_PEN, F_EMIT_LAST } state_t;
  state_t                 state;
  logic [BUF_BYTES*8-1:0] buffer;  // flat: iverilog can't part-select 2D packed arrays
  len_t                   fed;     // frame bytes appended so far
  len_t                   sent;    // frame bytes emitted so far
  logic [31:0]            crc_rem;

  wire feeding = fed < data_end;

  wire [31:0] tdata_mask = { {8{tkeep[3]}},
                             {8{tkeep[2]}},
                             {8{tkeep[1]}},
                             {8{tkeep[0]}} };
  wire [31:0] tdata_masked = tdata & tdata_mask;


  // output register
  logic        o_rdy, o_sof, o_eof;
  logic [31:0] o_dat;
  logic [1:0]  o_bv;
  assign out_rdy       = o_rdy;
  assign out_sof       = o_sof;
  assign out_eof       = o_eof;
  assign out_dat       = o_dat;
  assign out_bytevalid = o_bv;

  wire out_handshake = o_rdy && out_acpt;
  wire out_free      = !o_rdy || out_acpt;

  // one word out and (while feeding) one beat in — or everything stalls
  wire advance  = (state == F_RUN) && out_free && (!feeding || tvalid);
  assign tready = (state == F_RUN) && out_free && feeding;
  wire in_handshake = tvalid && tready;

  // body tail occupies pad_end % 4 octets of the penultimate word
  wire [1:0] tail_bytes = pad_end[1:0];

  // full words advance by 4; the final word carries only tail_bytes octets
  wire len_t sent_next = sent + ((state != F_EMIT_LAST) ? 16'd4 : 16'(tail_bytes));
  wire       pen_next  = (state == F_RUN) && (sent_next >= pad_end - 16'd4);   // next word is penultimate

  // FCS (little-endian on the wire). The CRC folds on the *output* stream —
  // header / DATA / PAD alike, 4 bytes per emitted word. After the last body
  // word, F_FOLD_PEN spends one bubble cycle folding the penultimate word's tail
  // bytes, so the EMIT_PEN / EMIT_LAST states read a fully registered CRC.
  wire [31:0] fcs = crc32_final(crc_rem);

  wire [31:0] pen_dat =
      (tail_bytes == 2'd1) ? {fcs[23:0], buffer[ 7:0]}   :
      (tail_bytes == 2'd2) ? {fcs[15:0], buffer[15:0]}   :
      (tail_bytes == 2'd3) ? {fcs[ 7:0], buffer[23:0]}   :
                                         buffer[31:0];      // aligned: all body

  wire [31:0] last_dat =
      (tail_bytes == 2'd1) ? {24'h0, fcs[31:24]}         :
      (tail_bytes == 2'd2) ? {16'h0, fcs[31:16]}         :
      (tail_bytes == 2'd3) ? { 8'h0, fcs[31: 8]}         :
                                     fcs[31: 0];           // aligned: whole FCS

  wire  [1:0] last_bv =
      (tail_bytes == 2'd1) ? 2'b11 :
      (tail_bytes == 2'd2) ? 2'b10 :
      (tail_bytes == 2'd3) ? 2'b01 :
                             2'b00; // aligned: all lanes valid

  // header octets, [k] = wire byte k: forced DST | SRC | regenerated LENGTH.
  // Bit-cast of a plain concat (fields in struct order) because Icarus does
  // not support assignment patterns (eth_header_t'{dst: ..., ...}).
  wire eth_hdr_bytes_t hdr_bytes = eth_hdr_to_bytes(eth_header_t'({FORCE_DST, FORCE_SRC, tuser}));

  // fold appended DATA/PAD bytes into the CRC (header folds at preload)
  function automatic logic [31:0] crc_fold(input logic [31:0] c, input logic [31:0] w,
                                           input logic [2:0] n);
    crc_fold = c;
    for (int k = 0; k < 4; k++)
      if (k < n) crc_fold = crc32_step(crc_fold, w[8*k +: 8]);
  endfunction

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= F_IDLE;
      buffer   <= '0;
      fed      <= '0;
      sent     <= '0;
      crc_rem  <= CRC32_INIT;
      data_end <= '0;
      pad_end  <= '0;
      o_rdy    <= 1'b0;
      o_sof    <= 1'b0;
      o_eof    <= 1'b0;
      o_dat    <= '0;
      o_bv     <= 2'b00;
    end else begin
      if (out_handshake) begin
        o_rdy <= 1'b0;
        o_sof <= 1'b0;
        o_eof <= 1'b0;
        o_bv  <= 2'b00;
      end

      case (state)
        F_IDLE: begin
          // SOP: preload the header (folding it into the CRC) + latch geometry
          if (tvalid) begin
            buffer   <= hdr_bytes;
            fed      <= len_t'(ETH_HDR_BYTES);
            sent     <= '0;
            crc_rem  <= CRC32_INIT;
            data_end <= ETH_HDR_BYTES + tuser;
            pad_end  <= ETH_HDR_BYTES + tuser + pad_len(tuser);
            state    <= F_RUN;
          end
        end

        F_RUN: begin
          if (advance) begin
            // one word out of the head, one (masked) beat into the tail
            // shift in a beat only on AXI handshake; PAD cycles (tready low) feed zeros
            {buffer, o_dat} <= {in_handshake ? tdata_masked : 32'h0, buffer};
            crc_rem         <= crc_fold(crc_rem, buffer[31:0], 3'd4);  // fold the emitted word
            o_rdy           <= 1'b1;
            o_sof           <= (sent == '0);
            sent            <= sent_next;
            if (in_handshake) begin
              fed <= fed + (!tlast ? 16'd4 : $countones(tkeep));  // last beat may be partial
            end

            if (pen_next)
              state <= F_FOLD_PEN;
          end
        end

        F_FOLD_PEN: begin
          // one-cycle bubble: fold the PEN word's body bytes (a whole word
          // when aligned) so the FCS is registered before it is emitted
          crc_rem <= crc_fold(crc_rem, buffer[31:0], !tail_bytes ? 3'd4 : 3'(tail_bytes));
          state   <= F_EMIT_PEN;
        end

        F_EMIT_PEN: begin
          // last body bytes, plus the first FCS bytes when not aligned
          if (out_free) begin
            o_rdy <= 1'b1;
            o_dat <= pen_dat;
            sent  <= sent_next;
            state <= F_EMIT_LAST;
          end
        end

        F_EMIT_LAST: begin
          // remaining FCS bytes; BYTEVALID = unused lanes
          if (out_free) begin
            o_rdy <= 1'b1;
            o_dat <= last_dat;
            o_eof <= 1'b1;
            o_bv  <= last_bv;
            sent  <= sent_next;
            state <= F_IDLE;
          end
        end

        default: state <= F_IDLE;
      endcase
    end
  end

endmodule
