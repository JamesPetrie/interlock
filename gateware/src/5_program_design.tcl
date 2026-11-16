# Generate FPGA Array Data
## run_tool -name {GENERATEPROGRAMMINGDATA} 
run_tool_wrapper "run_tool -name {GENERATEPROGRAMMINGDATA} "


# Generate FPGA Array Data
## run_tool -name GENERATEPROGRAMMINGDATA
run_tool_wrapper "run_tool -name GENERATEPROGRAMMINGDATA"

                  
# Configure and generate Design Initialization Data and Memories
# The following can be configured:
#   - Design initialization source - sNVM/uPROM/SPI-Flash
#   - sNVM user clients
#   - uPROM user clients
#   - Fabric RAM initialization content
#   - SPI-Flash user clients
# Examples TBD
#
# Example for configuring user snvm clients 
# Note that if using relative path, the path to the mem file specified in the SNVM.cfg file is relative to the libero *.prjx file. So please adjust the path according to the project location and where the SNVM.cfg and mem files are located. 
#configure_snvm -cfg_file {../snvm/SNVM.cfg} 

## run_tool -name {DEV_MEM_INIT} 
run_tool_wrapper "run_tool -name {DEV_MEM_INIT} "


configure_ram -cfg_file {./src/src_cfg/RAM.cfg}
#configure_snvm -cfg_file {./src/src_cfg/SNVM.cfg} 

generate_design_initialization_data
    
# Hold the VSC8575 PHY in reset (PHY_RST/NRESET low) for the entire JTAG
# programming window. The default I/O state during programming is tristate,
# and the board pulls NRESET high (R551, 2K to VDD25_VSC), so without this
# the PHY keeps running un-reset with MDC/MDIO floating and can wedge; the
# post-flash CORERESET_PF pulse (~us) is far below the 2 ms warm-reset
# minimum (VSC8575 datasheet VMDS-10457, Table 165) and cannot recover it.
# (board-specific: only boards with a PHY to hold in reset ship an .ios file)
if {[file exists $board_dir/prog_io_states.ios]} {
    configure_tool -name {IO_PROGRAMMING_STATE} \
        -params "ios_file:[file normalize $board_dir/prog_io_states.ios]"
}

# Configure and generate programming file data
# Examples for configuring the programming files TBD
## run_tool -name GENERATEPROGRAMMINGFILE
run_tool_wrapper "run_tool -name GENERATEPROGRAMMINGFILE"


puts "Programmingfile generated successfully\n"
    
# Export STAPL file
export_bitstream_file \
    -file_name {top} \
    -export_dir ${PrjLocation}/designer/top/export \
    -format STP \
    -master_file 0 \
    -encrypted_uek1_file 0 \
    -encrypted_uek1_file_components {} \
    -encrypted_uek2_file 0 \
    -encrypted_uek2_file_components {} \
    -trusted_facility_file 1 \
    -trusted_facility_file_components "FABRIC SNVM"

puts "Exported bit stream successfully\n"

# Export Programming Job
# Programming job files can be imported in FlasPro Express standalone for programming the device
export_prog_job \
    -job_file_name {top} \
    -export_dir ${PrjLocation}/designer/top/export \
    -bitstream_file_type {TRUSTED_FACILITY} \
    -bitstream_file_components {FABRIC SNVM} \
    -program_design 1 \
    -program_spi_flash 0 \
    -include_plaintext_passkey 0 
	
puts "Exported job file successfully\n"
puts "Full design flow passed execution\n"	
