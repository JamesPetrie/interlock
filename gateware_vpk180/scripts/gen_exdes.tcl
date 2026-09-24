# Step 1 on the Versal machine: configure one MRMAC from params.tcl and let
# Vivado generate its example design — GT quad, clocking, resets, the
# AXI-Lite bring-up logic and a packet generator/monitor, all validated by AMD
# for that preset. integrate.tcl then swaps the generator/monitor for ilock_pl.
#
#   vivado -mode batch -source scripts/gen_exdes.tcl
#
# Output: build/exdes_gen (throwaway project holding the .xci) and
#         build/exdes/mrmac_0_ex (the example design project).
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
if {$ILOCK_BOARD_REPO ne ""} { set_param board.repoPaths [list $ILOCK_BOARD_REPO] }

create_project mrmac_exdes_gen $root/build/exdes_gen -part $ILOCK_PART -force
catch { set_property board_part $ILOCK_BOARD_PART [current_project] }

create_ip -name mrmac -vendor xilinx.com -library ip -version 3.2 -module_name mrmac_0
# Preset first (it rewrites the dependent parameters), then the overrides.
set_property -dict [list \
    CONFIG.MRMAC_PRESET_C0                   $MRMAC_PRESET \
    CONFIG.GT_TYPE_C0                        {GTM} \
] [get_ips mrmac_0]
set_property -dict [list \
    CONFIG.GT_SIG_MODE                       $GT_SIG_MODE \
    CONFIG.GT_REF_CLK_FREQ_C0                $GT_REFCLK_MHZ \
    CONFIG.MRMAC_DATA_PATH_INTERFACE_PORT0_C0 $MRMAC_AXIS_IF \
    CONFIG.FEC_SLICE0_CFG_C0                 $MRMAC_FEC \
    CONFIG.MRMAC_LOCATION_C0                 $MRMAC0_LOC \
    CONFIG.MRMAC_EXDES_GT_LOCATION_C0        $PORT0_GT_QUAD \
    CONFIG.MRMAC_EXDES_GT_REFCLK_LOCATION_C0 $PORT0_GT_REFCLK \
] [get_ips mrmac_0]
report_property [get_ips mrmac_0] -regexp {CONFIG\.(MRMAC_SPEED|MAC_PORT0_RATE|MRMAC_DATA_PATH|FEC_SLICE0|GT_SIG|GT_REF|GT_CH0_TX_LINE|MRMAC_LOCATION|MRMAC_EXDES).*}

generate_target all [get_ips mrmac_0]
open_example_project -force -dir $root/build/exdes [get_ips mrmac_0]
puts "Example design at: $root/build/exdes  — next: scripts/integrate.tcl"
