# Bring-up step 1, run: program the link-test PDI over JTAG and start the
# example's PS program on the A72. Watch its output on the Versal UART
# (tools/sc_console.py -d /dev/ttyUSB2 ... or a plain terminal at 115200).
#   xsdb scripts/run_link_test.tcl [pdi] [elf]
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
set pdi [expr {[llength $argv] > 0 ? [lindex $argv 0] : [lindex [glob -nocomplain $root/build/link_test/*.pdi] 0]}]
set elf [expr {[llength $argv] > 1 ? [lindex $argv 1] : [lindex [glob -nocomplain $root/build/link_test/vitis/mrmac_test/Debug/*.elf] 0]}]
if {$pdi eq "" || $elf eq ""} { error "need pdi ($pdi) and elf ($elf)" }
connect
# right after a reset the chain can take a few scans to come up
set found 0
for {set i 0} {$i < 30} {incr i} {
  catch {jtag targets}
  if {[llength [targets -nocase -filter {name =~ "*Versal*"}]] > 0} { set found 1; break }
  after 1000
}
if {!$found} { error "Versal not visible on JTAG (chain reads all ones?) — check sc_app setJTAGselect FTDI / POR" }
targets -set -nocase -filter {name =~ "*Versal*"}
# The board is in JTAG boot mode; a PLM from an older tool may already be
# running (e.g. loaded by the system controller) and would reject a newer PDI
# as a partial load ("Image Header Table Validation failed"). A POR first
# makes the BootROM take this PDI as the boot image.
if {[catch {rst -por} err]} { puts "### rst -por: $err (use sc_app -c reset on the system controller)" }
after 4000
puts "### programming $pdi"
device program $pdi
after 2000
targets -set -nocase -filter {name =~ "*A72*#0"}
rst -processor
puts "### loading $elf"
dow $elf
con
puts "### running; UART output on the Versal console"
