# Two-port MRMAC link test (docs/vpk180-port.md): the MRMAC example twice, on cage 3 lane 3
# and cage 1 lane 3, sharing the example's CIPS block. In the example project (build/exdes):
#   port 0 (cage 3): mrmac_0 as generated (MRMAC_X0Y2) + a clone of its GT wizard moved to the
#                    quad's channels 2,3 (mrmac_0_gtwiz_d1) -> module lane 3 of QSFPDD3
#   port 1 (cage 1): mrmac_1 = clone of mrmac_0 relocated to MRMAC_X1Y1 + the original wizard
#                    (channels 0,1) on quad X1Y7 -> module lane 3 of QSFPDD1
# tools/gen_dual_link.py derives the two wrappers, the top and the XDC; the build exports
# build/dual_link/{*.pdi, mrmac_dual_link.xsa}.
#   vivado -mode batch -source scripts/dual_link_build.tcl
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
set xpr [glob -nocomplain $root/build/exdes/*/*.xpr]
if {[llength $xpr] != 1} { error "expected one example project under build/exdes: $xpr" }
open_project [lindex $xpr 0]
set proj [file dirname [lindex $xpr 0]]
set gen $root/build/dual_link/gen
file mkdir $gen

# ---- port 1 MRMAC: clone + relocate (the IP only accepts the example GT site adjacent to the MRMAC site)
if {[llength [get_ips -quiet mrmac_1]] == 0} { copy_ip -name mrmac_1 [get_ips mrmac_0] }
set_property -dict [list CONFIG.MRMAC_LOCATION_C0 $PORT1_MRMAC_LOC \
                         CONFIG.MRMAC_EXDES_GT_LOCATION_C0 $PORT1_IP_EXDES_GT_QUAD \
                         CONFIG.MRMAC_EXDES_GT_REFCLK_LOCATION_C0 $PORT1_IP_EXDES_GT_REFCLK] [get_ips mrmac_1]
puts "### mrmac_1 location: [get_property CONFIG.MRMAC_LOCATION_C0 [get_ips mrmac_1]]"

# ---- port 0 GT wizard: clone moved to channels 2,3. The wizard validates after every change, so the
# whole consistent state (channel enables, master clock channel, outclk ports) goes in one set_property.
if {[llength [get_ips -quiet mrmac_0_gtwiz_d1]] == 0} { copy_ip -name mrmac_0_gtwiz_d1 [get_ips mrmac_0_gtwiz_versal] }
set gtd1 [get_ips mrmac_0_gtwiz_d1]
if {![string match *QUAD0_RX2* [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]]} {
  set chan [list CONFIG.QUAD0_PROT0_TX0_EN false CONFIG.QUAD0_PROT0_RX0_EN false CONFIG.QUAD0_PROT0_TX1_EN false CONFIG.QUAD0_PROT0_RX1_EN false \
                 CONFIG.QUAD0_PROT0_TX2_EN true  CONFIG.QUAD0_PROT0_RX2_EN true  CONFIG.QUAD0_PROT0_TX3_EN true  CONFIG.QUAD0_PROT0_RX3_EN true]
  set mst  [list CONFIG.QUAD0_PROT0_TXMSTCLK TX2 CONFIG.QUAD0_PROT0_RXMSTCLK RX2]
  set oc23 [list CONFIG.QUAD0_TX2_OUTCLK_EN true CONFIG.QUAD0_RX2_OUTCLK_EN true CONFIG.QUAD0_TX3_OUTCLK_EN true CONFIG.QUAD0_RX3_OUTCLK_EN true]
  set oc01 [list CONFIG.QUAD0_TX0_OUTCLK_EN false CONFIG.QUAD0_RX0_OUTCLK_EN false CONFIG.QUAD0_TX1_OUTCLK_EN false CONFIG.QUAD0_RX1_OUTCLK_EN false]
  foreach v [list [concat $chan $mst $oc23 $oc01] [concat $chan $mst $oc23] [concat $chan $mst] [concat $chan $oc23 $mst]] {
    if {[catch {set_property -dict $v $gtd1} r]} { puts "### wizard variant rejected: [string range $r 0 200]" }
    if {[string match *QUAD0_RX2* [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]]} { break }
  }
}
set d1map [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]
puts "### gtwiz_d1 channel map: $d1map (success=[get_property CONFIG.INTF_QUAD_MAP_SUCESS $gtd1])"
if {![string match *QUAD0_RX2* $d1map] || [string match *QUAD0_RX0* $d1map]} { error "GT wizard clone did not move to channels 2,3: $d1map" }
generate_target all [get_ips mrmac_1 mrmac_0_gtwiz_d1]
proc gt_stub {ipname} {
  set ip [get_ips $ipname]
  foreach d [list [get_property IP_OUTPUT_DIR $ip] [get_property IP_DIR $ip]] {
    foreach f [glob -nocomplain $d/*.veo $d/*_stub.v] { return $f }
  }
  return ""
}
set stub0 [gt_stub mrmac_0_gtwiz_d1]
set stub1 [gt_stub mrmac_0_gtwiz_versal]
puts "### stubs: $stub0 | $stub1"

# ---- derive RTL + XDC
set cmd [list python3 $root/tools/gen_dual_link.py $proj/imports $gen \
  --p0-mrmac mrmac_0 --p0-gtwiz mrmac_0_gtwiz_d1     --p0-chan $PORT0_GT_CHAN --quad0 $PORT0_GT_QUAD --ref0 $PORT0_GT_REFCLK \
  --p1-mrmac mrmac_1 --p1-gtwiz mrmac_0_gtwiz_versal --p1-chan $PORT1_GT_CHAN --quad1 $PORT1_GT_QUAD --ref1 $PORT1_GT_REFCLK]
if {$stub0 ne ""} { lappend cmd --stub0 $stub0 }
if {$stub1 ne ""} { lappend cmd --stub1 $stub1 }
puts "### [exec {*}$cmd]"

# ---- sources: our top + wrappers replace the example's top/wrapper/XDC
foreach f [get_files -quiet -of [current_fileset] *mrmac_0_exdes_imp_top.sv] { set_property IS_ENABLED 0 $f }
foreach f [get_files -quiet -of [current_fileset] *imports/mrmac_0_exdes.sv] { set_property IS_ENABLED 0 $f }
foreach f [list $gen/mrmac_p0_exdes.sv $gen/mrmac_p1_exdes.sv $gen/mrmac_dual_top.sv] {
  if {[llength [get_files -quiet $f]] == 0} { add_files -norecurse $f }
}
set_property top mrmac_dual_top [current_fileset]
foreach f [get_files -quiet -of [current_fileset -constrset] *example_top.xdc] { set_property IS_ENABLED 0 $f }
if {[llength [get_files -quiet $gen/dual_link.xdc]] == 0} { add_files -fileset constrs_1 $gen/dual_link.xdc }
update_compile_order -fileset sources_1
puts "### top: [get_property top [current_fileset]]"

reset_run synth_1
launch_runs synth_1 -jobs 16
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
# ---- placement sanity before P&R: every quad / refclk buffer / MRMAC must carry the intended LOC
open_run synth_1
set bad 0
foreach pat {*quad_inst *IBUFDS_GTE5_REFCLK0 *i_mrmac_0_DUT/inst/*MRMAC_CORE*} {
  foreach c [get_cells -hier -filter "NAME =~ $pat"] {
    puts "### LOC [get_property LOC $c] : $c"
    if {[get_property LOC $c] eq ""} { set bad 1 }
  }
}
close_design
if {$bad} { error "a transceiver quad / refclk / MRMAC cell has no LOC - check the XDC patterns" }
launch_runs impl_1 -to_step write_device_image -jobs 16
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
report_timing_summary -file $root/build/dual_link/timing_summary.rpt
report_utilization    -file $root/build/dual_link/utilization.rpt
puts "### WNS: [get_property STATS.WNS [get_runs impl_1]]"
foreach f [glob -nocomplain [get_property DIRECTORY [get_runs impl_1]]/*.pdi] {
  file copy -force $f $root/build/dual_link/
  puts "### PDI: $root/build/dual_link/[file tail $f]"
}
write_hw_platform -fixed -include_bit -force -file $root/build/dual_link/mrmac_dual_link.xsa
puts "### XSA: $root/build/dual_link/mrmac_dual_link.xsa"
