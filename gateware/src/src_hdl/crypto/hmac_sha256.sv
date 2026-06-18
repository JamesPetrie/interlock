// hmac_sha256 — HMAC-SHA-256 (and a transparent plain-SHA mode) over a
// streamed byte message, wrapping a single sha256_msg engine.
//
// The caller streams the message exactly as it would to sha256_msg (start,
// in_valid/in_ready, in_data byte 0 in [7:0], in_bytes 1..4, in_last). With
// hmac_en low the bytes pass straight through and digest = SHA-256(message).
// With hmac_en high the wrapper computes HMAC-SHA-256:
//
//   inner = SHA256( (K' ^ ipad) || message )
//   mac   = SHA256( (K' ^ opad) || inner )
//
// where K' is the secret KEY zero-padded to the 64-byte block. The wrapper
// injects the ipad/opad block ahead of the caller's stream and runs the outer
// pass itself, so the caller is unaware of the two-pass structure — it just
// streams a message and reads back a digest. This lets traffic_commit drive
// one engine for both the plain hash chain and the HMAC seed/attestation.
//
// key is held on a port (the prototype ties it to a build-time constant; a
// real deployment drives it from secure storage). It is sampled continuously,
// so it must be stable across a start..done.

module hmac_sha256
  import sha256_pkg::swap32;
(
  input  wire         clk,
  input  wire         rst_n,

  input  wire [255:0] key,          // HMAC secret (32 bytes, byte 0 in MSBs)

  input  wire         start,        // begin; hmac_en sampled here
  input  wire         hmac_en,      // 1: HMAC, 0: plain SHA-256
  input  wire         in_valid,
  output wire         in_ready,
  input  wire [31:0]  in_data,      // message byte 0 in [7:0]
  input  wire [2:0]   in_bytes,     // valid bytes 1..4
  input  wire         in_last,

  output wire         done,
  output wire [255:0] digest
);

  // Key padded to the block and XORed with the HMAC constants, byte 0 in the
  // MSBs. key occupies the first 32 bytes; the rest is zero before the XOR.
  wire [511:0] kipad = {key ^ {32{8'h36}}, {32{8'h36}}};
  wire [511:0] kopad = {key ^ {32{8'h5c}}, {32{8'h5c}}};

  typedef enum logic [2:0] {
    H_IDLE, H_PLAIN, H_IPAD, H_IMSG, H_IWAIT, H_OPAD, H_OMSG, H_OWAIT
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

  // In the transparent phases (plain hash, or the message half of the inner
  // pass) the caller streams straight into the engine; in the inject phases the
  // engine is fed from the shift register and the caller is back-pressured.
  wire transparent = (state == H_PLAIN) || (state == H_IMSG);
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
  assign done   = done_r;
  assign digest = digest_r;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= H_IDLE;
      fbuf     <= '0;
      fcnt     <= '0;
      inner    <= '0;
      done_r   <= 1'b0;
      digest_r <= '0;
    end else begin
      done_r  <= 1'b0;

      case (state)
        // for HMAC, queue the ipad block ahead of the message (the engine
        // auto-starts on the first injected/streamed beat)
        H_IDLE: if (start) begin
          fbuf    <= kipad;
          fcnt    <= 5'd16;
          state   <= state_t'(hmac_en ? H_IPAD : H_PLAIN);
        end

        // ---- plain SHA-256: transparent passthrough ----
        H_PLAIN: if (m_done) begin digest_r <= m_digest; done_r <= 1'b1; state <= H_IDLE; end

        // ---- HMAC inner: SHA256( (K^ipad) || message ) ----
        H_IPAD: if (m_fire) begin
          fbuf <= fbuf << 32;
          fcnt <= fcnt - 5'd1;
          if (fcnt == 1) state <= H_IMSG;
        end
        H_IMSG: if (in_valid && m_ready && in_last) state <= H_IWAIT;
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
        H_OWAIT: if (m_done) begin digest_r <= m_digest; done_r <= 1'b1; state <= H_IDLE; end

        default: state <= H_IDLE;
      endcase
    end
  end

endmodule
