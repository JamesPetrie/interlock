# VPK180 interlock — board constraints for the PL top (ilock_pl).
# GT / MRMAC placement, GT refclk pins and clock definitions come from the
# generated MRMAC example design; only what ilock_pl adds is here.

# ---- status LEDs: gpio_led_0..3 (board file: BA49 AY50 BA48 AY49, LVCMOS15)
set_property PACKAGE_PIN BA49 [get_ports {led[0]}]
set_property PACKAGE_PIN AY50 [get_ports {led[1]}]
set_property PACKAGE_PIN BA48 [get_ports {led[2]}]
set_property PACKAGE_PIN AY49 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS15 [get_ports {led[*]}]

# ---- QSFP-DD sideband (UG1582 "QSFP-DD Control Signals"): RESETL / MODPRSL /
# INTL / INITMODE(LPMODE) of each cage go to Versal bank 711 PL pins; MODSELL
# goes through the U233 I2C GPIO expander; module I2C rides I2C1 via the
# TCA9548 mux (U35). Pin names/locations must be copied from the VPK180 board
# XDC (AMD board lounge). Until the PS drives them, tie: RESETL=1, INITMODE=0.
#set_property PACKAGE_PIN ??? [get_ports qsfpdd1_resetl]
#set_property PACKAGE_PIN ??? [get_ports qsfpdd1_initmode]
#set_property PACKAGE_PIN ??? [get_ports qsfpdd2_resetl]
#set_property PACKAGE_PIN ??? [get_ports qsfpdd2_initmode]

# ---- CDC: the shim's drop-flag sync and the XPM FIFOs carry their own
# constraints; the sticky->sync path is a static level.
set_false_path -to [get_pins -hierarchical -filter {NAME =~ *drop_sync_reg[0]/D}]
