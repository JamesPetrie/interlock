# Program the VPK180 over JTAG with the PDI from build/out.
#   vivado -mode batch -source scripts/program.tcl [-tclargs <hw_server host:port>]
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
set target [expr {[llength $argv] > 0 ? [lindex $argv 0] : "localhost:3121"}]
set pdi [lindex [glob -nocomplain $root/build/out/*.pdi] 0]
if {$pdi eq ""} { error "no PDI under build/out — run build.tcl first" }
open_hw_manager
connect_hw_server -url $target
open_hw_target
set dev [lindex [get_hw_devices xcvp1802*] 0]
current_hw_device $dev
set_property PROGRAM.FILE $pdi $dev
program_hw_devices $dev
puts "programmed $dev with $pdi"
close_hw_manager
