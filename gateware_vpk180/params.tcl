# ---------------------------------------------------------------------------
# VPK180 interlock — LINK PARAMETERS. Fill in before synthesis.
#
# Everything the port cannot decide on its own lives here: what optics sit in
# the QSFP-DD cages, what the far end runs, which cages/quads are cabled. The
# RTL (hdl/) is rate-agnostic; these values configure the MRMAC + GT and pick
# the AXI-Stream width the shim is built for. See docs/vpk180-port.md §4 for
# how each value is chosen and where it comes from.
#
# Allowed values below are taken from the MRMAC v3.2 IP definition (Vivado
# 2025.2 catalog); the GUI shows the same lists.
# ---------------------------------------------------------------------------

# --- device / board --------------------------------------------------------
set ILOCK_PART        xcvp1802-lsvc4072-2MP-e-S
set ILOCK_BOARD_PART  xilinx.com:vpk180:part0:1.0     ;# `get_board_parts *vpk180*` to confirm
set ILOCK_BOARD_REPO  ""                              ;# XilinxBoardStore/boards path if not default

# --- interlock core --------------------------------------------------------
set ILOCK_TOP_KIND    0          ;# 0 = recomp_ilock_core (what main builds), 1 = fabric_bridge (prod)
set ILOCK_TIMER_END   7999999    ;# 100 ms buckets at 80 MHz (testing override); prod = 79999
set ILOCK_BKTS_PER_CERT 10       ;# prod = 1000
set CORE_CLK_MHZ      80         ;# TIMER_END above assumes 80 MHz

# --- Ethernet rate / MAC (same for both ports) -----------------------------
# MRMAC preset. Pick the one matching optics + peer. Common choices:
#   "4x25GE Wide"                          25GbE, 1 NRZ lane per port  (needs NRZ-capable optics)
#   "1x100GE CAUI-4 Wide"                  100GbE, 4 x 25.78G NRZ, KR4 RS-FEC (100G SR4/LR4/CWDM4 QSFP28)
#   "1x100GE 100GAUI-2 (KP4 FEC) Narrow"   100GbE, 2 x 53.125G PAM4       (400G SR8-type optics in 4x100G breakout)
#   "1x100GE 100GAUI-1 Wide"               100GbE, 1 x 106.25G PAM4       (400G DR4/FR4-type optics in 4x100G breakout)
set MRMAC_PRESET      "1x100GE CAUI-4 Wide"
set MRMAC_PORT_RATE   100GE                              ;# 10GE | 25GE | 40GE | 50GE | 100GE
set MRMAC_AXIS_IF     "Independent 384b Non-Segmented"   ;# 64b for 10/25GE, 128b for 40/50GE, 384b for 100GE
set MAC_AXIS_W        384                                ;# MUST match MRMAC_AXIS_IF (ilock_pl MAC_AXIS_W)
set MRMAC_FEC         "100G (IEEE 802.3) - RS(528 514)"  ;# "FEC Disabled (Bypass)" | CL74 | RS(528 514) | RS(544 514)
set GT_SIG_MODE       NRZ                                ;# NRZ (10/25G lanes) | PAM4 (50/100G lanes)
set GT_REFCLK_MHZ     156.25                             ;# VPK180 GTM refclks (U298/U299 RC21008A) default 156.25
set GT_LINE_RATE_GBPS 25.78125                           ;# per lane

# --- which cages / quads --------------------------------------------------
# UG1582: QSFPDD1 (J1) + QSFPDD2 (J2) share GTM banks 208-211; QSFPDD3/4 sit on
# GTM 111/112/117/118. Take the exact GTM_QUAD_X?Y? / refclk site names from
# the VPK180 board XDC (AMD board lounge) or `get_sites GTM_QUAD_*` in Vivado.
set PORT0_CAGE        QSFPDD1
set PORT0_GT_QUAD     GTM_QUAD_X1Y?      ;# TODO
set PORT0_GT_REFCLK   GTM_REFCLK_X1Y?    ;# TODO
set PORT1_CAGE        QSFPDD2
set PORT1_GT_QUAD     GTM_QUAD_X1Y?      ;# TODO
set PORT1_GT_REFCLK   GTM_REFCLK_X1Y?    ;# TODO
set MRMAC0_LOC        MRMAC_X0Y?         ;# TODO: MRMAC site adjacent to PORT0_GT_QUAD
set MRMAC1_LOC        MRMAC_X0Y?         ;# TODO
# Which MRMAC data words port 0 of each block drives for MRMAC_AXIS_IF (check
# the generated example design's packet generator; 0 for a 1-port config)
set P0_WORD_BASE      0
set P1_WORD_BASE      0
