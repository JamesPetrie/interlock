# Second CoreTSE component instance, distinct from CORETSE_0 to expose
# MDIO_PHYID as 19 (vs CORETSE_0's 18).
#
# Generates RTL where the inner module is named
# CORETSE_1_CORETSE_1_0_CORETSE (vs CORETSE_0_CORETSE_0_0_CORETSE for the
# other component), so the two definitions don't collide at synthesis.
#
# Previous "ACT_UNIQUE_CORETSE_TOP" failure (sub-PR #3 iteration 1) may
# have been from an older IP version or different config — current IP
# (CoreTSE 3.2.119) has a single auto-namespaced module per component.
create_and_configure_core -core_vlnv Actel:DirectCore:CORETSE:$CORETSEver -component_name {CORETSE_1} -params {\
"GMII_TBI:1"  \
"MDIO_PHYID:19"  \
"PACKET_SIZE:11"  \
"SAL:true"  \
"SLIP_ENABLE:false"  \
"STATS:true"  \
"WoL:true"   }
