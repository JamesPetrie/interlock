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

sd_create_scalar_port -sd_name ${sd_name} -port_name {PHY_MDC} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PHY_RST} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {PKT_LED_1} -port_direction {OUT}
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

# Two packet-counter heartbeats, one per port -- see pkt_counter.sv. The
# counters follow the PHYSICAL ports, so they did not move when the roles
# were swapped below; the hosts behind them did.
#   PKT_LED   (LED_5 / B26): accepted frames on Port 0 / J15 (Spark side)
#   PKT_LED_1 (LED_4 / F22): accepted frames on Port 1 / J30 (Pi side)
# The Sub-PR #5 debug LEDs were retired 2026-08-20: LED_10's sticky
# RCG_ERROR latches during initial link-up, so it reads red on every
# power cycle regardless of health and cannot distinguish the two.
#
# They are HELD LOW here rather than deleted. Dropping the ports left the
# pins Unassigned (confirmed in top_pinrpt_number.rpt), and an unassigned
# PolarFire I/O does not turn its LED off -- on silicon all six stayed lit,
# which reads worse than the debug display we were trying to remove. These
# LEDs are active high (RCG_ERROR asserting is what made LED_10 red), so
# GND is off. Keeping the ports also keeps the pins named and fixed, so a
# later change cannot silently reuse them.

sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_D25} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_C26} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_C27} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_F23} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_H22} -port_direction {OUT}
sd_create_scalar_port -sd_name ${sd_name} -port_name {LED_OFF_H21} -port_direction {OUT}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_D25} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_C26} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_C27} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_F23} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_H22} -value {GND}
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {LED_OFF_H21} -value {GND}

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

# Second packet counter: the Port 1 heartbeat.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {pkt_counter} -hdl_file {hdl\pkt_counter.sv} -instance_name {pkt_counter_1}

# -------------------------------------------------------------------------
# Port 1 instances (second VSC8575 PHY port; internal loopback for now)
# -------------------------------------------------------------------------

# Second CoreTSE — uses a SEPARATE SmartDesign component (CORETSE_1, see
# src_components/CORETSE_1.tcl) with MDIO_PHYID=19 baked in, distinct
# from CORETSE_0's MDIO_PHYID=18.  Each component generates RTL with
# auto-namespaced module names (CORETSE_X_CORETSE_X_0_CORETSE), so the
# two definitions live alongside each other without conflict.
#
# This replaces the failed CoreTSE_with_param wrapper approach from
# earlier — Libero's HDL+ core path didn't accept wrappers referencing
# externally-generated modules.  The two-component approach side-steps
# that tooling limitation.
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
sd_connect_pins_to_constant -sd_name ${sd_name} -pin_names {CORETSE_1:SIGNAL_DETECT} -value {VCC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:SYNC}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_INTR}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:TSM_CONTROL}
# CORETSE_1's MDIO master:
#   - MDC: unused — only CORETSE_0's master generates MDC on the shared bus.
#     Firmware never initiates MDIO transactions from CORETSE_1's APB, so its
#     master stays idle and its MDC output isn't needed.
#   - MDO, MDOEN: NOT marked unused.  These outputs carry CORETSE_1's
#     internal MDIO slave's response (slave at MDIO_PHYID=19).  Sub-PR #9
#     wires them through the mdio_combiner module (below) so the slave's
#     response reaches the bus.  Sub-PR #8 mistakenly marked these unused,
#     which left the slave electrically silent (PHY 19 returned 0xFFFF).
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {CORETSE_1:MDC}
# CORETSE_1:MDI wired to BIBUF_0:Y below (added to the existing CORETSE_0:MDI net).
# CORETSE_1:MDO and CORETSE_1:MDOEN wired through mdio_combiner_0 below.

sd_instantiate_component -sd_name ${sd_name} -component_name {PF_IOD_CDR_C1} -instance_name {PF_IOD_CDR_C1_0}
sd_mark_pins_unused -sd_name ${sd_name} -pin_names {PF_IOD_CDR_C1_0:RX_VAL}

# Note: PF_IOD_CDR_CCC is SHARED with Port 0 (only one instance globally).
# Two PF_IOD_CDR_CCCs on the same device edge fight for the same HS_IO_CLK
# globals; sharing avoids that conflict at the cost of per-port PLL isolation.

sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {SSDetect} -hdl_file {hdl\SSDetect.v} -instance_name {SSDetect_1}

# iter-4 — tse1_loopback: CORETSE_1's MDIO bus is now wholly INTERNAL to
# the FPGA fabric.  CORETSE_1's master + slave communicate only with each
# other through this loopback; the external MDIO bus (to the VSC8575)
# carries only CORETSE_0's transactions.
#
# Reason: iter-3 verbose diagnostics confirmed CORETSE_1's slave at PHY 19
# was electrically indistinguishable from "nothing there" (matched the
# PHY 17 unused-address negative control exactly).  Hypothesis: CoreTSE's
# slave logic requires its own master to be generating MDC; with the
# master idle (firmware accessing PHY 19 via CORETSE_0's master), the
# slave never engages.  iter-4 tests this by giving CORETSE_1 its own
# private MDIO bus.
#
# The sub-PR #9 mdio_combiner is no longer needed and is not
# instantiated in this iteration.  src_hdl/mdio_combiner.sv stays in the
# project but unused.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {tse1_loopback} -hdl_file {hdl\tse1_loopback.sv} -instance_name {tse1_loopback_0}

# fabric_bridge — routes BOTH MAC directions through the fabric.
#
# PORT ROLES ARE SWAPPED RELATIVE TO THE RTL'S NAMING (2026-08-20).
# fabric_bridge's tse0 bundle is the CLIENT side -- reframe_rsp forces
# DST=MAC_CLIENT and the certificates egress there -- and tse1 is the SERVER
# side. Wiring below binds CORETSE_1 (J30) to tse0 and CORETSE_0 (J15) to
# tse1, so the certified client link comes out of J30 and the compute link
# out of J15:
#
#   J30 / Port 1 / CORETSE_1  ->  tse0 (client) ->  Raspberry Pi, sees certs
#   J15 / Port 0 / CORETSE_0  ->  tse1 (server) ->  Spark, quarantined compute
#
# Done here rather than in the PDC on purpose: the two SGMII pairs use
# opposite RX polarity conventions and Libero enforces PDCPF-13 differently
# on each (see io_constraints.pdc), and the PF_IOD_CDR instances are bound to
# specific transceiver lanes. Swapping the MAC bundles leaves every pin,
# polarity and CDR binding exactly as it was.
#
# THE CABLES MUST MATCH: Pi -> J30, Spark -> J15. Swapping one without the
# other puts the verifier on the compute link and no certificates reach it.
sd_instantiate_hdl_module -sd_name ${sd_name} -hdl_module_name {fabric_bridge} -hdl_file {hdl\fabric_bridge.sv} -instance_name {fabric_bridge_0}



# =========================================================================
# Net connections
# -------------------------------------------------------------------------

# =========================== Shared / common ============================
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:A" "CORESPI_0_0:PRESETN" "CoreUARTapb_0:PRESETN" "Core_reset_pf_0:FABRIC_RESET_N" "MIV_RV32_C0_0:RESETN" "PF_IOD_CDR_CCC_C0_0:ARST_N" "PHY_RST" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:B" "PF_IOD_CDR_CCC_C0_0:PLL_LOCK" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"AND2_2:Y" "CORETSE_0:PRESETN" "CORETSE_1:PRESETN" "PF_IOD_CDR_C0_0:RST_N" "PF_IOD_CDR_C1_0:RST_N" "SSDetect_0:rst_b" "SSDetect_1:rst_b" "pkt_counter_0:rst_n" "pkt_counter_1:rst_n" "fabric_bridge_0:rst_n" }
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
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:PCLK" "CORETSE_0:MRXCLK" "CORETSE_0:MTXCLK" "CORETSE_0:PCLK" "CORETSE_1:MRXCLK" "CORETSE_1:MTXCLK" "CORETSE_1:PCLK" "CoreUARTapb_0:PCLK" "Core_reset_pf_0:CLK" "MIV_RV32_C0_0:CLK" "PF_CCC_0_0:OUT0_FABCLK_0" "SSDetect_0:rck" "SSDetect_1:rck" "pkt_counter_0:clk" "pkt_counter_1:clk" "fabric_bridge_0:clk" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISCLKO" "SPISCLKO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISDI" "SPISDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISDO" "SPISDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:SPISS[0:0]" "SPISS" }
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
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORESPI_0_0:APB_bif" "CoreAPB3_0_0:APBmslave2" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APB3mmaster" "MIV_RV32_C0_0:APB_MSTR" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CoreAPB3_0_0:APBmslave1" "CoreUARTapb_0:APB_bif" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:CDR_CLOCKS" "PF_IOD_CDR_C1_0:CDR_CLOCKS" "PF_IOD_CDR_CCC_C0_0:CDR_CLOCKS" }

# ============= Port 0  (Mac side: CORETSE_0 / PF_IOD_CDR_C0 / SSDetect_0) ===
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:RX_N" "RX_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:RX_P" "RX_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:TX_N" "TX_N" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:TX_P" "TX_P" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C0_0:STREAM_START" "SSDetect_0:stream_start" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RXCLK" "CORETSE_0:TBI_RX_CLK" "PF_IOD_CDR_C0_0:RX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:RCG" "PF_IOD_CDR_C0_0:RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:TCG" "PF_IOD_CDR_C0_0:TX_DATA" "SSDetect_0:rx_data" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXRDY"       "fabric_bridge_0:tse1_mrx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXSOF"       "fabric_bridge_0:tse1_mrx_sof" "pkt_counter_0:frame_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXEOF"       "fabric_bridge_0:tse1_mrx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXDAT"       "fabric_bridge_0:tse1_mrx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXBYTEVALID" "fabric_bridge_0:tse1_mrx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MRXACPT"      "fabric_bridge_0:tse1_mrx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXRDY"       "fabric_bridge_0:tse1_mtx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXSOF"       "fabric_bridge_0:tse1_mtx_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXEOF"       "fabric_bridge_0:tse1_mtx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXDAT"       "fabric_bridge_0:tse1_mtx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXBYTEVALID" "fabric_bridge_0:tse1_mtx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MTXACPT"      "fabric_bridge_0:tse1_mtx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:D" "CORETSE_0:MDO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:E" "CORETSE_0:MDOEN" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:Y" "CORETSE_0:MDI" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"BIBUF_0:PAD" "PHY_MDIO" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:MDC" "PHY_MDC" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_0:APBS" "CoreAPB3_0_0:APBmslave0" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"pkt_counter_0:led" "PKT_LED" }

# ============= Port 1  (Spark side: CORETSE_1 / PF_IOD_CDR_C1 / SSDetect_1) =
# Mirror of Port 0. Differences vs Port 0 are noted.
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:RX_N" "RX_N_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:RX_P" "RX_P_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:TX_N" "TX_N_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:TX_P" "TX_P_1" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"PF_IOD_CDR_C1_0:STREAM_START" "SSDetect_1:stream_start" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RXCLK" "CORETSE_1:TBI_RX_CLK" "PF_IOD_CDR_C1_0:RX_CLK_R" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:RCG" "PF_IOD_CDR_C1_0:RX_DATA" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:TCG" "PF_IOD_CDR_C1_0:TX_DATA" "SSDetect_1:rx_data" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXRDY"       "fabric_bridge_0:tse0_mrx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXSOF"       "fabric_bridge_0:tse0_mrx_sof" "pkt_counter_1:frame_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXEOF"       "fabric_bridge_0:tse0_mrx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXDAT"       "fabric_bridge_0:tse0_mrx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXBYTEVALID" "fabric_bridge_0:tse0_mrx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MRXACPT"      "fabric_bridge_0:tse0_mrx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXRDY"       "fabric_bridge_0:tse0_mtx_rdy" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXSOF"       "fabric_bridge_0:tse0_mtx_sof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXEOF"       "fabric_bridge_0:tse0_mtx_eof" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXDAT"       "fabric_bridge_0:tse0_mtx_dat" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXBYTEVALID" "fabric_bridge_0:tse0_mtx_bytevalid" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MTXACPT"      "fabric_bridge_0:tse0_mtx_acpt" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDO"   "tse1_loopback_0:mdo" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDOEN" "tse1_loopback_0:mdoen" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:MDI"   "tse1_loopback_0:mdi" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"CORETSE_1:APBS" "CoreAPB3_0_0:APBmslave3" }
sd_connect_pins -sd_name ${sd_name} -pin_names {"pkt_counter_1:led" "PKT_LED_1" }

# Re-enable auto promotion of pins of type 'pad'
auto_promote_pad_pins -promote_all 1
# Save the smartDesign
save_smartdesign -sd_name ${sd_name}
# Generate SmartDesign top
generate_component -component_name ${sd_name}
