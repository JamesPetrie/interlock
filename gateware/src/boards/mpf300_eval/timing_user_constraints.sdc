#Constraining the JTAG clock to 10 Mhz
create_clock -name {TCK} -period 100 -waveform {0 50 } [ get_ports { TCK } ]

#Create PHY_MDC clock with clk/20 factor
create_generated_clock -name {PHY_MDC_CLOCK} -divide_by 28 -source [ get_pins { PF_CCC_0_0/PF_CCC_0_0/pll_inst_0/OUT0 } ] -phase 0 [ get_ports { PHY_MDC } ]

##Input Delay Constraint on the Reset_n pin
set_input_delay 0 -min -add_delay -clock {REF_CLK_0} [ get_ports { RESET_N } ]
set_input_delay 20 -max -add_delay -clock {REF_CLK_0} [ get_ports { RESET_N } ]

###Out delay constraint on the PHY_MDC and PHY_MDIO
set_output_delay 10 -max -clock {PHY_MDC_CLOCK} [ get_ports { PHY_MDIO } ]
set_output_delay 10 -min -clock {PHY_MDC_CLOCK} [ get_ports { PHY_MDIO } ]
set_input_delay 0 -min -add_delay -clock {PHY_MDC_CLOCK} [ get_ports { PHY_MDIO } ]
set_input_delay 20 -max -add_delay -clock {PHY_MDC_CLOCK} [ get_ports { PHY_MDIO } ]

#The below paths are asynchronous and CDC is taken care inside the IP
#The Below paths are set to asynchronous to aid the timing Tool not check the Async to Reg paths
#CDC analysis is run on the design and the report is analysed and it is CDC Clean
set_clock_groups -name {SGMII_CDR_0_0_CLK_OUT_GRP} -asynchronous -group [ get_clocks { PF_IOD_CDR_C0_0/PF_LANECTRL_0/I_LANECTRL/CLK_OUT_R } ]
set_clock_groups -name {Y_DIV_GRP} -asynchronous -group [ get_clocks {PF_IOD_CDR_CCC_C0_0/PF_CLK_DIV_0/I_CD/Y_DIV } ]
set_clock_groups -name {NWC_PLL_OUT0_GRP} -asynchronous -group [ get_clocks { PF_IOD_CDR_CCC_C0_0/PF_CCC_0/pll_inst_0/OUT0 } ]
set_clock_groups -name {NWC_PLL_OUT1_GRP} -asynchronous -group [ get_clocks { PF_IOD_CDR_CCC_C0_0/PF_CCC_0/pll_inst_0/OUT1 } ]
set_clock_groups -name {NWC_PLL_OUT2_GRP} -asynchronous -group [ get_clocks { PF_IOD_CDR_CCC_C0_0/PF_CCC_0/pll_inst_0/OUT2 } ]
set_clock_groups -name {NWC_PLL_OUT3_GRP} -asynchronous -group [ get_clocks { PF_IOD_CDR_CCC_C0_0/PF_CCC_0/pll_inst_0/OUT3} ]
set_clock_groups -name {PF_CCC_0_OUT0_GRP} -asynchronous -group [ get_clocks { PF_CCC_0_0/PF_CCC_0_0/pll_inst_0/OUT0 } ]
set_clock_groups -name {JTAG_Async} -asynchronous -group [ get_clocks { PF_CCC_0_0/PF_CCC_0_0/pll_inst_0/OUT0 } ] -group [ get_clocks { TCK } ]
#set_false_path  -from { SGMII_CDR_0_0/PF_LANECTRL_0/I_LANECTRL/HS_IO_CLK* } -through { SGMII_CDR_0_0/PF_LANECTRL_0/I_LANECTRL/CLK_OUT_R }
