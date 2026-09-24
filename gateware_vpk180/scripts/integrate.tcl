# Step 2 on the Versal machine: add the interlock RTL to the generated MRMAC
# example design and report what has to be rewired by hand.
#
#   vivado -mode batch -source scripts/integrate.tcl
#
# The example design's top (mrmac_0_exdes.v) instantiates a packet
# generator/monitor per port on the same pins ilock_pl expects
# (rx_axis_tdata<N>, rx_axis_tkeep_user<N>, rx_axis_tvalid_0, rx_axis_tlast_0,
# tx_axis_*, tx_axis_tready_0) plus rx_axi_clk / tx_axi_clk. The swap is:
#   * instantiate ilock_pl in mrmac_0_exdes.v with port 0 on the first MRMAC;
#   * add a second MRMAC instance (copy of the first with MRMAC1_LOC /
#     PORT1_GT_QUAD) for port 1 — or regenerate the exdes as a 2-block design;
#   * feed core_clk / core_rst_n from a clk_wizard (80 MHz) + proc_sys_reset;
#   * keep the exdes AXI-Lite bring-up logic, set FCS pass-through
#     (docs/vpk180-port.md §3.3) in its register sequence;
#   * route led[3:0] to the gpio_led pins (constraints/vpk180_ilock.xdc).
# This script does the mechanical part and prints the exdes hierarchy so the
# manual edits are quick; it is deliberately not trying to patch generated
# Verilog blindly.
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl

set exdes [glob -nocomplain $root/build/exdes/*/*.xpr]
if {[llength $exdes] != 1} { error "expected exactly one example project under build/exdes, found: $exdes (run gen_exdes.tcl first)" }
open_project [lindex $exdes 0]

# shared interlock RTL: the same list Verilator lints (core_sources.vc, paths
# relative to gateware_vpk180/) so the two never drift
set fh [open $root/core_sources.vc r]
foreach line [split [read $fh] "\n"] {
  set line [string trim $line]
  if {$line eq "" || [string index $line 0] eq "#"} { continue }
  add_files -norecurse [file normalize $root/$line]
}
close $fh
# VPK180 shim (sim-only CDC stand-in excluded from synthesis)
foreach f [glob $root/hdl/*.sv] {
  if {[file tail $f] eq "sim_axis_cdc_fifo.sv"} { continue }
  add_files -norecurse $f
}
add_files -fileset constrs_1 -norecurse $root/constraints/vpk180_ilock.xdc
set_property file_type SystemVerilog [get_files *.sv]
update_compile_order -fileset sources_1

puts "\n==== example design hierarchy (top: [get_property top [current_fileset]]) ===="
foreach f [get_files -of_objects [get_filesets sources_1] -filter {FILE_TYPE == Verilog || FILE_TYPE == SystemVerilog}] {
  puts "  $f"
}
puts "\nNow edit the exdes top as described in the header of this script, then run scripts/build.tcl."
