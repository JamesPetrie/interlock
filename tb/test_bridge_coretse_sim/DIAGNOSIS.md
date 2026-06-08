# fabric_bridge "no forwarding on hardware" — investigation

## Symptom
`eth_sanitize` programs the MPF300 and both ports link up, but no frames are
forwarded port-to-port. A prior **transparent cross-wire** version (direct
`MRX→MTX`, no logic) forwarded fine (SSH worked). The only functional diff
`main → eth_sanitize` is inserting `fabric_bridge` (`eth_deframe → eth_reframe`);
firmware, CoreTSE config, pins, and timing constraints are byte-identical.

## What was ruled OUT (the bridge logic is correct)
- **BFM sim** (`tb_bridge_hw.sv`, iverilog): bridge forwards + sanitizes.
- **Real-CoreTSE sim** (`tb_bridge_coretse.sv`, ModelSim, two *encrypted* CoreTSE
  MACs in near-end loopback with the bridge between A.MRX and B.MTX): bridge
  forwards + sanitizes correctly — forced DST `02:..:02` / SRC `02:..:01`, LEN and
  payload preserved, valid FCS (the receiving MAC's RX accepts it). Holds even at
  **HW-matched clocking** (fabric ~80 MHz / line 125 MHz) and with **CRC-insert
  OFF** egress (CFG2 `0x7201`, matching the firmware).
- **Timing**: fabric clock = 80 MHz, **+3.0 ns** worst slack, zero violations.
- **Synthesis (Synplify)**: only benign dead-code elimination (`sent_bytes[1:0]`
  are always 0; unused header dst/src bytes removed because reframe forces them).
- **Combinational loops**: 2, both *inside* the CoreTSE IP (`tsmac_top/amcxfif`),
  present in the working version too — benign.

## Decisive hardware datum
Streaming frames into the FPGA, the egress Spark NIC shows **no change in any
`ethtool -S` counter — not even `rx_crc_errors`/`rx_errors`**. So it receives
*nothing* (not corrupt frames — *no* frames).

## Leading hypothesis: cabling, not a bridge bug
The VSC8575 is a **quad PHY**; the eval kit has multiple RJ45 jacks. A Spark cable
will link (carrier=1) on a jack even if that jack is **not** one of the two the
design actually uses (Port 0 = `CORETSE_0`; Port 1 = `CORETSE_1`, the "J20" jack).
If the USB-C dongle (added hastily for the loopback test) is in the wrong jack,
the FPGA forwards correctly **out J20** but the dongle never sees it → exactly the
observed "ingress blinks, egress link-on-but-idle, zero egress counters."

## Next steps (need hands/eyes on the board)
1. **Cabling first (most likely fix):** put BOTH Spark cables in the design's two
   jacks — Port 0 and Port 1 (J20). Re-run the two-NIC forward test.
2. **Tiebreaker via the existing debug LEDs** while streaming:
   - `PKT_LED` (= `CORETSE_0:MRXSOF`) / `LED_DBG_RX1_CNT` (= `CORETSE_1:MRXSOF`)
     pulse iff that MAC RX is actually receiving frames from its jack.
   - `LED_DBG_MTXACPT_0/1` = that MAC TX ready-to-accept.
   This localizes ingress-vs-egress definitively.

A rebuild will NOT help — the bridge is proven correct in simulation. Do not
spend Libero build cycles chasing this until the cabling is confirmed.
