# Board-specific IP components for the MPF300-EVAL-KIT (AN4623 baseline).
# Sourced by src/1_create_design.tcl after the shared components; $board_dir is
# set in common.tcl.
source $board_dir/pf_init_monitor_0.tcl
source $board_dir/PF_IOD_CDR_C0.tcl
source $board_dir/PF_IOD_CDR_CCC_C0.tcl
# Port 1 IOD CDR — second instance only (loopback for now; bridge in a later
# PR). Note: PF_IOD_CDR_CCC is SHARED between the two IOD CDRs because two
# PF_IOD_CDR_CCC instances on the same device edge conflict on the limited
# pool of HS_IO_CLK globals (each instance claims hs_io_clk_3/7/11/15 and
# Libero's Globals Assigner can't find a non-overlapping placement).  Sharing
# loses the per-port PLL-fault isolation but is unavoidable on this board.
source $board_dir/PF_IOD_CDR_C1.tcl
