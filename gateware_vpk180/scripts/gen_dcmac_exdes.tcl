# 400G path, step 1: configure one DCMAC (400GE, GTM PAM4, RS-544) at
# PORTA_DCMAC_LOC and open its example design under build/dcmac_exdes.
#   vivado -mode batch -source scripts/gen_dcmac_exdes.tcl
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
source $root/params.tcl
if {$ILOCK_BOARD_REPO ne ""} { set_param board.repoPaths [list $ILOCK_BOARD_REPO] }
create_project dcmac_exdes_gen $root/build/dcmac_exdes_gen -part $ILOCK_PART -force
catch { set_property board_part $ILOCK_BOARD_PART [current_project] }
create_ip -name dcmac -vendor xilinx.com -library ip -version 3.1 -module_name dcmac_0
set ip [get_ips dcmac_0]
proc try_set {ip name value} {
  if {[catch {set_property $name $value $ip} err]} {
    set valid "?"; regexp {Valid values are - ([^\n]*)} $err -> valid
    puts "### REJECTED $name = '$value'   valid: $valid"
  } else { puts "### set $name = $value" }
}
# one 400GE port on four 106.25G PAM4 lanes (400GAUI-4: what an 800G-DR8 /
# 400G-DR4 module presents), RS-544 CL119 FEC, no other ports
try_set $ip CONFIG.GT_TYPE_C0          GTM
try_set $ip CONFIG.GT_REF_CLK_FREQ_C0  $GT_REFCLK_MHZ
try_set $ip CONFIG.DATA_RATE_CFG_0     $DCMAC_RATE
foreach k {1 2 3 4 5} { try_set $ip CONFIG.DATA_RATE_CFG_$k NA }
try_set $ip CONFIG.MAC_PORT0_CONFIG_C0 {400GAUI-4}   ;# per-port interface: 4 x 106.25G lanes (default 400GAUI-8 = 8 x 53.125G)
try_set $ip CONFIG.GT_MODE_C0          {400GE 400GAUI-4}
try_set $ip CONFIG.FEC_SLICE0_CFG_C0   {RS(544) CL119}
try_set $ip CONFIG.DCMAC_LOCATION_C0   $PORTA_DCMAC_LOC
# lane -> GT channel (QSFPDD1: lanes 1,2 on the odd channels of the first
# quad, lanes 3,4 on the even channels of the second); verified after
# generation against the gt_quad_base settings
if {[info exists PORTA_LANE_GT_LOCS]} {
  set i 1
  foreach l $PORTA_LANE_GT_LOCS { try_set $ip CONFIG.LANE${i}_GT_LOC_C0 $l; incr i }
}
puts "### final DCMAC configuration:"
foreach n {DATA_RATE_CFG_0 DATA_RATE_CFG_4 MAC_PORT0_CONFIG_C0 NUM_GT_CHANNELS_PORT0 DCMAC_LOCATION_C0 DCMAC_MODE_C0 DCMAC_DATA_PATH_INTERFACE_C0 FEC_SLICE0_CFG_C0 GT_TYPE_C0 GT_REF_CLK_FREQ_C0 GT_MODE_C0 GT_GROUP_SELECT_C0 GTM_GROUP_SELECT1_C0 GTM_GROUP_SELECT2_C0 LANE1_GT_LOC_C0 LANE2_GT_LOC_C0 LANE3_GT_LOC_C0 LANE4_GT_LOC_C0 NUM_GT_CHANNELS GT_CH0_TX_LINE_RATE_C0} {
  if {[catch {set v [get_property CONFIG.$n $ip]}]} { set v "(n/a)" }
  puts "###   $n = $v"
}
generate_target all $ip
open_example_project -force -dir $root/build/dcmac_exdes $ip
puts "### DCMAC example design at $root/build/dcmac_exdes"
