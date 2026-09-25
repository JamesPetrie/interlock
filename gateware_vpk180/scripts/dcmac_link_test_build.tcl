# 400G path, step 2: build the untouched DCMAC example design relocated onto
# PORTA_GT_QUADS / PORTA_GT_REFCLKS (in the order the example lists its quads).
#   vivado -mode batch -source scripts/dcmac_link_test_build.tcl
# Output: build/dcmac_link_test/*.pdi + dcmac_link_test.xsa
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
set xpr [glob -nocomplain $root/build/dcmac_exdes/*/*.xpr]
if {[llength $xpr] != 1} { error "expected one example project under build/dcmac_exdes: $xpr" }
open_project [lindex $xpr 0]
set xdc [lindex [get_files -filter {NAME =~ *example_top.xdc}] 0]
if {![file exists $xdc.orig]} { file copy $xdc $xdc.orig }
set fh [open $xdc.orig r]; set txt [read $fh]; close $fh
set qi 0; set ri 0; set out {}
foreach line [split $txt "\n"] {
  if {[regexp {^set_property LOC GTM_QUAD_X[0-9]+Y[0-9]+} $line]} {
    if {$qi < [llength $PORTA_GT_QUADS]} { regsub {GTM_QUAD_X[0-9]+Y[0-9]+} $line [lindex $PORTA_GT_QUADS $qi] line }
    incr qi
  } elseif {[regexp {^set_property LOC GTM_REFCLK_X[0-9]+Y[0-9]+} $line]} {
    if {$ri < [llength $PORTA_GT_REFCLKS]} { regsub {GTM_REFCLK_X[0-9]+Y[0-9]+} $line [lindex $PORTA_GT_REFCLKS $ri] line }
    incr ri
  }
  lappend out $line
}
set fh [open $xdc w]; puts -nonewline $fh [join $out "\n"]; close $fh
puts "### relocated $qi quad and $ri refclk LOC lines:"
foreach l $out { if {[regexp {^set_property LOC} $l]} { puts "###   $l" } }
update_compile_order -fileset sources_1
reset_run synth_1
launch_runs synth_1 -jobs 16
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
launch_runs impl_1 -to_step write_device_image -jobs 16
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
file mkdir $root/build/dcmac_link_test
report_timing_summary -file $root/build/dcmac_link_test/timing_summary.rpt
report_utilization    -file $root/build/dcmac_link_test/utilization.rpt
foreach f [glob -nocomplain [get_property DIRECTORY [get_runs impl_1]]/*.pdi] {
  file copy -force $f $root/build/dcmac_link_test/
  puts "### PDI: $root/build/dcmac_link_test/[file tail $f]"
}
write_hw_platform -fixed -include_bit -force -file $root/build/dcmac_link_test/dcmac_link_test.xsa
puts "### XSA: $root/build/dcmac_link_test/dcmac_link_test.xsa"
