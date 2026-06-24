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

  localparam int unsigned ETH_HDR_BITS        = $bits(eth_header_t);

  localparam int unsigned ETH_HDR_BYTES       = ETH_HDR_BITS / 8;
  localparam int unsigned ETH_FRAME_BYTES_MAX = ETH_LEN_MAX + ETH_HDR_BYTES + ETH_FCS_BYTES;
  localparam int unsigned ETH_FRAME_BYTES_MIN = 64;
  localparam int unsigned ETH_DATA_MIN        = ETH_FRAME_BYTES_MIN - ETH_HDR_BYTES - ETH_FCS_BYTES;

  // ---- flat wire-header type ---- (after ETH_HDR_BITS, which it depends on)
  typedef logic [ETH_HDR_BITS-1:0] eth_hdr_bits_t;

  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order (dst, src, len_type) mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  function automatic eth_header_t eth_hdr_from_wire_bits(input eth_hdr_bits_t b);
    eth_hdr_bits_t tmp; // intermediate bitvector
    int k;              // forward byte index
    int rk;             // mirror (reverse) byte index

    // Byte reverse
    // Note: hand-rolled because Icarus does not implement `{<<8{...}}`.
    for (k = 0; k < ETH_HDR_BYTES; k++) begin
      rk = ETH_HDR_BYTES-1-k;
      tmp[8*rk +: 8] = b[8*k +: 8];
    end

    // Cast to header struct type
    return eth_header_t'(tmp);
  endfunction

  // Inverse: serialize the struct back to wire octets.
  function automatic eth_hdr_bits_t eth_hdr_to_wire_bits(input eth_header_t h);
    eth_hdr_bits_t tmp; // intermediate bitvector
    int k;              // forward byte index
    int rk;             // mirror (reverse) byte index

    // Cast to bitvector type
    tmp = eth_hdr_bits_t'(h);

    // Byte reverse
    for (k = 0; k < ETH_HDR_BYTES; k++) begin
      rk = ETH_HDR_BYTES-1-k;
      eth_hdr_to_wire_bits[8*k +: 8] = tmp[8*rk +: 8];
    end
  endfunction

endpackage : eth_pkg
