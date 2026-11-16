# Second PF_IOD_CDR — drives Port 1's SGMII pins. Parameters mirror C0 exactly;
# only the component_name differs.
create_and_configure_core -core_vlnv Actel:SystemBuilder:PF_IOD_CDR:$PF_IOD_CDRver -component_name {PF_IOD_CDR_C1} -params {\
"CLOCK_TYPE:ASYNCHRONOUS" \
"DATA_RATE:1250" \
"DATA_WIDTH:9" \
"EXPOSE_DIAGNOSTIC_PORT:false" \
"EYE_WINDOW_N:6" \
"EYE_WINDOW_P:5" \
"FLAG_OFFSET_SIZE:4" \
"JUMP_STEP_SIZE:3" \
"LVDS_FAILSAFE_EN:false" \
"RATIO:5" \
"RX_BIT_SLIP_EN:false" \
"RX_CLK_MODE:CDR4" \
"RX_ENABLED:true" \
"RX_ONLY:false" \
"SIMULATION_MODE:FULL" \
"TX_ENABLED:true" \
"TX_TRAINING_MODE:AUTO" }
