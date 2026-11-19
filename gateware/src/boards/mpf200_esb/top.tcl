# Creating SmartDesign top — MPF200-ETH-SENSOR-BRIDGE-R2 variant.
#
# Same core as boards/mpf300_eval/top.tcl (two CoreTSE MACs <-> interlock core,
# Mi-V + CoreAPB3 peripherals, CORERESET_PF, PF_CCC 50 -> 80 MHz) with the PHY
# side swapped for this board:
#
#   MPF300-EVAL-KIT            MPF200-ETH-SENSOR-BRIDGE-R2
#   PF_IOD_CDR x2 (SGMII LVDS) PF_XCVR_ERM_C0, 2 lanes, PMA mode, 10-bit TBI
#   PF_IOD_CDR_CCC (TX_CLK_G)  PF_TX_PLL_C0 + per-lane TX_CLK_R / RX_CLK_R (regional)
#   INBUF_DIFF 125 MHz refclk  PF_XCVR_REF_CLK_C0, 156.25 MHz, dedicated pads
#   VSC8575 copper PHY + MDIO  none: SFP modules; both CoreTSE MDIO buses looped
#                              back internally so the Mi-V still reaches the PCS
#   PHY_RST/coma_mode/REF_CLK_SEL/SPI (ZL30364)   gone (CoreSPI kept, pins tied)
#   RESET_N push button        none on this board: EXT_RST_N tied to VCC
#   8 LEDs                     4 LEDs (LED1..LED4)
#   SSDetect (IOD stream start) not needed by the XCVR
#
# SFP sideband: TX_DISABLE is driven from the inverted fabric reset (module
# enabled once the fabric is out of reset); LOS gates CoreTSE SIGNAL_DETECT.
# Pin numbers and sources: io_constraints.pdc; rationale: docs/mpf200-esb-port.md.
set sd_name {top}
create_smartdesign -sd_name ${sd_name}

# Disable auto promotion of pins of type 'pad'
auto_promote_pad_pins -promote_all 0

# ---------------------------------------------------------------------------
# Top level Scalar Ports
# ---------------------------------------------------------------------------
# Clocks
sd_create_scalar_port -sd_name ${sd_name} -port_name {CLK_50M} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {XCVR_REFCLK_P} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {XCVR_REFCLK_N} -port_direction {IN} -port_is_pad {1}

# SFP1 = XCVR quad 2 lane 0 = interlock port 0 (frontend side)
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_RXD_P} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_RXD_N} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_TXD_P} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_TXD_N} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_LOS} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP1_TX_DISABLE} -port_direction {OUT}

# SFP2 = XCVR quad 2 lane 1 = interlock port 1 (compute side)
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_RXD_P} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_RXD_N} -port_direction {IN} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_TXD_P} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_TXD_N} -port_direction {OUT} -port_is_pad {1}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_LOS} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {SFP2_TX_DISABLE} -port_direction {OUT}

# UART to the FT4232H (channel C)
sd_create_scalar_port -sd_name ${sd_name} -port_name {RX} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TX} -port_direction {OUT}

# LEDs (LED1..LED4)
sd_create_scalar_port -sd_name ${sd_name} -port_name {LINK_OK} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LINK_OK_1} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PKT_LED} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_RCG_ERR} -port_direction {OUT}

# JTAG (Mi-V debug through COREJTAGDEBUG)
sd_create_scalar_port -sd_name ${sd_name} -port_name {TCK} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TDI} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TMS} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TRSTB} -port_direction {IN}
sd_create_scalar_port -sd_name ${sd_name} -port_name {TDO} -port_direction {OUT}


# ---------------------------------------------------------------------------
# Glue macros
# ---------------------------------------------------------------------------
# AND2_2: CoreTSE / core reset = fabric reset AND TX PLL lock (the kit gated on
# the IOD CDR CCC's PLL_LOCK; the TX PLL now owns the 125 MHz TBI TX clocks).
sd_instantiate_macro -sd_name ${sd_name} -macro_name {AND2} -instance_name {AND2_2}
# INV_TXDIS: SFP TX_DISABLE = NOT fabric_reset_n (modules enabled when the fabric runs).
sd_instantiate_macro -sd_name ${sd_name} -macro_name {INV} -instance_name {INV_TXDIS}
# INV_LOS0/1: CoreTSE SIGNAL_DETECT = NOT SFPx_LOS (LOS is pulled high with no module).
sd_instantiate_macro -sd_name ${sd_name} -macro_name {INV} -instance_name {INV_LOS0}
sd_instantiate_macro -sd_name ${sd_name} -macro_name {INV} -instance_name {INV_LOS1}
# OR2_RCG: 8b/10b decode error from either port -> sticky LED4.
sd_instantiate_macro -sd_name ${sd_name} -macro_name {OR2} -instance_name {OR2_RCG}


# ---------------------------------------------------------------------------
# Shared instances (identical to the MPF300 kit unless noted)
# ---------------------------------------------------------------------------

# Add Core_reset_pf_0 instance — no reset push button on this board, EXT_RST_N tied high
sd_instantiate_component -sd_name ${sd_name} -component_name {Core_reset_pf} -instance_name {Core_reset_pf_0}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {Core_reset_pf_0:SS_BUSY} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {Core_reset_pf_0:FF_US_RESTORE} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {Core_reset_pf_0:EXT_RST_N} -value {VCC}



# Add CoreAPB3_0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CoreAPB3_0} -instance_name {CoreAPB3_0_0}



# Add COREJTAGDEBUG_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {COREJTAGDEBUG_C0} -instance_name {COREJTAGDEBUG_C0_0}



# Add CORESPI_0_0 instance.
# The ZL30364 clock generator it drove on the MPF300 kit does not exist here.
# The peripheral is kept (APB slot 2) so the firmware's address map and the
# AN4623 SPI driver keep working; its pins are tied off / left unused.
sd_instantiate_component -sd_name ${sd_name} -component_name {CORESPI_0} -instance_name {CORESPI_0_0}
sd_create_pin_slices -sd_name ${sd_name} -pin_name {CORESPI_0_0:SPISS} -pin_slices {[0:0]}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISS[0:0]}
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
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISCLKO}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISDO}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIINT}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIRXAVAIL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPITXRFM}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISDI} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPISSI} -value {VCC}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPICLKI} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIOEN}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORESPI_0_0:SPIMODE}



# Add CORETSE_0 instance (port 0, SFP1).  SIGNAL_DETECT now comes from the
# SFP LOS pin (via INV_LOS0) instead of being tied to VCC; MDC is unused because
# there is no external MDIO bus — the MDIO master/slave pair is looped back
# through tse0_loopback_0 exactly like CORETSE_1 already was on the kit.
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
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:SYNC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:TSM_INTR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:TSM_CONTROL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_0:MDC}



# Add CoreUARTapb_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {CoreUARTapb_0} -instance_name {CoreUARTapb_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:TXRDY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:RXRDY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:PARITY_ERR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:OVERFLOW}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CoreUARTapb_0:FRAMING_ERR}



# Add MIV_RV32_C0_0 instance
sd_instantiate_component -sd_name ${sd_name} -component_name {MIV_RV32_C0} -instance_name {MIV_RV32_C0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:JTAG_TDO_DR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:EXT_RESETN}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:EXT_IRQ} -value {GND}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {MIV_RV32_C0_0:TIME_COUNT_OUT}



# Add PF_CCC_0_0 instance (50 MHz oscillator -> 80 MHz fabric clock, unchanged)
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_CCC_0} -instance_name {PF_CCC_0_0}



# Add pf_init_monitor_0_0 instance (Bank 5 VDDI status variant, see pf_init_monitor_0.tcl)
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



# ---------------------------------------------------------------------------
# Transceiver side (replaces PF_IOD_CDR_C0/C1 + PF_IOD_CDR_CCC_C0 + INBUF_DIFF)
# ---------------------------------------------------------------------------

# Add PF_XCVR_REF_CLK_C0_0 instance — 156.25 MHz LVDS on the dedicated refclk pads
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_XCVR_REF_CLK_C0} -instance_name {PF_XCVR_REF_CLK_C0_0}

# Add PF_TX_PLL_C0_0 instance — 5000 Mb/s base rate from 156.25 MHz; CLK_125 aux output unused
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_TX_PLL_C0} -instance_name {PF_TX_PLL_C0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_TX_PLL_C0_0:CLK_125}

# Add PF_XCVR_ERM_C0_0 instance — lane 0 = SFP1 / port 0, lane 1 = SFP2 / port 1.
# PMA-mode status outputs are not needed: CoreTSE's PCS does its own comma
# alignment and link monitoring on the raw 10-bit stream.
sd_instantiate_component -sd_name ${sd_name} -component_name {PF_XCVR_ERM_C0} -instance_name {PF_XCVR_ERM_C0_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE0_RX_BYPASS_DATA}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE0_RX_IDLE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE0_RX_READY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE0_RX_VAL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE0_TX_CLK_STABLE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE1_RX_BYPASS_DATA}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE1_RX_IDLE}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE1_RX_READY}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE1_RX_VAL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_XCVR_ERM_C0_0:LANE1_TX_CLK_STABLE}



# ---------------------------------------------------------------------------
# HDL instances
# ---------------------------------------------------------------------------
# Port-0 packet heartbeat (LED3) and the sticky 8b/10b error flag (LED4).
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {pkt_counter} -hdl_file {hdl\pkt_counter.sv} -instance_name {pkt_counter_0}
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {sticky_bit}  -hdl_file {hdl\sticky_bit.sv}  -instance_name {sticky_bit_0}

# Internal MDIO loopbacks.  With no external PHY both CoreTSE MDIO buses are
# private: each master talks only to its own internal PCS slave (addresses 18
# and 19), which is what the firmware's PHY-18/PHY-19 accesses already assume.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {tse1_loopback} -hdl_file {hdl\tse1_loopback.sv} -instance_name {tse0_loopback_0}
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {tse1_loopback} -hdl_file {hdl\tse1_loopback.sv} -instance_name {tse1_loopback_0}

# ---------------------------------------------------------------------------
# Port 1 instances
# ---------------------------------------------------------------------------
# Second CoreTSE — separate component with MDIO_PHYID=19 (see src_components/CORETSE_1.tcl).
sd_instantiate_component -sd_name ${sd_name} -component_name {CORETSE_1} -instance_name {CORETSE_1}
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
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:SYNC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_INTR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_CONTROL}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MDC}

# Interlock core — routes BOTH MAC directions through the fabric. $ilock_core
# (common.tcl, from INTERLOCK_CORE) is fabric_bridge (workload, default) or
# recomp_ilock_core (recomputation); same ports, so the wiring below is shared.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name $ilock_core -hdl_file [format {hdl\%s.sv} $ilock_core] -instance_name {core_0}



# =========================================================================
# Net connections
# -------------------------------------------------------------------------

# =========================== Shared / common ============================
# Fabric reset (from CORERESET_PF): Mi-V, APB peripherals, XCVR lane resets,
# and the inverted copy that enables the SFP transmitters.
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:A" "CORESPI_0_0:PRESETN" "CoreUARTapb_0:PRESETN" "Core_reset_pf_0:FABRIC_RESET_N" "MIV_RV32_C0_0:RESETN" "PF_XCVR_ERM_C0_0:LANE0_PCS_ARST_N" "PF_XCVR_ERM_C0_0:LANE0_PMA_ARST_N" "PF_XCVR_ERM_C0_0:LANE1_PCS_ARST_N" "PF_XCVR_ERM_C0_0:LANE1_PMA_ARST_N" "INV_TXDIS:A" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INV_TXDIS:Y" "SFP1_TX_DISABLE" "SFP2_TX_DISABLE" }
# MAC / core reset additionally waits for the TX PLL (source of the TBI TX clocks).
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:B" "PF_TX_PLL_C0_0:PLL_LOCK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:Y" "CORETSE_0:PRESETN" "CORETSE_1:PRESETN" "pkt_counter_0:rst_n" "sticky_bit_0:rst_n" "core_0:rst_n" }
# JTAG
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
# 80 MHz fabric clock
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:PCLK" "CORETSE_0:MRXCLK" "CORETSE_0:MTXCLK" "CORETSE_0:PCLK" "CORETSE_1:MRXCLK" "CORETSE_1:MTXCLK" "CORETSE_1:PCLK" "CoreUARTapb_0:PCLK" "Core_reset_pf_0:CLK" "MIV_RV32_C0_0:CLK" "PF_CCC_0_0:OUT0_FABCLK_0" "pkt_counter_0:clk" "sticky_bit_0:clk" "core_0:clk" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_CCC_0_0:REF_CLK_0" "CLK_50M" }
# UART
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreUARTapb_0:RX" "RX" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreUARTapb_0:TX" "TX" }
# Reset controller inputs
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:BANK_x_VDDI_STATUS" "Core_reset_pf_0:BANK_y_VDDI_STATUS" "pf_init_monitor_0_0:BANK_5_VDDI_STATUS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:FPGA_POR_N" "pf_init_monitor_0_0:FABRIC_POR_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:INIT_DONE" "pf_init_monitor_0_0:DEVICE_INIT_DONE" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:PLL_LOCK" "PF_CCC_0_0:PLL_LOCK_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"Core_reset_pf_0:PLL_POWERDOWN_B" "PF_CCC_0_0:PLL_POWERDOWN_N_0" }
# APB fabric
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:APB_bif" "CoreAPB3_0_0:APBmslave2" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APB3mmaster" "MIV_RV32_C0_0:APB_MSTR" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APBmslave1" "CoreUARTapb_0:APB_bif" }
# Transceiver clocking: refclk pads -> PF_XCVR_REF_CLK -> TX PLL + both CDRs; TX PLL clocks -> lanes
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_REF_CLK_C0_0:REF_CLK_PAD_P" "XCVR_REFCLK_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_REF_CLK_C0_0:REF_CLK_PAD_N" "XCVR_REFCLK_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_REF_CLK_C0_0:REF_CLK" "PF_TX_PLL_C0_0:REF_CLK" "PF_XCVR_ERM_C0_0:LANE0_CDR_REF_CLK_0" "PF_XCVR_ERM_C0_0:LANE1_CDR_REF_CLK_0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_TX_PLL_C0_0:CLKS_TO_XCVR" "PF_XCVR_ERM_C0_0:CLKS_FROM_TXPLL_0" }

# ============= Port 0  (frontend side: SFP1 / lane 0 / CORETSE_0) ==========
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE0_RXD_P" "SFP1_RXD_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE0_RXD_N" "SFP1_RXD_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE0_TXD_P" "SFP1_TXD_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE0_TXD_N" "SFP1_TXD_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RXCLK" "CORETSE_0:TBI_RX_CLK" "PF_XCVR_ERM_C0_0:LANE0_RX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:TXCLK" "CORETSE_0:TBI_TX_CLK" "PF_XCVR_ERM_C0_0:LANE0_TX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RCG" "PF_XCVR_ERM_C0_0:LANE0_RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:TCG" "PF_XCVR_ERM_C0_0:LANE0_TX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INV_LOS0:A" "SFP1_LOS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INV_LOS0:Y" "CORETSE_0:SIGNAL_DETECT" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXRDY"       "core_0:tse0_mrx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXSOF"       "core_0:tse0_mrx_sof" "pkt_counter_0:frame_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXEOF"       "core_0:tse0_mrx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXDAT"       "core_0:tse0_mrx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXBYTEVALID" "core_0:tse0_mrx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXACPT"      "core_0:tse0_mrx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXRDY"       "core_0:tse0_mtx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXSOF"       "core_0:tse0_mtx_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXEOF"       "core_0:tse0_mtx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXDAT"       "core_0:tse0_mtx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXBYTEVALID" "core_0:tse0_mtx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXACPT"      "core_0:tse0_mtx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MDO"   "tse0_loopback_0:mdo" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MDOEN" "tse0_loopback_0:mdoen" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MDI"   "tse0_loopback_0:mdi" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:APBS" "CoreAPB3_0_0:APBmslave0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:ANX_STATE[8:8]" "LINK_OK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RCG_ERROR" "OR2_RCG:A" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"pkt_counter_0:led" "PKT_LED" }

# ============= Port 1  (compute side: SFP2 / lane 1 / CORETSE_1) ===========
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE1_RXD_P" "SFP2_RXD_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE1_RXD_N" "SFP2_RXD_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE1_TXD_P" "SFP2_TXD_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_XCVR_ERM_C0_0:LANE1_TXD_N" "SFP2_TXD_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RXCLK" "CORETSE_1:TBI_RX_CLK" "PF_XCVR_ERM_C0_0:LANE1_RX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:TXCLK" "CORETSE_1:TBI_TX_CLK" "PF_XCVR_ERM_C0_0:LANE1_TX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RCG" "PF_XCVR_ERM_C0_0:LANE1_RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:TCG" "PF_XCVR_ERM_C0_0:LANE1_TX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INV_LOS1:A" "SFP2_LOS" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"INV_LOS1:Y" "CORETSE_1:SIGNAL_DETECT" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXRDY"       "core_0:tse1_mrx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXSOF"       "core_0:tse1_mrx_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXEOF"       "core_0:tse1_mrx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXDAT"       "core_0:tse1_mrx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXBYTEVALID" "core_0:tse1_mrx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXACPT"      "core_0:tse1_mrx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXRDY"       "core_0:tse1_mtx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXSOF"       "core_0:tse1_mtx_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXEOF"       "core_0:tse1_mtx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXDAT"       "core_0:tse1_mtx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXBYTEVALID" "core_0:tse1_mtx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXACPT"      "core_0:tse1_mtx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDO"   "tse1_loopback_0:mdo" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDOEN" "tse1_loopback_0:mdoen" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDI"   "tse1_loopback_0:mdi" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:APBS" "CoreAPB3_0_0:APBmslave3" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:ANX_STATE[8:8]" "LINK_OK_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RCG_ERROR" "OR2_RCG:B" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"OR2_RCG:Y" "sticky_bit_0:d" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"sticky_bit_0:q" "LED_RCG_ERR" }

# Re-enable auto promotion of pins of type 'pad'
auto_promote_pad_pins -promote_all 1
# Save the smartDesign
save_smartdesign -sd_name ${sd_name}
# Generate SmartDesign top
generate_component -component_name ${sd_name}
