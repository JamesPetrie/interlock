# ---- Board selection -------------------------------------------------------
# INTERLOCK_BOARD picks the target board (build.sh --board sets it; default mpf300):
#   mpf300  MPF300-EVAL-KIT (Rev D)       MPF300TS-1FCG1152I  2x RJ45 (VSC8575 + IOD CDR)  Libero v2024.2
#   mpf200  MPF200-ETH-SENSOR-BRIDGE-R2   MPF200T-FCG784E     2x SFP+ (XCVR quad 2)        Libero >= v2025.2
# Everything board-specific lives in ./src/boards/<board>/ (IP components, the
# top SmartDesign, PDC/SDC/IOS) plus the per-board IP-version table below.
# See docs/mpf200-esb-port.md for the MPF200 port.
if {![info exists ::env(INTERLOCK_BOARD)] || $::env(INTERLOCK_BOARD) eq ""} {
    set board mpf300
} else {
    set board $::env(INTERLOCK_BOARD)
}

# ---- Interlock core selection ----------------------------------------------
# INTERLOCK_CORE (environment variable) picks the interlock core the top
# SmartDesign instantiates as core_0; default workload. Names match the
# Makefile's DESIGN=<design> (and src_hdl/<design>.vc):
#   workload       fabric_bridge      production (inference-workload) interlock
#   recomputation  recomp_ilock_core  recomputation interlock
# Both cores have the same port list, so top.tcl's wiring is shared.
if {![info exists ::env(INTERLOCK_CORE)] || $::env(INTERLOCK_CORE) eq ""} {
    set ilock_design workload
} else {
    set ilock_design $::env(INTERLOCK_CORE)
}
switch -- $ilock_design {
    workload      { set ilock_core fabric_bridge }
    recomputation { set ilock_core recomp_ilock_core }
    default {
        error "unknown INTERLOCK_CORE '$ilock_design' (known: workload, recomputation)"
    }
}

# IP versions shared by both boards (pinned at the v2024.2-compatible values —
# see NOTES.md for why PF_INIT_MONITOR/PF_IOD_CDR had to move off the AN4623 pins).
set CoreAPB3ver {4.2.100}
set COREJTAGDEBUGver {4.0.100}
set CORESPIver {5.2.104}
set CORETSEver {3.2.119}
set CoreUARTapbver {5.7.100}
set CORERESET_PFver {2.3.100}
set MIV_RV32ver {3.0.100}

# Cores script.tcl pre-downloads into the vault before create_and_configure_core.
set _direct_repo {www.microchip-ip.com/repositories/DirectCore}
set _sg_repo     {www.microchip-ip.com/repositories/SgCore}
set board_cores [list \
        "Actel:DirectCore:CoreAPB3:$CoreAPB3ver $_direct_repo" \
        "Actel:DirectCore:COREJTAGDEBUG:$COREJTAGDEBUGver $_direct_repo" \
        "Actel:DirectCore:CORESPI:$CORESPIver $_direct_repo" \
        "Actel:DirectCore:CORETSE:$CORETSEver $_direct_repo" \
        "Actel:DirectCore:CoreUARTapb:$CoreUARTapbver $_direct_repo" \
        "Actel:DirectCore:CORERESET_PF:$CORERESET_PFver $_direct_repo" \
        "Microsemi:MiV:MIV_RV32:$MIV_RV32ver $_direct_repo"]

switch -- $board {
    mpf300 {
        set die_eval {MPF300TS}
        set eval_package {FCG1152}
        set eval_part_range {IND}
        set eval_part_speed {-1}
        set eval_die_voltage {1.05}
        set board_dir {./src/boards/mpf300_eval}
        set fw_hex {./src/src_softconsole/iog_cdr.hex}
        # Versions the kit was validated with (Libero v2024.2).  PF_INIT_MONITOR
        # 2.0.307 cannot be configured by Libero 2025.x: the package downloads
        # but extracts only its core_xml fileset -> "Cannot find Spirit core
        # configuration file" (a clean refetch gives the same), so on 2025.1 or
        # newer the 2.0.308 package (min_libero_version 2025.1) is used instead.
        # Older releases lack get_libero_release and keep 2.0.307.  (NOTES.md)
        set PF_CCCver {2.2.220}
        set PF_INIT_MONITORver {2.0.307}
        set _rel ""
        catch {set _rel [get_libero_release]}
        if {[regexp {^(\d{4})\.(\d+)} $_rel -> _y _m] && ($_y > 2025 || ($_y == 2025 && $_m >= 1))} {
            set PF_INIT_MONITORver {2.0.308}
        }
        set PF_IOD_CDRver {2.4.110}
        set PF_IOD_CDR_CCCver {2.1.111}
        lappend board_cores \
            "Actel:SgCore:PF_CCC:$PF_CCCver $_sg_repo" \
            "Actel:SgCore:PF_INIT_MONITOR:$PF_INIT_MONITORver $_sg_repo" \
            "Actel:SystemBuilder:PF_IOD_CDR:$PF_IOD_CDRver $_sg_repo" \
            "Actel:SystemBuilder:PF_IOD_CDR_CCC:$PF_IOD_CDR_CCCver $_sg_repo"
    }
    mpf200 {
        # Device settings follow Microchip's own MPF200-ETH-SENSOR-BRIDGE reference
        # design (github.com/microchip-fpga-solutions/mpf200-esb-mipi, Libero 2025.2):
        # STD speed grade, extended temperature range, VDD = 1.0 V (board jumper J18
        # default).  A STD-timed bitstream is valid on -1 silicon.
        set die_eval {MPF200T}
        set eval_package {FCG784}
        set eval_part_range {EXT}
        set eval_part_speed {STD}
        set eval_die_voltage {1.0}
        set board_dir {./src/boards/mpf200_esb}
        set fw_hex {./src/src_softconsole/iog_cdr_mpf200.hex}
        # Versions from the 2025.2 reference design's common.tcl.  PF_XCVR_ERM
        # 3.1.206 needs Libero >= 2025.1 (package metadata); 3.1.205 is the
        # 2024.2-era equivalent used for dry runs on this box.
        set PF_CCCver {2.2.222}
        set PF_INIT_MONITORver {2.0.308}
        set PF_XCVR_ERMver {3.1.206}
        set PF_TX_PLLver {2.0.304}
        set PF_XCVR_REF_CLKver {1.0.103}
        lappend board_cores \
            "Actel:SgCore:PF_CCC:$PF_CCCver $_sg_repo" \
            "Actel:SgCore:PF_INIT_MONITOR:$PF_INIT_MONITORver $_sg_repo" \
            "Actel:SgCore:PF_TX_PLL:$PF_TX_PLLver $_sg_repo" \
            "Actel:SgCore:PF_XCVR_REF_CLK:$PF_XCVR_REF_CLKver $_sg_repo" \
            "Actel:SystemBuilder:PF_XCVR_ERM:$PF_XCVR_ERMver $_sg_repo"
    }
    default {
        error "INTERLOCK_BOARD must be 'mpf300' or 'mpf200', got '$board'"
    }
}
puts "=== interlock board: $board ($die_eval $eval_package speed=$eval_part_speed range=$eval_part_range VDD=$eval_die_voltage) ==="
puts "=== interlock core: $ilock_design ($ilock_core) ==="

# Optional override hook: a Tcl file sourced after the board table, for tool
# experiments without editing this file (e.g. dry-running the mpf200 design on
# the Silver-licensed MPF300TS die under Libero 2024.2 — see
# docs/mpf200-esb-port.md).  Never needed for a normal build.
if {[info exists ::env(INTERLOCK_TCL_OVERRIDE)] && $::env(INTERLOCK_TCL_OVERRIDE) ne ""} {
    puts "=== sourcing overrides from $::env(INTERLOCK_TCL_OVERRIDE) ==="
    source $::env(INTERLOCK_TCL_OVERRIDE)
}


#tool profiles
set synprofile1 {Synplify Pro ME}
set simuprofile1 {ModelSim ME Pro}


# A common procedure called by all tests.

# Computes the runtime for each command run in Libero

#

proc run_tool_wrapper { cmd } {

 

  # get tool name from the command

  #

  regexp {run_tool\s+-name\s+\{*(\w*)\}*} $cmd full1 tool;

 

  puts "Starting $tool command";

  set full_cmd "time \{ $cmd \}";

  set TIME_start [clock seconds];
  set runtime [ eval $full_cmd ];
  set TIME_taken [expr [clock seconds] - $TIME_start];
  puts "\nRUNTIME:$tool=$TIME_taken secs\n";
 	

  set runtime_secs [ expr [lindex $runtime 0]/1000000 ];

  puts "\nRUNTIME_bytime:$tool=$runtime_secs secs\n";

 

}



