# PF_TX_PLL_C0 — transmit PLL for the two 1.25 Gb/s SGMII/1000BASE-X lanes.
#
# Recipe from AN4623 Appendix 4 ("1G Ethernet BASE-T and BASE-X Using
# Transceiver", table 7-2): TX PLL base data rate 5000 Mb/s (bit clock
# 2500 MHz), lane TX clock division factor 4 -> 1250 Mb/s on the wire.  The
# appendix uses a 125 MHz reference; this board only offers 156.25 MHz on
# quad 2, and 5000 / 156.25 = 32 is an integer multiplier, so the same base
# rate is kept and only TxPLL_REF changes.  Parameter set verified to configure
# cleanly in Libero (PF_TX_PLL 2.0.304) during the port.
create_and_configure_core -core_vlnv Actel:SgCore:PF_TX_PLL:$PF_TX_PLLver -component_name {PF_TX_PLL_C0} -params {\
"CORE:PF_TX_PLL"  \
"INIT:0x0"  \
"TxPLL_AUX_LOW_SEL:true"  \
"TxPLL_AUX_OUT:125"  \
"TxPLL_BANDWIDTH:Low"  \
"TxPLL_CLK_125_EN:false"  \
"TxPLL_DYNAMIC_RECONFIG_INTERFACE_EN:false"  \
"TxPLL_EXT_WAVE_SEL:0"  \
"TxPLL_FAB_LOCK_EN:false"  \
"TxPLL_FAB_REF:200"  \
"TxPLL_INTEGER_MODE:false"  \
"TxPLL_JITTER_MODE_AT_POWERUP:false"  \
"TxPLL_LANE_ALIGNMENT_EN:false"  \
"TxPLL_MODE:NORMAL"  \
"TxPLL_OUT:2500.000"  \
"TxPLL_REF:156.25"  \
"TxPLL_RN_FILTER:false"  \
"TxPLL_SOURCE:DEDICATED"  \
"TxPLL_SSM_DEPTH:0"  \
"TxPLL_SSM_DIVVAL:1"  \
"TxPLL_SSM_DOWN_SPREAD:false"  \
"TxPLL_SSM_FREQ:64"  \
"TxPLL_SSM_RAND_PATTERN:0"   }
