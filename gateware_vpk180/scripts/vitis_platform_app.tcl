# Build a standalone A72 program from an example's sample C sources against an
# XSA, in a fresh Vitis workspace (platform + app in one session, which is the
# only way the workspace builder has worked for us).
#   xsct scripts/vitis_platform_app.tcl <xsa> <workspace-dir> <app-name> <src-dir> [extra src dirs...]
# Result: <workspace-dir>/<app-name>/Debug/<app-name>.elf, plus the platform's
# BSP under <workspace-dir>/<plat>/export/... for scripts/build_ps_app.sh.
lassign $argv xsa ws app
set srcdirs [lrange $argv 3 end]
if {![file exists $xsa]} { error "missing $xsa" }
set plat "plat_[file rootname [file tail $xsa]]"
file mkdir $ws
setws $ws
if {[catch {platform read $ws/$plat/platform.spr}]} {
  platform create -name $plat -hw $xsa -proc psv_cortexa72_0 -os standalone -arch 64-bit -out $ws
}
platform active $plat
platform generate
if {[catch {app read $app}]} {
  app create -name $app -platform $plat -domain standalone_domain -template {Empty Application(C)}
  foreach d $srcdirs { foreach f [glob -nocomplain $d/*.c $d/*.h] { importsources -name $app -path $f } }
}
app build -name $app
puts "### ELF: [glob -nocomplain $ws/$app/Debug/*.elf]"
puts "### BSP: [glob -nocomplain $ws/$plat/export/$plat/sw/$plat/standalone_domain]"
