# Bring-up step 1 (docs/vpk180-port.md §6): build the UNTOUCHED MRMAC example
# design, relocated from its default quad to the port-0 cage quad, so the link
# can be validated with the example's own generator/monitor before any
# interlock RTL is involved. Produces build/link_test/*.pdi and an .xsa for the
# example's PS software (mrmac_exdes_test.c).
#   vivado -mode batch -source scripts/link_test_build.tcl
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
set xpr [glob -nocomplain $root/build/exdes/*/*.xpr]
if {[llength $xpr] != 1} { error "expected one example project under build/exdes: $xpr" }
open_project [lindex $xpr 0]

# relocate GT quad + refclk in the example XDC (backup kept as *.orig)
set xdc [lindex [get_files -filter {NAME =~ *example_top.xdc}] 0]
if {![file exists $xdc.orig]} { file copy $xdc $xdc.orig }
set fh [open $xdc.orig r]; set txt [read $fh]; close $fh
regsub -all {LOC GTM_QUAD_X[0-9]+Y[0-9]+} $txt "LOC $PORT0_GT_QUAD" txt
regsub -all {LOC GTM_REFCLK_X[0-9]+Y[0-9]+} $txt "LOC $PORT0_GT_REFCLK" txt
set fh [open $xdc w]; puts -nonewline $fh $txt; close $fh
puts "### XDC LOC lines now:"
foreach l [split $txt "\n"] { if {[regexp {^set_property LOC} $l]} { puts "###   $l" } }

update_compile_order -fileset sources_1
reset_run synth_1
launch_runs synth_1 -jobs 16
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
launch_runs impl_1 -to_step write_device_image -jobs 16
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
file mkdir $root/build/link_test
report_timing_summary -file $root/build/link_test/timing_summary.rpt
report_utilization    -file $root/build/link_test/utilization.rpt
foreach f [glob -nocomplain [get_property DIRECTORY [get_runs impl_1]]/*.pdi] {
  file copy -force $f $root/build/link_test/
  puts "### PDI: $root/build/link_test/[file tail $f]"
}
write_hw_platform -fixed -include_bit -force -file $root/build/link_test/mrmac_link_test.xsa
puts "### XSA: $root/build/link_test/mrmac_link_test.xsa"
