# PF_XCVR_ERM_C0 — two transceiver lanes carrying 1G Ethernet to the SFP+ cages.
#
# Replaces the MPF300 kit's PF_IOD_CDR pair.  The MPF200-ETH-SENSOR-BRIDGE-R2
# has no copper PHY; its two SFP+ cages sit directly on XCVR quad 2, lane 0
# (SFP1) and lane 1 (SFP2).  CoreTSE stays in TBI mode and keeps doing the
# whole PCS (8b/10b, comma alignment, clause 37 / SGMII auto-negotiation), so
# the transceiver runs in plain PMA mode with a 10-bit fabric interface, exactly
# as AN4623 Appendix 4 table 7-2 prescribes:
#   data rate 1250 Mb/s, TX clock division 4 (from the 5000 Mb/s TX PLL),
#   CDR lock to data, PCS-fabric width 10 bits at 125 MHz, PMA mode,
#   regional TX/RX clocks, PCS reset RX only.
# Differences from the appendix: two lanes in one component (same quad, shared
# TX PLL and reference), CDR reference 156.25 MHz (the board's quad-2 clock),
# and enhanced receiver management off (no CTRL_CLK/CTRL_ARST_N ports).
#
# The lane outputs LANEn_TX_CLK_R / LANEn_RX_CLK_R (125 MHz) take over the
# roles of PF_IOD_CDR_CCC TX_CLK_G and PF_IOD_CDR RX_CLK_R; LANEn_TX_DATA /
# LANEn_RX_DATA are the 10-bit TCG/RCG buses.
#
# Interface-clock notes from the Libero dry runs (keep Regional/Regional):
#  - Place-and-route aborts at PRE_PLACEMENT with "Fail to create regional
#    clock region for ... LANEn" whenever the lane pads are NOT assigned in the
#    PDC; the regional clock needs the lane location (io_constraints.pdc has it).
#  - Per-lane "Global" TX clocks fail PRPF_029 ("XCVR Quad uses 2 RX/TX global
#    clocks; only one global per quad").  If a global TX clock is ever wanted,
#    it has to be "Global (Shared)" (one TX_CLK_G for the quad, TX_WCLK inputs).
#  - TX and RX both Global is rejected by the configurator's DRC.
#
# Version: the 2025.2 catalog ships 3.1.206 (min Libero 2025.1); the same
# parameter set was verified on 3.1.205 under Libero 2024.2.
create_and_configure_core -core_vlnv Actel:SystemBuilder:PF_XCVR_ERM:$PF_XCVR_ERMver -component_name {PF_XCVR_ERM_C0} -params {\
"EXPOSE_ALL_DEBUG_PORTS:false" \
"EXPOSE_FWF_EN_PORTS:false" \
"SHOW_UNIVERSAL_SOLN_PORTS:false" \
"UI_CDR_LOCK_MODE:Lock to data" \
"UI_CDR_REFERENCE_CLK_FREQ:156.25" \
"UI_CDR_REFERENCE_CLK_SOURCE:Dedicated" \
"UI_CDR_REFERENCE_CLK_TOLERANCE:1" \
"UI_ENABLE_32BIT_DATA_WIDTH:false" \
"UI_ENABLE_64B66B:false" \
"UI_ENABLE_64B67B:false" \
"UI_ENABLE_64B6XB_MODE:false" \
"UI_ENABLE_8B10B_MODE:false" \
"UI_ENABLE_BER:false" \
"UI_ENABLE_DISPARITY:false" \
"UI_ENABLE_FIBRE_CHANNEL_DISPARITY:false" \
"UI_ENABLE_PHASE_COMP_MODE:false" \
"UI_ENABLE_PIPE_MODE:false" \
"UI_ENABLE_PMA_MODE:true" \
"UI_ENABLE_SCRAMBLING:false" \
"UI_ENABLE_SWITCH_BETWEEN_CDR_REFCLKS:false" \
"UI_ENABLE_SWITCH_BETWEEN_TXPLLS:false" \
"UI_EXPOSE_APBLINK_PORTS:false" \
"UI_EXPOSE_CDR_BITSLIP_PORT:false" \
"UI_EXPOSE_DYNAMIC_RECONFIGURATION_PORTS:false" \
"UI_EXPOSE_JA_CLOCK_PORT:false" \
"UI_EXPOSE_RX_READY_VAL_CDR_PORT:false" \
"UI_EXPOSE_TX_BYPASS_DATA:false" \
"UI_EXPOSE_TX_ELEC_IDLE:false" \
"UI_INTERFACE_RXCLOCK:Regional" \
"UI_INTERFACE_TXCLOCK:Regional" \
"UI_IS_CONFIGURED:true" \
"UI_NUMBER_OF_LANES:2" \
"UI_PCS_ARST_N:RX Only" \
"UI_PMA_ARST_N:TX and RX PMA" \
"UI_PROTOCOL_PRESET_USED:None" \
"UI_RX_DATA_RATE:1250" \
"UI_RX_PCS_FAB_IF_WIDTH:10" \
"UI_TX_CLK_DIV_FACTOR:4" \
"UI_TX_DATA_RATE:1250" \
"UI_TX_PCS_FAB_IF_WIDTH:10" \
"UI_TX_RX_MODE:Duplex" \
"UI_USE_INTERFACE_CLK_AS_PLL_REFCLK:false" \
"UI_XCVR_RX_CALIBRATION:None (CDR)" \
"UI_XCVR_RX_DATA_EYE_CALIBRATION:false" \
"UI_XCVR_RX_DFE_COEFF_CALIBRATION:false" \
"UI_XCVR_RX_ENHANCED_MANAGEMENT:false" \
"XT_ES_DEVICE:false" }
