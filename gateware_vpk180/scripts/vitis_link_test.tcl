# Bring-up step 1, software half: build the MRMAC example's own PS program
# (mrmac_exdes_test.c, generated with the IP for the chosen configuration)
# against the link-test XSA, as a standalone A72 application.
#   xsct scripts/vitis_link_test.tcl
# Output: build/link_test/vitis/mrmac_test/Debug/mrmac_test.elf
set here [file dirname [file normalize [info script]]]
set root [file normalize $here/..]
set xsa  $root/build/link_test/mrmac_link_test.xsa
set csrc [lindex [glob -nocomplain $root/build/exdes/*/*.gen/sources_1/ip/mrmac_0/sample_c_files/mrmac_exdes_test.c] 0]
if {![file exists $xsa]}  { error "missing $xsa (run build.sh link-test first)" }
if {$csrc eq ""}          { error "example C source not found under build/exdes" }
set ws $root/build/link_test/vitis
file mkdir $ws
setws $ws
if {[catch {platform read link_test} err]} {
  platform create -name link_test -hw $xsa -proc psv_cortexa72_0 -os standalone -arch 64-bit -out $ws
}
platform active link_test
platform generate
if {[catch {app read mrmac_test}]} {
  app create -name mrmac_test -platform link_test -domain standalone_domain -template {Empty Application(C)}
  importsources -name mrmac_test -path $csrc
}
app build -name mrmac_test
puts "### ELF: [glob -nocomplain $ws/mrmac_test/Debug/*.elf]"
