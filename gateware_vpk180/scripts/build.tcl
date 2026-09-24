# Step 3: synthesize, implement, write the PDI. Works on whichever project
# integrate.tcl left under build/exdes.
#   vivado -mode batch -source scripts/build.tcl
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
set exdes [glob -nocomplain $root/build/exdes/*/*.xpr]
if {[llength $exdes] != 1} { error "expected exactly one example project under build/exdes: $exdes" }
open_project [lindex $exdes 0]
reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
launch_runs impl_1 -to_step write_device_image -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
report_timing_summary -file $root/build/timing_summary.rpt
report_utilization    -file $root/build/utilization.rpt
file mkdir $root/build/out
foreach pdi [glob -nocomplain [get_property DIRECTORY [get_runs impl_1]]/*.pdi] {
  file copy -force $pdi $root/build/out/
  puts "PDI: $root/build/out/[file tail $pdi]"
}
