package eth_pkg;

  localparam int unsigned MAC_ADDR_BYTES =    6;
  localparam int unsigned ETH_LEN_BYTES  =    2;
  localparam int unsigned ETH_LEN_MAX    = 1500;
  localparam int unsigned ETH_FCS_BYTES  =    4;

  typedef logic [(MAC_ADDR_BYTES*8) - 1:0] mac_addr_t;
  typedef logic [(ETH_LEN_BYTES *8) - 1:0]  eth_len_t;

  typedef struct packed {
    mac_addr_t dst;        // destination MAC
    mac_addr_t src;        // source MAC
    eth_len_t  len_type;   // Length/Type field
  } eth_header_t;

  localparam int unsigned ETH_HDR_BYTES       = $bits(eth_header_t) / 8;
  localparam int unsigned ETH_FRAME_BYTES_MAX = ETH_LEN_MAX + ETH_HDR_BYTES + ETH_FCS_BYTES;
  localparam int unsigned ETH_FRAME_BYTES_MIN = 64;
  localparam int unsigned ETH_DATA_MIN        = ETH_FRAME_BYTES_MIN - ETH_HDR_BYTES - ETH_FCS_BYTES;

  // ---- byte-granular wire type ---- (after ETH_HDR_BYTES, which it depends on)
  // FLAT bit vector, NOT a 2D packed array: wire byte k occupies bits [8*k +: 8].
  // A 2D packed array (`logic [N-1:0][7:0]`) sliced inside concatenation-shifts
  // simulates correctly (Icarus/ModelSim) but can synthesize with a different
  // byte ordering on SynplifyPro — a silent synth-vs-sim mismatch. Everything
  // here uses explicit flat `+:` slices, which every tool interprets identically.
  typedef logic [ETH_HDR_BYTES*8-1:0] eth_hdr_bytes_t;   // wire byte k = bits [8*k +: 8]

  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order (dst, src, len_type) mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  function automatic eth_header_t eth_hdr_from_bytes(input eth_hdr_bytes_t b);
    eth_hdr_bytes_t r;
    for (int k = 0; k < ETH_HDR_BYTES; k++)
      r[8*(ETH_HDR_BYTES-1-k) +: 8] = b[8*k +: 8];
    return eth_header_t'(r);
  endfunction

  // Inverse: serialize the struct back to wire octets (octet 0 first).
  function automatic eth_hdr_bytes_t eth_hdr_to_bytes(input eth_header_t h);
    eth_hdr_bytes_t r = eth_hdr_bytes_t'(h);
    eth_hdr_bytes_t b;
    for (int k = 0; k < ETH_HDR_BYTES; k++)
      b[8*k +: 8] = r[8*(ETH_HDR_BYTES-1-k) +: 8];
    return b;
  endfunction

endpackage : eth_pkg
