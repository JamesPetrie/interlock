// canon_pkg — typedefs for the interlock's canon packet formats.

package canon_pkg;

  // ---- field widths ----
  localparam int unsigned CANON_ID_W        =   64;
  localparam int unsigned CANON_PKT_IDX_W   =   16;
  localparam int unsigned CANON_PLD_LEN_W   =   16;
  localparam int unsigned CANON_TOK_BYTES   =    4;
  localparam int unsigned CANON_ENC_TAG_LEN =   32;

  // ---- scalar field types ----
  typedef logic [CANON_PKT_IDX_W-1:0]        canon_pkt_idx_t;
  typedef logic [CANON_PLD_LEN_W-1:0]        canon_pld_len_t;

  // Request identifier
  typedef struct packed {
    logic              inf;     // MSB 1: inference packet, 0: other
    logic [CANON_ID_W-1-1:0] id_cont; // unique identifier bits continued
  } canon_id_t;

  // =====================================================================
  // Request packet
  // =====================================================================
  typedef struct packed {
    canon_id_t          id;           // request ID + fragment tag (inf bit selects payload unit)
    canon_pkt_idx_t     pkt_idx;      // the index of the current packet within the request
    // canon_id_t          refs;         // REVISIT Can we get rid of this in the first version?
    canon_pld_len_t     pld_len;      // total payload length in bytes (incl. encryption tag)
    // hash_t        hash;               // REVISIT do we even need this?
  } canon_req_hdr_t;

  localparam int unsigned CANON_REQ_HDR_BYTES = $bits(canon_req_hdr_t) / 8;

  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  typedef logic [CANON_REQ_HDR_BYTES-1:0][7:0] canon_req_hdr_bytes_t;

  function automatic canon_req_hdr_t canon_req_hdr_from_bytes(input canon_req_hdr_bytes_t b);
    canon_req_hdr_bytes_t r;
    for (int k = 0; k < CANON_REQ_HDR_BYTES; k++) r[CANON_REQ_HDR_BYTES-1-k] = b[k];
    return canon_req_hdr_t'(r);
  endfunction

  function automatic canon_req_hdr_bytes_t canon_req_hdr_to_bytes(input canon_req_hdr_t h);
    canon_req_hdr_bytes_t r = canon_req_hdr_bytes_t'(h);
    canon_req_hdr_bytes_t b;
    for (int k = 0; k < CANON_REQ_HDR_BYTES; k++) b[k] = r[CANON_REQ_HDR_BYTES-1-k];
    return b;
  endfunction

  // =====================================================================
  // Response packet
  // =====================================================================
  // by the interlock.
  typedef struct packed {
    canon_id_t          id;           // matches the originating request
    canon_pkt_idx_t     pkt_idx;      // the index of the current packet within the response
    canon_pld_len_t     pld_len;      // total payload length in bytes (incl. encryption tag)
  } canon_rsp_hdr_t;

  localparam int unsigned CANON_RSP_HDR_BYTES = $bits(canon_rsp_hdr_t) / 8;


  // Parse a wire header (octet 0 first) into the struct. Each multi-octet field
  // is big-endian on the wire (first octet = MSB). Because the struct field
  // order mirrors the wire field order and every field is
  // big-endian, parsing is a plain byte-reverse of the whole header
  // reinterpreted as the struct — octet 0 lands at the struct MSB.
  typedef logic [CANON_RSP_HDR_BYTES-1:0][7:0] canon_rsp_hdr_bytes_t;

  function automatic canon_rsp_hdr_t canon_rsp_hdr_from_bytes(input canon_rsp_hdr_bytes_t b);
    canon_rsp_hdr_bytes_t r;
    for (int k = 0; k < CANON_RSP_HDR_BYTES; k++) r[CANON_RSP_HDR_BYTES-1-k] = b[k];
    return canon_rsp_hdr_t'(r);
  endfunction

  function automatic canon_rsp_hdr_bytes_t canon_rsp_hdr_to_bytes(input canon_rsp_hdr_t h);
    canon_rsp_hdr_bytes_t r = canon_rsp_hdr_bytes_t'(h);
    canon_rsp_hdr_bytes_t b;
    for (int k = 0; k < CANON_RSP_HDR_BYTES; k++) b[k] = r[CANON_RSP_HDR_BYTES-1-k];
    return b;
  endfunction

endpackage : canon_pkg
