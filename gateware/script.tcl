source ./common.tcl

# ---------------------------------------------------------------------------
# Build configuration knobs (environment variables; defaults preserve the
# tree's historical behavior -- recomp top, 100 ms testing buckets):
#   TOP       = recomp | prod   which interlock top to build
#   BUCKET_MS = 100 | 1         bucket width (1 = production timing)
# The values are stamped into the PROJECT's imported HDL copies only; the
# checked-in sources are never modified. See 1_create_design.tcl.
# ---------------------------------------------------------------------------
set ILOCK_TOP "recomp"
if { [info exists ::env(TOP)] } { set ILOCK_TOP $::env(TOP) }
if { $ILOCK_TOP ni {prod recomp} } { error "TOP must be 'prod' or 'recomp' (got '$ILOCK_TOP')" }
set ILOCK_BUCKET_MS 100
if { [info exists ::env(BUCKET_MS)] } { set ILOCK_BUCKET_MS $::env(BUCKET_MS) }
if { $ILOCK_BUCKET_MS ni {1 100} } { error "BUCKET_MS must be 1 or 100 (got '$ILOCK_BUCKET_MS')" }
set ILOCK_TIMER_END     [expr {80000 * $ILOCK_BUCKET_MS - 1}]
set ILOCK_BKTS_PER_CERT [expr {1000 / $ILOCK_BUCKET_MS}]
puts "Build config: TOP=$ILOCK_TOP BUCKET_MS=$ILOCK_BUCKET_MS (TIMER_END=$ILOCK_TIMER_END BKTS_PER_CERT=$ILOCK_BKTS_PER_CERT)"

set Prjname "Libero_Project"
set PrjLocation "./$Prjname"

#variable used in the design
set SimTime 100us
set Effort_Level true
set Repair_Min_Delay true
set Multi_Pass_Layout true


# Remove existing project if present
file delete -force ${PrjLocation}

# Create and configure new project
new_project \
    -name "$Prjname" \
    -location "$PrjLocation" \
    -family "PolarFire" \
    -die $die_eval \
    -package $eval_package \
    -die_voltage "1.05" \
    -speed "-1" \
    -part_range $eval_part_range \
    -hdl "VERILOG"

# Set VHDL language version for this project
#project_settings \
    -vhdl_mode VHDL_2008

select_profile -name $synprofile1
select_profile -name $simuprofile1

puts "Project created successfully"

# Pre-download all pinned IP cores into the vault. This mirrors the pattern
# Microchip's own SmartHLS-2024.2 PolarFire scripts use.
set _direct_repo {www.microchip-ip.com/repositories/DirectCore}
set _sg_repo     {www.microchip-ip.com/repositories/SgCore}
foreach _entry [list \
        "Actel:DirectCore:CoreAPB3:$CoreAPB3ver $_direct_repo" \
        "Actel:DirectCore:COREJTAGDEBUG:$COREJTAGDEBUGver $_direct_repo" \
        "Actel:DirectCore:CORESPI:$CORESPIver $_direct_repo" \
        "Actel:DirectCore:CORETSE:$CORETSEver $_direct_repo" \
        "Actel:DirectCore:CoreUARTapb:$CoreUARTapbver $_direct_repo" \
        "Actel:DirectCore:CORERESET_PF:$CORERESET_PFver $_direct_repo" \
        "Microsemi:MiV:MIV_RV32:$MIV_RV32ver $_direct_repo" \
        "Actel:SgCore:PF_CCC:$PF_CCCver $_sg_repo" \
        "Actel:SgCore:PF_INIT_MONITOR:$PF_INIT_MONITORver $_sg_repo" \
        "Actel:SystemBuilder:PF_IOD_CDR:$PF_IOD_CDRver $_sg_repo" \
        "Actel:SystemBuilder:PF_IOD_CDR_CCC:$PF_IOD_CDR_CCCver $_sg_repo"] {
    set _vlnv [lindex $_entry 0]
    set _loc  [lindex $_entry 1]
    puts "Pre-downloading $_vlnv ..."
    if {[catch {download_core -vlnv $_vlnv -location $_loc} _err]} {
        puts "  WARNING: $_err"
    }
}

# Create design
source ./src/1_create_design.tcl

# Constrain design
source ./src/2_constrain_design.tcl

# Implement design
source ./src/4_implement_design.tcl

# Program design
source ./src/5_program_design.tcl

# Close project
close_project -save 1 
