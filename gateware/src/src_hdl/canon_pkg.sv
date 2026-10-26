// canon_pkg — typedefs for the interlock's canon packet formats.

package canon_pkg;

  // Redundant with the qualified eth_pkg:: references below, but Libero's
  // compile-order scanner only tracks import statements — without this,
  // canon_pkg compiles before eth_pkg and synthesis fails.
  import eth_pkg::*;

  // ---- format-level size bound ----
  // A canonical packet (header + payload) must fit a single Ethernet frame's
  // DATA field — no segmentation (see docs/ethernet-frame-sanitization.md).
  localparam int unsigned CANON_PKT_BYTES_MAX = eth_pkg::ETH_LEN_MAX;

  // ---- field widths ----
  // Header word layout (docs/verification-protocol.md, *Packet formats*):
  localparam int unsigned CANON_PLD_LEN_W   =   32;
  localparam int unsigned CANON_TYPE_W      =   32;
  localparam int unsigned CANON_DIR_W       =   16;
  localparam int unsigned CANON_BKT_W       =   32;
  localparam int unsigned CANON_SLOT_W      =   16;
  localparam int unsigned CANON_REQ_RSVD_W  =   64;
  localparam int unsigned CANON_KCOMMIT_W   =  256;
  localparam int unsigned CANON_NONCE_W     =  128;
  localparam int unsigned CANON_RSP_RSVD_W  =  320;


  localparam int unsigned CANON_TOK_BYTES   =    2; // recomp_feed only supports TOK_BYTES <= 2 currently


  // ---- scalar field types ----
  typedef logic [CANON_PLD_LEN_W-1:0]        canon_len_t;
  typedef logic [CANON_BKT_W-1:0]            canon_bkt_t;
  typedef logic [CANON_SLOT_W-1:0]           canon_slot_t;
  typedef logic [CANON_KCOMMIT_W-1:0]        canon_kcommit_t;

  localparam canon_slot_t CANON_NUM_SLOT  = canon_slot_t'(16'd64);

  typedef logic [8*CANON_TOK_BYTES - 1:0]    canon_tok_t;

  // ---- TYPE (low nibble of the word, upper bits zero) ----
  typedef enum logic [CANON_TYPE_W-1:0] {
    CANON_TYPE_CERT    = 32'h0, // certificate (all-zero header)
    CANON_TYPE_NONCE   = 32'h1, // nonce (control, inbound)
    CANON_TYPE_SYNC    = 32'h2, // sync (control, feedback)
    CANON_TYPE_EXP     = 32'h3, // expected slice commitment (recomp ingress)
    CANON_TYPE_TRAFFIC = 32'hF  // request or response, per DIR
  } canon_type_t;

  // ---- pipeline direction ----
  // Selects which canonical header type a datapath block parses: requests
  // (client -> server) or responses (the reverse).
  typedef enum logic [CANON_DIR_W-1:0] {
    CANON_DIR_REQ = 16'h0000,
    CANON_DIR_RSP = 16'hFFFF
    // TODO add DIR_RECOMP instead of reusing DIR_REQ
  } canon_dir_t;

  // ---- position ----
  // ID = DIR ‖ BUCKET ‖ SLOT, the packet's position in its log. The headers
  // carry the three fields directly; REFERENCE carries them as one value.
  typedef struct packed {
    canon_dir_t  dir;
    canon_bkt_t  bucket;
    canon_slot_t slot;
  } canon_id_t;

  // =====================================================================
  // Request packet
  // =====================================================================
  typedef struct packed {
    canon_len_t              pld_len;      // total payload length in bytes (incl. encryption tag)
    canon_type_t             pkt_type;     // TYPE: traffic or one of the control packets
    canon_dir_t              dir;          // Direction of the packet
    canon_bkt_t              bucket;       // Index of the bucket the packet is targeting
    canon_slot_t             slot;         // Slot within the bucket
    canon_id_t               reference;    // Reference transaction used as context
    logic [CANON_REQ_RSVD_W-1:0] reserved0;
    canon_kcommit_t              key_commit;
  } canon_req_hdr_t;

  localparam int unsigned CANON_REQ_HDR_BITS  = $bits(canon_req_hdr_t);
  localparam int unsigned CANON_REQ_HDR_BYTES = CANON_REQ_HDR_BITS / 8;

  // ---- flat wire-header type ---- (after CANON_REQ_HDR_BITS, which it depends on)
  typedef logic [CANON_REQ_HDR_BITS-1:0] canon_req_hdr_bits_t;

  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  function automatic canon_req_hdr_t canon_req_hdr_from_wire_bits(input canon_req_hdr_bits_t b);
    canon_req_hdr_bits_t tmp; // intermediate bitvector
    int k;                    // forward byte index
    int rk;                   // mirror (reverse) byte index

    // Byte reverse
    // Note: hand-rolled because Icarus does not implement `{<<8{...}}`.
    for (k = 0; k < CANON_REQ_HDR_BYTES; k++) begin
      rk = CANON_REQ_HDR_BYTES-1-k;
      tmp[8*rk +: 8] = b[8*k +: 8];
    end

    // Cast to header struct type
    return canon_req_hdr_t'(tmp);
  endfunction

  // Inverse: serialize the struct back to wire octets.
  function automatic canon_req_hdr_bits_t canon_req_hdr_to_wire_bits(input canon_req_hdr_t h);
    canon_req_hdr_bits_t tmp; // intermediate bitvector
    int k;                    // forward byte index
    int rk;                   // mirror (reverse) byte index

    // Cast to bitvector type
    tmp = canon_req_hdr_bits_t'(h);

    // Byte reverse
    for (k = 0; k < CANON_REQ_HDR_BYTES; k++) begin
      rk = CANON_REQ_HDR_BYTES-1-k;
      canon_req_hdr_to_wire_bits[8*k +: 8] = tmp[8*rk +: 8];
    end
  endfunction

  // =====================================================================
  // Response packet
  // =====================================================================
  // by the interlock.

  typedef struct packed {
    canon_len_t                  pld_len;   // total payload length in bytes (incl. encryption tag)
    canon_type_t                 pkt_type;  // TYPE: traffic or one of the control packets
    canon_dir_t                  dir;       // Direction of the packet
    canon_bkt_t                  bucket;    // Index of the bucket the packet is targeting
    canon_slot_t                 slot;      // Slot within the bucket
    canon_id_t                   reference; // Reference transaction used as context
    logic [CANON_RSP_RSVD_W-1:0] reserved0;
  } canon_rsp_hdr_t;

  localparam int unsigned CANON_RSP_HDR_BITS  = $bits(canon_rsp_hdr_t);
  localparam int unsigned CANON_RSP_HDR_BYTES = CANON_RSP_HDR_BITS / 8;

  // ---- flat wire-header type ---- (after CANON_RSP_HDR_BITS, which it depends on)
  typedef logic [CANON_RSP_HDR_BITS-1:0] canon_rsp_hdr_bits_t;

  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  function automatic canon_rsp_hdr_t canon_rsp_hdr_from_wire_bits(input canon_rsp_hdr_bits_t b);
    canon_rsp_hdr_bits_t tmp; // intermediate bitvector
    int k;                    // forward byte index
    int rk;                   // mirror (reverse) byte index

    // Byte reverse
    // Note: hand-rolled because Icarus does not implement `{<<8{...}}`.
    for (k = 0; k < CANON_RSP_HDR_BYTES; k++) begin
      rk = CANON_RSP_HDR_BYTES-1-k;
      tmp[8*rk +: 8] = b[8*k +: 8];
    end

    // Cast to header struct type
    return canon_rsp_hdr_t'(tmp);
  endfunction

  // Inverse: serialize the struct back to wire octets.
  function automatic canon_rsp_hdr_bits_t canon_rsp_hdr_to_wire_bits(input canon_rsp_hdr_t h);
    canon_rsp_hdr_bits_t tmp; // intermediate bitvector
    int k;                    // forward byte index
    int rk;                   // mirror (reverse) byte index

    // Cast to bitvector type
    tmp = canon_rsp_hdr_bits_t'(h);

    // Byte reverse
    for (k = 0; k < CANON_RSP_HDR_BYTES; k++) begin
      rk = CANON_RSP_HDR_BYTES-1-k;
      canon_rsp_hdr_to_wire_bits[8*k +: 8] = tmp[8*rk +: 8];
    end
  endfunction

endpackage : canon_pkg
