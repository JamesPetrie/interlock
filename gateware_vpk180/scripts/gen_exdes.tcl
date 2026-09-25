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
set ip [get_ips mrmac_0]
# Preset first (it rewrites the dependent parameters), then the overrides one
# at a time: a rejected value prints Vivado's list of valid ones instead of
# aborting the run, and the final configuration is reported for checking.
proc try_set {ip name value} {
  if {[catch {set_property $name $value $ip} err]} {
    regexp {Valid values are - ([^\n]*)} $err -> valid
    puts "### REJECTED $name = '$value'   valid: [expr {[info exists valid] ? $valid : $err}]"
  } else { puts "### set $name = $value" }
}
try_set $ip CONFIG.MRMAC_PRESET_C0 $MRMAC_PRESET
try_set $ip CONFIG.GT_TYPE_C0 GTM
# FEC lives behind the MRMAC mode: MAC+PCS has no FEC slice; MAC+PCS+FEC does.
if {$MRMAC_FEC ne "FEC Disabled (Bypass)"} { try_set $ip CONFIG.MRMAC_MODE_C0 {MAC+PCS+FEC} }
foreach {name value} [list \
    CONFIG.GT_SIG_MODE                        $GT_SIG_MODE \
    CONFIG.GT_REF_CLK_FREQ_C0                 $GT_REFCLK_MHZ \
    CONFIG.MRMAC_DATA_PATH_INTERFACE_PORT0_C0 $MRMAC_AXIS_IF \
    CONFIG.FEC_SLICE0_CFG_C0                  $MRMAC_FEC \
    CONFIG.MRMAC_LOCATION_C0                  $MRMAC0_LOC \
    CONFIG.MRMAC_EXDES_GT_LOCATION_C0         $PORT0_EXDES_GT_QUAD \
    CONFIG.MRMAC_EXDES_GT_REFCLK_LOCATION_C0  $PORT0_EXDES_GT_REFCLK] {
  try_set $ip $name $value
}
puts "### final MRMAC configuration:"
foreach n {MRMAC_PRESET_C0 MRMAC_MODE_C0 MRMAC_SPEED_C0 MAC_PORT0_RATE_C0 MRMAC_DATA_PATH_INTERFACE_PORT0_C0 FEC_SLICE0_CFG_C0 GT_SIG_MODE GT_TYPE_C0 GT_REF_CLK_FREQ_C0 GT_CH0_TX_LINE_RATE_C0 GT_MODE_C0 NUM_GT_CHANNELS MRMAC_LOCATION_C0 MRMAC_EXDES_GT_LOCATION_C0 MRMAC_EXDES_GT_REFCLK_LOCATION_C0} {
  puts "###   $n = [get_property CONFIG.$n $ip]"
}

generate_target all [get_ips mrmac_0]
open_example_project -force -dir $root/build/exdes [get_ips mrmac_0]
puts "Example design at: $root/build/exdes  — next: scripts/integrate.tcl"
