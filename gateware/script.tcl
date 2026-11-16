source ./common.tcl
set Prjname "Libero_Project"
set PrjLocation "./$Prjname"

#variable used in the design
set SimTime 100us
set Effort_Level true
set Repair_Min_Delay true
set Multi_Pass_Layout true


# Remove existing project if present
file delete -force ${PrjLocation}

# Create and configure new project (device comes from the board table in common.tcl)
new_project \
    -name "$Prjname" \
    -location "$PrjLocation" \
    -family "PolarFire" \
    -die $die_eval \
    -package $eval_package \
    -die_voltage $eval_die_voltage \
    -speed $eval_part_speed \
    -part_range $eval_part_range \
    -hdl "VERILOG"

# Set VHDL language version for this project
#project_settings \
    -vhdl_mode VHDL_2008

select_profile -name $synprofile1
select_profile -name $simuprofile1

puts "Project created successfully"

# Pre-download all pinned IP cores into the vault. This mirrors the pattern
# Microchip's own SmartHLS-2024.2 PolarFire scripts use.  The list is assembled
# per board in common.tcl ($board_cores).
foreach _entry $board_cores {
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
