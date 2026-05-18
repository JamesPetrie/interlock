# Creating SmartDesign top
set sd_name {top}
create_smartdesign -sd_name ${sd_name}

# Disable auto promotion of pins of type 'pad'
auto_promote_pad_pins -promote_all 0

# Create top level Scalar Ports
sd_create_scalar_port -sd_name ${sd_name} -port_name {REFCLK_N} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {REFCLK_P} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {REF_CLK_0} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RESET_N} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX_N} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX_P} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SPISDI} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TCK} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TDI} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TMS} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TRSTB} -port_direction {IN}

sd_create_scalar_port -sd_name ${sd_name} -port_name {LINK_OK} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PHY_MDC} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PHY_RST} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RD_BC_ERROR} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PKT_LED} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {REF_CLK_SEL} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SPISCLKO} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SPISDO} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SPISS} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TDO} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX_N} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX_P} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {coma_mode} -port_direction {OUT}

# Port 1 SGMII pins — second VSC8575 RJ45 (J20)
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX_N_1} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX_P_1} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX_N_1} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX_P_1} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LINK_OK_1} -port_direction {OUT}

# Sub-PR #5 debug LEDs — instrumentation for diagnosing the bidirectional
# bridge. Each LED exposes one internal signal so a single flash cycle
# yields four extra diagnostic bits.
#   LED_DBG_MTXACPT_1 (LED_8 / C27) : CORETSE_1:MTXACPT — is CORETSE_1's
#       TX side asserting "ready to accept" on the A→B bridge?
#   LED_DBG_MTXACPT_0 (LED_9 / F23) : CORETSE_0:MTXACPT — same for the
#       reverse direction (B→A bridge).
#   LED_DBG_RCG_ERR_1 (LED_10 / H22): Sticky CORETSE_1:RCG_ERROR — latches
#       high on the first 8b/10b decode error on Port 1's TBI RX (clears
#       only on reset). LED off = clean SGMII reception on Port 1.
#   LED_DBG_RX1_CNT   (LED_11 / H21): bit-0 of a frame counter on
#       CORETSE_1:MRXSOF — toggles per frame that CORETSE_1 receives
#       from the Spark side.
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_DBG_MTXACPT_1} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_DBG_MTXACPT_0} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_DBG_RCG_ERR_1} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_DBG_RX1_CNT}   -port_direction {OUT}

sd_create_scalar_port -sd_name ${sd_name} -port_name {PHY_MDIO} -port_direction {INOUT} -port_is_pad {1}


sd_invert_pins -sd_name ${sd_name} -pin_names {coma_mode}
# Add AND2_2 instance
sd_instantiate_macro -sd_name ${sd_name} -macro_name {AND2} -instance_name {AND2_2}



# Add BIBUF_0 instance
sd_instantiate_macro -sd_name ${sd_name} -macro_name {BIBUF} -instance_name {BIBUF_0}



# Add Core_reset_pf_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {Core_reset_pf} -instance_name {Core_reset_pf_0}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {Core_reset_pf_0:SS_BUSY} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {Core_reset_pf_0:FF_US_RESTORE} -value {GND}



# Add CoreAPB3_0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CoreAPB3_0} -instance_name {CoreAPB3_0_0}



# Add COREJTAGDEBUG_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {COREJTAGDEBUG_C0} -instance_name {COREJTAGDEBUG_C0_0}



# Add CORESPI_0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CORESPI_0} -instance_name {CORESPI_0_0}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[0:0]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[1:1]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[1:1]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[2:2]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[2:2]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[3:3]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[3:3]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[4:4]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[4:4]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[5:5]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[5:5]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[6:6]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[6:6]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[7:7]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[7:7]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIINT}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIRXAVAIL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPITXRFM}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISSI} -value {VCC}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPICLKI} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIOEN}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIMODE}



# Add CORETSE_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CORETSE_0} -instance_name {CORETSE_0}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[0:0]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[0:0]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[1:1]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[1:1]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[2:2]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[2:2]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[3:3]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[3:3]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[4:4]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[4:4]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[5:5]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[5:5]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[6:6]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[6:6]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[7:7]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[7:7]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[8:8]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_0:ANX_STATE} -pin_slices {[9:9]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:ANX_STATE[9:9]}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_0:STBP} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_0:MTXCFRM} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:MTXHWM}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_0:SIGNAL_DETECT} -value {VCC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:SYNC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:TSM_INTR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:TSM_CONTROL}



# Add CoreUARTapb_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CoreUARTapb_0} -instance_name {CoreUARTapb_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:TXRDY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:RXRDY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:PARITY_ERR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:OVERFLOW}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:FRAMING_ERR}



# Add INBUF_DIFF_0 instance
sd_instantiate_macro -sd_name ${sd_name} -macro_name {INBUF_DIFF} -instance_name {INBUF_DIFF_0}



# Add MIV_RV32_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {MIV_RV32_C0} -instance_name {MIV_RV32_C0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:JTAG_TDO_DR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:EXT_RESETN}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:EXT_IRQ} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:TIME_COUNT_OUT}



# Add PF_CCC_0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_CCC_0} -instance_name {PF_CCC_0_0}



# Add pf_init_monitor_0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {pf_init_monitor_0} -instance_name {pf_init_monitor_0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:PCIE_INIT_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:USRAM_INIT_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:SRAM_INIT_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:XCVR_INIT_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:USRAM_INIT_FROM_SNVM_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:USRAM_INIT_FROM_UPROM_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:USRAM_INIT_FROM_SPI_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:SRAM_INIT_FROM_SNVM_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:SRAM_INIT_FROM_UPROM_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:SRAM_INIT_FROM_SPI_DONE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {pf_init_monitor_0_0:AUTOCALIB_DONE}



# Add PF_IOD_CDR_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_IOD_CDR_C0} -instance_name {PF_IOD_CDR_C0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_IOD_CDR_C0_0:RX_VAL}



# Add PF_IOD_CDR_CCC_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_IOD_CDR_CCC_C0} -instance_name {PF_IOD_CDR_CCC_C0_0}
sd_show_bif_pins -sd_name ${sd_name} -bif_pin_name {PF_IOD_CDR_CCC_C0_0:CDR_CLOCKS} -pin_names {PF_IOD_CDR_CCC_C0_0:PLL_LOCK}



# Add SSDetect_0 instance
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {SSDetect} -hdl_file {hdl\SSDetect.v} -instance_name {SSDetect_0}
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {pkt_counter} -hdl_file {hdl\pkt_counter.sv} -instance_name {pkt_counter_0}

# Sub-PR #5 debug-LED HDL instances.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {pkt_counter} -hdl_file {hdl\pkt_counter.sv} -instance_name {pkt_counter_1}
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {sticky_bit}  -hdl_file {hdl\sticky_bit.sv}  -instance_name {sticky_bit_0}

# -------------------------------------------------------------------------
# Port 1 instances (second VSC8575 PHY port; internal loopback for now)
# -------------------------------------------------------------------------

# Second CoreTSE — second instance of the CORETSE_0 component (sharing the
# same component avoids ACT_UNIQUE_* module-name collisions in CoreTSE's
# evaluation RTL). MDIO_PHYID baked into the config is unused on this
# instance because CORETSE_1's MDIO outputs aren't connected to the bus.
sd_instantiate_component -sd_name ${sd_name} -component_name {CORETSE_0} -instance_name {CORETSE_1}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[0:0]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[0:0]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[1:1]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[1:1]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[2:2]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[2:2]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[3:3]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[3:3]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[4:4]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[4:4]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[5:5]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[5:5]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[6:6]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[6:6]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[7:7]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[7:7]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[8:8]}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORETSE_1:ANX_STATE} -pin_slices {[9:9]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:ANX_STATE[9:9]}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_1:STBP} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_1:MTXCFRM} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MTXHWM}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_1:SIGNAL_DETECT} -value {VCC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:SYNC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_INTR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_CONTROL}
# CORETSE_1's MDIO master pins are unused — both PHYs share the on-board MDIO
# bus, driven only from CORETSE_0.  Tie inputs to safe values; mark outputs unused.
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MDC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MDO}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MDOEN}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_1:MDI} -value {GND}

sd_instantiate_component -sd_name ${sd_name} -component_name {PF_IOD_CDR_C1} -instance_name {PF_IOD_CDR_C1_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_IOD_CDR_C1_0:RX_VAL}

# Note: PF_IOD_CDR_CCC is SHARED with Port 0 (only one instance globally).
# Two PF_IOD_CDR_CCCs on the same device edge fight for the same HS_IO_CLK
# globals; sharing avoids that conflict at the cost of per-port PLL isolation.

sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {SSDetect} -hdl_file {hdl\SSDetect.v} -instance_name {SSDetect_1}



# Add scalar net connections
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:A" "CORESPI_0_0:PRESETN" "CoreUARTapb_0:PRESETN" "Core_reset_pf_0:FABRIC_RESET_N" "MIV_RV32_C0_0:RESETN" "PF_IOD_CDR_CCC_C0_0:ARST_N" "PHY_RST" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:B" "PF_IOD_CDR_CCC_C0_0:PLL_LOCK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:Y" "CORETSE_0:PRESETN" "CORETSE_1:PRESETN" "PF_IOD_CDR_C0_0:RST_N" "PF_IOD_CDR_C1_0:RST_N" "SSDetect_0:rst_b" "SSDetect_1:rst_b" "pkt_counter_0:rst_n" "pkt_counter_1:rst_n" "sticky_bit_0:rst_n" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:D" "CORETSE_0:MDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:E" "CORETSE_0:MDOEN" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:PAD" "PHY_MDIO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:Y" "CORETSE_0:MDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TCK" "TCK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TDI" "TDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TDO" "TDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TGT_TCK_0" "MIV_RV32_C0_0:JTAG_TCK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TGT_TDI_0" "MIV_RV32_C0_0:JTAG_TDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TGT_TDO_0" "MIV_RV32_C0_0:JTAG_TDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TGT_TMS_0" "MIV_RV32_C0_0:JTAG_TMS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TGT_TRSTN_0" "MIV_RV32_C0_0:JTAG_TRSTN" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TMS" "TMS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"COREJTAGDEBUG_C0_0:TRSTB" "TRSTB" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:PCLK" "CORETSE_0:MRXCLK" "CORETSE_0:MTXCLK" "CORETSE_0:PCLK" "CORETSE_1:MRXCLK" "CORETSE_1:MTXCLK" "CORETSE_1:PCLK" "CoreUARTapb_0:PCLK" "Core_reset_pf_0:CLK" "MIV_RV32_C0_0:CLK" "PF_CCC_0_0:OUT0_FABCLK_0" "SSDetect_0:rck" "SSDetect_1:rck" "pkt_counter_0:clk" "pkt_counter_1:clk" "sticky_bit_0:clk" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISCLKO" "SPISCLKO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISDI" "SPISDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISDO" "SPISDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISS[0:0]" "SPISS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:ANX_STATE[8:8]" "LINK_OK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MDC" "PHY_MDC" }
# A→B bridge: CORETSE_0 MAC RX → CORETSE_1 MAC TX (Mac → FPGA → Spark)
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXACPT" "CORETSE_1:MTXACPT" "LED_DBG_MTXACPT_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXEOF" "CORETSE_1:MTXEOF" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXRDY" "CORETSE_1:MTXRDY" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXSOF" "CORETSE_1:MTXSOF" "pkt_counter_0:frame_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"pkt_counter_0:led" "PKT_LED" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RCG_ERROR" "RD_BC_ERROR" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RXCLK" "CORETSE_0:TBI_RX_CLK" "PF_IOD_CDR_C0_0:RX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:TBI_TX_CLK" "CORETSE_0:TXCLK" "CORETSE_1:TBI_TX_CLK" "CORETSE_1:TXCLK" "PF_IOD_CDR_CCC_C0_0:TX_CLK_G" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreUARTapb_0:RX" "RX" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreUARTapb_0:TX" "TX" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:BANK_x_VDDI_STATUS" "Core_reset_pf_0:BANK_y_VDDI_STATUS" "pf_init_monitor_0_0:BANK_6_VDDI_STATUS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:EXT_RST_N" "REF_CLK_SEL" "RESET_N" "coma_mode" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:FPGA_POR_N" "pf_init_monitor_0_0:FABRIC_POR_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:INIT_DONE" "pf_init_monitor_0_0:DEVICE_INIT_DONE" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:PLL_LOCK" "PF_CCC_0_0:PLL_LOCK_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:PLL_POWERDOWN_B" "PF_CCC_0_0:PLL_POWERDOWN_N_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INBUF_DIFF_0:PADN" "REFCLK_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INBUF_DIFF_0:PADP" "REFCLK_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INBUF_DIFF_0:Y" "PF_IOD_CDR_CCC_C0_0:REF_CLK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_CCC_0_0:REF_CLK_0" "REF_CLK_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:RX_N" "RX_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:RX_P" "RX_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:STREAM_START" "SSDetect_0:stream_start" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:TX_N" "TX_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:TX_P" "TX_P" }

# Add bus net connections
# A→B bridge bus signals (continuation of the A→B MAC bridge above)
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXBYTEVALID" "CORETSE_1:MTXBYTEVALID" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXDAT" "CORETSE_1:MTXDAT" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RCG" "PF_IOD_CDR_C0_0:RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:TCG" "PF_IOD_CDR_C0_0:TX_DATA" "SSDetect_0:rx_data" }

# Add bus interface net connections
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:APB_bif" "CoreAPB3_0_0:APBmslave2" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:APBS" "CoreAPB3_0_0:APBmslave0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:APBS" "CoreAPB3_0_0:APBmslave3" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APB3mmaster" "MIV_RV32_C0_0:APB_MSTR" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APBmslave1" "CoreUARTapb_0:APB_bif" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:CDR_CLOCKS" "PF_IOD_CDR_C1_0:CDR_CLOCKS" "PF_IOD_CDR_CCC_C0_0:CDR_CLOCKS" }

# -------------------------------------------------------------------------
# Port 1 wiring — mirrors Port 0's pattern with internal loopback inside
# CORETSE_1 (mac_bridge integration is a later PR). Both IOD CDRs share
# PF_IOD_CDR_CCC_C0_0; the reset / PLL_LOCK chain is shared via AND2_2.
# -------------------------------------------------------------------------

# Port 1 SGMII pin connections
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:RX_N" "RX_N_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:RX_P" "RX_P_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:TX_N" "TX_N_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:TX_P" "TX_P_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:STREAM_START" "SSDetect_1:stream_start" }

# Port 1 TBI clocks — independent recovered RX clock; TX clock shared from
# PF_IOD_CDR_CCC_C0_0 (single CCC drives both ports' TX side).
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RXCLK" "CORETSE_1:TBI_RX_CLK" "PF_IOD_CDR_C1_0:RX_CLK_R" }
# Note: extend the existing CORETSE_0:TBI_TX_CLK line above to include
# CORETSE_1:TBI_TX_CLK and CORETSE_1:TXCLK instead of creating a new net.

# B→A bridge: CORETSE_1 MAC RX → CORETSE_0 MAC TX (Spark → FPGA → Mac).
# Combined with the A→B bridge above, this forms a transparent
# bidirectional ethernet bridge — frames in either port emerge on the
# other. Direct wiring works because both CoreTSE MAC interfaces run on
# the same fabric clock (PF_CCC_0_0:OUT0_FABCLK_0); no FIFO / CDC needed
# for a pass-through. When the interlock Core (drop rules, hash chain,
# attestation) is added in a later PR, mac_bridge will be inserted into
# each direction between the two CoreTSEs at that point.
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXACPT" "CORETSE_0:MTXACPT" "LED_DBG_MTXACPT_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXEOF" "CORETSE_0:MTXEOF" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXRDY" "CORETSE_0:MTXRDY" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXSOF" "CORETSE_0:MTXSOF" "pkt_counter_1:frame_sof" }

# Sub-PR #5 debug-LED wiring:
#   - CORETSE_1:RCG_ERROR -> sticky_bit_0:d -> LED_DBG_RCG_ERR_1
#   - pkt_counter_1:led   -> LED_DBG_RX1_CNT
# CORETSE_0:RCG_ERROR remains on RD_BC_ERROR (LED_4); MTXACPT signals are
# wired into the existing bridge nets above (multi-fanout to LED ports).
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RCG_ERROR" "sticky_bit_0:d" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"sticky_bit_0:q"      "LED_DBG_RCG_ERR_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"pkt_counter_1:led"   "LED_DBG_RX1_CNT" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXBYTEVALID" "CORETSE_0:MTXBYTEVALID" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXDAT" "CORETSE_0:MTXDAT" }

# Port 1 TBI data — IOD CDR <-> CoreTSE, with SSDetect_1 listening on the TCG net
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RCG" "PF_IOD_CDR_C1_0:RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:TCG" "PF_IOD_CDR_C1_0:TX_DATA" "SSDetect_1:rx_data" }

# Port 1 link-up indicator -> LED_6 (C26)
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:ANX_STATE[8:8]" "LINK_OK_1" }

# Re-enable auto promotion of pins of type 'pad'
auto_promote_pad_pins -promote_all 1
# Save the smartDesign
save_smartdesign -sd_name ${sd_name}
# Generate SmartDesign top
generate_component -component_name ${sd_name}
