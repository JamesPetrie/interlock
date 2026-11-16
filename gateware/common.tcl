# ---- Board selection -------------------------------------------------------
# INTERLOCK_BOARD (environment variable) picks the target board; default mpf300:
#   mpf300  MPF300-EVAL-KIT (Rev D)       MPF300TS-1FCG1152I  2x RJ45 (VSC8575 + IOD CDR)  Libero v2024.2
# Everything board-specific lives in ./src/boards/<board>/ (IP components, the
# top SmartDesign, PDC/SDC/IOS) plus the per-board IP-version table below.
if {![info exists ::env(INTERLOCK_BOARD)] || $::env(INTERLOCK_BOARD) eq ""} {
    set board mpf300
} else {
    set board $::env(INTERLOCK_BOARD)
}

# IP versions shared by all boards (pinned at the v2024.2-compatible values —
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
        # Versions the kit was validated with (Libero v2024.2); NOTES.md explains
        # why PF_INIT_MONITOR / PF_IOD_CDR had to move off the AN4623 pins.
        set PF_CCCver {2.2.220}
        set PF_INIT_MONITORver {2.0.307}
        set PF_IOD_CDRver {2.4.110}
        set PF_IOD_CDR_CCCver {2.1.111}
        lappend board_cores \
            "Actel:SgCore:PF_CCC:$PF_CCCver $_sg_repo" \
            "Actel:SgCore:PF_INIT_MONITOR:$PF_INIT_MONITORver $_sg_repo" \
            "Actel:SystemBuilder:PF_IOD_CDR:$PF_IOD_CDRver $_sg_repo" \
            "Actel:SystemBuilder:PF_IOD_CDR_CCC:$PF_IOD_CDR_CCCver $_sg_repo"
    }
    default {
        error "unknown INTERLOCK_BOARD '$board' (known: mpf300)"
    }
}
puts "=== interlock board: $board ($die_eval $eval_package speed=$eval_part_speed range=$eval_part_range VDD=$eval_die_voltage) ==="


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



