# Board-specific IP components for the MPF200-ETH-SENSOR-BRIDGE-R2.
# Sourced by src/1_create_design.tcl after the shared components; $board_dir is
# set in common.tcl.  Order matters only in that the XCVR configurator expects
# its reference clock and TX PLL to exist.
source $board_dir/pf_init_monitor_0.tcl
source $board_dir/PF_XCVR_REF_CLK_C0.tcl
source $board_dir/PF_TX_PLL_C0.tcl
source $board_dir/PF_XCVR_ERM_C0.tcl
