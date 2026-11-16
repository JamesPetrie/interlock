# Exporting Component Description of PF_IOD_CDR_CCC_C0 to TCL
# Family: PolarFire
# Part Number: MPF300TS-1FCG1152I
# Create and Configure the core component PF_IOD_CDR_CCC_C0
create_and_configure_core -core_vlnv Actel:SystemBuilder:PF_IOD_CDR_CCC:$PF_IOD_CDR_CCCver -component_name {PF_IOD_CDR_CCC_C0} -params {\
"CCC_PLL_MULTIPLIER:5" \
"CCC_PLL_REF_FREQ:125.000" \
"DATA_RATE:1250" \
"GENERATE_TX_FAB_CLK:true" \
"TX_CLK_G_FREQ:125.000" }
# Exporting Component Description of PF_IOD_CDR_CCC_C0 to TCL done
