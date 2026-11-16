# User timing constraints — MPF200-ETH-SENSOR-BRIDGE-R2 build.
#
# Everything clock-related is derived automatically (derive_constraints_sdc):
# the 50 MHz input, the PF_CCC 80 MHz fabric clock, the TX PLL and the per-lane
# 125 MHz TX_CLK_R / RX_CLK_R of PF_XCVR_ERM_C0.  This file only declares the
# clock domains that are mutually asynchronous (CDC inside the IP / core) and
# constrains the JTAG clock, mirroring the MPF300 kit's SDC minus the IOD CDR
# and VSC8575 MDIO items that do not exist on this board.

# Constraining the JTAG clock to 10 MHz
create_clock -name {TCK} -period 100 -waveform {0 50 } [ get_ports { TCK } ]

# Transceiver lane clocks (recovered RX clocks and TX word clocks) vs. fabric clock.
# CDC between the 125 MHz TBI side and the 80 MHz MAC-FIFO side is inside CoreTSE.
set_clock_groups -name {XCVR_LANE0_RX_GRP} -asynchronous -group [ get_clocks { PF_XCVR_ERM_C0_0/I_XCVR/LANE0/RX_CLK_R } ]
set_clock_groups -name {XCVR_LANE1_RX_GRP} -asynchronous -group [ get_clocks { PF_XCVR_ERM_C0_0/I_XCVR/LANE1/RX_CLK_R } ]
set_clock_groups -name {XCVR_LANE0_TX_GRP} -asynchronous -group [ get_clocks { PF_XCVR_ERM_C0_0/I_XCVR/LANE0/TX_CLK_R } ]
set_clock_groups -name {XCVR_LANE1_TX_GRP} -asynchronous -group [ get_clocks { PF_XCVR_ERM_C0_0/I_XCVR/LANE1/TX_CLK_R } ]
set_clock_groups -name {PF_CCC_0_OUT0_GRP} -asynchronous -group [ get_clocks { PF_CCC_0_0/PF_CCC_0_0/pll_inst_0/OUT0 } ]
set_clock_groups -name {JTAG_Async} -asynchronous -group [ get_clocks { PF_CCC_0_0/PF_CCC_0_0/pll_inst_0/OUT0 } ] -group [ get_clocks { TCK } ]
