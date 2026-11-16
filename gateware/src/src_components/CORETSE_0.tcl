# Exporting Component Description of CORETSE_0 to TCL
# Family: PolarFire
# Part Number: MPF300TS-1FCG1152I
# Create and Configure the core component CORETSE_0
create_and_configure_core -core_vlnv Actel:DirectCore:CORETSE:$CORETSEver -component_name {CORETSE_0} -params {\
"GMII_TBI:1"  \
"MDIO_PHYID:18"  \
"PACKET_SIZE:11"  \
"SAL:true"  \
"SLIP_ENABLE:false"  \
"STATS:true"  \
"WoL:true"   }
# Exporting Component Description of CORETSE_0 to TCL done
