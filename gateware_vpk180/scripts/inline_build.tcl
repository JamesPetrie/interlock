# In-line pass-through test (docs/vpk180-port.md): four MRMACs in the example project.
#   port 0 = cage 3 lane 1 (mrmac_0 @ MRMAC_X0Y2, original wizard, quad X0Y9)
#   port 1 = cage 1 lane 3 (mrmac_1 @ MRMAC_X1Y1, original wizard, quad X1Y7)
#   port 2 = cage 2 lane 1 (mrmac_2 @ PORT2_MRMAC_LOC, original wizard, quad X1Y6)   traffic endpoint (4-port only)
#   port 3 = cage 4 lane 7 (mrmac_3 @ PORT3_MRMAC_LOC, wizard clone, quad X0Y16)   traffic endpoint
# ilock_pl (TOP_KIND=2) sits between ports 0 and 1; passthru_ctl_axil selects per port whether the
# example generator or the external client drives TX, and crossover vs reflect. Outputs: build/inline/.
#   vivado -mode batch -source scripts/inline_build.tcl
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
set nports [expr {[llength $argv] ? [lindex $argv 0] : 4}]   ;# 2 = pass-through pair only (reflect test), 4 = with endpoints
set topo   [expr {[llength $argv] > 1 ? [lindex $argv 1] : "plain"}]   ;# plain | psdirect (PS frame ports + core) | ilock (peer-facing: core between the two MRMACs, no generators)
puts "### topology: $topo"
puts "### ports: $nports"
set xpr [glob -nocomplain $root/build/exdes/*/*.xpr]
if {[llength $xpr] != 1} { error "expected one example project under build/exdes: $xpr" }
open_project [lindex $xpr 0]
set proj [file dirname [lindex $xpr 0]]
set out [expr {$topo eq "ilock" ? "$root/build/ilock" : "$root/build/inline"}]
set gen $out/gen
file mkdir $gen

# ---- MRMAC clones. The IP only accepts the example GT site adjacent to the MRMAC site; learn it
# from the rejection message and set the three location parameters together.
proc mrmac_clone {name loc} {
  if {[llength [get_ips -quiet $name]] == 0} { copy_ip -name $name [get_ips mrmac_0] }
  set ip [get_ips $name]
  if {[get_property CONFIG.MRMAC_LOCATION_C0 $ip] ne $loc} {
    # the accepted example GT site is not reported through catch, so try candidates: the observed
    # pattern MRMAC_XsYk -> GTM_QUAD_XsY(3k+2) first (X0Y2->X0Y8, X0Y4->X0Y14), then every quad
    regexp {MRMAC_X(\d)Y(\d+)} $loc -> sx sy
    set cands [list "GTM_QUAD_X${sx}Y[expr {3*$sy+2}]"]
    for {set y 0} {$y <= 22} {incr y} { lappend cands "GTM_QUAD_X${sx}Y$y" }
    set ok 0
    foreach q $cands {
      regexp {GTM_QUAD_X(\d)Y(\d+)} $q -> qx qy
      set r "GTM_REFCLK_X${qx}Y[expr {2*$qy}]"
      if {![catch {set_property -dict [list CONFIG.MRMAC_LOCATION_C0 $loc CONFIG.MRMAC_EXDES_GT_LOCATION_C0 $q CONFIG.MRMAC_EXDES_GT_REFCLK_LOCATION_C0 $r] $ip}]} { set ok 1; break }
    }
    if {!$ok} { error "no example GT site accepted for $name at $loc" }
  }
  puts "### $name: [get_property CONFIG.MRMAC_LOCATION_C0 $ip] (example GT site [get_property CONFIG.MRMAC_EXDES_GT_LOCATION_C0 $ip] / [get_property CONFIG.MRMAC_EXDES_GT_REFCLK_LOCATION_C0 $ip])"
}
mrmac_clone mrmac_1 $PORT1_MRMAC_LOC
if {$nports == 4} {
  mrmac_clone mrmac_2 $PORT2_MRMAC_LOC
  mrmac_clone mrmac_3 $PORT3_MRMAC_LOC
}

# ---- GT wizard clone on channels 2,3 (one atomic set; see docs/vpk180-port.md §5)
if {[llength [get_ips -quiet mrmac_0_gtwiz_d1]] == 0} { copy_ip -name mrmac_0_gtwiz_d1 [get_ips mrmac_0_gtwiz_versal] }
set gtd1 [get_ips mrmac_0_gtwiz_d1]
if {![string match *QUAD0_RX2* [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]]} {
  set chan [list CONFIG.QUAD0_PROT0_TX0_EN false CONFIG.QUAD0_PROT0_RX0_EN false CONFIG.QUAD0_PROT0_TX1_EN false CONFIG.QUAD0_PROT0_RX1_EN false \
                 CONFIG.QUAD0_PROT0_TX2_EN true  CONFIG.QUAD0_PROT0_RX2_EN true  CONFIG.QUAD0_PROT0_TX3_EN true  CONFIG.QUAD0_PROT0_RX3_EN true]
  set mst  [list CONFIG.QUAD0_PROT0_TXMSTCLK TX2 CONFIG.QUAD0_PROT0_RXMSTCLK RX2]
  set oc23 [list CONFIG.QUAD0_TX2_OUTCLK_EN true CONFIG.QUAD0_RX2_OUTCLK_EN true CONFIG.QUAD0_TX3_OUTCLK_EN true CONFIG.QUAD0_RX3_OUTCLK_EN true]
  foreach v [list [concat $chan $mst $oc23] [concat $chan $mst]] {
    if {[catch {set_property -dict $v $gtd1} r]} { puts "### wizard variant rejected: [string range $r 0 200]" }
    if {[string match *QUAD0_RX2* [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]]} { break }
  }
}
set d1map [get_property CONFIG.INTF0_CHANNEL_MAP $gtd1]
puts "### gtwiz_d1 channel map: $d1map"
if {![string match *QUAD0_RX2* $d1map] || [string match *QUAD0_RX0* $d1map]} { error "GT wizard clone is not on channels 2,3: $d1map" }
generate_target all [get_ips -quiet mrmac_1 mrmac_2 mrmac_3 mrmac_0_gtwiz_d1]
proc gt_stub {ipname} {
  set ip [get_ips $ipname]
  foreach d [list [get_property IP_OUTPUT_DIR $ip] [get_property IP_DIR $ip]] {
    foreach f [glob -nocomplain $d/*.veo $d/*_stub.v] { return $f }
  }
  return ""
}

# ---- derive RTL + XDC
set cmd [list python3 $root/tools/gen_inline.py $proj/imports $gen --nports $nports]
# psdirect: the workload interlock (fabric_bridge, TOP_KIND 1) with the runtime bypass, 1 ms buckets at
# 100 MHz, one certificate per 1000 buckets; INLINE_CORE=0 selects the recomputation core instead
set core_kind [expr {[info exists ::env(INLINE_CORE)] ? $::env(INLINE_CORE) : 1}]
if {$topo eq "psdirect"} { lappend cmd --ps-direct --core-kind $core_kind --runtime-bypass --timer-end 99999 --bkts-per-cert 1000 }
# ilock: the peer-facing design — the interlock between the two MRMACs, nothing generating traffic on the FPGA
if {$topo eq "ilock"}    { lappend cmd --no-gen --core-kind $core_kind --runtime-bypass --timer-end 99999 --bkts-per-cert 1000 }
lappend cmd \
  --port 0:mrmac_0:[expr {$PORT0_GT_CHAN ? "mrmac_0_gtwiz_d1" : "mrmac_0_gtwiz_versal"}]:$PORT0_GT_CHAN:$PORT0_GT_QUAD:$PORT0_GT_REFCLK \
  --port 1:mrmac_1:mrmac_0_gtwiz_versal:$PORT1_GT_CHAN:$PORT1_GT_QUAD:$PORT1_GT_REFCLK
if {$nports == 4} {
  lappend cmd --port 2:mrmac_2:[expr {$PORT2_GT_CHAN ? "mrmac_0_gtwiz_d1" : "mrmac_0_gtwiz_versal"}]:$PORT2_GT_CHAN:$PORT2_GT_QUAD:$PORT2_GT_REFCLK \
              --port 3:mrmac_3:mrmac_0_gtwiz_d1:$PORT3_GT_CHAN:$PORT3_GT_QUAD:$PORT3_GT_REFCLK
}
set s0 [gt_stub mrmac_0_gtwiz_versal]; if {$s0 ne ""} { lappend cmd --stub-d0 $s0 }
set s1 [gt_stub mrmac_0_gtwiz_d1];     if {$s1 ne ""} { lappend cmd --stub-d1 $s1 }
puts "### [exec {*}$cmd]"

# ---- sources: the in-line top + wrappers replace the example's and the two-port build's
foreach f [get_files -quiet -of [current_fileset] *mrmac_0_exdes_imp_top.sv] { set_property IS_ENABLED 0 $f }
foreach f [get_files -quiet -of [current_fileset] *imports/mrmac_0_exdes.sv] { set_property IS_ENABLED 0 $f }
foreach f [get_files -quiet *dual_link/gen/*] { set_property IS_ENABLED 0 $f }
set hdl [list $root/hdl/ps_frame_port.sv $root/hdl/axis_pkt_fifo.sv $root/hdl/axis_downsize.sv $root/hdl/axis_upsize.sv $root/hdl/axis2tse.sv \
              $root/hdl/tse2axis.sv $root/hdl/mac_port_shim.sv $root/hdl/mrmac_axis_adapt.sv $root/hdl/ilock_pl.sv \
              $root/hdl/passthru_ctl_axil.sv $root/../gateware/src/src_hdl/pkt_counter.sv $root/../gateware/src/src_hdl/sticky_bit.sv]
if {$topo eq "psdirect" || $topo eq "ilock"} {              ;# the interlock core RTL (shared with the PolarFire build)
  set fh [open $root/core_sources.vc r]
  foreach l [split [read $fh] "\n"] { set l [string trim $l]; if {$l ne "" && ![string match #* $l]} { lappend hdl [file normalize $root/$l] } }
  close $fh
}
for {set n 0} {$n < $nports} {incr n} { lappend hdl $gen/mrmac_p${n}_exdes.sv }
lappend hdl $gen/mrmac_inline_top.sv
foreach f $hdl {
  if {![file exists $f]} { error "missing source $f" }
  if {[llength [get_files -quiet $f]] == 0} { add_files -norecurse $f } else { set_property IS_ENABLED 1 [get_files $f] }
}
set_property top mrmac_inline_top [current_fileset]
foreach f [get_files -quiet -of [current_fileset -constrset] *example_top.xdc] { set_property IS_ENABLED 0 $f }
foreach x [list $gen/inline.xdc $gen/inline_loc.xdc] {
  if {[llength [get_files -quiet $x]] == 0} { add_files -fileset constrs_1 $x } else { set_property IS_ENABLED 1 [get_files $x] }
}
set_property USED_IN_SYNTHESIS 0 [get_files $gen/inline_loc.xdc]   ;# placement only matters to implementation
update_compile_order -fileset sources_1
puts "### top: [get_property top [current_fileset]]"

# Vivado 2025.2 aborted three times in synthesis ("IO Insertion", after "Stitch Unchanged Partitions",
# abort in a background thread) with the interlock core in the design: no incremental synthesis, fewer threads
reset_run synth_1
set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1]
catch {set_property INCREMENTAL_CHECKPOINT {} [get_runs synth_1]}
set_param general.maxThreads 8
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
launch_runs impl_1 -to_step write_device_image -jobs 16
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
# ---- placement sanity: every quad / refclk buffer must sit where params.tcl says (a silently unmatched
# LOC would give a working image on the wrong pins)
set bad 0; set nq 0
foreach pat {*quad_inst *IBUFDS_GTE5_REFCLK0} {
  foreach c [get_cells -hier -filter "NAME =~ $pat"] {
    puts "### LOC [get_property LOC $c] : $c"
    incr nq
    if {[get_property LOC $c] eq ""} { set bad 1 }
  }
}
set want [list $PORT0_GT_QUAD $PORT0_GT_REFCLK $PORT1_GT_QUAD $PORT1_GT_REFCLK]
if {$nports == 4} { lappend want $PORT2_GT_QUAD $PORT2_GT_REFCLK $PORT3_GT_QUAD $PORT3_GT_REFCLK }
foreach site $want { if {[llength [get_cells -hier -quiet -filter "LOC == $site"]] == 0} { puts "### nothing placed at $site"; set bad 1 } }
if {$bad || $nq != 2 * $nports} { error "transceiver placement check failed (got $nq cells, bad=$bad)" }
report_timing_summary -file $out/timing_summary.rpt
report_utilization    -file $out/utilization.rpt
puts "### WNS: [get_property STATS.WNS [get_runs impl_1]]"
foreach f [glob -nocomplain [get_property DIRECTORY [get_runs impl_1]]/*.pdi] {
  file copy -force $f $out/
  puts "### PDI: $out/[file tail $f]"
}
write_hw_platform -fixed -include_bit -force -file $out/mrmac_inline.xsa
puts "### XSA: $out/mrmac_inline.xsa"
