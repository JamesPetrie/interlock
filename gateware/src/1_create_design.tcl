# Import source files

file copy -force "./src/src_softconsole/iog_cdr.hex" "./$Prjname/iog_cdr.hex"
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

build_design_hierarchy

# Create, configure and generate core components
source ./src/src_components/COREJTAGDEBUG_C0.tcl
source ./src/src_components/Core_reset_pf.tcl
source ./src/src_components/CoreAPB3_0.tcl
source ./src/src_components/CORESPI_0.tcl
source ./src/src_components/CORETSE_0.tcl
source ./src/src_components/CoreUARTapb_0.tcl
source ./src/src_components/MIV_RV32_C0.tcl
source ./src/src_components/PF_CCC_0.tcl
source ./src/src_components/pf_init_monitor_0.tcl
source ./src/src_components/PF_IOD_CDR_C0.tcl
source ./src/src_components/PF_IOD_CDR_CCC_C0.tcl
# Port 1 IOD CDR — second instance only (loopback for now; bridge in a later
# PR). Note: PF_IOD_CDR_CCC is SHARED between the two IOD CDRs because two
# PF_IOD_CDR_CCC instances on the same device edge conflict on the limited
# pool of HS_IO_CLK globals (each instance claims hs_io_clk_3/7/11/15 and
# Libero's Globals Assigner can't find a non-overlapping placement).  Sharing
# loses the per-port PLL-fault isolation but is unavoidable on this board.
# The second CoreTSE re-uses CORETSE_0 as a second instance (CoreTSE's
# evaluation RTL hardcodes module names that collide if you create two
# distinct CoreTSE components, but instantiating the same component twice
# is fine).
source ./src/src_components/PF_IOD_CDR_C1.tcl
# Second CoreTSE component instance with MDIO_PHYID=19, so its internal
# PCS slave responds at a different MDIO address than CORETSE_0's slave
# at 18.  Each component generates an auto-namespaced inner module
# (CORETSE_X_CORETSE_X_0_CORETSE), so two definitions shouldn't collide.
# Sub-PR #3 iteration 1's duplicate-module error may have been from an
# older IP version or different config.
source ./src/src_components/CORETSE_1.tcl

file copy -force "./src/src_hdl/miv_rv32_opsrv_cfg_pkg.v" "./$Prjname/component/Microsemi/MiV/MIV_RV32/$MIV_RV32ver/miv_rv32_opsrv_cfg_pkg.v"


# Generate SmartDesign Components

build_design_hierarchy
source ./src/src_components/top.tcl

# Set top level module
build_design_hierarchy
set_root -module {top::work}
build_design_hierarchy
save_project
puts "Design generated successfully\n"
