// hmac_sha256 — HMAC-SHA-256 over a streamed byte message, wrapping a single
// sha256_msg engine. Same auto-framed interface as sha256_msg: the caller
// streams an in_last-delimited message (in_valid/in_ready, in_data byte 0 in
// [7:0], in_bytes 1..4, in_last) and reads back one digest per message. There
// is no start strobe and no mode bit — the block always computes HMAC-SHA-256:
//
//   inner = SHA256( (K' ^ ipad) || message )
//   mac   = SHA256( (K' ^ opad) || inner )
//
// where K' is the secret KEY zero-padded to the 64-byte block. The wrapper
// injects the ipad/opad block ahead of the caller's stream and runs the outer
// pass itself, so the caller is unaware of the two-pass structure. For a plain
// (un-keyed) hash, instantiate sha256_msg directly — it presents this same
// interface.
//
// A message is framed by in_last, like sha256_msg: the first in_valid beat
// after reset or a finished mac begins a new message (in_ready stays low while
// the ipad block is injected, so that first beat is naturally held). Unlike
// sha256_msg, the two passes share one engine and cannot overlap, so HMACs do
// not pipeline back-to-back — the next message is taken only once the previous
// mac completes. (cert_build drives one message per certificate period, far
// longer than a mac, so this costs nothing there.)
//
// key is held on a port (the prototype ties it to a build-time constant; a
// real deployment drives it from secure storage). It is sampled continuously,
// so it must be stable across a message.

module hmac_sha256
  import sha256_pkg::swap32;
(
  input  wire         clk,
  input  wire         rst_n,

  input  wire [255:0] key,          // HMAC secret (32 bytes, byte 0 in MSBs)

  input  wire         in_valid,
  output wire         in_ready,
  input  wire [31:0]  in_data,      // message byte 0 in [7:0]
  input  wire [2:0]   in_bytes,     // valid bytes 1..4
  input  wire         in_last,

  output wire         done,
  output wire [255:0] digest,
  output wire [31:0]  len           // message byte count, valid with done
);

  // Key padded to the block and XORed with the HMAC constants, byte 0 in the
  // MSBs. key occupies the first 32 bytes; the rest is zero before the XOR.
  wire [511:0] kipad = {key ^ {32{8'h36}}, {32{8'h36}}};
  wire [511:0] kopad = {key ^ {32{8'h5c}}, {32{8'h5c}}};

  typedef enum logic [2:0] {
    H_IDLE, H_IPAD, H_IMSG, H_IWAIT, H_OPAD, H_OMSG, H_OWAIT
  } state_t;
  state_t state;

  // Feed shift register for the injected regions (ipad / opad / inner), top
  // word first, byte 0 in the MSBs; fcnt counts the words left to inject.
  logic [511:0] fbuf;
  logic [4:0]   fcnt;
  logic [255:0] inner;              // inner-pass digest
  wire  [31:0]  fword = swap32(fbuf[511:480]);

  // sha256_msg handshake. The engine auto-frames on in_last, so the inner and
  // outer passes (each closed by m_last) start themselves — no start strobe.
  logic         m_valid, m_last;
  logic [31:0]  m_data;
  logic [2:0]   m_bytes;
  wire          m_ready, m_done;
  wire [255:0]  m_digest;

  sha256_msg u_msg (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (m_valid),
    .in_ready (m_ready),
    .in_data  (m_data),
    .in_bytes (m_bytes),
    .in_last  (m_last),
    .done     (m_done),
    .digest   (m_digest)
  );

  // In the transparent phase (the message half of the inner pass) the caller
  // streams straight into the engine; in the inject phases the engine is fed
  // from the shift register and the caller is back-pressured.
  wire transparent = (state == H_IMSG);
  wire inject      = (state == H_IPAD) || (state == H_OPAD) || (state == H_OMSG);
  assign in_ready = transparent && m_ready;

  always_comb begin
    if (transparent) begin
      m_valid = in_valid;
      m_data  = in_data;
      m_bytes = in_bytes;
      m_last  = in_last;
    end else begin
      m_valid = inject;
      m_data  = fword;
      m_bytes = 3'd4;
      m_last  = (state == H_OMSG) && (fcnt == 1);   // inner digest closes the outer hash
    end
  end

  wire m_fire = m_valid && m_ready;

  logic         done_r;
  logic [255:0] digest_r;
  logic [31:0]  msg_len;            // caller bytes absorbed for this message
  logic [31:0]  len_r;
  assign done   = done_r;
  assign digest = digest_r;
  assign len    = len_r;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= H_IDLE;
      fbuf     <= '0;
      fcnt     <= '0;
      inner    <= '0;
      msg_len  <= '0;
      done_r   <= 1'b0;
      digest_r <= '0;
      len_r    <= '0;
    end else begin
      done_r  <= 1'b0;

      case (state)
        // queue the ipad block ahead of the message; the first in_valid beat
        // frames the message (held by in_ready low until H_IMSG). The engine
        // auto-starts on the first injected beat.
        H_IDLE: if (in_valid) begin
          fbuf    <= kipad;
          fcnt    <= 5'd16;
          msg_len <= '0;
          state   <= H_IPAD;
        end

        // ---- HMAC inner: SHA256( (K^ipad) || message ) ----
        H_IPAD: if (m_fire) begin
          fbuf <= fbuf << 32;
          fcnt <= fcnt - 5'd1;
          if (fcnt == 1) state <= H_IMSG;
        end
        H_IMSG: if (in_valid && m_ready) begin
          msg_len <= msg_len + {29'b0, in_bytes};   // count caller bytes for len
          if (in_last) state <= H_IWAIT;
        end
        H_IWAIT: if (m_done) begin
          inner   <= m_digest;                // inner done; queue the outer pass
          fbuf    <= kopad;
          fcnt    <= 5'd16;
          state   <= H_OPAD;
        end

        // ---- HMAC outer: SHA256( (K^opad) || inner ) ----
        H_OPAD: if (m_fire) begin
          fbuf <= fbuf << 32;
          fcnt <= fcnt - 5'd1;
          if (fcnt == 1) begin fbuf <= {inner, 256'b0}; fcnt <= 5'd8; state <= H_OMSG; end
        end
        H_OMSG: if (m_fire) begin
          fbuf <= fbuf << 32;
          fcnt <= fcnt - 5'd1;
          if (fcnt == 1) state <= H_OWAIT;
        end
        H_OWAIT: if (m_done) begin digest_r <= m_digest; len_r <= msg_len; done_r <= 1'b1; state <= H_IDLE; end

        default: state <= H_IDLE;
      endcase
    end
  end

endmodule
