# Import source files

# Mi-V firmware image for this board ($fw_hex from common.tcl); RAM.cfg always
# refers to it as iog_cdr.hex inside the project.
file copy -force $fw_hex "./$Prjname/iog_cdr.hex"
import_files -hdl_source {./src/src_hdl/SSDetect.v}
import_files -hdl_source {./src/src_hdl/pkt_counter.sv}
import_files -hdl_source {./src/src_hdl/sticky_bit.sv}
import_files -hdl_source {./src/src_hdl/mdio_combiner.sv}
import_files -hdl_source {./src/src_hdl/tse1_loopback.sv}
import_files -hdl_source {./src/src_hdl/canon_pkg.sv}
import_files -hdl_source {./src/src_hdl/fabric_bridge.sv}
import_files -hdl_source {./src/src_hdl/eth_pkg.sv}
import_files -hdl_source {./src/src_hdl/crc32_pkg.sv}
import_files -hdl_source {./src/src_hdl/eth_deframe.sv}
import_files -hdl_source {./src/src_hdl/eth_reframe.sv}
import_files -hdl_source {./src/src_hdl/crypto/sha256_core.sv}
import_files -hdl_source {./src/src_hdl/crypto/sha256_msg.sv}
import_files -hdl_source {./src/src_hdl/crypto/sha256_pkg.sv}
import_files -hdl_source {./src/src_hdl/crypto/hmac_sha256.sv}
import_files -hdl_source {./src/src_hdl/axis_splitter.sv}
import_files -hdl_source {./src/src_hdl/leaf_hash.sv}
import_files -hdl_source {./src/src_hdl/record_layer.sv}
import_files -hdl_source {./src/src_hdl/serializer.sv}
import_files -hdl_source {./src/src_hdl/traffic_commit.sv}
import_files -hdl_source {./src/src_hdl/cert_build.sv}
import_files -hdl_source {./src/src_hdl/axis_mux3.sv}
import_files -hdl_source {./src/src_hdl/canon_proc.sv}
import_files -hdl_source {./src/src_hdl/axis_pkt_gate.sv}
import_files -hdl_source {./src/src_hdl/batch_buffer.sv}
import_files -hdl_source {./src/src_hdl/recomp_pkg.sv}
import_files -hdl_source {./src/src_hdl/norm_chk.sv}
import_files -hdl_source {./src/src_hdl/log2_iter.sv}
import_files -hdl_source {./src/src_hdl/recomp_feed.sv}
import_files -hdl_source {./src/src_hdl/recomp_ilock_core.sv}

build_design_hierarchy

# Create, configure and generate core components shared by all boards
source ./src/src_components/COREJTAGDEBUG_C0.tcl
source ./src/src_components/Core_reset_pf.tcl
source ./src/src_components/CoreAPB3_0.tcl
source ./src/src_components/CORESPI_0.tcl
source ./src/src_components/CORETSE_0.tcl
source ./src/src_components/CoreUARTapb_0.tcl
source ./src/src_components/MIV_RV32_C0.tcl
source ./src/src_components/PF_CCC_0.tcl
# Second CoreTSE component instance with MDIO_PHYID=19, so its internal
# PCS slave responds at a different MDIO address than CORETSE_0's slave
# at 18.  Each component generates an auto-namespaced inner module
# (CORETSE_X_CORETSE_X_0_CORETSE), so two definitions shouldn't collide.
# Sub-PR #3 iteration 1's duplicate-module error may have been from an
# older IP version or different config.
# The second CoreTSE re-uses CORETSE_0 as a second instance (CoreTSE's
# evaluation RTL hardcodes module names that collide if you create two
# distinct CoreTSE components, but instantiating the same component twice
# is fine).
source ./src/src_components/CORETSE_1.tcl

# Board-specific components (PHY-side IP and the init monitor), from the board
# directory selected in common.tcl.
source $board_dir/components.tcl

file copy -force "./src/src_hdl/miv_rv32_opsrv_cfg_pkg.v" "./$Prjname/component/Microsemi/MiV/MIV_RV32/$MIV_RV32ver/miv_rv32_opsrv_cfg_pkg.v"


# Generate SmartDesign Components

build_design_hierarchy
# Top-level SmartDesign is board-specific (same core wiring, different PHY side).
source $board_dir/top.tcl

# Set top level module
build_design_hierarchy
set_root -module {top::work}
build_design_hierarchy
save_project
puts "Design generated successfully\n"
