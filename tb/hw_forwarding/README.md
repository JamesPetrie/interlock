# Hardware forwarding + TYPE-drop test scripts (run on spark-c191)

Host-NIC tests that run against a flashed MPF300 bridge (no VM/USB needed).
Companions to `~/fpe/canon_fwd.sh` and `~/fpe/canon_send.py` on the Spark.

- `canon_send_type.py <iface> [count] [datalen] [ethertype_hex]` — sends
  Ethernet II / TYPE frames (EtherType >= 1536, default 0x86DD IPv6) into a port.
  These are the frames the deframe must drop (L/T > ETH_LEN_MAX=1500).
- `type_drop.sh [send-iface] [recv-iface] [count] [plen]` — 3-phase test:
  (1) LENGTH frames forward, (2) TYPE 0x86DD frames dropped (==0), (3) LENGTH
  frames still forward (proves no wedge). PASS iff base1>0 && typed==0 && base2>0.

See `docs/eth_sanitize-hw-findings.md` for results.
