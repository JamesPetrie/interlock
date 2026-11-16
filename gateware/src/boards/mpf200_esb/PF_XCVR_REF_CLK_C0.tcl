# PF_XCVR_REF_CLK_C0 — transceiver reference clock input for XCVR quad 2.
#
# Board: MPF200-ETH-SENSOR-BRIDGE-R2.  Oscillator X1 (DSC1123CI2-156.2500,
# 156.25 MHz LVDS) drives XCVR_2A_REFCLK_P/N = balls W23/W24 (schematic Rev 2
# sheet 6, user guide DS50003757B table 1-2).  This one reference feeds the
# TX PLL and both lanes' CDRs; nothing is routed to the fabric from here (the
# fabric clock comes from the separate 50 MHz oscillator via PF_CCC_0).
create_and_configure_core -core_vlnv Actel:SgCore:PF_XCVR_REF_CLK:$PF_XCVR_REF_CLKver -component_name {PF_XCVR_REF_CLK_C0} -params {\
"ENABLE_FAB_CLK_0:false"  \
"ENABLE_FAB_CLK_1:false"  \
"ENABLE_REF_CLK_0:true"  \
"ENABLE_REF_CLK_1:false"  \
"REF_CLK_MODE_0:DIFFERENTIAL"  \
"REF_CLK_MODE_1:LVCMOS"   }
