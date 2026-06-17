// sha256_pkg — SHA-256 (FIPS 180-4) primitives, shared by the commitment
// block's hash core. Big-endian throughout: a digest packs H0 in the most
// significant word (digest[255:224]) down to H7 in digest[31:0], matching the
// byte order hashlib emits.
//
// Usage: the compression core seeds {a..h} from a chaining value, runs 64
// rounds via sha256_round over the message schedule (sha256_w_next extends it
// past the first 16 block words), then adds the working vars back into the
// chaining value.

package sha256_pkg;

  // Initial hash value H(0).
  localparam logic [255:0] SHA256_H_INIT = {
    32'h6a09e667, 32'hbb67ae85, 32'h3c6ef372, 32'ha54ff53a,
    32'h510e527f, 32'h9b05688c, 32'h1f83d9ab, 32'h5be0cd19
  };

  // Round constants K[t].
  function automatic logic [31:0] sha256_k(input int t);
    case (t)
      0:  sha256_k = 32'h428a2f98; 1:  sha256_k = 32'h71374491;
      2:  sha256_k = 32'hb5c0fbcf; 3:  sha256_k = 32'he9b5dba5;
      4:  sha256_k = 32'h3956c25b; 5:  sha256_k = 32'h59f111f1;
      6:  sha256_k = 32'h923f82a4; 7:  sha256_k = 32'hab1c5ed5;
      8:  sha256_k = 32'hd807aa98; 9:  sha256_k = 32'h12835b01;
      10: sha256_k = 32'h243185be; 11: sha256_k = 32'h550c7dc3;
      12: sha256_k = 32'h72be5d74; 13: sha256_k = 32'h80deb1fe;
      14: sha256_k = 32'h9bdc06a7; 15: sha256_k = 32'hc19bf174;
      16: sha256_k = 32'he49b69c1; 17: sha256_k = 32'hefbe4786;
      18: sha256_k = 32'h0fc19dc6; 19: sha256_k = 32'h240ca1cc;
      20: sha256_k = 32'h2de92c6f; 21: sha256_k = 32'h4a7484aa;
      22: sha256_k = 32'h5cb0a9dc; 23: sha256_k = 32'h76f988da;
      24: sha256_k = 32'h983e5152; 25: sha256_k = 32'ha831c66d;
      26: sha256_k = 32'hb00327c8; 27: sha256_k = 32'hbf597fc7;
      28: sha256_k = 32'hc6e00bf3; 29: sha256_k = 32'hd5a79147;
      30: sha256_k = 32'h06ca6351; 31: sha256_k = 32'h14292967;
      32: sha256_k = 32'h27b70a85; 33: sha256_k = 32'h2e1b2138;
      34: sha256_k = 32'h4d2c6dfc; 35: sha256_k = 32'h53380d13;
      36: sha256_k = 32'h650a7354; 37: sha256_k = 32'h766a0abb;
      38: sha256_k = 32'h81c2c92e; 39: sha256_k = 32'h92722c85;
      40: sha256_k = 32'ha2bfe8a1; 41: sha256_k = 32'ha81a664b;
      42: sha256_k = 32'hc24b8b70; 43: sha256_k = 32'hc76c51a3;
      44: sha256_k = 32'hd192e819; 45: sha256_k = 32'hd6990624;
      46: sha256_k = 32'hf40e3585; 47: sha256_k = 32'h106aa070;
      48: sha256_k = 32'h19a4c116; 49: sha256_k = 32'h1e376c08;
      50: sha256_k = 32'h2748774c; 51: sha256_k = 32'h34b0bcb5;
      52: sha256_k = 32'h391c0cb3; 53: sha256_k = 32'h4ed8aa4a;
      54: sha256_k = 32'h5b9cca4f; 55: sha256_k = 32'h682e6ff3;
      56: sha256_k = 32'h748f82ee; 57: sha256_k = 32'h78a5636f;
      58: sha256_k = 32'h84c87814; 59: sha256_k = 32'h8cc70208;
      60: sha256_k = 32'h90befffa; 61: sha256_k = 32'ha4506ceb;
      62: sha256_k = 32'hbef9a3f7; 63: sha256_k = 32'hc67178f2;
      default: sha256_k = 32'h0;
    endcase
  endfunction

  function automatic logic [31:0] rotr(input logic [31:0] x, input int n);
    return (x >> n) | (x << (32 - n));
  endfunction

  // Byte-reverse a word: maps a big-endian message word (byte 0 in the MSBs,
  // as the hash buffers hold it) to AXI-Stream lane order (byte 0 in [7:0]).
  // Spelled out rather than the {<<8{g}} stream operator, which Icarus 14 (the
  // sim) does not parse.
  function automatic logic [31:0] swap32(input logic [31:0] g);
    return {g[7:0], g[15:8], g[23:16], g[31:24]};
  endfunction

  function automatic logic [31:0] big_s0(input logic [31:0] x);
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22);
  endfunction
  function automatic logic [31:0] big_s1(input logic [31:0] x);
    return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25);
  endfunction
  function automatic logic [31:0] small_s0(input logic [31:0] x);
    return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3);
  endfunction
  function automatic logic [31:0] small_s1(input logic [31:0] x);
    return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10);
  endfunction
  function automatic logic [31:0] ch(input logic [31:0] e, f, g);
    return (e & f) ^ (~e & g);
  endfunction
  function automatic logic [31:0] maj(input logic [31:0] a, b, c);
    return (a & b) ^ (a & c) ^ (b & c);
  endfunction

  // One compression round. Working vars are packed {a,b,c,d,e,f,g,h} with a in
  // the most significant word; kw is K[t]+W[t] (the only round-varying input).
  function automatic logic [255:0] sha256_round(input logic [255:0] s,
                                                input logic [31:0]  kw);
    logic [31:0] a, b, c, d, e, f, g, h, t1, t2;
    {a, b, c, d, e, f, g, h} = s;
    t1 = h + big_s1(e) + ch(e, f, g) + kw;
    t2 = big_s0(a) + maj(a, b, c);
    sha256_round = {t1 + t2, a, b, c, d + t1, e, f, g};
  endfunction

  // Next message-schedule word from the 16 most recent (w0 oldest = W[t-16],
  // w15 newest = W[t-1]).
  function automatic logic [31:0] sha256_w_next(input logic [31:0] w0, w1, w9, w14);
    return small_s1(w14) + w9 + small_s0(w1) + w0;
  endfunction

endpackage : sha256_pkg
